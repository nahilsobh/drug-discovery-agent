"""
Long-term memory layer for the RedClaw AI Factory Strategic Discovery Agent.

Persists confirmed hits, negative results, ADMET profiles, and adverse event signals
across sessions. Implements the dual-memory architecture from the AIA4S Consortium
paper (Drug Discovery Today 2026): short-term = SESSION dict; long-term = this module.

Usage by other tools (side-effect writes):
    from tools.memory import record_hit, record_negative, record_admet, record_adverse_event

Usage as agent tool:
    from tools.memory import recall_longterm_memory
"""

import json
import os
from datetime import date
from pathlib import Path


# ── Path helpers ───────────────────────────────────────────────────────────────

def _memory_path() -> str:
    base = Path(__file__).parent.parent
    return str(base / "knowledge_base" / "agent_longterm_memory.json")


def _load_memory() -> dict:
    path = _memory_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "_meta": {
            "version": "1.0",
            "created": str(date.today()),
            "last_updated": "",
            "total_records": 0,
        },
        "confirmed_hits":       [],
        "negative_results":     [],
        "admet_profiles":       [],
        "adverse_event_signals": [],
        "sar_patterns":         [],
    }


def _save_memory(mem: dict) -> None:
    # Update meta
    total = sum(
        len(mem.get(k, []))
        for k in ("confirmed_hits", "negative_results", "admet_profiles",
                  "adverse_event_signals", "sar_patterns")
    )
    mem.setdefault("_meta", {})["last_updated"] = str(date.today())
    mem["_meta"]["total_records"] = total
    path = _memory_path()
    with open(path, "w") as f:
        json.dump(mem, f, indent=2)


# ── Write helpers (called as side-effects from tool functions) ─────────────────

def record_hit(
    target: str,
    chembl_id: str,
    compound: str,
    ic50_nm: float,
    assay_type: str,
    assay_description: str,
    provenance_quality: str,
    query_context: str = "",
) -> None:
    """Persist a confirmed hit to long-term memory."""
    mem = _load_memory()
    # Avoid exact duplicates (same target + compound + ic50)
    existing = {
        (h["target"].lower(), h["compound"].lower(), h.get("ic50_nm"))
        for h in mem["confirmed_hits"]
    }
    if (target.lower(), compound.lower(), ic50_nm) not in existing:
        mem["confirmed_hits"].append({
            "date":               str(date.today()),
            "target":             target,
            "chembl_id":          chembl_id,
            "compound":           compound,
            "ic50_nm":            ic50_nm,
            "assay_type":         assay_type,
            "assay_description":  assay_description[:120],
            "provenance_quality": provenance_quality,
            "query_context":      query_context,
        })
        _save_memory(mem)


def record_negative(
    target: str,
    compound: str,
    failure_reason: str,
    red_flags: list,
    query_context: str = "",
) -> None:
    """Persist a failed / TIER-3 compound to prevent future re-runs."""
    mem = _load_memory()
    existing = {
        (h["target"].lower(), h["compound"].lower())
        for h in mem["negative_results"]
    }
    if (target.lower(), compound.lower()) not in existing:
        mem["negative_results"].append({
            "date":           str(date.today()),
            "target":         target,
            "compound":       compound,
            "failure_reason": failure_reason,
            "red_flags":      red_flags,
            "query_context":  query_context,
        })
        _save_memory(mem)


def record_admet(
    drug: str,
    tier: str,
    red_flags: list,
    minor_flags: list,
    summary_score: float,
) -> None:
    """Persist an ADMET profile result."""
    mem = _load_memory()
    existing = {h["drug"].lower() for h in mem["admet_profiles"]}
    if drug.lower() not in existing:
        mem["admet_profiles"].append({
            "date":          str(date.today()),
            "drug":          drug,
            "tier":          tier,
            "red_flags":     red_flags,
            "minor_flags":   minor_flags,
            "summary_score": summary_score,
        })
        _save_memory(mem)


def record_adverse_event(
    drug: str,
    signal: str,
    serious_pct: float,
    total_reports: int,
) -> None:
    """Persist an adverse event signal from FAERS."""
    mem = _load_memory()
    existing = {h["drug"].lower() for h in mem["adverse_event_signals"]}
    if drug.lower() not in existing:
        mem["adverse_event_signals"].append({
            "date":          str(date.today()),
            "drug":          drug,
            "signal":        signal,
            "serious_pct":   serious_pct,
            "total_reports": total_reports,
        })
        _save_memory(mem)


# ── Agent tool: recall ─────────────────────────────────────────────────────────

def recall_longterm_memory(
    query_type: str,
    target_filter: str = "",
    max_results: int = 20,
) -> dict:
    """
    Retrieve records from the persistent cross-session knowledge base.

    query_type: "hits" | "negatives" | "admet" | "adverse_events" | "all"
    target_filter: optional gene symbol or drug name substring filter (case-insensitive)
    max_results: maximum records to return per category
    """
    mem = _load_memory()
    tf = target_filter.lower() if target_filter else ""

    def _filter(records: list, *keys) -> list:
        if not tf:
            return records[-max_results:]
        out = [
            r for r in records
            if any(tf in str(r.get(k, "")).lower() for k in keys)
        ]
        return out[-max_results:]

    result: dict = {
        "query_type":     query_type,
        "target_filter":  target_filter or "none",
        "total_in_store": mem["_meta"].get("total_records", 0),
        "last_updated":   mem["_meta"].get("last_updated", "never"),
    }

    if query_type in ("hits", "all"):
        records = _filter(mem["confirmed_hits"], "target", "compound", "chembl_id")
        result["confirmed_hits"] = records
        result["hits_count"] = len(records)

    if query_type in ("negatives", "all"):
        records = _filter(mem["negative_results"], "target", "compound")
        result["negative_results"] = records
        result["negatives_count"] = len(records)

    if query_type in ("admet", "all"):
        records = _filter(mem["admet_profiles"], "drug")
        result["admet_profiles"] = records
        result["admet_count"] = len(records)

    if query_type in ("adverse_events", "all"):
        records = _filter(mem["adverse_event_signals"], "drug")
        result["adverse_event_signals"] = records
        result["adverse_events_count"] = len(records)

    if query_type not in ("hits", "negatives", "admet", "adverse_events", "all"):
        result["error"] = f"Unknown query_type '{query_type}'. Use: hits|negatives|admet|adverse_events|all"

    note_parts = []
    if result.get("hits_count"):
        note_parts.append(f"{result['hits_count']} prior hit(s) found")
    if result.get("negatives_count"):
        note_parts.append(f"{result['negatives_count']} known negative(s) — skip these compounds")
    if result.get("admet_count"):
        note_parts.append(f"{result['admet_count']} ADMET profile(s) cached")
    result["note"] = "; ".join(note_parts) if note_parts else "No matching records found — proceed with fresh analysis."

    return result
