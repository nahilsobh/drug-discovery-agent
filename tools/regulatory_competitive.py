import os
import json
import concurrent.futures
import requests
from tools.session import SESSION
from tools.constants import (
    SPONSORS, CT_URL, OT_URL, UNIPROT_URL, OPENFDA_URL,
    COMPETITORS, PREVALENCE_MAP,
)
from tools.discovery import _translational_confidence, _load_pipeline_enrichment, get_biology
from tools.genomeclaw import fold_target


def map_regulatory_path(drug: str, indication: str) -> dict:
    """
    Map the regulatory pathway for a drug + indication:
    checks local FDA guidelines knowledge base and Open Targets for biomarker requirements.
    Also loads pipeline_enrichment for known drug phase/status context.
    """
    # Load local FDA guidelines (comprehensive — 30+ indication keys)
    guidelines = {}
    fda_path = "knowledge_base/fda_guidelines.json"
    if os.path.exists(fda_path):
        with open(fda_path) as f:
            guidelines = json.load(f)
    # Remove metadata key
    guidelines.pop("_meta", None)
    guidelines.pop("_designations", None)

    # Normalise indication for lookup — try progressively broader keys
    def _normalise(s):
        return s.lower().replace(" ", "_").replace("-", "_").replace("/", "_")

    key = _normalise(indication)
    local_guideline = guidelines.get(key, {})
    # Fuzzy fallback: try partial match on any guideline key
    # Exclude stop words that are too generic (disease, cancer, syndrome, etc.) to avoid false matches
    _FUZZY_STOPWORDS = {"disease", "cancer", "syndrome", "disorder", "condition",
                        "failure", "infection", "carcinoma", "positive", "negative"}
    if not local_guideline:
        ind_lower = indication.lower()
        for gk, gv in guidelines.items():
            meaningful_words = [w for w in gk.split("_")
                                if len(w) > 5 and w not in _FUZZY_STOPWORDS]
            if meaningful_words and any(word in ind_lower for word in meaningful_words):
                local_guideline = gv
                break

    # Drug enrichment (phase/status/modality)
    enrichment = _load_pipeline_enrichment()
    drug_enr = enrichment.get(drug.lower(), {})

    # Get biomarker from Open Targets
    bio = get_biology(drug)
    biomarker = None
    if "associations" in bio:
        for assoc in bio["associations"]:
            if indication.lower() in assoc["disease"].lower():
                biomarker = bio.get("symbol")
                break

    # Determine likely expedited pathways
    designations = guidelines.get("_designations", {})
    expedited = []
    ind_lower = indication.lower()
    serious_conditions = ["cancer", "leukemia", "lymphoma", "glioblastoma", "alzheimer",
                          "parkinson", "als", "multiple sclerosis", "hemophilia", "sma",
                          "dmd", "huntington", "pnh", "rare", "orphan"]
    if any(c in ind_lower for c in serious_conditions):
        expedited.append("Fast Track (serious condition + potential unmet need)")
    if local_guideline.get("typical_designations"):
        expedited.extend(local_guideline["typical_designations"])
    expedited = list(dict.fromkeys(expedited))  # deduplicate

    # CDx requirement
    cdx_required = local_guideline.get("cdx_required", False)
    cdx_examples = local_guideline.get("cdx_examples", [])
    cdx_note = ""
    if cdx_required:
        cdx_note = "CDx required. Examples: " + (", ".join(cdx_examples) if cdx_examples else "see cdx_registry.json")
    elif biomarker:
        cdx_note = f"Consider CDx for {biomarker} selection (FoundationOne CDx or equivalent)"
    else:
        cdx_note = "No CDx requirement identified for this indication"

    # Load CDx registry for approved test platforms matching this biomarker/indication
    cdx_registry_hits = []
    cdx_registry_path = "knowledge_base/cdx_registry.json"
    if os.path.exists(cdx_registry_path):
        try:
            with open(cdx_registry_path) as f:
                cdx_db = json.load(f)
            cdx_db.pop("_meta", None)
            req_biomarker_lower = (local_guideline.get("required_biomarker") or biomarker or "").lower()
            ind_lower_short = indication.lower()
            for bm_key, bm_data in cdx_db.items():
                # Match if biomarker keyword or indication appears in CDx entry
                bm_str = (bm_key + " " + bm_data.get("biomarker", "")).lower()
                ind_match = any(ind_lower_short in str(i).lower() for i in bm_data.get("indications", []))
                bm_match = any(w in bm_str for w in req_biomarker_lower.split() if len(w) > 4)
                if ind_match or bm_match:
                    for entry in bm_data.get("approved_cdx", []):
                        cdx_registry_hits.append({
                            "biomarker":        bm_key,
                            "drug":             entry.get("drug"),
                            "platform":         entry.get("platform"),
                            "assay_type":       entry.get("assay_type"),
                            "fda_approval_date": entry.get("fda_approval_date", ""),
                            "approval_type":    entry.get("approval_type"),
                            "notes":            entry.get("notes", ""),
                        })
        except Exception:
            pass

    out = {
        "drug":              drug,
        "indication":        indication,
        "primary_endpoint":  local_guideline.get("primary_endpoint", "Consult FDA guidance for specific indication"),
        "key_secondary_endpoints": local_guideline.get("key_secondary_endpoints", []),
        "required_biomarker": local_guideline.get("required_biomarker") or biomarker or "Not specified",
        "fda_notes":         local_guideline.get("fda_notes", "Standard regulatory package"),
        "fda_preference":    local_guideline.get("fda_preference", ""),
        "companion_dx":      cdx_note,
        "cdx_required":      cdx_required,
        "cdx_approved_platforms": cdx_registry_hits[:5],  # top 5 matching platforms
        "expedited_pathways": expedited or ["Breakthrough Therapy / Fast Track likely eligible if unmet need confirmed"],
        "drug_phase":        drug_enr.get("phase", "Unknown"),
        "drug_status":       drug_enr.get("status", "Unknown"),
        "drug_modality":     drug_enr.get("modality", "Unknown"),
    }
    SESSION["regulatory"].append(out)
    return out


