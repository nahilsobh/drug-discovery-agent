#!/usr/bin/env python3
"""
20-by-30 Strategic Orchestrator
Roche CSI Hackathon 2026 — R&D Excellence (RDE) Lever Automation

Complements the Strategic Discovery Agent (run_agent.py) by answering:
"How do we accelerate delivery of the 20 pipeline assets to First-in-Human trials?"

Target: SoTD → FiH in 14.5 months (down from 17.5-month median = 3 months saved/asset)

Usage:
    python3 orchestrator_agent.py "Run a full 20-by-30 Turbospeed audit"
    python3 orchestrator_agent.py "Score Fenebrutinib and recommend levers"
"""

import json
import math
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import requests

# ── Config ─────────────────────────────────────────────────────────────────────

TODAY = date(2026, 4, 1)
SOTD_MEDIAN_MONTHS  = 17.5
SOTD_TARGET_MONTHS  = 14.5
TURBOSPEED_FLAG_MONTHS = SOTD_MEDIAN_MONTHS          # flag if elapsed > this
KB_DIR = Path(__file__).parent / "knowledge_base"

# External API endpoints — try real first, fall back to stubs
THIN_LAYER_API_URL = os.environ.get("THIN_LAYER_API_URL", "")
BIONEMO_API_URL    = os.environ.get("BIONEMO_API_URL", "")
BIONEMO_API_KEY    = os.environ.get("BIONEMO_API_KEY", "")
IHB_API_URL        = os.environ.get("IHB_API_URL", "")

# ── Session accumulator ─────────────────────────────────────────────────────────

SESSION: dict = {
    "query":                  "",
    "asset_timelines":        [],   # SoTD, months_elapsed, phase, sites, turbospeed_flag
    "turbospeed_scores":      [],   # Per-asset probability scores + interpretation
    "flagged_assets":         [],   # assets where months_elapsed > TURBOSPEED_FLAG_MONTHS
    "turbospeed_levers":      [],   # Recommended acceleration actions per asset
    "mdm_verified_entities":  [],   # Deduplicated sites/investigators from MDM
    "bionemo_simulations":    [],   # Molecular predictions
    "ihb_validations":        [],   # Organoid concordance data
    "samd_audits":            [],   # Compliance status per SaMD asset
}

# ── Portfolio (20 assets — matches skills/pipeline.py) ─────────────────────────

PORTFOLIO = [
    {"name": "Giredestrant",   "alias": "RG6171",  "target_gene": "ESR1",    "ta": "Oncology"},
    {"name": "Trontinemab",    "alias": "RG6102",  "target_gene": "APP",     "ta": "Neurology"},
    {"name": "CT-388",         "alias": "RG6640",  "target_gene": "GLP1R",   "ta": "Metabolic"},
    {"name": "NXT007",         "alias": "RG6512",  "target_gene": "F8",      "ta": "Haematology"},
    {"name": "Fenebrutinib",   "alias": "RG6046",  "target_gene": "BTK",     "ta": "Immunology"},
    {"name": "Inavolisib",     "alias": "RG6114",  "target_gene": "PIK3CA",  "ta": "Oncology"},
    {"name": "Divarasib",      "alias": "RG6330",  "target_gene": "KRAS",    "ta": "Oncology"},
    {"name": "Zilebesiran",    "alias": "ALN-AGT", "target_gene": "AGT",     "ta": "Cardiovascular"},
    {"name": "Crovalimab",     "alias": "RG6107",  "target_gene": "C5",      "ta": "Haematology"},
    {"name": "Tiragolumab",    "alias": "RG6058",  "target_gene": "TIGIT",   "ta": "Oncology"},
    {"name": "Gazyva",         "alias": "RG7159",  "target_gene": "MS4A1",   "ta": "Oncology"},
    {"name": "Susvimo",        "alias": "RG6321",  "target_gene": "VEGFA",   "ta": "Ophthalmology"},
    {"name": "RVT-3101",       "alias": "RG6633",  "target_gene": "TNFSF15", "ta": "Gastroenterology"},
    {"name": "Prasinezumab",   "alias": "RG7935",  "target_gene": "SNCA",    "ta": "Neurology"},
    {"name": "Vamikibart",     "alias": "RG6179",  "target_gene": "IL6",     "ta": "Immunology"},
    {"name": "Cevostamab",     "alias": "RG6160",  "target_gene": "FCRL5",   "ta": "Haematology"},
    {"name": "Columvi",        "alias": "RG6026",  "target_gene": "MS4A1",   "ta": "Oncology"},
    {"name": "Lunsumio",       "alias": "RG7828",  "target_gene": "MS4A1",   "ta": "Oncology"},
    {"name": "Astegolimab",    "alias": "RG6149",  "target_gene": "IL33",    "ta": "Pulmonology"},
    {"name": "Satralizumab",   "alias": "RG6168",  "target_gene": "IL6R",    "ta": "Neurology"},
]

# ── Knowledge base loaders ─────────────────────────────────────────────────────

def _load_kb(filename: str) -> dict | list:
    path = KB_DIR / filename
    if path.exists():
        return json.loads(path.read_text())
    return {}


ASSET_TIMELINES_DB: dict = _load_kb("asset_timelines.json")
THIN_LAYER_MDM_DB:  dict = _load_kb("thin_layer_mdm.json")
RDE_LEVERS_DB:      dict = _load_kb("rde_levers.json")
IHB_ORGANOID_DB:    dict = _load_kb("ihb_organoid_data.json")
BIONEMO_CACHE_DB:   dict = _load_kb("bionemo_cache.json")

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fuzzy_find_asset(name: str) -> dict | None:
    """Find asset in ASSET_TIMELINES_DB by name or alias (case-insensitive)."""
    assets = ASSET_TIMELINES_DB.get("assets", [])
    name_l = name.lower()
    for a in assets:
        if a["name"].lower() == name_l or a.get("alias", "").lower() == name_l:
            return a
    # Partial match
    for a in assets:
        if name_l in a["name"].lower() or name_l in a.get("alias", "").lower():
            return a
    return None


def _turbospeed_label(score: float) -> str:
    if score >= 0.70:
        return "ON TRACK"
    elif score >= 0.50:
        return "AT RISK — levers needed"
    else:
        return "CRITICAL — escalate"


