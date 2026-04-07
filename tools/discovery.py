import time
import json
import concurrent.futures
import requests
from tools.session import SESSION
from tools.constants import SPONSORS, CT_URL, OT_URL


def search_roche_trials(therapeutic_area: str, phase: str = None) -> dict:
    """Query ClinicalTrials.gov for active Roche/Genentech trials in a therapeutic area."""
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)
    params = {
        "filter.advanced": sponsor_filter,
        "query.cond": therapeutic_area,
        "pageSize": 50,
    }
    if phase:
        params["aggFilters"] = f"phase:{phase}"

    r = requests.get(CT_URL, params=params, timeout=15)
    studies = r.json().get("studies", [])

    results = []
    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        arms = proto.get("armsInterventionsModule", {}).get("interventions", [])

        drugs = [
            {"name": a.get("name"), "aliases": a.get("otherNames", [])}
            for a in arms if a.get("type") == "DRUG"
        ]
        results.append({
            "nct_id":    ident.get("nctId"),
            "title":     ident.get("briefTitle"),
            "status":    status.get("overallStatus"),
            "phase":     proto.get("designModule", {}).get("phases", []),
            "drugs":     drugs,
        })

    out = {"therapeutic_area": therapeutic_area, "trial_count": len(results), "trials": results}
    SESSION["trials"].append(out)
    return out


