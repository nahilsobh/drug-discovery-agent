import json
import requests
from tools.session import SESSION
from tools.constants import OT_URL
from tools.genomeclaw import query_genomeclaw_databases
from tools.memory import record_hit, record_adverse_event

# openFDA FAERS base URL for adverse event queries
FAERS_URL = "https://api.fda.gov/drug/event.json"


def find_hits(target: str, max_ic50_nm: float = 1000.0, max_results: int = 10) -> dict:
    """
    Hit identification: query ChEMBL for known active compounds against a gene target.
    Returns ranked hits by IC50/Ki with SMILES, assay type, and pIC50.
    """
    try:
        # Resolve target name to ChEMBL target ID
        t_url = f"https://www.ebi.ac.uk/chembl/api/data/target/search?q={requests.utils.quote(target)}&format=json&limit=3"
        t_r = requests.get(t_url, timeout=12)
        targets_data = t_r.json().get("targets", [])
        if not targets_data:
            return {"status": "no_target", "target": target, "note": "Not found in ChEMBL — try gene symbol (e.g. EGFR, KRAS)"}

        chembl_target = next(
            (t for t in targets_data if t.get("target_type") == "SINGLE PROTEIN"),
            targets_data[0]
        )
        chembl_id   = chembl_target["target_chembl_id"]
        target_name = chembl_target.get("pref_name", target)

        # Fetch IC50 bioactivities sorted by potency
        act_url = (
            f"https://www.ebi.ac.uk/chembl/api/data/activity"
            f"?target_chembl_id={chembl_id}"
            f"&standard_type__in=IC50,Ki,Kd"
            f"&standard_value__lte={max_ic50_nm}"
            f"&standard_units=nM"
            f"&pchembl_value__isnull=false"
            f"&format=json&limit={max_results}&order_by=pchembl_value"
        )
        act_r = requests.get(act_url, timeout=15)
        activities = act_r.json().get("activities", [])

        hits = []
        seen_mol = set()
        for a in activities:
            mol_id = a.get("molecule_chembl_id", "")
            if mol_id in seen_mol:
                continue
            seen_mol.add(mol_id)
            pchembl = a.get("pchembl_value")
            hits.append({
                "molecule_chembl_id": mol_id,
                "compound_name":      a.get("molecule_pref_name") or mol_id,
                "assay_type":         a.get("standard_type"),
                "value_nM":           a.get("standard_value"),
                "pIC50":              round(float(pchembl), 2) if pchembl else None,
                "assay_description":  (a.get("assay_description") or "")[:80],
            })

        # Sort descending by pIC50 (higher = more potent)
        hits.sort(key=lambda h: h.get("pIC50") or 0, reverse=True)

        # Assay provenance: count unique assay descriptions (Lowe: silent biases in single-lab data)
        unique_assays = len({h["assay_description"] for h in hits if h.get("assay_description")})
        if unique_assays > 1:
            provenance_quality = f"multi-assay ({unique_assays} unique assays) — cross-lab confirmation present"
        elif hits:
            provenance_quality = "single-assay — recommend orthogonal assay confirmation before advancing"
        else:
            provenance_quality = "no hits — provenance N/A"

        SESSION["biology"].append({
            "type": "hit_identification",
            "target": target,
            "chembl_id": chembl_id,
            "hits_found": len(hits),
        })

        # Persist to long-term memory
        for h in hits:
            record_hit(
                target=target_name,
                chembl_id=chembl_id,
                compound=h.get("compound_name", h.get("molecule_chembl_id", "")),
                ic50_nm=h.get("value_nM", 0.0),
                assay_type=h.get("assay_type", ""),
                assay_description=h.get("assay_description", ""),
                provenance_quality=provenance_quality,
                query_context=f"find_hits target={target} max_ic50={max_ic50_nm}nM",
            )

        return {
            "status":            "ok",
            "target":            target_name,
            "chembl_id":         chembl_id,
            "hits_found":        len(hits),
            "max_ic50_nM":       max_ic50_nm,
            "hits":              hits,
            "provenance_quality": provenance_quality,
            "interpretation": (
                f"{len(hits)} known actives ≤{max_ic50_nm}nM — "
                + (
                    "well-validated chemical space; run predict_admet on top candidates before advancing."
                    if len(hits) >= 5 else
                    "sparse hit matter. Recommend DEL or computational virtual screening before wet-lab commitment. "
                    "Benchmark: Sandbox AQ / UCSF Bhatt lab screened 5.5M molecules computationally in ~1 month "
                    "vs 250K physically in 1 year, achieving 30× higher hit rate — "
                    "computational pre-filtering is the faster, cheaper path."
                )
            ),
        }
    except Exception as e:
        return {"status": "error", "target": target, "error": str(e)}