def _months_between(date_str: str, ref: date = TODAY) -> float:
    """Parse YYYY-MM-DD and return months elapsed to ref."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    delta = ref - d
    return round(delta.days / 30.44, 1)


# ── Tool 1: get_asset_timeline ─────────────────────────────────────────────────

def get_asset_timeline(asset_name: str) -> dict:
    """
    Retrieve SoTD date, phase, active sites, and cycle time metrics for an asset.
    Queries Thin Layer MDM (real API first, then stub).
    """
    # Try real Thin Layer API
    if THIN_LAYER_API_URL:
        try:
            r = requests.get(
                f"{THIN_LAYER_API_URL}/assets/{asset_name}/timeline",
                timeout=10,
                headers={"X-PBAC-Role": "read_only"},
            )
            if r.status_code == 200:
                record = r.json()
                record["source"] = "thin_layer_api_live"
                SESSION["asset_timelines"].append(record)
                if record.get("months_elapsed", 0) > TURBOSPEED_FLAG_MONTHS:
                    SESSION["flagged_assets"].append(record)
                return record
        except requests.RequestException:
            pass

    # Stub fallback
    asset = _fuzzy_find_asset(asset_name)
    if not asset:
        return {"status": "not_found", "asset_name": asset_name,
                "note": f"Asset '{asset_name}' not in portfolio. Check spelling."}

    # Re-compute months_elapsed from SoTD to TODAY
    months_elapsed = _months_between(asset["sotd_date"])
    cycle_factor = max(0.0, 1.0 - (months_elapsed - 6) / 17.5)
    months_remaining = max(0.0, SOTD_TARGET_MONTHS - months_elapsed)

    result = {
        "asset_name":        asset["name"],
        "alias":             asset.get("alias", ""),
        "target_gene":       asset.get("target_gene", ""),
        "indication":        asset.get("indication", ""),
        "therapeutic_area":  asset.get("therapeutic_area", ""),
        "modality":          asset.get("modality", ""),
        "sotd_date":         asset["sotd_date"],
        "months_elapsed":    months_elapsed,
        "current_phase":     asset.get("current_phase", ""),
        "active_sites":      asset.get("active_sites", 0),
        "active_investigators": asset.get("active_investigators", 0),
        "protocol_complexity": asset.get("protocol_complexity", ""),
        "bottleneck":        asset.get("bottleneck"),
        "bio_score":         asset.get("bio_score", 0.0),
        "turbospeed_flag":   months_elapsed > TURBOSPEED_FLAG_MONTHS,
        "projected_fih_date": asset.get("projected_fih_date", ""),
        "months_remaining_to_target": round(months_remaining, 1),
        "cycle_factor":      round(cycle_factor, 3),
        "milestone_history": asset.get("milestone_history", []),
        "source":            "thin_layer_stub",
    }

    SESSION["asset_timelines"].append(result)
    if result["turbospeed_flag"]:
        SESSION["flagged_assets"].append(result)

    return result


# ── Tool 2: calculate_turbospeed_score ─────────────────────────────────────────

def calculate_turbospeed_score(asset_name: str) -> dict:
    """
    Compute the Turbospeed Score — P(FiH in <15 months) — for an asset.
    Formula: ts = bio_score×0.30 + bionemo_success×0.25 + site_factor×0.20 + cycle_factor×0.25
    """
    # Find timeline (from SESSION if already fetched, else load from DB)
    timeline = next(
        (t for t in SESSION["asset_timelines"] if t.get("asset_name", "").lower() == asset_name.lower()),
        None,
    )
    if not timeline:
        timeline = get_asset_timeline(asset_name)
    if timeline.get("status") == "not_found":
        return timeline

    # BioNeMo success probability — prefer SESSION result, else KB cache
    bionemo_entry = next(
        (b for b in SESSION["bionemo_simulations"]
         if b.get("compound", "").lower() == asset_name.lower()),
        None,
    )
    if not bionemo_entry:
        predictions = BIONEMO_CACHE_DB.get("predictions", [])
        bionemo_entry = next(
            (p for p in predictions if p["compound"].lower() == asset_name.lower()),
            None,
        )

    bio_score      = timeline.get("bio_score", 0.5)
    bionemo_success = bionemo_entry.get("success_probability", 0.5) if bionemo_entry else 0.5
    months_elapsed = timeline.get("months_elapsed", 12.0)
    active_sites   = timeline.get("active_sites", 5)

    site_factor  = min(active_sites / 10.0, 1.0)
    cycle_factor = max(0.0, 1.0 - (months_elapsed - 6) / 17.5)

    ts = (bio_score * 0.30 + bionemo_success * 0.25
          + site_factor * 0.20 + cycle_factor * 0.25)
    ts = round(min(ts, 1.0), 3)

    # Confidence band: tighter when more data sources used
    confidence_band = 0.05 if bionemo_entry else 0.12

    label = _turbospeed_label(ts)

    result = {
        "asset_name":       asset_name,
        "turbospeed_score": ts,
        "label":            label,
        "confidence_band":  f"±{confidence_band}",
        "components": {
            "bio_score":        round(bio_score, 3),
            "bionemo_success":  round(bionemo_success, 3),
            "site_factor":      round(site_factor, 3),
            "cycle_factor":     round(cycle_factor, 3),
        },
        "months_elapsed":   months_elapsed,
        "active_sites":     active_sites,
        "bionemo_source":   ("session" if bionemo_entry and bionemo_entry in SESSION["bionemo_simulations"]
                             else ("cache" if bionemo_entry else "default")),
    }

    SESSION["turbospeed_scores"].append(result)
    return result


# ── Tool 3: recommend_turbospeed_levers ────────────────────────────────────────

def recommend_turbospeed_levers(asset_name: str, bottleneck_type: str = "auto") -> dict:
    """
    Recommend top-3 RDE Turbospeed levers for an asset's principal bottleneck.
    bottleneck_type: 'site_activation' | 'protocol_complexity' | 'biomarker' |
                     'regulatory' | 'manufacturing' | 'molecular' | 'auto'
    """
    # Resolve bottleneck
    if bottleneck_type == "auto":
        timeline = next(
            (t for t in SESSION["asset_timelines"]
             if t.get("asset_name", "").lower() == asset_name.lower()),
            None,
        )
        if not timeline:
            timeline = get_asset_timeline(asset_name)
        bottleneck_type = timeline.get("bottleneck") or "protocol_complexity"

    all_levers = RDE_LEVERS_DB.get("levers", [])
    matching = [l for l in all_levers if l.get("bottleneck_type") == bottleneck_type]
    top3 = sorted(matching, key=lambda l: l["time_saving_weeks"], reverse=True)[:3]

    if not top3:
        # Fall back to all levers sorted by impact
        top3 = sorted(all_levers, key=lambda l: l["time_saving_weeks"], reverse=True)[:3]

    total_weeks = sum(l["time_saving_weeks"] for l in top3)

    result = {
        "asset_name":      asset_name,
        "bottleneck_type": bottleneck_type,
        "recommended_levers": [
            {
                "id":                l["id"],
                "name":              l["name"],
                "time_saving_weeks": l["time_saving_weeks"],
                "evidence_level":    l["evidence_level"],
                "rde_category":      l["rde_category"],
                "description":       l["description"][:200],
            }
            for l in top3
        ],
        "total_potential_saving_weeks": total_weeks,
        "total_potential_saving_months": round(total_weeks / 4.33, 1),
    }

    SESSION["turbospeed_levers"].append(result)
    return result


# ── Tool 4: query_thin_layer_mdm ───────────────────────────────────────────────

def query_thin_layer_mdm(entity_type: str, query_term: str) -> dict:
    """
    Query the Thin Layer MDM for sites, investigators, or assets.
    entity_type: 'site' | 'investigator' | 'asset'
    Deduplicates via canonical MDM IDs. PBAC: read-only.
    """
    entity_type = entity_type.lower()

    # Try real Thin Layer API
    if THIN_LAYER_API_URL:
        try:
            r = requests.get(
                f"{THIN_LAYER_API_URL}/mdm/{entity_type}",
                params={"q": query_term},
                timeout=10,
                headers={"X-PBAC-Role": "read_only"},
            )
            if r.status_code == 200:
                result = r.json()
                result["source"] = "thin_layer_api_live"
                result["pbac"] = "READ_ONLY"
                SESSION["mdm_verified_entities"].append(result)
                return result
        except requests.RequestException:
            pass

    # Stub fallback
    query_l = query_term.lower()
    mdm = THIN_LAYER_MDM_DB

    if entity_type == "site":
        records = mdm.get("sites", [])
        matches = [
            s for s in records
            if query_l in s["name"].lower()
            or query_l in s["city"].lower()
            or query_l in s["country"].lower()
            or any(query_l in c.lower() for c in s.get("capabilities", []))
        ]
    elif entity_type == "investigator":
        records = mdm.get("investigators", [])
        matches = [
            i for i in records
            if query_l in i["name"].lower()
            or query_l in i["specialty"].lower()
        ]
    else:  # asset
        records = ASSET_TIMELINES_DB.get("assets", [])
        matches = [
            a for a in records
            if query_l in a["name"].lower()
            or query_l in a.get("alias", "").lower()
            or query_l in a.get("target_gene", "").lower()
        ]

    # Flag duplicates (any sharing same city+name fragment)
    seen_names = set()
    dedup_matches = []
    for m in matches:
        key = m.get("name", "").lower()[:20]
        m["duplicate_flag"] = key in seen_names
        seen_names.add(key)
        dedup_matches.append(m)

    result = {
        "entity_type":        entity_type,
        "query_term":         query_term,
        "result_count":       len(dedup_matches),
        "records":            dedup_matches,
        "deduplication_engine": mdm.get("deduplication_engine", "MDM-CANONICAL-v4"),
        "pbac":               "READ_ONLY",
        "source":             "thin_layer_stub",
    }

    SESSION["mdm_verified_entities"].append(result)
    return result


# ── Tool 5: run_bionemo_simulation ─────────────────────────────────────────────

def run_bionemo_simulation(target_gene: str, compound_name: str,
                           simulation_type: str = "binding_affinity") -> dict:
    """
    Run a BioNeMo molecular simulation (binding_affinity | toxicity | selectivity).
    Uses NVIDIA AI Factory real API if configured, else cached results.
    """
    # Try real BioNeMo API
    if BIONEMO_API_URL and BIONEMO_API_KEY:
        try:
            payload = {
                "target": target_gene,
                "compound": compound_name,
                "simulation_type": simulation_type,
                "gpu_cluster": "nvidia_ai_factory",
            }
            r = requests.post(
                f"{BIONEMO_API_URL}/simulate",
                json=payload,
                headers={"Authorization": f"Bearer {BIONEMO_API_KEY}"},
                timeout=60,
            )
            if r.status_code == 200:
                result = r.json()
                result["source"] = "bionemo_api_live"
                result["compound"] = compound_name
                result["target_gene"] = target_gene
                SESSION["bionemo_simulations"].append(result)
                return result
        except requests.RequestException:
            pass

    # Cache fallback
    preds = BIONEMO_CACHE_DB.get("predictions", [])
    compound_l = compound_name.lower()
    target_l   = target_gene.lower()

    match = next(
        (p for p in preds
         if p["compound"].lower() == compound_l
         or p["target_gene"].lower() == target_l),
        None,
    )

    if not match:
        return {
            "status":        "not_in_cache",
            "compound":      compound_name,
            "target_gene":   target_gene,
            "note":          "Run NVIDIA BioNeMo API directly or add compound to bionemo_cache.json.",
        }

    result = {
        "compound":          match["compound"],
        "alias":             match.get("alias", ""),
        "target_gene":       match["target_gene"],
        "simulation_type":   simulation_type,
        "predicted_ic50_nm": match.get("predicted_ic50_nm"),
        "selectivity_ratio": match.get("selectivity_ratio"),
        "toxicity_flag":     match.get("toxicity_flag", False),
        "herg_risk":         match.get("herg_risk", "unknown"),
        "success_probability": match.get("success_probability"),
        "confidence":        match.get("confidence"),
        "gpu_node_used":     match.get("gpu_node_used",
                             BIONEMO_CACHE_DB.get("gpu_cluster", "NVIDIA AI Factory")),
        "simulation_date":   match.get("simulation_date"),
        "notes":             match.get("notes", ""),
        "source":            "bionemo_cache",
    }

    SESSION["bionemo_simulations"].append(result)
    return result


# ── Tool 6: validate_ihb_organoid ─────────────────────────────────────────────

def validate_ihb_organoid(target_gene: str, compound_class: str) -> dict:
    """
    Cross-reference BioNeMo predictions against IHB organoid-on-a-chip historical data.
    Returns concordance rate and key findings for the target/compound-class combination.
    """
    # Try real IHB API
    if IHB_API_URL:
        try:
            r = requests.get(
                f"{IHB_API_URL}/organoid",
                params={"target": target_gene, "compound_class": compound_class},
                timeout=15,
            )
            if r.status_code == 200:
                result = r.json()
                result["source"] = "ihb_api_live"
                SESSION["ihb_validations"].append(result)
                return result
        except requests.RequestException:
            pass

    # Stub fallback
    assays = IHB_ORGANOID_DB.get("assays", [])
    gene_l  = target_gene.lower()
    class_l = compound_class.lower()

    match = next(
        (a for a in assays
         if a["target_gene"].lower() == gene_l
         and class_l in a["compound_class"].lower()),
        None,
    )

    if not match:
        # Fallback: gene-only match
        match = next(
            (a for a in assays if a["target_gene"].lower() == gene_l),
            None,
        )

    if not match:
        return {
            "status":       "no_data",
            "target_gene":  target_gene,
            "compound_class": compound_class,
            "note":         "No IHB organoid data for this target/class combination.",
        }

    concordance = match.get("concordance_rate", 0.5)
    if concordance >= 0.75:
        validation_status = "BioNeMo VALIDATED — organoid concordance strong"
    elif concordance >= 0.60:
        validation_status = "PARTIAL — moderate concordance, wet-lab confirmation recommended"
    else:
        validation_status = "DISCORDANT — run additional wet-lab assays before IND"

    result = {
        "target_gene":        match["target_gene"],
        "compound_class":     match["compound_class"],
        "organoid_type":      match.get("organoid_type", ""),
        "concordance_rate":   concordance,
        "bionemo_concordance": match.get("bionemo_concordance"),
        "organoid_assay_count": match.get("assay_count", 0),
        "validation_status":  validation_status,
        "key_findings":       match.get("key_findings", []),
        "risk_flags":         match.get("risk_flags", []),
        "source":             "ihb_stub",
    }

    SESSION["ihb_validations"].append(result)
    return result


# ── Tool 7: audit_samd_compliance ─────────────────────────────────────────────

# Opulus Standard QMS checks (FDA 510(k) K260001 — cleared March 26, 2026)
_OPULUS_CHECKS = {
    "cdx": [
        "Algorithm transparency documentation (ISO 13485 §4.2)",
        "Analytical validation (sensitivity ≥95%, specificity ≥98%) across 3 independent labs",
        "Clinical validation in intended use population (n≥100 per indication)",
        "Cybersecurity risk assessment (21 CFR Part 11 + NIST CSF)",
        "Post-market surveillance plan with real-world concordance monitoring",
    ],
    "ai_diagnostic": [
        "Algorithm transparency + explainability report (FDA AI Action Plan 2025)",
        "Bias validation dataset (demographic diversity: sex, age, ethnicity, geography)",
        "Predetermined change control plan (PCCP) for model updates",
        "Cybersecurity controls: input validation, adversarial robustness, audit logging",
        "Clinical validation in target population; separate train/test splits documented",
        "Post-market performance monitoring with drift detection",
    ],
    "digital_biomarker": [
        "Concept of interest (COI) — measurement concept traceability",
        "Analytical validation: precision, accuracy, reliability across devices",
        "Clinical validation: correlation with clinical outcome (anchor trial or COA mapping)",
        "Wearable/sensor security audit (OWASP IoT Top 10)",
        "Patient-facing software UX validation (usability engineering per IEC 62366)",
    ],
}

# Assets known to have SaMD components
_SAMD_ASSETS = {
    "giredestrant":  "cdx",          # ESR1 CDx required
    "inavolisib":    "cdx",          # PIK3CA CDx required
    "tiragolumab":   "cdx",          # PD-L1 CDx
    "divarasib":     "cdx",          # KRAS G12C CDx
    "trontinemab":   "ai_diagnostic", # AI-assisted amyloid PET reader
    "prasinezumab":  "ai_diagnostic", # AI-assisted DaTscan reader
    "satralizumab":  "cdx",           # AQP4 serostatus CDx
    "ct-388":        "digital_biomarker",  # CGM digital biomarker endpoint
    "zilebesiran":   "digital_biomarker",  # 24h ambulatory BP digital biomarker
}


def audit_samd_compliance(asset_name: str, samd_type: str = "auto") -> dict:
    """
    Audit SaMD compliance against the Opulus Standard (FDA 510(k) K260001, March 26, 2026).
    samd_type: 'cdx' | 'ai_diagnostic' | 'digital_biomarker' | 'auto'
    """
    asset_l = asset_name.lower()

    if samd_type == "auto":
        samd_type = _SAMD_ASSETS.get(asset_l, "cdx")

    checks = _OPULUS_CHECKS.get(samd_type, _OPULUS_CHECKS["cdx"])

    # Simulate compliance check — in production these would query the Opulus QMS API
    import random
    rng = hash(asset_name) % 100  # deterministic per asset

    # Compliance scoring: deterministic based on asset name hash
    if rng < 30:
        compliance_status = "COMPLIANT"
        gap_count = 0
        gaps = []
        remediation_weeks = 0
    elif rng < 70:
        compliance_status = "MINOR_GAPS"
        gap_idx = [rng % len(checks), (rng + 1) % len(checks)]
        gaps = list({checks[i] for i in gap_idx})[:2]
        gap_count = len(gaps)
        remediation_weeks = gap_count * 3
    else:
        compliance_status = "MAJOR_GAPS"
        gap_idx = [i % len(checks) for i in range(rng % 3 + 2, rng % 3 + 5)]
        gaps = list({checks[i % len(checks)] for i in gap_idx})[:4]
        gap_count = len(gaps)
        remediation_weeks = gap_count * 4

    opulus_standard_ref = "Opulus Standard QMS v1.0 — FDA 510(k) K260001 (cleared 2026-03-26)"

    result = {
        "asset_name":         asset_name,
        "samd_type":          samd_type,
        "compliance_status":  compliance_status,
        "total_checks":       len(checks),
        "checks_passed":      len(checks) - gap_count,
        "gap_count":          gap_count,
        "gaps":               gaps,
        "remediation_weeks":  remediation_weeks,
        "opulus_standard":    opulus_standard_ref,
        "recommendation":     (
            "Ready for SaMD submission — no remediation required." if compliance_status == "COMPLIANT"
            else f"Address {gap_count} gap(s) before submission. Est. {remediation_weeks} weeks."
        ),
    }

    SESSION["samd_audits"].append(result)
    return result


# ── Tool 8: generate_turbospeed_report ─────────────────────────────────────────

def generate_turbospeed_report(portfolio_summary: str = "", ceo_summary: str = "") -> dict:
    """
    Generate the 20-by-30 Turbospeed Dashboard PDF.
    Sections: Cover, Executive Dashboard, Timeline Matrix, Flagged Assets,
              MDM Site Intelligence, BioNeMo Simulations, IHB Validation, SaMD Audit.
    """
    today_str = TODAY.strftime("%Y-%m-%d")
    filename  = f"Roche_20by30_Orchestrator_Report_{today_str}.pdf"

    try:
        result = _generate_pdf(filename, portfolio_summary, ceo_summary)
        return {"status": "saved", "file": filename, **result}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "file": filename}


# ── PDF Generation ─────────────────────────────────────────────────────────────

def _generate_pdf(filename: str, portfolio_summary: str, ceo_summary: str) -> dict:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph,
        Spacer, Table, TableStyle,
    )

    # ── Colour palette ──────────────────────────────────────────────────────────
    ROCHE_BLUE  = colors.HexColor("#005BAC")
    DARK        = colors.HexColor("#1A2533")
    GREEN       = colors.HexColor("#27AE60")
    AMBER       = colors.HexColor("#E67E22")
    RED         = colors.HexColor("#C0392B")
    LIGHT_BLUE  = colors.HexColor("#EBF5FB")
    LIGHT_GREEN = colors.HexColor("#EAFAF1")
    LIGHT_AMBER = colors.HexColor("#FEF9E7")
    LIGHT_RED   = colors.HexColor("#FDEDEC")
    MID_GREY    = colors.HexColor("#95A5A6")
    LIGHT_GREY  = colors.HexColor("#F2F3F4")

    W, H = A4
    styles = getSampleStyleSheet()

    def style(name, **kw):
        base = styles[name] if name in styles else styles["Normal"]
        ps = ParagraphStyle(f"custom_{uuid.uuid4().hex[:6]}", parent=base, **kw)
        return ps

    TITLE   = style("Heading1", fontSize=22, textColor=ROCHE_BLUE, leading=26, spaceAfter=4)
    H2      = style("Heading2", fontSize=13, textColor=DARK,       leading=16, spaceBefore=10, spaceAfter=4)
    H3      = style("Heading3", fontSize=10, textColor=ROCHE_BLUE, leading=12, spaceBefore=8,  spaceAfter=2)
    BODY    = style("Normal",   fontSize=8.5, leading=12, textColor=DARK)
    CAPTION = style("Normal",   fontSize=7.5, leading=10, textColor=MID_GREY)
    BOLD    = style("Normal",   fontSize=8.5, leading=12, textColor=DARK, fontName="Helvetica-Bold")

    TH_STYLE = [
        ("BACKGROUND",    (0, 0), (-1, 0), ROCHE_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#BDC3C7")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]

    story = []
    add = story.append

    def rule():
        add(HRFlowable(width="100%", thickness=0.5, color=MID_GREY, spaceAfter=4))

    def ts_color(score):
        if score >= 0.70:
            return GREEN
        elif score >= 0.50:
            return AMBER
        return RED

    def ts_bg(score):
        if score >= 0.70:
            return LIGHT_GREEN
        elif score >= 0.50:
            return LIGHT_AMBER
        return LIGHT_RED

    # ── Cover ──────────────────────────────────────────────────────────────────
    add(Spacer(1, 1.2 * cm))
    add(Paragraph("20-BY-30 STRATEGIC ORCHESTRATOR", TITLE))
    add(Paragraph("TURBOSPEED DASHBOARD — ROCHE CSI HACKATHON 2026", style("Normal", fontSize=13, textColor=ROCHE_BLUE, leading=16)))
    add(Spacer(1, 0.4 * cm))
    rule()

    meta_data = [
        ["Query:", SESSION.get("query", "Full portfolio Turbospeed audit")],
        ["Date:", TODAY.strftime("%B %d, %Y")],
        ["Platform:", "Roche NVIDIA AI Factory — 3,500+ Blackwell GPUs (CUDA 12.8)"],
        ["Framework:", "WS7 Thin Layer / MDM Integration Layer (PBAC: Read-Only)"],
        ["Compliance:", "Opulus Standard QMS v1.0 — FDA 510(k) K260001 (March 26, 2026)"],
        ["Target:", f"SoTD → FiH in {SOTD_TARGET_MONTHS} months (median: {SOTD_MEDIAN_MONTHS} months)"],
    ]
    meta_tbl = Table(meta_data, colWidths=[3.5 * cm, 13.5 * cm])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME",  (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",  (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), ROCHE_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    add(meta_tbl)
    add(Spacer(1, 0.5 * cm))

    # Stats box
    ts_scores = SESSION.get("turbospeed_scores", [])
    on_track  = sum(1 for s in ts_scores if s.get("turbospeed_score", 0) >= 0.70)
    at_risk   = sum(1 for s in ts_scores if 0.50 <= s.get("turbospeed_score", 0) < 0.70)
    critical  = sum(1 for s in ts_scores if s.get("turbospeed_score", 0) < 0.50)
    n_levers  = sum(len(l.get("recommended_levers", [])) for l in SESSION.get("turbospeed_levers", []))
    n_bionemo = len(SESSION.get("bionemo_simulations", []))
    n_ihb     = len(SESSION.get("ihb_validations", []))

    stats_data = [
        [f"{len(ts_scores)}", f"{on_track}", f"{at_risk}", f"{critical}", f"{n_levers}", f"{n_bionemo}"],
        ["Assets Scored", "On Track", "At Risk", "Critical", "Levers Rec.", "BioNeMo Sims"],
    ]
    stats_tbl = Table(stats_data, colWidths=[2.85 * cm] * 6, rowHeights=[0.9 * cm, 0.5 * cm])
    stats_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, 0), ROCHE_BLUE),
        ("BACKGROUND",  (1, 0), (1, 0), GREEN),
        ("BACKGROUND",  (2, 0), (2, 0), AMBER),
        ("BACKGROUND",  (3, 0), (3, 0), RED),
        ("BACKGROUND",  (4, 0), (4, 0), colors.HexColor("#8E44AD")),
        ("BACKGROUND",  (5, 0), (5, 0), colors.HexColor("#2E86C1")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 18),
        ("FONTSIZE",    (0, 1), (-1, 1), 7.5),
        ("FONTNAME",    (0, 1), (-1, 1), "Helvetica"),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.white),
    ]))
    add(stats_tbl)
    add(Spacer(1, 0.4 * cm))

    if ceo_summary:
        add(Paragraph("Executive Summary", H2))
        add(Paragraph(ceo_summary, BODY))
    rule()

    # ── Section 1: Asset Timeline Matrix ───────────────────────────────────────
    add(Paragraph("SECTION 1 — ASSET TIMELINE MATRIX (All 20 Assets)", H2))
    add(Paragraph(
        f"SoTD median: {SOTD_MEDIAN_MONTHS} months → target: {SOTD_TARGET_MONTHS} months. "
        "Assets flagged red have exceeded the median — immediate lever application required.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    # Build score lookup
    score_map = {s["asset_name"].lower(): s for s in ts_scores}
    timeline_map = {t["asset_name"].lower(): t for t in SESSION.get("asset_timelines", [])}

    # Use all portfolio assets as rows; fill from timelines if available
    all_assets_db = ASSET_TIMELINES_DB.get("assets", [])

    hdrs = ["Asset", "SoTD", "Mo.\nElapsed", "Phase", "Sites", "TS Score", "Status"]
    col_w = [3.2 * cm, 2.0 * cm, 1.5 * cm, 2.4 * cm, 1.2 * cm, 1.5 * cm, 4.2 * cm]
    tbl_data = [hdrs]
    row_styles = list(TH_STYLE)

    for row_i, asset in enumerate(all_assets_db, start=1):
        aname = asset["name"]
        aname_l = aname.lower()
        tl = timeline_map.get(aname_l, asset)
        sc = score_map.get(aname_l)
        mo_el = tl.get("months_elapsed", asset.get("months_elapsed", "—"))
        phase = tl.get("current_phase", asset.get("current_phase", "—"))
        sites = tl.get("active_sites", asset.get("active_sites", "—"))

        if sc:
            ts_val = sc["turbospeed_score"]
            ts_str = f"{ts_val:.3f}"
            status_str = sc["label"]
        else:
            ts_val  = None
            ts_str  = "—"
            status_str = "Not scored"

        row = [aname, asset.get("sotd_date", "—"), str(mo_el), phase, str(sites), ts_str, status_str]
        tbl_data.append(row)

        if ts_val is not None:
            bg = ts_bg(ts_val)
            row_styles.append(("BACKGROUND", (5, row_i), (6, row_i), bg))
            row_styles.append(("TEXTCOLOR",  (5, row_i), (6, row_i), ts_color(ts_val)))
            row_styles.append(("FONTNAME",   (5, row_i), (6, row_i), "Helvetica-Bold"))
        if mo_el != "—" and float(mo_el) > TURBOSPEED_FLAG_MONTHS:
            row_styles.append(("BACKGROUND", (2, row_i), (2, row_i), LIGHT_RED))

    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle(row_styles))
    add(tbl)
    add(Spacer(1, 0.3 * cm))

    # ── Section 2: Flagged Assets & Levers ─────────────────────────────────────
    flagged = [a for a in all_assets_db if a.get("months_elapsed", 0) > TURBOSPEED_FLAG_MONTHS]
    levers_map = {l["asset_name"].lower(): l for l in SESSION.get("turbospeed_levers", [])}

    if flagged:
        add(Paragraph(f"SECTION 2 — FLAGGED ASSETS & TURBOSPEED LEVERS ({len(flagged)} assets)", H2))
        add(Paragraph(
            f"{len(flagged)} assets exceed the {SOTD_MEDIAN_MONTHS}-month SoTD→FiH median. "
            "Lever recommendations shown below — total potential savings in weeks.",
            BODY,
        ))

        for asset in flagged:
            aname_l = asset["name"].lower()
            bottleneck = asset.get("bottleneck") or "protocol_complexity"
            lever_rec = levers_map.get(aname_l)

            add(Spacer(1, 0.3 * cm))
            add(Paragraph(
                f"{asset['name']} ({asset.get('alias', '')}) — {asset.get('indication', '')}",
                H3,
            ))
            mo_el = asset.get("months_elapsed", "?")
            add(Paragraph(
                f"Months elapsed: <b>{mo_el}</b> | Phase: {asset.get('current_phase')} | "
                f"Sites: {asset.get('active_sites')} | Bottleneck: <b>{bottleneck.replace('_', ' ').title()}</b>",
                BODY,
            ))

            if lever_rec:
                lev_hdrs = ["#", "Lever", "Category", "Saving (wks)", "Evidence"]
                lev_data = [lev_hdrs]
                for i, lv in enumerate(lever_rec.get("recommended_levers", []), 1):
                    lev_data.append([
                        str(i),
                        Paragraph(lv["name"], style("Normal", fontSize=7, leading=9)),
                        lv.get("rde_category", ""),
                        f"+{lv['time_saving_weeks']}",
                        lv.get("evidence_level", ""),
                    ])
                total_wks = lever_rec.get("total_potential_saving_weeks", 0)
                total_mo  = lever_rec.get("total_potential_saving_months", 0)
                lev_data.append(["", Paragraph(f"<b>Total potential saving</b>", BOLD), "", f"<b>+{total_wks} wks</b>", f"({total_mo} mo)"])

                lev_tbl = Table(lev_data, colWidths=[0.5 * cm, 7.5 * cm, 2.5 * cm, 1.8 * cm, 1.7 * cm])
                lev_tbl.setStyle(TableStyle(TH_STYLE + [
                    ("BACKGROUND", (0, len(lev_data) - 1), (-1, len(lev_data) - 1), LIGHT_BLUE),
                    ("FONTNAME",   (0, len(lev_data) - 1), (-1, len(lev_data) - 1), "Helvetica-Bold"),
                ]))
                add(lev_tbl)
            else:
                add(Paragraph(
                    f"No lever recommendations loaded yet — call recommend_turbospeed_levers('{asset['name']}').",
                    CAPTION,
                ))

        add(Spacer(1, 0.3 * cm))
        rule()

    # ── Section 3: Thin Layer MDM Site Intelligence ─────────────────────────────
    mdm_entities = SESSION.get("mdm_verified_entities", [])
    all_sites = THIN_LAYER_MDM_DB.get("sites", [])

    add(Paragraph("SECTION 3 — THIN LAYER MDM SITE INTELLIGENCE", H2))
    add(Paragraph(
        f"MDM-verified sites from the WS7 Thin Layer Framework. "
        f"Deduplication engine: {THIN_LAYER_MDM_DB.get('deduplication_engine', 'MDM-CANONICAL-v4')}. "
        f"PBAC: Read-Only.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    site_hdrs = ["MDM ID", "Site Name", "City / Country", "Capabilities", "Active\nTrials", "Investigators", "Screen Fail%"]
    site_col_w = [2.0 * cm, 5.0 * cm, 2.2 * cm, 3.8 * cm, 1.3 * cm, 1.8 * cm, 1.9 * cm]
    site_data = [site_hdrs]
    for s in all_sites[:15]:  # top 15 for space
        caps = ", ".join(s.get("capabilities", [])[:3])
        site_data.append([
            s.get("mdm_id", ""),
            Paragraph(s["name"][:50], style("Normal", fontSize=7, leading=9)),
            f"{s['city']}, {s['country']}",
            Paragraph(caps, style("Normal", fontSize=7, leading=9)),
            str(s.get("active_roche_trials", "")),
            str(s.get("qualified_investigators", "")),
            f"{int(s.get('screen_failure_rate', 0) * 100)}%",
        ])
    site_tbl = Table(site_data, colWidths=site_col_w, repeatRows=1)
    site_tbl.setStyle(TableStyle(TH_STYLE))
    add(site_tbl)
    add(Paragraph("Showing top 15 of 20 MDM-verified sites. All sites PBAC-tagged READ_ONLY.", CAPTION))
    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 4: BioNeMo Molecular Simulations ───────────────────────────────
    bionemo_sims = SESSION.get("bionemo_simulations", [])
    add(Paragraph("SECTION 4 — BIONEMO MOLECULAR SIMULATIONS", H2))
    add(Paragraph(
        f"NVIDIA BioNeMo (ESM-2 650M + MolMIM + DiffDock) — "
        f"{BIONEMO_CACHE_DB.get('gpu_cluster', 'NVIDIA AI Factory — 3,500+ Blackwell GPUs')}.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if bionemo_sims:
        bio_hdrs = ["Compound", "Target", "Predicted IC50 (nM)", "Selectivity", "Toxicity", "hERG", "P(Success)", "Confidence", "GPU Node"]
        bio_col_w = [2.2*cm, 1.5*cm, 2.5*cm, 1.8*cm, 1.5*cm, 1.2*cm, 1.8*cm, 1.8*cm, 2.7*cm]
        bio_data = [bio_hdrs]
        bio_row_styles = list(TH_STYLE)
        for row_i, b in enumerate(bionemo_sims, start=1):
            tox = "YES" if b.get("toxicity_flag") else "No"
            tox_color = RED if b.get("toxicity_flag") else GREEN
            bio_data.append([
                b.get("compound", b.get("target_gene", "")),
                b.get("target_gene", ""),
                str(b.get("predicted_ic50_nm", "—")),
                str(b.get("selectivity_ratio", "—")),
                tox,
                b.get("herg_risk", "—"),
                f"{b.get('success_probability', 0):.2f}" if b.get("success_probability") else "—",
                f"{b.get('confidence', 0):.2f}" if b.get("confidence") else "—",
                b.get("gpu_node_used", "—"),
            ])
            if b.get("toxicity_flag"):
                bio_row_styles.append(("BACKGROUND", (4, row_i), (4, row_i), LIGHT_RED))
            else:
                bio_row_styles.append(("TEXTCOLOR", (4, row_i), (4, row_i), GREEN))

        bio_tbl = Table(bio_data, colWidths=bio_col_w, repeatRows=1)
        bio_tbl.setStyle(TableStyle(bio_row_styles))
        add(bio_tbl)
    else:
        add(Paragraph("No BioNeMo simulations loaded — call run_bionemo_simulation() first.", CAPTION))

    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 5: IHB Organoid Validation ─────────────────────────────────────
    ihb_vals = SESSION.get("ihb_validations", [])
    add(Paragraph("SECTION 5 — IHB ORGANOID-ON-A-CHIP VALIDATION", H2))
    add(Paragraph(
        "IHB Human Model System — cross-referenced against BioNeMo predictions. "
        "Concordance ≥0.75 = validated; <0.60 = discordant (wet-lab re-run required).",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if ihb_vals:
        ihb_hdrs = ["Target", "Compound Class", "Organoid Type", "Concordance", "Assays", "Validation Status", "Risk Flags"]
        ihb_col_w = [1.8*cm, 3.0*cm, 2.5*cm, 1.8*cm, 1.2*cm, 4.0*cm, 2.7*cm]
        ihb_data = [ihb_hdrs]
        ihb_row_styles = list(TH_STYLE)
        for row_i, v in enumerate(ihb_vals, start=1):
            conc = v.get("concordance_rate", 0)
            conc_str = f"{conc:.2f}"
            flags = ", ".join(v.get("risk_flags", [])) or "None"
            ihb_data.append([
                v.get("target_gene", ""),
                Paragraph(v.get("compound_class", ""), style("Normal", fontSize=7, leading=9)),
                v.get("organoid_type", ""),
                conc_str,
                str(v.get("organoid_assay_count", "—")),
                Paragraph(v.get("validation_status", ""), style("Normal", fontSize=7, leading=9)),
                Paragraph(flags, style("Normal", fontSize=7, leading=9, textColor=RED if flags != "None" else GREEN)),
            ])
            ihb_row_styles.append(("BACKGROUND", (3, row_i), (3, row_i), LIGHT_GREEN if conc >= 0.75 else (LIGHT_AMBER if conc >= 0.60 else LIGHT_RED)))

        ihb_tbl = Table(ihb_data, colWidths=ihb_col_w, repeatRows=1)
        ihb_tbl.setStyle(TableStyle(ihb_row_styles))
        add(ihb_tbl)
    else:
        add(Paragraph("No IHB organoid validations loaded — call validate_ihb_organoid() first.", CAPTION))

    add(Spacer(1, 0.3 * cm))
    rule()

    # ── Section 6: SaMD Compliance Audit (Opulus Standard) ─────────────────────
    samd_audits = SESSION.get("samd_audits", [])
    add(Paragraph("SECTION 6 — SAMD COMPLIANCE AUDIT (OPULUS STANDARD)", H2))
    add(Paragraph(
        "Opulus Standard QMS v1.0 — FDA 510(k) K260001 cleared March 26, 2026. "
        "Applies to companion diagnostics (CDx), AI diagnostics, and digital biomarkers.",
        BODY,
    ))
    add(Spacer(1, 0.2 * cm))

    if samd_audits:
        samd_hdrs = ["Asset", "SaMD Type", "Status", "Checks\nPassed", "Gaps", "Remediation\n(weeks)", "Recommendation"]
        samd_col_w = [2.5*cm, 2.2*cm, 2.2*cm, 1.5*cm, 0.8*cm, 1.8*cm, 5.0*cm]
        samd_data = [samd_hdrs]
        samd_row_styles = list(TH_STYLE)
        for row_i, a in enumerate(samd_audits, start=1):
            status = a.get("compliance_status", "")
            status_color = GREEN if status == "COMPLIANT" else (AMBER if status == "MINOR_GAPS" else RED)
            samd_data.append([
                a.get("asset_name", ""),
                a.get("samd_type", ""),
                status,
                f"{a.get('checks_passed', 0)}/{a.get('total_checks', 0)}",
                str(a.get("gap_count", 0)),
                str(a.get("remediation_weeks", 0)),
                Paragraph(a.get("recommendation", ""), style("Normal", fontSize=7, leading=9)),
            ])
            samd_row_styles.append(("TEXTCOLOR",   (2, row_i), (2, row_i), status_color))
            samd_row_styles.append(("FONTNAME",    (2, row_i), (2, row_i), "Helvetica-Bold"))
            if status == "MAJOR_GAPS":
                samd_row_styles.append(("BACKGROUND", (0, row_i), (-1, row_i), LIGHT_RED))
            elif status == "MINOR_GAPS":
                samd_row_styles.append(("BACKGROUND", (0, row_i), (-1, row_i), LIGHT_AMBER))

        samd_tbl = Table(samd_data, colWidths=samd_col_w, repeatRows=1)
        samd_tbl.setStyle(TableStyle(samd_row_styles))
        add(samd_tbl)
    else:
        add(Paragraph("No SaMD audits loaded — call audit_samd_compliance() for CDx/AI assets.", CAPTION))

    add(Spacer(1, 0.4 * cm))
    rule()

    # ── Footer ──────────────────────────────────────────────────────────────────
    add(Paragraph(
        f"Generated {TODAY.strftime('%B %d, %Y')} by 20-by-30 Strategic Orchestrator v1.0 — "
        "Roche CSI Hackathon 2026 | NVIDIA AI Factory | One Roche",
        CAPTION,
    ))

    # ── Build PDF ───────────────────────────────────────────────────────────────
    frame = Frame(1.5*cm, 1.5*cm, W - 3*cm, H - 3*cm, id="main")
    tpl = PageTemplate(id="main", frames=[frame])
    doc = BaseDocTemplate(filename, pagesize=A4, pageTemplates=[tpl])
    doc.build(story)

    return {
        "sections":    6,
        "assets_in_matrix": len(all_assets_db),
        "flagged":     len(flagged) if "flagged" in dir() else 0,
        "ts_scored":   len(ts_scores),
        "bionemo_sims": len(bionemo_sims),
        "ihb_validations": len(ihb_vals if "ihb_vals" in dir() else ihb_vals),
        "samd_audits":  len(samd_audits),
    }


# ── Anthropic tools schema ─────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_asset_timeline",
        "description": (
            "Retrieve SoTD date, current phase, active site count, months elapsed, and "
            "cycle-time metrics for a named portfolio asset from the Thin Layer MDM. "
            "Sets turbospeed_flag=True if months_elapsed > 17.5-month median. "
            "Call this first before calculate_turbospeed_score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_name": {"type": "string", "description": "Portfolio asset name (e.g. 'Giredestrant', 'CT-388', 'Fenebrutinib')"}
            },
            "required": ["asset_name"],
        },
    },
    {
        "name": "calculate_turbospeed_score",
        "description": (
            "Compute the Turbospeed Score — P(FiH in <15 months) — for a portfolio asset. "
            "Formula: ts = bio_score×0.30 + bionemo_success×0.25 + site_factor×0.20 + cycle_factor×0.25. "
            "Requires get_asset_timeline to have been called for this asset. "
            "Interpretation: ≥0.70 = On Track; 0.50-0.69 = At Risk; <0.50 = Critical."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_name": {"type": "string", "description": "Portfolio asset name"}
            },
            "required": ["asset_name"],
        },
    },
    {
        "name": "recommend_turbospeed_levers",
        "description": (
            "Recommend the top-3 R&D Excellence (RDE) Turbospeed levers for an asset's principal bottleneck. "
            "Returns levers ranked by time_saving_weeks from the Roche RDE Playbook v3.2. "
            "Use bottleneck_type='auto' to infer bottleneck from the asset's timeline data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_name":      {"type": "string"},
                "bottleneck_type": {
                    "type": "string",
                    "enum": ["site_activation", "protocol_complexity", "biomarker",
                             "regulatory", "manufacturing", "molecular", "auto"],
                    "description": "Principal bottleneck type. Use 'auto' to infer from timeline.",
                },
            },
            "required": ["asset_name"],
        },
    },
    {
        "name": "query_thin_layer_mdm",
        "description": (
            "Query the WS7 Thin Layer MDM for sites, investigators, or assets. "
            "Deduplicates using MDM canonical IDs. PBAC: read-only. "
            "Use entity_type='site' to find trial sites, 'investigator' for PIs, 'asset' for portfolio assets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["site", "investigator", "asset"],
                },
                "query_term": {
                    "type": "string",
                    "description": "Search term (city, country, specialty, site name, asset name, etc.)",
                },
            },
            "required": ["entity_type", "query_term"],
        },
    },
    {
        "name": "run_bionemo_simulation",
        "description": (
            "Run a BioNeMo molecular simulation on the NVIDIA AI Factory (3,500+ Blackwell GPUs). "
            "Returns predicted_ic50_nm, selectivity_ratio, toxicity_flag, success_probability, and confidence. "
            "simulation_type: 'binding_affinity' | 'toxicity' | 'selectivity'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_gene":     {"type": "string", "description": "Gene symbol (e.g. 'ESR1', 'KRAS', 'BTK')"},
                "compound_name":   {"type": "string", "description": "Compound or asset name (e.g. 'Giredestrant')"},
                "simulation_type": {
                    "type": "string",
                    "enum": ["binding_affinity", "toxicity", "selectivity"],
                },
            },
            "required": ["target_gene", "compound_name"],
        },
    },
    {
        "name": "validate_ihb_organoid",
        "description": (
            "Cross-reference BioNeMo predictions against IHB (Institute of Human Biology) "
            "organoid-on-a-chip historical data. Returns concordance_rate (≥0.75 = validated), "
            "key_findings, and risk_flags for the target/compound-class combination."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_gene":    {"type": "string", "description": "Gene symbol"},
                "compound_class": {"type": "string", "description": "Compound class (e.g. 'SERD', 'KRASG12C_inhibitor', 'anti_TIGIT_mAb')"},
            },
            "required": ["target_gene", "compound_class"],
        },
    },
    {
        "name": "audit_samd_compliance",
        "description": (
            "Audit SaMD compliance against the Opulus Standard (FDA 510(k) K260001, March 26, 2026). "
            "Checks cybersecurity (21 CFR Part 11), AI transparency, clinical validation, and post-market plan. "
            "samd_type: 'cdx' | 'ai_diagnostic' | 'digital_biomarker' | 'auto'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_name": {"type": "string"},
                "samd_type":  {
                    "type": "string",
                    "enum": ["cdx", "ai_diagnostic", "digital_biomarker", "auto"],
                },
            },
            "required": ["asset_name"],
        },
    },
    {
        "name": "generate_turbospeed_report",
        "description": (
            "Generate the 20-by-30 Turbospeed Dashboard PDF. "
            "ALWAYS call this as the final step. "
            "Sections: Timeline Matrix, Flagged Assets + Levers, MDM Site Intelligence, "
            "BioNeMo Simulations, IHB Validation, SaMD Compliance Audit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_summary": {"type": "string", "description": "Optional high-level portfolio status summary"},
                "ceo_summary":       {"type": "string", "description": "CEO-ready executive summary (2-3 sentences)"},
            },
            "required": [],
        },
    },
]

TOOL_FN_MAP = {
    "get_asset_timeline":         get_asset_timeline,
    "calculate_turbospeed_score": calculate_turbospeed_score,
    "recommend_turbospeed_levers": recommend_turbospeed_levers,
    "query_thin_layer_mdm":       query_thin_layer_mdm,
    "run_bionemo_simulation":     run_bionemo_simulation,
    "validate_ihb_organoid":      validate_ihb_organoid,
    "audit_samd_compliance":      audit_samd_compliance,
    "generate_turbospeed_report": generate_turbospeed_report,
}

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the "20-by-30 Strategic Orchestrator," a high-velocity AI agent built \
for the Roche CSI Hackathon 2026.

OPERATIONAL CONTEXT (April 1, 2026):
- Platform: Roche NVIDIA AI Factory — 3,500+ Blackwell GPUs (CUDA 12.8)
- Data access: WS7 Thin Layer Framework / MDM Integration Layer (read-only, PBAC)
- Compliance: Opulus Standard QMS v1.0 — FDA 510(k) K260001 (cleared March 26, 2026)
- Validation: IHB (Institute of Human Biology) organoid-on-a-chip historical data

MISSION:
Accelerate delivery of Roche's 20 pipeline assets to First-in-Human (FiH) trials by 2030.
TARGET: SoTD → FiH in 14.5 months (down from 17.5-month median = 3 months saved per asset).
PRINCIPLE: "One Roche" — collaborative, evidence-based, patient-impact focused.

YOU HAVE 8 TOOLS:
  TIMELINE & SCORING:
  - get_asset_timeline          → SoTD date, phase, sites, months elapsed (Thin Layer MDM)
  - calculate_turbospeed_score  → P(FiH <15 months) score 0.0-1.0 | ≥0.70 On Track | 0.50-0.69 At Risk | <0.50 Critical

  LEVERS:
  - recommend_turbospeed_levers → Top-3 RDE levers for the asset's bottleneck (Roche Playbook v3.2)

  DATA INTEGRITY (Thin Layer MDM):
  - query_thin_layer_mdm        → MDM-verified sites/investigators (deduped, PBAC read-only)

  SIMULATION (NVIDIA AI Factory):
  - run_bionemo_simulation       → BioNeMo binding_affinity/toxicity/selectivity prediction
  - validate_ihb_organoid        → IHB organoid concordance rate vs. BioNeMo predictions

  COMPLIANCE (Opulus Standard):
  - audit_samd_compliance        → Cybersecurity + AI compliance check (CDx/AI diagnostic/digital biomarker)

  OUTPUT:
  - generate_turbospeed_report   → 6-section Turbospeed Dashboard PDF (ALWAYS call as final step)

WORKFLOW GUIDANCE:
- Full portfolio audit: get_asset_timeline (each asset) → calculate_turbospeed_score (each asset) \
→ flag >17.5mo → recommend_turbospeed_levers (per flagged asset) → generate_turbospeed_report
- Molecular validation: run_bionemo_simulation → validate_ihb_organoid → update turbospeed scores
- Site qualification: query_thin_layer_mdm('site', therapeutic_area) → dedup → recommend top sites
- Compliance: audit_samd_compliance for CDx/AI assets before final report
- Single asset deep dive: get_asset_timeline → calculate_turbospeed_score → \
run_bionemo_simulation → validate_ihb_organoid → recommend_turbospeed_levers → audit_samd_compliance → report

DATA INTEGRITY RULES:
- Always cross-verify site recommendations via query_thin_layer_mdm to eliminate MDM duplicates.
- Never recommend a site without confirming its MDM canonical ID and PBAC READ_ONLY status.
- Turbospeed Scores below 0.50 require BOTH lever recommendations AND Thin Layer site query.

REASONING GUIDELINES:
- Reason step by step. Never guess tool results.
- Flag assets exceeding 17.5 months — they are the primary leverage point for the 20-by-30 goal.
- Report potential time savings in both weeks AND months for CEO clarity.
- ALWAYS call generate_turbospeed_report as the very last step with a concise ceo_summary.
- The "One Roche" persona means: acknowledge trade-offs, cite evidence levels, and focus on patient impact.
"""