def rank_portfolio(assets: list = None) -> dict:
    """
    Score every asset in the Roche portfolio by composite opportunity:
    bio_score × unexplored_indications × (1 + competitive_vacuum).
    Loads assets from roche_pipeline.json if none provided.
    Merges pipeline_enrichment.json for phase/TA/indication context.
    """
    if not assets:
        try:
            with open("knowledge_base/roche_pipeline.json") as f:
                data = json.load(f)
            assets = data if isinstance(data, list) else data.get("assets", [])
        except Exception:
            return {"error": "Could not load knowledge_base/roche_pipeline.json"}

    enrichment = _load_pipeline_enrichment()
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)

    def _score_asset(asset):
        name    = asset.get("name", "Unknown")
        ensembl = asset.get("id") or asset.get("ensembl_id")
        if not ensembl:
            return None

        # Biology: top disease score from Open Targets
        try:
            q = """query($id:String!){target(ensemblId:$id){
                     associatedDiseases(page:{index:0,size:10}){
                       rows{disease{name} score}}}}"""
            r = requests.post(OT_URL, json={"query": q, "variables": {"id": ensembl}}, timeout=10)
            rows = r.json().get("data", {}).get("target", {}).get("associatedDiseases", {}).get("rows", [])
        except Exception:
            rows = []

        if not rows:
            return None

        top_score   = rows[0]["score"]
        top_disease = rows[0]["disease"]["name"]

        # Count indications with no Roche trial (parallel CT.gov queries)
        candidate_diseases = [row["disease"]["name"] for row in rows if row["score"] >= 0.5]

        def _has_roche_trial(dis):
            try:
                ct_r = requests.get(CT_URL, params={
                    "filter.advanced": sponsor_filter,
                    "query.term": dis, "pageSize": 1,
                }, timeout=8)
                return bool(ct_r.json().get("studies"))
            except Exception:
                return True  # assume covered on error to avoid false gaps

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            has_trial = list(pool.map(_has_roche_trial, candidate_diseases))
        unexplored = sum(1 for covered in has_trial if not covered)

        # Competitive vacuum
        try:
            comp_r = requests.get(CT_URL, params={
                "query.cond": top_disease,
                "query.term": "AstraZeneca OR Eli Lilly OR Novartis",
                "pageSize": 1,
            }, timeout=8)
            vacuum = 0 if comp_r.json().get("studies") else 1
        except Exception:
            vacuum = 0

        composite = round(top_score * unexplored * (1 + vacuum), 3)

        # Merge enrichment metadata
        enr = enrichment.get(name.lower(), {})
        if not enr:
            alias_val = asset.get("alias", "")
            for alias_part in alias_val.replace(";", ",").split(","):
                enr = enrichment.get(alias_part.strip().lower(), {})
                if enr:
                    break

        entry = {
            "name":               name,
            "top_disease":        top_disease,
            "bio_score":          round(top_score, 3),
            "unexplored_inds":    unexplored,
            "competitive_vacuum": bool(vacuum),
            "composite_score":    composite,
        }
        if enr:
            entry["phase"]              = enr.get("phase")
            entry["status"]             = enr.get("status")
            entry["therapeutic_area"]   = enr.get("therapeutic_area")
            entry["primary_indication"] = enr.get("primary_indication")
            entry["modality"]           = enr.get("modality")
            entry["mechanism"]          = enr.get("mechanism")
            entry["safety_signals"]     = enr.get("safety_signals")
        return entry

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_score_asset, assets))

    ranked = [r for r in results if r is not None]
    ranked.sort(key=lambda x: x["composite_score"], reverse=True)
    out = {"status": "ok", "ranked_assets": ranked, "total": len(ranked)}
    SESSION["portfolio"] = ranked
    return out