def get_biology(target: str) -> dict:
    """Query Open Targets for disease associations of a drug or gene target."""
    # First try as a drug name → get linked target → get associations
    drug_query = """
    query($name: String!) {
      search(queryString: $name, entityNames: ["drug"], page: {index: 0, size: 1}) {
        hits {
          object {
            ... on Drug {
              name
              linkedTargets { rows { id approvedSymbol } }
            }
          }
        }
      }
    }"""
    r = requests.post(OT_URL, json={"query": drug_query, "variables": {"name": target}}, timeout=10)
    hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    ensembl_id = None
    symbol = None
    if hits:
        rows = hits[0].get("object", {}).get("linkedTargets", {}).get("rows", [])
        if rows:
            ensembl_id = rows[0]["id"]
            symbol = rows[0]["approvedSymbol"]

    # If not found as drug, try as gene symbol
    if not ensembl_id:
        gene_query = """
        query($name: String!) {
          search(queryString: $name, entityNames: ["target"], page: {index: 0, size: 1}) {
            hits { id name }
          }
        }"""
        r = requests.post(OT_URL, json={"query": gene_query, "variables": {"name": target}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if hits:
            ensembl_id = hits[0]["id"]
            symbol = hits[0]["name"]

    if not ensembl_id:
        return {"error": f"Target '{target}' not found in Open Targets"}

    # Get top disease associations
    assoc_query = """
    query($id: String!) {
      target(ensemblId: $id) {
        approvedSymbol
        associatedDiseases(page: {index: 0, size: 10}) {
          rows { disease { name id } score }
        }
      }
    }"""
    r = requests.post(OT_URL, json={"query": assoc_query, "variables": {"id": ensembl_id}}, timeout=10)
    data = r.json().get("data", {}).get("target", {})
    rows = data.get("associatedDiseases", {}).get("rows", [])

    associations = [
        {"disease": row["disease"]["name"], "score": round(row["score"], 3)}
        for row in rows
    ]

    out = {
        "target":       target,
        "ensembl_id":   ensembl_id,
        "symbol":       symbol or data.get("approvedSymbol"),
        "associations": associations,
    }
    SESSION["biology"].append(out)
    return out


def check_competitor_trials(disease: str, competitor: str) -> dict:
    """Check how many trials a competitor has for a given disease."""
    COMPETITOR_MAP = {
        "astrazeneca": "AstraZeneca",
        "lilly":       "Eli Lilly",
        "eli lilly":   "Eli Lilly",
        "novartis":    "Novartis",
        "pfizer":      "Pfizer",
        "merck":       "Merck",
        "bms":         "Bristol-Myers Squibb",
        "abbvie":      "AbbVie",
    }
    sponsor_name = COMPETITOR_MAP.get(competitor.lower(), competitor)
    params = {
        "query.cond": disease,
        "query.term": sponsor_name,
        "pageSize":   10,
    }
    try:
        r = requests.get(CT_URL, params=params, timeout=10)
        r.raise_for_status()
        studies = r.json().get("studies", [])
    except Exception as e:
        return {"status": "error", "source": "clinicaltrials.gov", "error": str(e),
                "competitor": sponsor_name, "disease": disease}
    return {
        "status":        "ok",
        "competitor":    sponsor_name,
        "disease":       disease,
        "trial_count":   len(studies),
        "trials":        [s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
                          for s in studies],
    }


def _translational_confidence(ta: str) -> tuple[str, str]:
    """
    Return (tier, rationale) for a therapeutic area based on known animal→human
    translation quality (Lowe/Scannell predictive-validity framework).

    HIGH   — Anti-infectives, metabolic/diabetes: strong cell→animal→human concordance.
    MODERATE — Oncology, immunology, cardiovascular: reasonable but imperfect models.
    LOW    — CNS/neurology: near-absent predictive models; highest clinical attrition.
    """
    ta_lower = ta.lower()
    CNS_KEYWORDS      = {"cns", "neurolog", "alzheimer", "parkinson", "psychiatr",
                         "schizophreni", "depression", "dementia", "epilep", "neuro"}
    HIGH_KEYWORDS     = {"infect", "antibacter", "antiviral", "bacterial", "viral",
                         "hiv", "tuberculosis", "diabetes", "metabol", "obesity"}
    for kw in CNS_KEYWORDS:
        if kw in ta_lower:
            return ("LOW",
                    "CNS/neurology has near-absent predictive animal models and the "
                    "highest clinical attrition in pharma. Biology score should be "
                    "treated as hypothesis-generating only.")
    for kw in HIGH_KEYWORDS:
        if kw in ta_lower:
            return ("HIGH",
                    "Anti-infective or metabolic indication — cell/animal models show "
                    "strong concordance with human outcomes. Biology score is more "
                    "reliable here than in most other TAs.")
    return ("MODERATE",
            "Moderate animal-to-human translation. Biology score is informative but "
            "verify with phenotypic or organoid data before committing to IND.")


def find_gaps(therapeutic_area: str, min_bio_score: float = 0.60) -> dict:
    """
    Core gap analysis: cross-references Open Targets biology with Roche's
    ClinicalTrials.gov pipeline to surface high-evidence, zero-trial opportunities.
    """
    # 1. Get Roche trials in this area
    trial_data = search_roche_trials(therapeutic_area)
    if trial_data.get("status") == "error":
        return {"status": "error", "source": "clinicaltrials.gov",
                "error": trial_data.get("error", "unknown"), "therapeutic_area": therapeutic_area}
    roche_diseases = set()
    for trial in trial_data["trials"]:
        title_lower = trial.get("title", "").lower()
        roche_diseases.add(title_lower)

    # 2. Get top biological targets for the therapeutic area
    target_query = """
    query($area: String!) {
      search(queryString: $area, entityNames: ["disease"], page: {index: 0, size: 5}) {
        hits {
          id
          name
          object {
            ... on Disease {
              name
              associatedTargets(page: {index: 0, size: 10}) {
                rows { target { approvedSymbol id } score }
              }
            }
          }
        }
      }
    }"""
    try:
        r = requests.post(OT_URL, json={"query": target_query, "variables": {"area": therapeutic_area}}, timeout=10)
        r.raise_for_status()
        disease_hits = r.json().get("data", {}).get("search", {}).get("hits", [])
    except Exception as e:
        return {"status": "error", "source": "opentargets.org",
                "error": str(e), "therapeutic_area": therapeutic_area}

    gaps = []
    for hit in disease_hits:
        disease_name = hit.get("name", "")
        assoc_targets = hit.get("object", {}).get("associatedTargets", {}).get("rows", [])

        for row in assoc_targets:
            score = row.get("score", 0)
            if score < min_bio_score:
                continue
            symbol = row["target"]["approvedSymbol"]
            ensembl = row["target"]["id"]

            # Check if Roche has a trial for this target
            ct_params = {
                "filter.advanced": ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS),
                "query.term": symbol,
                "pageSize": 5,
            }
            ct_r = requests.get(CT_URL, params=ct_params, timeout=10)
            roche_trials = ct_r.json().get("studies", [])
            time.sleep(0.2)

            if not roche_trials:
                tc_tier, tc_rationale = _translational_confidence(disease_name)
                gaps.append({
                    "disease":                 disease_name,
                    "target":                  symbol,
                    "ensembl_id":              ensembl,
                    "bio_score":               round(score, 3),
                    "roche_trials":            0,
                    "status":                  "STRATEGIC GAP",
                    "translational_confidence": tc_tier,
                    "translational_note":       tc_rationale,
                })

    ta_tc_tier, ta_tc_rationale = _translational_confidence(therapeutic_area)
    out = {
        "status":                    "ok",
        "therapeutic_area":          therapeutic_area,
        "roche_active_trials":       trial_data["trial_count"],
        "gaps_found":                len(gaps),
        "translational_confidence":  ta_tc_tier,
        "translational_note":        ta_tc_rationale,
        "gaps": sorted(gaps, key=lambda x: x["bio_score"], reverse=True),
    }
    SESSION["gaps"].extend(out["gaps"])
    return out


def _load_pipeline_enrichment() -> dict:
    """Load pipeline_enrichment.json as a name→metadata lookup (case-insensitive)."""
    try:
        with open("knowledge_base/pipeline_enrichment.json") as f:
            raw = json.load(f)
        return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def find_combinations(disease: str) -> dict:
    """
    Find Roche drugs that target complementary pathways in the same disease.
    Returns pairs of drugs that appear in combination arms or share a disease indication.
    """
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)
    params = {"filter.advanced": sponsor_filter, "query.cond": disease, "pageSize": 50}
    r = requests.get(CT_URL, params=params, timeout=15)
    studies = r.json().get("studies", [])

    # Collect drugs per study
    combinations = []
    all_drugs = {}  # drug_name → set of diseases from OT

    for study in studies:
        arms = study.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions", [])
        drug_names = [
            a["name"] for a in arms
            if a.get("type") == "DRUG" and any(
                a["name"].upper().startswith(p) or
                any(o.upper().startswith(p) for o in a.get("otherNames", []))
                for p in ("RG", "RO", "GDC", "MTIG")
            )
        ]
        if len(drug_names) >= 2:
            nct = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            combinations.append({"nct_id": nct, "drugs": drug_names})

    # Deduplicate drug pairs
    seen_pairs = set()
    unique_pairs = []
    for combo in combinations:
        drugs = sorted(combo["drugs"])
        for i in range(len(drugs)):
            for j in range(i + 1, len(drugs)):
                pair = (drugs[i], drugs[j])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    unique_pairs.append({"drug_a": pair[0], "drug_b": pair[1], "nct_id": combo["nct_id"]})

    out = {
        "disease":      disease,
        "combo_trials": len(combinations),
        "unique_pairs": unique_pairs[:20],
    }
    SESSION["combinations"].append(out)
    return out