def query_adverse_events(drug: str, event_type: str = "serious", top_n: int = 10) -> dict:
    """
    Post-market surveillance: query FDA Adverse Event Reporting System (FAERS)
    for a drug. Returns top MedDRA preferred terms, serious/fatal counts, and
    disproportionality signal (PRR-style count ratio).
    """
    try:
        drug_q = requests.utils.quote(drug)

        # Total reports for this drug
        total_r = requests.get(
            f"{FAERS_URL}?search=patient.drug.medicinalproduct:\"{drug_q}\"&limit=1",
            timeout=10
        )
        total_meta = total_r.json().get("meta", {}).get("results", {})
        total_reports = total_meta.get("total", 0)

        if total_reports == 0:
            return {"status": "no_data", "drug": drug, "note": "No FAERS reports found — check spelling or try generic name"}

        # Top reaction terms
        react_r = requests.get(
            f"{FAERS_URL}?search=patient.drug.medicinalproduct:\"{drug_q}\""
            f"&count=patient.reaction.reactionmeddrapt.exact&limit={top_n}",
            timeout=10
        )
        reactions = react_r.json().get("results", [])

        # Serious reports
        serious_r = requests.get(
            f"{FAERS_URL}?search=patient.drug.medicinalproduct:\"{drug_q}\"+AND+serious:1&limit=1",
            timeout=10
        )
        serious_count = serious_r.json().get("meta", {}).get("results", {}).get("total", 0)

        # Fatal reports
        fatal_r = requests.get(
            f"{FAERS_URL}?search=patient.drug.medicinalproduct:\"{drug_q}\"+AND+seriousnessdeath:1&limit=1",
            timeout=10
        )
        fatal_count = fatal_r.json().get("meta", {}).get("results", {}).get("total", 0)

        serious_pct = round(100 * serious_count / total_reports, 1) if total_reports else 0
        fatal_pct   = round(100 * fatal_count   / total_reports, 1) if total_reports else 0

        top_reactions = [
            {"reaction": r["term"], "reports": r["count"],
             "pct": round(100 * r["count"] / total_reports, 2)}
            for r in reactions
        ]

        signal = (
            "HIGH safety signal" if fatal_pct > 5 or serious_pct > 50
            else "MODERATE safety signal" if serious_pct > 20
            else "LOW safety signal"
        )

        SESSION["trial_outcomes"].append({
            "type": "adverse_events",
            "drug": drug,
            "total_reports": total_reports,
            "serious_pct": serious_pct,
            "signal": signal,
        })

        # Persist to long-term memory
        record_adverse_event(
            drug=drug,
            signal=signal,
            serious_pct=serious_pct,
            total_reports=total_reports,
        )

        return {
            "status":         "ok",
            "drug":           drug,
            "total_reports":  total_reports,
            "serious_count":  serious_count,
            "serious_pct":    serious_pct,
            "fatal_count":    fatal_count,
            "fatal_pct":      fatal_pct,
            "signal":         signal,
            "top_reactions":  top_reactions,
            "interpretation": (
                f"{total_reports} FAERS reports for {drug}. "
                f"{serious_pct}% serious, {fatal_pct}% fatal. "
                f"{signal}. Top AE: {reactions[0]['term'] if reactions else 'N/A'}."
            ),
        }
    except Exception as e:
        return {"status": "error", "drug": drug, "error": str(e)}


def find_repurposing_candidates(target_or_disease: str) -> dict:
    """
    Find approved drugs that could be repositioned into a new disease indication.
    Skips Phase I entirely — fastest path to clinic.
    Uses Open Targets drugAndClinicalCandidates with maxClinicalStage == 'APPROVAL'.
    """
    disease_q = """
    query($q: String!) {
      search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 1}) {
        hits { id name }
      }
    }"""
    try:
        r = requests.post(OT_URL, json={"query": disease_q, "variables": {"q": target_or_disease}}, timeout=10)
        r.raise_for_status()
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
    except Exception as e:
        return {"status": "error", "source": "opentargets.org", "error": str(e),
                "query": target_or_disease}

    candidates = []
    search_disease = target_or_disease

    if hits:
        disease_id     = hits[0]["id"]
        search_disease = hits[0]["name"]

        drugs_q = """
        query($id: String!) {
          disease(efoId: $id) {
            drugAndClinicalCandidates {
              rows {
                drug { name drugType }
                maxClinicalStage
              }
            }
          }
        }"""
        try:
            r = requests.post(OT_URL, json={"query": drugs_q, "variables": {"id": disease_id}}, timeout=10)
            r.raise_for_status()
            drug_rows = (
                r.json().get("data", {}).get("disease", {})
                        .get("drugAndClinicalCandidates", {}).get("rows", [])
            )
        except Exception as e:
            return {"status": "error", "source": "opentargets.org", "error": str(e),
                    "query": target_or_disease}
        for row in drug_rows:
            if row.get("maxClinicalStage") == "APPROVAL":
                drug = row.get("drug", {})
                candidates.append({
                    "drug_name":      drug.get("name", ""),
                    "drug_type":      drug.get("drugType", ""),
                    "target_disease": search_disease,
                    "stage":          "APPROVAL",
                })

    # Enrich up to 5 APPROVAL candidates with ChemBL + BindingDB binding data
    for cand in candidates[:5]:
        db_info = query_genomeclaw_databases(cand["drug_name"], ["chembl", "bindingdb"])
        if db_info.get("status") not in ("genomeclaw_not_built", "genomeclaw_offline"):
            cand["chembl_best_pIC50"]    = db_info.get("summary", {}).get("chembl_best_pIC50")
            cand["bindingdb_best_Ki_nM"] = db_info.get("summary", {}).get("bindingdb_best_Ki_nM")

    # Rank by best pIC50 when available
    candidates.sort(
        key=lambda c: (c.get("chembl_best_pIC50") or 0),
        reverse=True,
    )

    out = {
        "status":           "ok",
        "query":            target_or_disease,
        "candidates_found": len(candidates),
        "candidates":       candidates,
        "strategic_caution": (
            "Repurposing is seductive but historically has a very low hit rate — "
            "successful cases are countable on one hand (per Lowe/NIH analyses). "
            "COVID-era experience reinforced this: most repurposing attempts failed. "
            "Before committing resources: (1) run predict_admet to filter TIER-1 only, "
            "(2) run map_regulatory_path to confirm a viable endpoint exists, "
            "(3) treat any candidate as hypothesis-generating, not decision-ready."
        ),
    }
    SESSION["repurposing"].extend(candidates)
    return out
