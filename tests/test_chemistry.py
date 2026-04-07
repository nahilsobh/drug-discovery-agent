"""Tests for tools/chemistry.py — find_hits, query_adverse_events, find_repurposing_candidates."""

import pytest
from unittest.mock import patch, MagicMock, call

from tests.conftest import (
    make_response,
    CHEMBL_TARGET_RESP, CHEMBL_ACTIVITY_RESP,
    FAERS_META_RESP, FAERS_REACT_RESP, FAERS_SERIOUS_RESP, FAERS_FATAL_RESP,
    OT_DISEASE_SEARCH_RESP, OT_DRUG_CANDIDATES_RESP,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_session():
    from tools.session import SESSION
    SESSION["biology"].clear()
    SESSION["trial_outcomes"].clear()
    SESSION["repurposing"].clear()


def _patch_no_op_memory():
    """Patch record_hit / record_adverse_event so tests don't touch real JSON file."""
    return patch.multiple(
        "tools.chemistry",
        record_hit=MagicMock(),
        record_adverse_event=MagicMock(),
    )


# ── find_hits ─────────────────────────────────────────────────────────────────

class TestFindHits:
    def setup_method(self):
        _reset_session()

    def _chembl_get(self, url, **kwargs):
        if "target/search" in url:
            return make_response(CHEMBL_TARGET_RESP)
        if "activity" in url:
            return make_response(CHEMBL_ACTIVITY_RESP)
        return make_response({})

    def test_returns_ok_status(self):
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert result["status"] == "ok"

    def test_target_name_populated(self):
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert "egfr" in result["target"].lower() or "epidermal" in result["target"].lower()

    def test_hits_found_count(self):
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        # 2 unique molecules (Erlotinib appears twice, Gefitinib once)
        assert result["hits_found"] == 2

    def test_hits_sorted_by_pIC50_descending(self):
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        pIC50s = [h["pIC50"] for h in result["hits"] if h["pIC50"] is not None]
        assert pIC50s == sorted(pIC50s, reverse=True)

    def test_best_ic50_kept_for_erlotinib(self):
        """Erlotinib has 0.5 and 2.5 nM — best (0.5) should be kept."""
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        erl = next(h for h in result["hits"] if "erlotinib" in h["compound_name"].lower())
        assert erl["value_nM"] == 0.5

    def test_conflict_detection_erlotinib(self):
        """Erlotinib at 0.5 and 2.5 nM → 5× fold discrepancy > 3× threshold."""
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert len(result["conflicting_measurements"]) == 1
        conflict = result["conflicting_measurements"][0]
        assert conflict["fold_discrepancy"] == 5.0
        assert "IC50 spread detected" in conflict["note"]

    def test_no_conflict_when_single_measurement(self):
        """Gefitinib has only one measurement — no conflict."""
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        conflict_ids = [c["chembl_id"] for c in result["conflicting_measurements"]]
        assert "CHEMBL940" not in conflict_ids

    def test_provenance_multi_assay_label(self):
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert "multi-assay" in result["provenance_quality"]

    def test_record_hit_called_for_each_hit(self):
        mock_record_hit = MagicMock()
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             patch("tools.chemistry.record_hit", mock_record_hit), \
             patch("tools.chemistry.record_adverse_event", MagicMock()):
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert mock_record_hit.call_count == result["hits_found"]

    def test_returns_error_on_no_target(self):
        empty_target = make_response({"targets": []})
        with patch("tools.chemistry.requests.get", return_value=empty_target), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("NONEXISTENT_TARGET_XYZ")
        assert result["status"] == "no_target"

    def test_returns_error_on_exception(self):
        with patch("tools.chemistry.requests.get", side_effect=Exception("network error")), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert result["status"] == "error"
        assert "network error" in result["error"]

    def test_max_ic50_filter_applied(self):
        # Activity returns values at 0.5 and 1.2 nM; max_ic50=1.0 should query for ≤1.0
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get) as mock_get, \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            find_hits("EGFR", max_ic50_nm=1.0)
        calls = [str(c) for c in mock_get.call_args_list]
        assert any("1.0" in c for c in calls)

    def test_session_biology_updated(self):
        _reset_session()
        from tools.session import SESSION
        with patch("tools.chemistry.requests.get", side_effect=self._chembl_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            find_hits("EGFR")
        assert len(SESSION["biology"]) > 0
        assert SESSION["biology"][-1]["type"] == "hit_identification"

    def test_conflict_list_empty_when_no_discrepancy(self):
        """If all molecules have only one measurement, conflicting_measurements is empty."""
        single_meas = {"activities": [
            {"molecule_chembl_id": "CHEMBL553", "molecule_pref_name": "Erlotinib",
             "standard_value": "0.5", "standard_type": "IC50",
             "pchembl_value": "9.3", "assay_description": "assay1"},
        ]}
        def _get(url, **kwargs):
            if "target/search" in url:
                return make_response(CHEMBL_TARGET_RESP)
            return make_response(single_meas)
        with patch("tools.chemistry.requests.get", side_effect=_get), \
             _patch_no_op_memory():
            from tools.chemistry import find_hits
            result = find_hits("EGFR")
        assert result["conflicting_measurements"] == []


# ── query_adverse_events ──────────────────────────────────────────────────────

class TestQueryAdverseEvents:
    def setup_method(self):
        _reset_session()

    def _faers_get(self, url, **kwargs):
        if "count=patient.reaction" in url:
            return make_response(FAERS_REACT_RESP)
        if "serious:1" in url:
            return make_response(FAERS_SERIOUS_RESP)
        if "seriousnessdeath" in url:
            return make_response(FAERS_FATAL_RESP)
        # default: total reports
        return make_response(FAERS_META_RESP)

    def test_returns_ok_status(self):
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["status"] == "ok"

    def test_correct_total_reports(self):
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["total_reports"] == 4201

    def test_serious_percentage_computed(self):
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        expected = round(100 * 1197 / 4201, 1)
        assert result["serious_pct"] == expected

    def test_signal_moderate_for_28pct(self):
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["signal"] == "MODERATE safety signal"

    def test_high_signal_when_fatal_over_5pct(self):
        high_fatal = {"meta": {"results": {"total": 500}}}
        def _get(url, **kwargs):
            if "seriousnessdeath" in url:
                return make_response(high_fatal)
            if "serious:1" in url:
                return make_response(FAERS_SERIOUS_RESP)
            if "count=patient.reaction" in url:
                return make_response(FAERS_REACT_RESP)
            return make_response(FAERS_META_RESP)
        with patch("tools.chemistry.requests.get", side_effect=_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["signal"] == "HIGH safety signal"

    def test_low_signal_when_below_20pct_serious(self):
        low_serious = {"meta": {"results": {"total": 100}}}
        def _get(url, **kwargs):
            if "seriousnessdeath" in url:
                return make_response({"meta": {"results": {"total": 10}}})
            if "serious:1" in url:
                return make_response(low_serious)
            if "count=patient.reaction" in url:
                return make_response(FAERS_REACT_RESP)
            return make_response(FAERS_META_RESP)
        with patch("tools.chemistry.requests.get", side_effect=_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["signal"] == "LOW safety signal"

    def test_no_data_when_zero_reports(self):
        with patch("tools.chemistry.requests.get",
                   return_value=make_response({"meta": {"results": {"total": 0}}})), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("UnknownDrug")
        assert result["status"] == "no_data"

    def test_top_reactions_populated(self):
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert len(result["top_reactions"]) == 2
        assert result["top_reactions"][0]["reaction"] == "Diarrhoea"

    def test_record_adverse_event_called(self):
        mock_record_ae = MagicMock()
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             patch("tools.chemistry.record_hit", MagicMock()), \
             patch("tools.chemistry.record_adverse_event", mock_record_ae):
            from tools.chemistry import query_adverse_events
            query_adverse_events("Erlotinib")
        mock_record_ae.assert_called_once()

    def test_returns_error_on_exception(self):
        with patch("tools.chemistry.requests.get", side_effect=Exception("timeout")), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            result = query_adverse_events("Erlotinib")
        assert result["status"] == "error"

    def test_session_trial_outcomes_updated(self):
        _reset_session()
        from tools.session import SESSION
        with patch("tools.chemistry.requests.get", side_effect=self._faers_get), \
             _patch_no_op_memory():
            from tools.chemistry import query_adverse_events
            query_adverse_events("Erlotinib")
        assert any(e["type"] == "adverse_events" for e in SESSION["trial_outcomes"])


# ── find_repurposing_candidates ───────────────────────────────────────────────

class TestFindRepurposingCandidates:
    def setup_method(self):
        _reset_session()

    def _ot_post(self, url, json=None, **kwargs):
        q = (json or {}).get("query", "")
        if 'entityNames: ["disease"]' in q:
            return make_response(OT_DISEASE_SEARCH_RESP)
        if "drugAndClinicalCandidates" in q:
            return make_response(OT_DRUG_CANDIDATES_RESP)
        return make_response({"data": {}})

    def test_returns_ok_status(self):
        with patch("tools.chemistry.requests.post", side_effect=self._ot_post), \
             patch("tools.chemistry.query_genomeclaw_databases",
                   return_value={"status": "genomeclaw_not_built"}):
            from tools.chemistry import find_repurposing_candidates
            result = find_repurposing_candidates("non-small cell lung cancer")
        assert result["status"] == "ok"

    def test_filters_approval_only(self):
        with patch("tools.chemistry.requests.post", side_effect=self._ot_post), \
             patch("tools.chemistry.query_genomeclaw_databases",
                   return_value={"status": "genomeclaw_not_built"}):
            from tools.chemistry import find_repurposing_candidates
            result = find_repurposing_candidates("non-small cell lung cancer")
        # Only APPROVAL rows in fixture; PHASE3 row should be excluded
        assert all(c["stage"] == "APPROVAL" for c in result["candidates"])

    def test_strategic_caution_present(self):
        with patch("tools.chemistry.requests.post", side_effect=self._ot_post), \
             patch("tools.chemistry.query_genomeclaw_databases",
                   return_value={"status": "genomeclaw_not_built"}):
            from tools.chemistry import find_repurposing_candidates
            result = find_repurposing_candidates("non-small cell lung cancer")
        assert "strategic_caution" in result
        assert "Repurposing" in result["strategic_caution"]

    def test_empty_when_no_disease_found(self):
        with patch("tools.chemistry.requests.post",
                   return_value=make_response({"data": {"search": {"hits": []}}})):
            from tools.chemistry import find_repurposing_candidates
            result = find_repurposing_candidates("very rare unknown disease xyz")
        assert result["status"] == "ok"
        assert result["candidates_found"] == 0

    def test_returns_error_on_ot_exception(self):
        with patch("tools.chemistry.requests.post", side_effect=Exception("API down")):
            from tools.chemistry import find_repurposing_candidates
            result = find_repurposing_candidates("lung cancer")
        assert result["status"] == "error"

    def test_session_repurposing_updated(self):
        _reset_session()
        from tools.session import SESSION
        with patch("tools.chemistry.requests.post", side_effect=self._ot_post), \
             patch("tools.chemistry.query_genomeclaw_databases",
                   return_value={"status": "genomeclaw_not_built"}):
            from tools.chemistry import find_repurposing_candidates
            find_repurposing_candidates("non-small cell lung cancer")
        assert len(SESSION["repurposing"]) > 0