def find_shared_targets(disease1: str, disease2: str, min_score: float = 0.70) -> dict:
    """
    Find gene targets shared between two diseases with high Open Targets confidence.
    Answers: "What targets are shared between Alzheimer's and Parkinson's with bio score > 0.7?"
    Uses OT search + disease association queries run in parallel.
    """
    def resolve_disease(name):
        q = """query($q: String!) {
          search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 1}) {
            hits { id name }
          }
        }"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"q": name}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        return (hits[0]["id"], hits[0]["name"]) if hits else (None, name)

    def fetch_targets(disease_id, min_s):
        q = """query($id: String!, $size: Int!) {
          disease(efoId: $id) {
            associatedTargets(page: {index: 0, size: $size}) {
              rows {
                target { id approvedSymbol approvedName }
                score
              }
            }
          }
        }"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"id": disease_id, "size": 200}}, timeout=15)
        rows = (r.json().get("data", {}).get("disease", {})
                        .get("associatedTargets", {}).get("rows", []))
        return {
            row["target"]["approvedSymbol"]: {
                "ensembl_id":    row["target"]["id"],
                "gene_name":     row["target"]["approvedName"],
                "score":         round(row["score"], 3),
            }
            for row in rows if row.get("score", 0) >= min_s
        }

    # Resolve both diseases and fetch targets in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(resolve_disease, disease1)
        f2 = ex.submit(resolve_disease, disease2)
        id1, name1 = f1.result()
        id2, name2 = f2.result()

    if not id1 or not id2:
        missing = disease1 if not id1 else disease2
        return {"status": "error", "note": f"Could not resolve disease: {missing}"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        t1 = ex.submit(fetch_targets, id1, min_score)
        t2 = ex.submit(fetch_targets, id2, min_score)
        targets1 = t1.result()
        targets2 = t2.result()

    # Intersect
    shared_symbols = set(targets1.keys()) & set(targets2.keys())
    shared = []
    for sym in sorted(shared_symbols, key=lambda s: -(targets1[s]["score"] + targets2[s]["score"]) / 2):
        shared.append({
            "gene_symbol":  sym,
            "gene_name":    targets1[sym]["gene_name"],
            "ensembl_id":   targets1[sym]["ensembl_id"],
            f"score_{name1[:20]}": targets1[sym]["score"],
            f"score_{name2[:20]}": targets2[sym]["score"],
            "mean_score":   round((targets1[sym]["score"] + targets2[sym]["score"]) / 2, 3),
        })

    return {
        "disease1":         name1,
        "disease2":         name2,
        "min_score":        min_score,
        "targets_in_d1":    len(targets1),
        "targets_in_d2":    len(targets2),
        "shared_count":     len(shared),
        "shared_targets":   shared[:30],
    }


def find_phenocopiers(target_gene: str, disease_context: str = "", top_n: int = 15) -> dict:
    """
    Find genes that share downstream biology with a target — 'phenocopiers'.
    Based on the Plex Research Wnt pathway approach (AIA4S 2026): genes whose perturbation
    produces similar transcriptomic/network profiles surface novel targets for the same indication.

    Tries Open Targets similar-targets API first; falls back to STRING interaction partners.
    Cross-filters by disease_context if provided.
    """
    gene = target_gene.strip().upper()

    # Step 1: Resolve to Ensembl ID via Open Targets
    ensembl_id = None
    try:
        resolve_q = """
        query($gene: String!) {
          search(queryString: $gene, entityNames: ["target"], page: {index: 0, size: 3}) {
            hits { id entity { ... on Target { approvedSymbol id } } }
          }
        }"""
        r = requests.post(OT_URL, json={"query": resolve_q, "variables": {"gene": gene}}, timeout=10)
        for hit in r.json().get("data", {}).get("search", {}).get("hits", []):
            entity = hit.get("entity", {})
            if entity.get("approvedSymbol", "").upper() == gene:
                ensembl_id = entity.get("id")
                break
        if not ensembl_id:
            hits_list = r.json().get("data", {}).get("search", {}).get("hits", [])
            if hits_list:
                ensembl_id = hits_list[0].get("id")
    except Exception:
        pass

    phenocopiers = []
    method_used  = "none"

    # Step 2a: Open Targets similar-targets endpoint
    if ensembl_id:
        try:
            sim_q = """
            query($id: String!, $size: Int!) {
              target(ensemblId: $id) {
                similarEntities(additionalIds: [], size: $size) {
                  score
                  object { ... on Target { approvedSymbol approvedName id } }
                }
              }
            }"""
            r = requests.post(
                OT_URL,
                json={"query": sim_q, "variables": {"id": ensembl_id, "size": top_n + 5}},
                timeout=12,
            )
            similar = (r.json().get("data", {}).get("target") or {}).get("similarEntities", [])
            if similar:
                method_used = "OpenTargets_similar_targets"
                for s in similar[:top_n]:
                    obj = s.get("object", {})
                    sym = obj.get("approvedSymbol", "")
                    if sym and sym.upper() != gene:
                        phenocopiers.append({
                            "gene_symbol":      sym,
                            "gene_name":        obj.get("approvedName", ""),
                            "ensembl_id":       obj.get("id", ""),
                            "similarity_score": round(s.get("score", 0), 3),
                            "disease_score":    None,
                            "rationale":        f"Open Targets functional similarity to {gene}",
                        })
        except Exception:
            pass

    # Step 2b: STRING fallback — functional interaction partners
    if not phenocopiers:
        try:
            string_url = (
                f"https://string-db.org/api/json/interaction_partners"
                f"?identifiers={gene}&required_score=800&limit={top_n + 5}&species=9606"
            )
            r = requests.get(string_url, timeout=12)
            partners = r.json() if r.status_code == 200 else []
            if partners:
                method_used = "STRING_functional_partners"
                for p in partners[:top_n]:
                    partner_gene = p.get("preferredName_B") or p.get("stringId_B", "")
                    if partner_gene and partner_gene.upper() != gene:
                        phenocopiers.append({
                            "gene_symbol":      partner_gene,
                            "gene_name":        p.get("annotation", ""),
                            "ensembl_id":       "",
                            "similarity_score": round(p.get("score", 0) / 1000, 3),
                            "disease_score":    None,
                            "rationale":        f"STRING functional partner of {gene} (score {p.get('score',0)})",
                        })
        except Exception:
            pass

    # Step 3: Cross-filter by disease_context — add Open Targets disease scores
    if disease_context and phenocopiers:
        try:
            # Resolve disease EFO ID
            dis_q = """
            query($d: String!) {
              search(queryString: $d, entityNames: ["disease"], page: {index: 0, size: 1}) {
                hits { id }
              }
            }"""
            dr = requests.post(OT_URL, json={"query": dis_q, "variables": {"d": disease_context}}, timeout=8)
            dis_hits = dr.json().get("data", {}).get("search", {}).get("hits", [])
            efo_id = dis_hits[0]["id"] if dis_hits else None

            if efo_id:
                # Fetch top associated targets for this disease
                assoc_q = """
                query($efo: String!) {
                  disease(efoId: $efo) {
                    associatedTargets(page: {index: 0, size: 200}) {
                      rows { target { approvedSymbol } score }
                    }
                  }
                }"""
                ar = requests.post(OT_URL, json={"query": assoc_q, "variables": {"efo": efo_id}}, timeout=12)
                rows = (ar.json().get("data", {}).get("disease") or {}).get("associatedTargets", {}).get("rows", [])
                score_lookup = {row["target"]["approvedSymbol"].upper(): row["score"] for row in rows}

                for p in phenocopiers:
                    p["disease_score"] = round(score_lookup.get(p["gene_symbol"].upper(), 0), 3)

                # Keep only those with disease score > 0.3, or all if none qualify
                filtered = [p for p in phenocopiers if (p["disease_score"] or 0) > 0.3]
                if filtered:
                    phenocopiers = sorted(filtered, key=lambda x: x["disease_score"] or 0, reverse=True)
        except Exception:
            pass

    result = {
        "query_target":   gene,
        "ensembl_id":     ensembl_id or "unresolved",
        "disease_context": disease_context or "none",
        "method":         method_used,
        "phenocopiers_count": len(phenocopiers),
        "phenocopiers":   phenocopiers[:top_n],
        "note": (
            f"These {len(phenocopiers)} gene(s) share downstream biology with {gene} — "
            "candidate targets for the same indication. "
            "Validate top hits with find_gaps and get_biology before advancing."
            if phenocopiers else
            f"No phenocopiers found for {gene}. Try a broader disease_context or check gene symbol."
        ),
    }
    SESSION["phenocopiers"].append(result)
    return result