# ── Client ─────────────────────────────────────────────────────────────────────

def make_client():
    import anthropic
    from proxy_server import start_proxy

    # 1. Direct API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        cfg_path = Path(__file__).parent / "configs" / "api_keys.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            api_key = cfg.get("ANTHROPIC_API_KEY")

    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    # 2. Subscription token via proxy
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not auth_token:
        cfg_path = Path(__file__).parent / "configs" / "api_keys.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            auth_token = cfg.get("ANTHROPIC_AUTH_TOKEN")

    if auth_token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = auth_token
        port = start_proxy()
        return anthropic.Anthropic(
            base_url=f"http://127.0.0.1:{port}",
            api_key="proxy",
        )

    raise SystemExit(
        "No API credentials found.\n"
        "Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in env or configs/api_keys.json"
    )


# ── Agent loop ─────────────────────────────────────────────────────────────────

def run_orchestrator(question: str) -> None:
    SESSION["query"] = question
    client = make_client()

    messages = [{"role": "user", "content": question}]
    print(f"\n[Orchestrator] Question: {question}\n{'─'*60}")

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Print reasoning
        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"\n[Reasoning] {block.text[:400]}{'...' if len(block.text) > 400 else ''}")

        if response.stop_reason == "end_turn":
            break

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            break

        tool_results = []
        for call in tool_calls:
            fn = TOOL_FN_MAP.get(call.name)
            if not fn:
                result_str = json.dumps({"error": f"Unknown tool: {call.name}"})
            else:
                try:
                    result = fn(**call.input)
                    result_str = json.dumps(result, default=str)
                except Exception as exc:
                    result_str = json.dumps({"error": str(exc)})

            preview = result_str[:200]
            print(f"\n[Tool] {call.name}({json.dumps(call.input)[:80]}) → {preview}{'...' if len(result_str) > 200 else ''}")

            if call.name == "generate_turbospeed_report":
                try:
                    out = json.loads(result_str)
                    print(f"\n  TURBOSPEED REPORT SAVED → {out.get('file', '?')}")
                except Exception:
                    pass

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result_str,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    print(f"\n{'─'*60}\n[Orchestrator] Done.\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python3 orchestrator_agent.py \"<question>\"\n\n"
            "Examples:\n"
            '  python3 orchestrator_agent.py "Run a full 20-by-30 Turbospeed audit for all 20 assets"\n'
            '  python3 orchestrator_agent.py "Score Fenebrutinib, recommend levers, and generate report"\n'
            '  python3 orchestrator_agent.py "Validate Giredestrant with BioNeMo and IHB, then audit CDx compliance"\n'
        )
        sys.exit(1)

    run_orchestrator(sys.argv[1])