def query_competitive_intel(therapeutic_area: str = None, competitor: str = None,
                             indication: str = None) -> dict:
    """
    Query the static competitive intelligence knowledge base (competitive_intel.json).
    Returns key competitor assets filtered by therapeutic area, competitor name, or indication.
    Fast — no API calls. Use before monitor_competitive_signals for initial context,
    or when you need specific competitor mechanism/phase details.
    """
    ci_path = "knowledge_base/competitive_intel.json"
    if not os.path.exists(ci_path):
        return {"error": "competitive_intel.json not found"}

    with open(ci_path) as f:
        db = json.load(f)
    db.pop("_meta", None)

    results = []
    ta_lower = therapeutic_area.lower() if therapeutic_area else None
    comp_lower = competitor.lower() if competitor else None
    ind_lower = indication.lower() if indication else None

    def _search(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _search(v, path + "/" + k)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and "asset" in item:
                    # Check filters
                    asset_str = json.dumps(item).lower()
                    path_lower = path.lower()

                    if ta_lower and ta_lower.replace(" ", "_") not in path_lower and ta_lower not in asset_str:
                        return
                    if comp_lower:
                        path_parts = [p.lower() for p in path.split("/")]
                        if not any(comp_lower in p for p in path_parts):
                            return
                    if ind_lower and ind_lower not in asset_str and ind_lower.replace(" ", "_") not in path_lower:
                        return

                    competitor_name = path.split("/")[-2] if "/" in path else "Unknown"
                    results.append({
                        "competitor":  competitor_name,
                        "area":        path.split("/")[1] if "/" in path else "Unknown",
                        "sub_area":    path.split("/")[2] if path.count("/") >= 2 else "Unknown",
                        "asset":       item.get("asset"),
                        "type":        item.get("type"),
                        "target":      item.get("target"),
                        "phase":       item.get("phase"),
                        "indication":  item.get("indication"),
                        "notes":       item.get("notes", ""),
                    })

    _search(db)
    return {
        "filters": {"therapeutic_area": therapeutic_area, "competitor": competitor, "indication": indication},
        "total": len(results),
        "assets": results,
    }


def list_pipeline_assets(therapeutic_area: str = None, phase: str = None,
                          status: str = None, modality: str = None) -> dict:
    """
    List Roche/Genentech pipeline assets from enriched knowledge base.
    Filters by therapeutic_area, phase (approved/3/2/1), status (active/approved/discontinued),
    or modality (mAb/bispecific/small_molecule/ADC/ASO/mRNA/etc.).
    Returns matching assets with full metadata. Fast — no API calls.
    """
    enrichment = _load_pipeline_enrichment()
    results = []
    for name, meta in enrichment.items():
        if name.startswith("_"):
            continue
        if therapeutic_area and therapeutic_area.lower() not in meta.get("therapeutic_area", "").lower():
            continue
        if phase and str(phase).lower() != str(meta.get("phase", "")).lower():
            continue
        if status and status.lower() != meta.get("status", "").lower():
            continue
        if modality and modality.lower() not in meta.get("modality", "").lower():
            continue
        results.append({
            "name":              name,
            "alias":             meta.get("alias", ""),
            "phase":             meta.get("phase"),
            "status":            meta.get("status"),
            "therapeutic_area":  meta.get("therapeutic_area"),
            "primary_indication": meta.get("primary_indication"),
            "modality":          meta.get("modality"),
            "mechanism":         meta.get("mechanism"),
            "safety_signals":    meta.get("safety_signals"),
        })

    results.sort(key=lambda x: (
        {"approved": 0, "3": 1, "2": 2, "1": 3, "discontinued": 4, "partner_licensed": 5}.get(
            str(x.get("phase", "")), 9
        ),
        x.get("name", "")
    ))

    out = {
        "filters_applied": {
            "therapeutic_area": therapeutic_area,
            "phase": phase,
            "status": status,
            "modality": modality,
        },
        "total": len(results),
        "assets": results,
    }
    return out


def monitor_competitive_signals(disease: str, competitors: list = None) -> dict:
    """
    Live 8-competitor activity dashboard for a disease.
    Fires all ClinicalTrials.gov queries in parallel, then checks openFDA for recent approvals.
    """
    comps = competitors if competitors else COMPETITORS

    def query_competitor(comp):
        try:
            params = {
                "query.cond": disease,
                "query.term": comp,
                "pageSize":   20,
                "aggFilters": "status:act",
            }
            resp   = requests.get(CT_URL, params=params, timeout=10)
            resp.raise_for_status()
            studies = resp.json().get("studies", [])
            phases  = []
            for s in studies:
                phases.extend(
                    s.get("protocolSection", {}).get("designModule", {}).get("phases", [])
                )
            phase_order = {"PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}
            max_phase = max(phases, key=lambda p: phase_order.get(p, 0), default="") if phases else ""
            return {"competitor": comp, "trial_count": len(studies), "max_phase": max_phase}
        except Exception as e:
            return {"competitor": comp, "trial_count": 0, "max_phase": "", "error": str(e)}

    def query_openfda():
        try:
            resp = requests.get(
                OPENFDA_URL,
                params={"search": f'indications_and_usage:"{disease}"', "limit": 5},
                timeout=10,
            )
            resp.raise_for_status()
            return [item.get("application_number", "") for item in resp.json().get("results", [])]
        except Exception as e:
            return {"_fda_error": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
        comp_futures = {executor.submit(query_competitor, c): c for c in comps}
        fda_future   = executor.submit(query_openfda)
        signals      = [f.result() for f in concurrent.futures.as_completed(comp_futures)]
        fda_result   = fda_future.result()

    signals.sort(key=lambda x: x["trial_count"], reverse=True)
    ct_errors = [s["competitor"] for s in signals if "error" in s]

    fda_approvals = fda_result if isinstance(fda_result, list) else []
    fda_error = fda_result.get("_fda_error") if isinstance(fda_result, dict) else None

    out = {
        "status":                "ok" if not ct_errors and not fda_error else "partial",
        "disease":               disease,
        "competitors_checked":   len(comps),
        "signals":               signals,
        "recent_fda_approvals":  len(fda_approvals),
    }
    if ct_errors:
        out["ct_errors"] = ct_errors
    if fda_error:
        out["fda_error"] = fda_error
    SESSION["competitive_signals"].append(out)
    return out


def score_trial_outcome(nct_id_or_drug: str, indication: str) -> dict:
    """
    Estimate the likelihood of trial success (0.0–1.0) using a weighted heuristic
    derived from DiMasi/BIO meta-analyses. Returns score + risk factors.

    TA-adjusted priors (per Lowe/Scannell predictive-validity framework):
      CNS / neurology          : -0.10  (poorest animal→human translation)
      Anti-infectives / viral  : +0.08  (best cell→animal→human concordance)
      Metabolic / diabetes     : +0.05  (Db/Db and Ob/Ob models are predictive)
      Oncology                 :  0.00  (already reflected in phase base rates)
    """
    PHASE_BASE = {"PHASE1": 0.65, "PHASE2": 0.35, "PHASE3": 0.58, "PHASE4": 0.85}

    # TA modifier lookup — keyed by lowercase substrings found in indication
    TA_MODIFIERS = {
        "cns":           ("CNS/neurology — poor animal-to-human translation", -0.10),
        "neurolog":      ("CNS/neurology — poor animal-to-human translation", -0.10),
        "alzheimer":     ("CNS/neurology — poor animal-to-human translation", -0.10),
        "parkinson":     ("CNS/neurology — poor animal-to-human translation", -0.10),
        "psychiatric":   ("CNS/neurology — poor animal-to-human translation", -0.10),
        "schizophreni":  ("CNS/neurology — poor animal-to-human translation", -0.10),
        "depression":    ("CNS/neurology — poor animal-to-human translation", -0.10),
        "infect":        ("Anti-infective — high cell/animal predictive validity", +0.08),
        "antibacter":    ("Anti-infective — high cell/animal predictive validity", +0.08),
        "antiviral":     ("Anti-infective — high cell/animal predictive validity", +0.08),
        "bacterial":     ("Anti-infective — high cell/animal predictive validity", +0.08),
        "viral":         ("Anti-infective — high cell/animal predictive validity", +0.08),
        "hiv":           ("Anti-infective — high cell/animal predictive validity", +0.08),
        "diabetes":      ("Metabolic/diabetes — Db/Db + Ob/Ob models are predictive", +0.05),
        "metabol":       ("Metabolic/diabetes — Db/Db + Ob/Ob models are predictive", +0.05),
        "obesity":       ("Metabolic/diabetes — Db/Db + Ob/Ob models are predictive", +0.05),
        "type 2":        ("Metabolic/diabetes — Db/Db + Ob/Ob models are predictive", +0.05),
    }

    if nct_id_or_drug.upper().startswith("NCT"):
        r = requests.get(f"{CT_URL}/{nct_id_or_drug.upper()}", timeout=10)
        study = r.json() if r.status_code == 200 else {}
        studies = [study] if study else []
    else:
        params = {"query.term": nct_id_or_drug, "query.cond": indication, "pageSize": 5}
        r = requests.get(CT_URL, params=params, timeout=10)
        studies = r.json().get("studies", [])

    if not studies:
        return {"error": f"No trials found for '{nct_id_or_drug}'"}

    proto  = studies[0].get("protocolSection", {})
    design = proto.get("designModule", {})
    phases = design.get("phases", [])
    top_phase = phases[-1] if phases else ""
    score  = PHASE_BASE.get(top_phase, 0.40)
    risk_factors = []

    # TA-adjusted prior (Lowe/Scannell predictive-validity framework)
    indication_lower = indication.lower()
    ta_modifier_applied = None
    for keyword, (label, delta) in TA_MODIFIERS.items():
        if keyword in indication_lower:
            score += delta
            ta_modifier_applied = f"TA prior: {label} ({'+' if delta > 0 else ''}{delta:.2f})"
            risk_factors.append(ta_modifier_applied)
            break  # apply only the first (most specific) match

    # Enrollment modifier
    enroll = (proto.get("designModule", {}).get("enrollmentInfo") or {}).get("count")
    if isinstance(enroll, (int, float)):
        if enroll < 30:
            score -= 0.05
            risk_factors.append(f"Small enrollment ({enroll})")
        elif enroll > 100:
            score += 0.05

    # Primary endpoint
    outcomes = proto.get("outcomesModule", {}).get("primaryOutcomes", [])
    if outcomes:
        measure = (outcomes[0].get("measure") or "").lower()
        if "survival" in measure:
            score += 0.05
        elif not measure:
            score -= 0.05
            risk_factors.append("No primary endpoint specified")
    else:
        score -= 0.05
        risk_factors.append("Primary endpoint missing")

    # Single-arm penalty
    arms = proto.get("armsInterventionsModule", {}).get("armGroups", [])
    if len(arms) == 1:
        score -= 0.05
        risk_factors.append("Single-arm study")

    # Terminated
    status = proto.get("statusModule", {}).get("overallStatus", "")
    if status == "TERMINATED":
        score *= 0.2
        risk_factors.append("TERMINATED")

    score = round(max(0.05, min(0.95, score)), 3)
    result = {
        "trial_id":        nct_id_or_drug,
        "indication":      indication,
        "phase":           top_phase,
        "enrollment":      enroll,
        "outcome_score":   score,
        "ta_modifier":     ta_modifier_applied,
        "risk_factors":    risk_factors,
    }
    SESSION["trial_outcomes"].append(result)
    return result


def check_orphan_eligibility(disease: str) -> dict:
    """
    Check if a disease qualifies for Orphan Drug Designation (US < 200K, EU < 165K patients).
    Returns eligibility, benefits (7yr exclusivity, tax credits, fee waivers), and confidence.
    """
    key = disease.lower().strip()
    prevalence = None
    confidence = "low"

    for map_key, data in PREVALENCE_MAP.items():
        if map_key in key or key in map_key:
            prevalence = data["prevalence"]
            confidence = "high"
            break

    us_eligible = (prevalence < 200000) if prevalence is not None else None
    eu_eligible = (prevalence < 165000) if prevalence is not None else None

    benefits = []
    if us_eligible:
        benefits += [
            "7 years market exclusivity (US FDA)",
            "Tax credits up to 25% on clinical trial costs",
            "Reduced FDA filing fees (PDUFA waiver)",
            "Smaller trial size requirements accepted",
        ]
    if eu_eligible:
        benefits += [
            "10 years market exclusivity (EMA)",
            "EMA protocol assistance and scientific advice",
            "Reduced regulatory fees",
        ]

    result = {
        "disease":              disease,
        "us_eligible":          us_eligible,
        "eu_eligible":          eu_eligible,
        "estimated_prevalence": prevalence,
        "benefits":             benefits,
        "confidence":           confidence,
        "note":                 "Verify with current NORD/Orphanet databases before IND filing" if confidence == "low" else "",
    }
    SESSION["orphan_flags"].append(result)
    return result


def get_protein_structure_context(gene_symbol: str) -> dict:
    """
    Validates target druggability before committing R&D resources.
    Queries UniProt REST for protein function + binding sites, and Open Targets
    tractability scores to recommend modality (small molecule / antibody / PROTAC).
    """
    # 1. UniProt REST
    uniprot_fields = "protein_name,cc_function,ft_binding,ft_domain,length"
    r = requests.get(
        UNIPROT_URL,
        params={"query": f"gene:{gene_symbol} AND organism_id:9606",
                "fields": uniprot_fields, "format": "json", "size": 1},
        timeout=10,
    )
    protein_name = ""
    function_txt = ""
    if r.status_code == 200:
        entries = r.json().get("results", [])
        if entries:
            e = entries[0]
            protein_name = (
                e.get("proteinDescription", {})
                 .get("recommendedName", {})
                 .get("fullName", {})
                 .get("value", "")
            )
            for c in e.get("comments", []):
                if c.get("commentType") == "FUNCTION":
                    texts = c.get("texts", [])
                    if texts:
                        function_txt = texts[0].get("value", "")[:300]
                        break

    # 2. Open Targets tractability
    gene_q = """
    query($q: String!) {
      search(queryString: $q, entityNames: ["target"], page: {index: 0, size: 1}) {
        hits { id name }
      }
    }"""
    r = requests.post(OT_URL, json={"query": gene_q, "variables": {"q": gene_symbol}}, timeout=10)
    hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    druggability = "Unknown"
    modality     = "Unknown"
    tractability = {}

    if hits:
        ensembl_id = hits[0]["id"]
        tract_q = """
        query($id: String!) {
          target(ensemblId: $id) {
            tractability { label modality value }
          }
        }"""
        r = requests.post(OT_URL, json={"query": tract_q, "variables": {"id": ensembl_id}}, timeout=10)
        tract_rows = r.json().get("data", {}).get("target", {}).get("tractability", []) or []

        sm_labels = [t["label"] for t in tract_rows if t.get("modality") == "SM"  and t.get("value")]
        ab_labels = [t["label"] for t in tract_rows if t.get("modality") == "AB"  and t.get("value")]
        oc_labels = [t["label"] for t in tract_rows if t.get("modality") == "OC"  and t.get("value")]

        HIGH_SM = {"Approved Drug", "Advanced Clinical"}
        HIGH_AB = {"Approved Drug", "Advanced Clinical", "Antibodies in clinical phase"}
        MED_SM  = {"Phase 1 Clinical", "Structure with Ligand", "High-Quality Pocket", "Druggable Family"}

        if any(l in HIGH_SM for l in sm_labels):
            druggability, modality = "High", "Small Molecule"
        elif any(l in HIGH_AB for l in ab_labels):
            druggability, modality = "High", "Antibody"
        elif any(l in MED_SM for l in sm_labels) or oc_labels:
            druggability, modality = "Medium", "Small Molecule"
        elif ab_labels:
            druggability, modality = "Medium", "Antibody"
        else:
            druggability = "Low"

        tractability = {"sm": sm_labels[:3], "ab": ab_labels[:3], "other": oc_labels[:2]}

    # 3. GenomeClaw fold — upgrade druggability verdict with real 3D pLDDT
    fold = fold_target(gene_symbol)
    plddt = fold.get("plddt_mean") if fold.get("status") not in ("genomeclaw_offline", "error", "timeout") else None
    if plddt is not None:
        if plddt > 0.70 and druggability in ("Unknown", "Low", "Medium"):
            druggability = "High (3D structure confirmed)"
        elif plddt > 0.50 and druggability in ("Unknown", "Low"):
            druggability = "Medium (partial 3D structure)"
        elif plddt <= 0.50 and druggability == "Unknown":
            druggability = "Low (disordered)"

    result = {
        "gene_symbol":           gene_symbol,
        "protein_name":          protein_name,
        "function_summary":      function_txt,
        "druggability":          druggability,
        "recommended_modality":  modality,
        "tractability_evidence": tractability,
        "plddt_mean":            plddt,
        "fold_confidence":       fold.get("confidence_label"),
    }
    SESSION["protein_structures"].append(result)
    return result
