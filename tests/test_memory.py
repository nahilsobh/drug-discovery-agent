"""Tests for tools/memory.py — long-term cross-session knowledge base."""

import json
import os
import tempfile
import pytest
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _import_memory():
    """Import memory module with patched path so tests use a temp file."""
    import importlib
    import tools.memory as mem
    importlib.reload(mem)
    return mem


# ── _memory_path / _load_memory / _save_memory ────────────────────────────────

class TestMemoryIO:
    def test_load_returns_empty_schema_when_file_missing(self, tmp_path):
        nonexistent = str(tmp_path / "does_not_exist.json")
        with patch("tools.memory._memory_path", return_value=nonexistent):
            from tools.memory import _load_memory
            mem = _load_memory()
        assert "confirmed_hits" in mem
        assert "negative_results" in mem
        assert "admet_profiles" in mem
        assert "adverse_event_signals" in mem
        assert "sar_patterns" in mem
        assert mem["_meta"]["version"] == "1.0"

    def test_load_returns_empty_schema_on_corrupt_json(self, tmp_path):
        bad_file = tmp_path / "corrupt.json"
        bad_file.write_text("NOT JSON {{{")
        with patch("tools.memory._memory_path", return_value=str(bad_file)):
            from tools.memory import _load_memory
            mem = _load_memory()
        assert mem["confirmed_hits"] == []

    def test_save_and_reload_roundtrip(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import _load_memory, _save_memory
            mem = _load_memory()
            mem["confirmed_hits"].append({"target": "EGFR", "compound": "Erlotinib", "ic50_nm": 0.5})
            _save_memory(mem)
            mem2 = _load_memory()
        assert mem2["confirmed_hits"][0]["compound"] == "Erlotinib"

    def test_save_updates_total_records_meta(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import _load_memory, _save_memory
            mem = _load_memory()
            mem["confirmed_hits"].append({"target": "T", "compound": "C", "ic50_nm": 1.0})
            mem["admet_profiles"].append({"drug": "D"})
            _save_memory(mem)
            with open(f) as fh:
                saved = json.load(fh)
        assert saved["_meta"]["total_records"] == 2

    def test_save_updates_last_updated_date(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import _load_memory, _save_memory
            import datetime
            mem = _load_memory()
            _save_memory(mem)
            with open(f) as fh:
                saved = json.load(fh)
        assert saved["_meta"]["last_updated"] == str(datetime.date.today())


# ── record_hit ────────────────────────────────────────────────────────────────

class TestRecordHit:
    def test_records_new_hit(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_hit, _load_memory
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 0.5, "IC50", "Enzymatic", "multi-assay")
            mem = _load_memory()
        assert len(mem["confirmed_hits"]) == 1
        hit = mem["confirmed_hits"][0]
        assert hit["target"] == "EGFR"
        assert hit["compound"] == "Erlotinib"
        assert hit["ic50_nm"] == 0.5

    def test_deduplicates_exact_same_hit(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_hit, _load_memory
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 0.5, "IC50", "assay1", "multi")
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 0.5, "IC50", "assay2", "single")
            mem = _load_memory()
        assert len(mem["confirmed_hits"]) == 1

    def test_case_insensitive_dedup(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_hit, _load_memory
            record_hit("egfr", "CHEMBL203", "erlotinib", 0.5, "IC50", "a", "b")
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 0.5, "IC50", "a", "b")
            mem = _load_memory()
        assert len(mem["confirmed_hits"]) == 1

    def test_records_different_ic50_as_new(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_hit, _load_memory
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 0.5, "IC50", "a", "b")
            record_hit("EGFR", "CHEMBL203", "Erlotinib", 1.0, "IC50", "a", "b")
            mem = _load_memory()
        assert len(mem["confirmed_hits"]) == 2

    def test_truncates_long_assay_description(self, tmp_path):
        f = str(tmp_path / "mem.json")
        long_desc = "x" * 200
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_hit, _load_memory
            record_hit("T", "C1", "Drug", 1.0, "IC50", long_desc, "multi")
            mem = _load_memory()
        assert len(mem["confirmed_hits"][0]["assay_description"]) <= 120


# ── record_negative ───────────────────────────────────────────────────────────

class TestRecordNegative:
    def test_records_new_negative(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_negative, _load_memory
            record_negative("EGFR", "BadDrug", "hERG blocker", ["hERG blocker"], "test")
            mem = _load_memory()
        assert len(mem["negative_results"]) == 1
        neg = mem["negative_results"][0]
        assert neg["compound"] == "BadDrug"
        assert "hERG blocker" in neg["red_flags"]

    def test_deduplicates_same_target_compound(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_negative, _load_memory
            record_negative("EGFR", "BadDrug", "reason1", [], "ctx1")
            record_negative("EGFR", "BadDrug", "reason2", [], "ctx2")
            mem = _load_memory()
        assert len(mem["negative_results"]) == 1

    def test_different_compound_is_stored(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_negative, _load_memory
            record_negative("EGFR", "BadDrug1", "reason", [], "")
            record_negative("EGFR", "BadDrug2", "reason", [], "")
            mem = _load_memory()
        assert len(mem["negative_results"]) == 2


# ── record_admet ──────────────────────────────────────────────────────────────

class TestRecordAdmet:
    def test_records_admet_profile(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_admet, _load_memory
            record_admet("Erlotinib", "TIER-1", [], ["Poor solubility"], 0.82)
            mem = _load_memory()
        assert len(mem["admet_profiles"]) == 1
        assert mem["admet_profiles"][0]["tier"] == "TIER-1"
        assert mem["admet_profiles"][0]["summary_score"] == 0.82

    def test_deduplicates_same_drug(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_admet, _load_memory
            record_admet("Erlotinib", "TIER-1", [], [], 0.82)
            record_admet("erlotinib", "TIER-2", ["hERG"], [], 0.45)
            mem = _load_memory()
        assert len(mem["admet_profiles"]) == 1


# ── record_adverse_event ──────────────────────────────────────────────────────

class TestRecordAdverseEvent:
    def test_records_adverse_event(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_adverse_event, _load_memory
            record_adverse_event("Erlotinib", "MODERATE safety signal", 28.5, 4201)
            mem = _load_memory()
        assert len(mem["adverse_event_signals"]) == 1
        ae = mem["adverse_event_signals"][0]
        assert ae["drug"] == "Erlotinib"
        assert ae["serious_pct"] == 28.5

    def test_deduplicates_same_drug(self, tmp_path):
        f = str(tmp_path / "mem.json")
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import record_adverse_event, _load_memory
            record_adverse_event("Erlotinib", "LOW", 10.0, 100)
            record_adverse_event("erlotinib", "HIGH", 60.0, 200)
            mem = _load_memory()
        assert len(mem["adverse_event_signals"]) == 1


# ── recall_longterm_memory ────────────────────────────────────────────────────

class TestRecallLongtermMemory:
    def _seed_memory(self, f):
        data = {
            "_meta": {"version": "1.0", "created": "2026-01-01",
                      "last_updated": "2026-04-07", "total_records": 3},
            "confirmed_hits": [
                {"target": "EGFR", "compound": "Erlotinib", "ic50_nm": 0.5,
                 "chembl_id": "CHEMBL203", "assay_type": "IC50",
                 "assay_description": "Enzymatic", "provenance_quality": "multi"},
                {"target": "KRAS", "compound": "Sotorasib", "ic50_nm": 0.9,
                 "chembl_id": "CHEMBL999", "assay_type": "IC50",
                 "assay_description": "Covalent inhibition", "provenance_quality": "multi"},
            ],
            "negative_results": [
                {"target": "EGFR", "compound": "ToxicDrug", "failure_reason": "hERG", "red_flags": []}
            ],
            "admet_profiles": [
                {"drug": "Erlotinib", "tier": "TIER-1", "red_flags": [], "minor_flags": [], "summary_score": 0.82}
            ],
            "adverse_event_signals": [
                {"drug": "Erlotinib", "signal": "MODERATE", "serious_pct": 28.5, "total_reports": 4201}
            ],
            "sar_patterns": [],
        }
        with open(f, "w") as fh:
            json.dump(data, fh)

    def test_recall_hits_returns_all(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("hits")
        assert result["hits_count"] == 2
        assert len(result["confirmed_hits"]) == 2

    def test_recall_hits_with_target_filter(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("hits", target_filter="EGFR")
        assert result["hits_count"] == 1
        assert result["confirmed_hits"][0]["compound"] == "Erlotinib"

    def test_recall_negatives(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("negatives")
        assert result["negatives_count"] == 1
        assert "skip these compounds" in result["note"]

    def test_recall_admet(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("admet")
        assert result["admet_count"] == 1

    def test_recall_adverse_events(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("adverse_events")
        assert result["adverse_events_count"] == 1

    def test_recall_all(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("all")
        assert "confirmed_hits" in result
        assert "negative_results" in result
        assert "admet_profiles" in result
        assert "adverse_event_signals" in result

    def test_recall_unknown_query_type_returns_error(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("banana")
        assert "error" in result

    def test_recall_max_results_limits_output(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("hits", max_results=1)
        assert result["hits_count"] == 1

    def test_recall_empty_store_returns_no_records_note(self, tmp_path):
        f = str(tmp_path / "mem.json")
        # Don't seed — file doesn't exist
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("hits")
        assert "No matching records" in result["note"]

    def test_total_in_store_reflects_meta(self, tmp_path):
        f = str(tmp_path / "mem.json")
        self._seed_memory(f)
        with patch("tools.memory._memory_path", return_value=f):
            from tools.memory import recall_longterm_memory
            result = recall_longterm_memory("all")
        assert result["total_in_store"] == 3
