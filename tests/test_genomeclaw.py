"""Tests for tools/genomeclaw.py — fold_target, score_variant_effect, predict_admet,
_metabolite_analysis, query_genomeclaw_databases."""

import json
import os
import pytest
import requests as _requests
from unittest.mock import patch, MagicMock

from tests.conftest import make_response


def make_text_response(json_data, status_code=200):
    """Response whose .text is the JSON string (fold_target uses json.loads(poll.text))."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    return resp


def _reset_session():
    from tools.session import SESSION
    for key in SESSION:
        if isinstance(SESSION[key], list):
            SESSION[key].clear()


# ── _check_genomeclaw_health ──────────────────────────────────────────────────

class TestCheckGenomeclawHealth:
    def test_returns_true_on_200(self):
        with patch("tools.genomeclaw.requests.get", return_value=make_response({}, 200)):
            from tools.genomeclaw import _check_genomeclaw_health
            assert _check_genomeclaw_health() is True

    def test_returns_false_on_non_200(self):
        with patch("tools.genomeclaw.requests.get", return_value=make_response({}, 503)):
            from tools.genomeclaw import _check_genomeclaw_health
            assert _check_genomeclaw_health() is False

    def test_returns_false_on_exception(self):
        with patch("tools.genomeclaw.requests.get", side_effect=Exception("refused")):
            from tools.genomeclaw import _check_genomeclaw_health
            assert _check_genomeclaw_health() is False


# ── _fetch_uniprot_sequence ───────────────────────────────────────────────────

class TestFetchUniprotSequence:
    UNIPROT_RESP = {"results": [{"sequence": {"value": "MRPSGTAGAALLALLAALCPASRALEEKKVC"}}]}

    def test_returns_sequence_on_success(self):
        with patch("tools.genomeclaw.requests.get", return_value=make_response(self.UNIPROT_RESP)):
            from tools.genomeclaw import _fetch_uniprot_sequence
            seq = _fetch_uniprot_sequence("EGFR")
        assert seq == "MRPSGTAGAALLALLAALCPASRALEEKKVC"

    def test_returns_empty_string_on_error(self):
        with patch("tools.genomeclaw.requests.get", side_effect=Exception("timeout")):
            from tools.genomeclaw import _fetch_uniprot_sequence
            seq = _fetch_uniprot_sequence("EGFR")
        assert seq == ""

    def test_returns_empty_string_when_no_results(self):
        with patch("tools.genomeclaw.requests.get", return_value=make_response({"results": []})):
            from tools.genomeclaw import _fetch_uniprot_sequence
            seq = _fetch_uniprot_sequence("FAKEGENE")
        assert seq == ""


# ── fold_target ───────────────────────────────────────────────────────────────

class TestFoldTarget:
    SHORT_SEQ = "MRPSGTAGAALLALLAALC"  # 19 aa — well under BOLTZ_MAX_SEQ

    FOLD_COMPLETED = {
        "status": "completed",
        "mean_plddt": 0.852,
        "elapsed_secs": 15,
        "pdb": "ATOM 1 N ...",
    }
    FOLD_SUBMIT_RESP = {"job_id": "abc123"}

    def _make_post(self, url, **kwargs):
        return make_response(self.FOLD_SUBMIT_RESP, 202)

    def _make_get_poll(self, url, **kwargs):
        if "/api/fold/" in url:
            return make_text_response(self.FOLD_COMPLETED, 200)
        # UniProt
        return make_response({"results": [{"sequence": {"value": self.SHORT_SEQ}}]}, 200)

    def test_returns_error_when_sequence_unresolvable(self):
        with patch("tools.genomeclaw.requests.get",
                   return_value=make_response({"results": []}, 200)), \
             patch("tools.genomeclaw.requests.post",
                   return_value=make_response({}, 200)):
            from tools.genomeclaw import fold_target
            result = fold_target("FAKEGENE999")
        assert result["status"] == "error"

    def test_accepts_raw_sequence(self):
        """Passing a valid AA sequence directly skips UniProt lookup and folds successfully."""
        with patch("tools.genomeclaw.requests.post", side_effect=self._make_post), \
             patch("tools.genomeclaw.requests.get", side_effect=self._make_get_poll), \
             patch("tools.genomeclaw.time.sleep"):
            from tools.genomeclaw import fold_target
            result = fold_target(self.SHORT_SEQ)
        assert "plddt_mean" in result

    def test_clawapi_offline_returns_offline_status(self):
        import requests as req
        with patch("tools.genomeclaw.requests.post",
                   side_effect=req.exceptions.ConnectionError("refused")):
            from tools.genomeclaw import fold_target
            result = fold_target(self.SHORT_SEQ)
        assert result["status"] == "genomeclaw_offline"

    def test_domain_fallback_for_long_gene(self):
        """Gene with known domain > BOLTZ_MAX_SEQ should trigger domain clipping."""
        long_seq = "A" * 2200  # exceeds BOLTZ_MAX_SEQ=2048
        with patch("tools.genomeclaw._fetch_uniprot_sequence", return_value=long_seq), \
             patch("tools.genomeclaw.requests.post", side_effect=self._make_post), \
             patch("tools.genomeclaw.requests.get", side_effect=self._make_get_poll), \
             patch("tools.genomeclaw.time.sleep"):
            from tools.genomeclaw import fold_target
            result = fold_target("LRRK2")
        # LRRK2 is in KNOWN_DOMAINS — should not return "too long" error
        assert "Sequence too long" not in str(result.get("note", ""))

    def test_unknown_long_gene_returns_too_long_error(self):
        long_seq = "A" * 2200
        with patch("tools.genomeclaw._fetch_uniprot_sequence", return_value=long_seq):
            from tools.genomeclaw import fold_target
            result = fold_target("VERYLONGUNKNOWNGENE")
        assert result["status"] == "error"
        assert "too long" in result["note"].lower() or "Sequence too long" in result["note"]


# ── score_variant_effect ──────────────────────────────────────────────────────

class TestScoreVariantEffect:
    VARIANT_RESP = {
        "delta_log_likelihood": -3.5,
        "assessment": "Likely damaging",
        "wildtype_probability": 0.90,
        "mutant_probability": 0.05,
    }

    def test_returns_result_from_clawapi(self):
        seq = "M" * 900  # position 858 < 900
        with patch("tools.genomeclaw._fetch_uniprot_sequence", return_value=seq), \
             patch("tools.genomeclaw.requests.post",
                   return_value=make_response(self.VARIANT_RESP, 200)):
            from tools.genomeclaw import score_variant_effect
            result = score_variant_effect("EGFR", "L858R")
        assert result.get("status") != "error" or "delta_log_likelihood" in result

    def test_clawapi_offline_returns_offline_status(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("refused")), \
             patch("tools.genomeclaw.requests.get", side_effect=Exception("refused")):
            from tools.genomeclaw import score_variant_effect
            result = score_variant_effect("EGFR", "L858R")
        assert "offline" in str(result).lower() or "error" in result.get("status", "")

    def test_session_variant_effects_updated_on_success(self):
        _reset_session()
        from tools.session import SESSION
        # Sequence must be long enough for position 50 (use L50R, seq ≥50 residues)
        with patch("tools.genomeclaw.requests.post",
                   return_value=make_response(self.VARIANT_RESP, 200)), \
             patch("tools.genomeclaw._fetch_uniprot_sequence",
                   return_value="M" * 200):
            from tools.genomeclaw import score_variant_effect
            score_variant_effect("EGFR", "L50R")
        assert len(SESSION.get("variant_effects", [])) > 0


# ── _metabolite_analysis ──────────────────────────────────────────────────────

class TestMetaboliteAnalysis:
    def test_clawapi_success_returns_clawapi_method(self):
        met_resp = {"metabolites": [
            {"smiles": "CC(=O)O", "tier": "TIER-1", "key_change": "ester hydrolysis"}
        ]}
        with patch("tools.genomeclaw.requests.post", return_value=make_response(met_resp, 200)):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)OC", "TIER-3", "TestDrug")
        assert result["method"] == "clawapi"
        assert result["evaluated"] is True

    def test_hazard_reduction_true_when_parent_tier3_and_metabolite_tier1(self):
        met_resp = {"metabolites": [
            {"smiles": "CC(=O)O", "tier": "TIER-1", "key_change": "hydrolysis"}
        ]}
        with patch("tools.genomeclaw.requests.post", return_value=make_response(met_resp, 200)):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)OC", "TIER-3", "TestDrug")
        assert result["metabolite_hazard_reduction"] is True

    def test_rule_based_fallback_on_api_failure(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("offline")):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)OC", "TIER-1", "TestDrug")
        assert result["method"] == "rule_based_fallback"

    def test_ester_detected_in_rule_based(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("offline")):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)OCC", "TIER-2", "EsterDrug")
        labile = result.get("labile_groups_detected", [])
        assert any("ester" in g.lower() for g in labile)

    def test_amide_detected_in_rule_based(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("offline")):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)NCC", "TIER-2", "AmideDrug")
        labile = result.get("labile_groups_detected", [])
        assert any("amide" in g.lower() for g in labile)

    def test_no_labile_groups_returns_not_evaluated(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("offline")):
            from tools.genomeclaw import _metabolite_analysis
            # Simple alkane SMILES — no labile groups
            result = _metabolite_analysis("CCCCCC", "TIER-1", "HexaneDrug")
        assert result["evaluated"] is False

    def test_rule_based_hazard_reduction_when_tier3_and_labile(self):
        with patch("tools.genomeclaw.requests.post", side_effect=Exception("offline")):
            from tools.genomeclaw import _metabolite_analysis
            result = _metabolite_analysis("CC(=O)OCC", "TIER-3", "EsterDrug")
        assert result["metabolite_hazard_reduction"] is True


# ── predict_admet ─────────────────────────────────────────────────────────────

class TestPredictAdmet:
    ERLOTINIB_SMILES = "C#Cc1cccc(Nc2ncnc3cc(OCCOC)c(OCCOC)cc23)c1"

    CLAWADMET_OUTPUT = json.dumps({
        "summary_score": 0.82,
        "summary_label": "SAFE",
        "results": [
            {"model": "hERG",      "label": "hERG non-blocker", "confidence": 0.91, "value": 0.1},
            {"model": "Ames",      "label": "Non-mutagenic",     "confidence": 0.88, "value": 0.05},
            {"model": "BBB",       "label": "BBB penetrant",     "confidence": 0.77, "value": 0.8},
            {"model": "Solubility","label": "Highly soluble",    "confidence": 0.85, "value": 0.9},
            {"model": "HalfLife",  "label": "Long half-life",    "confidence": 0.80, "value": 0.7},
            {"model": "CYP3A4",    "label": "CYP3A4 inhibitor",  "confidence": 0.72, "value": 0.6},
            {"model": "CYP2D6",    "label": "CYP2D6 non-inhibitor", "confidence": 0.91, "value": 0.1},
            {"model": "Pgp",       "label": "Pgp non-substrate", "confidence": 0.83, "value": 0.2},
            {"model": "PPB",       "label": "High PPB",          "confidence": 0.78, "value": 0.9},
        ]
    })

    def _make_proc(self, stdout, returncode=0):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = ""
        return proc

    def _patch_binary(self):
        return patch("os.path.isfile", return_value=True)

    def _patch_subprocess(self, proc):
        return patch("subprocess.run", return_value=proc)

    def _patch_no_memory(self):
        return patch.multiple("tools.genomeclaw",
                              record_admet=MagicMock(),
                              record_negative=MagicMock())

    def _patch_pubchem(self):
        pubchem_resp = make_response({"PropertyTable": {"Properties": [
            {"IsomericSMILES": self.ERLOTINIB_SMILES}
        ]}})
        return patch("tools.genomeclaw.requests.get", return_value=pubchem_resp)

    def test_tier1_for_clean_compound(self):
        proc = self._make_proc(self.CLAWADMET_OUTPUT)
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["tier"] == "TIER-1"
        assert result["red_flags"] == []

    def test_tier3_when_herg_blocker(self):
        risky_output = json.dumps({
            "summary_score": 0.35, "summary_label": "RISKY",
            "results": [
                {"model": "hERG", "label": "hERG blocker", "confidence": 0.92, "value": 0.9},
                {"model": "Ames", "label": "Non-mutagenic", "confidence": 0.88, "value": 0.1},
                {"model": "BBB",  "label": "BBB penetrant", "confidence": 0.77, "value": 0.8},
                {"model": "Solubility", "label": "Soluble", "confidence": 0.80, "value": 0.7},
                {"model": "HalfLife", "label": "Short", "confidence": 0.80, "value": 0.3},
                {"model": "CYP3A4", "label": "CYP3A4 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "CYP2D6", "label": "CYP2D6 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "Pgp", "label": "Pgp non-substrate", "confidence": 0.80, "value": 0.1},
                {"model": "PPB", "label": "High PPB", "confidence": 0.80, "value": 0.9},
            ]
        })
        proc = self._make_proc(risky_output)
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["tier"] == "TIER-3"
        assert any("hERG" in f for f in result["red_flags"])

    def test_tier2_when_moderate_herg_signal(self):
        moderate_output = json.dumps({
            "summary_score": 0.65, "summary_label": "SAFE",
            "results": [
                {"model": "hERG", "label": "hERG non-blocker", "confidence": 0.70, "value": 0.4},
                {"model": "Ames", "label": "Non-mutagenic", "confidence": 0.90, "value": 0.1},
                {"model": "BBB",  "label": "BBB penetrant", "confidence": 0.80, "value": 0.8},
                {"model": "Solubility", "label": "Soluble", "confidence": 0.85, "value": 0.8},
                {"model": "HalfLife", "label": "Long", "confidence": 0.80, "value": 0.7},
                {"model": "CYP3A4", "label": "CYP3A4 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "CYP2D6", "label": "CYP2D6 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "Pgp", "label": "Pgp non-substrate", "confidence": 0.80, "value": 0.1},
                {"model": "PPB", "label": "High PPB", "confidence": 0.80, "value": 0.9},
            ]
        })
        proc = self._make_proc(moderate_output)
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["tier"] == "TIER-2"
        assert any("Moderate hERG" in f for f in result["minor_flags"])

    def test_returns_not_built_when_binary_missing(self):
        with patch("os.path.isfile", return_value=False), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["status"] == "genomeclaw_not_built"

    def test_returns_smiles_not_resolved_for_drug_name_without_smiles(self):
        with patch("tools.genomeclaw.requests.get",
                   return_value=make_response({"PropertyTable": {"Properties": []}}, 200)):
            from tools.genomeclaw import predict_admet
            result = predict_admet("COMPLETELY_UNKNOWN_DRUG_XYZ")
        assert result["status"] == "smiles_not_resolved"

    def test_returns_error_on_subprocess_failure(self):
        proc = self._make_proc("", returncode=1)
        proc.stderr = "fatal error"
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["status"] == "error"

    def test_returns_timeout_on_subprocess_timeout(self):
        import subprocess
        with self._patch_binary(), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("clawadmet", 30)), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES)
        assert result["status"] == "timeout"

    def test_include_metabolites_false_skips_analysis(self):
        proc = self._make_proc(self.CLAWADMET_OUTPUT)
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            result = predict_admet(self.ERLOTINIB_SMILES, include_metabolites=False)
        assert result["metabolite_analysis"]["method"] == "skipped"

    def test_record_admet_called(self):
        proc = self._make_proc(self.CLAWADMET_OUTPUT)
        mock_record_admet = MagicMock()
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), \
             patch("tools.genomeclaw.record_admet", mock_record_admet), \
             patch("tools.genomeclaw.record_negative", MagicMock()):
            from tools.genomeclaw import predict_admet
            predict_admet(self.ERLOTINIB_SMILES)
        mock_record_admet.assert_called_once()

    def test_record_negative_called_for_tier3(self):
        risky_output = json.dumps({
            "summary_score": 0.35, "summary_label": "RISKY",
            "results": [
                {"model": "hERG", "label": "hERG blocker", "confidence": 0.92, "value": 0.9},
                {"model": "Ames", "label": "Non-mutagenic", "confidence": 0.88, "value": 0.1},
                {"model": "BBB",  "label": "BBB penetrant", "confidence": 0.80, "value": 0.8},
                {"model": "Solubility", "label": "Soluble", "confidence": 0.85, "value": 0.8},
                {"model": "HalfLife", "label": "Long", "confidence": 0.80, "value": 0.7},
                {"model": "CYP3A4", "label": "CYP3A4 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "CYP2D6", "label": "CYP2D6 non-inhibitor", "confidence": 0.80, "value": 0.1},
                {"model": "Pgp", "label": "Pgp non-substrate", "confidence": 0.80, "value": 0.1},
                {"model": "PPB", "label": "High PPB", "confidence": 0.80, "value": 0.9},
            ]
        })
        proc = self._make_proc(risky_output)
        mock_record_neg = MagicMock()
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), \
             patch("tools.genomeclaw.record_admet", MagicMock()), \
             patch("tools.genomeclaw.record_negative", mock_record_neg):
            from tools.genomeclaw import predict_admet
            predict_admet(self.ERLOTINIB_SMILES)
        mock_record_neg.assert_called_once()

    def test_session_admet_profiles_updated(self):
        _reset_session()
        from tools.session import SESSION
        proc = self._make_proc(self.CLAWADMET_OUTPUT)
        with self._patch_binary(), self._patch_subprocess(proc), \
             self._patch_pubchem(), self._patch_no_memory():
            from tools.genomeclaw import predict_admet
            predict_admet(self.ERLOTINIB_SMILES)
        assert len(SESSION["admet_profiles"]) > 0


# ── query_genomeclaw_databases ────────────────────────────────────────────────

class TestQueryGenomeclawDatabases:
    GNOMAD_RESP = {"data": {"gene": {
        "symbol": "EGFR", "chrom": "7",
        "gnomad_constraint": {"pLI": 0.98, "obs_lof": 5, "exp_lof": 50, "lof_z": 3.5}
    }}}

    CHEMBL_TARGET_RESP = {"targets": [
        {"target_chembl_id": "CHEMBL203", "pref_name": "EGFR"}
    ]}
    CHEMBL_ACT_RESP = {"activities": [
        {"standard_value": "0.5"}
    ]}
    CHEMBL_COUNT_RESP = {"page_meta": {"total_count": 250}}

    CLINVAR_RESP = {"esearchresult": {"count": "85"}}
    STRING_RESP = [
        {"preferredName_A": "EGFR", "preferredName_B": "ERBB2", "score": 900}
    ]
    CBIOPORTAL_RESP = {"hugoGeneSymbol": "EGFR", "entrezGeneId": 1956, "type": "protein-coding"}

    def _get_router(self, url, **kwargs):
        if "clinvar" in url or "eutils" in url:
            return make_response(self.CLINVAR_RESP)
        if "string-db" in url:
            return make_response(self.STRING_RESP)
        if "cbioportal" in url and "mutations" not in url:
            return make_response(self.CBIOPORTAL_RESP)
        if "cbioportal" in url and "mutations" in url:
            return make_response([])
        if "chembl" in url and "target" in url:
            return make_response(self.CHEMBL_TARGET_RESP)
        if "chembl" in url and "activity" in url and "limit=1" in url:
            return make_response(self.CHEMBL_COUNT_RESP)
        if "chembl" in url and "activity" in url:
            return make_response(self.CHEMBL_ACT_RESP)
        return make_response({})

    def _post_router(self, url, json=None, **kwargs):
        if "gnomad" in url:
            return make_response(self.GNOMAD_RESP)
        if "opentargets" in url:
            return make_response({"data": {"search": {"hits": [{"id": "ENSG0001", "score": 0.9,
                "object": {"id": "ENSG0001", "approvedSymbol": "EGFR",
                    "associatedDiseases": {"rows": [
                        {"disease": {"name": "lung cancer"}, "score": 0.85}
                    ]}
                }}]}}})
        return make_response({})

    def test_returns_composite_result(self):
        with patch("tools.genomeclaw.requests.get", side_effect=self._get_router), \
             patch("tools.genomeclaw.requests.post", side_effect=self._post_router):
            from tools.genomeclaw import query_genomeclaw_databases
            result = query_genomeclaw_databases("EGFR")
        assert result.get("status") not in ("genomeclaw_offline", "error")

    def test_filters_to_requested_databases(self):
        with patch("tools.genomeclaw.requests.get", side_effect=self._get_router), \
             patch("tools.genomeclaw.requests.post", side_effect=self._post_router):
            from tools.genomeclaw import query_genomeclaw_databases
            result = query_genomeclaw_databases("EGFR", databases=["gnomad"])
        # result has "details" key containing per-DB results
        assert "gnomad" in result.get("details", {})
        assert "chembl" not in result.get("details", {})

    def test_gnomad_pli_high_intolerance(self):
        with patch("tools.genomeclaw.requests.get", side_effect=self._get_router), \
             patch("tools.genomeclaw.requests.post", side_effect=self._post_router):
            from tools.genomeclaw import query_genomeclaw_databases
            result = query_genomeclaw_databases("EGFR", databases=["gnomad"])
        gnomad = result.get("details", {}).get("gnomad", {})
        assert gnomad.get("pLI") == 0.98
        assert gnomad.get("lof_intolerant") is True

    def test_summary_druggability_score_present(self):
        with patch("tools.genomeclaw.requests.get", side_effect=self._get_router), \
             patch("tools.genomeclaw.requests.post", side_effect=self._post_router):
            from tools.genomeclaw import query_genomeclaw_databases
            result = query_genomeclaw_databases("EGFR")
        assert "summary" in result or "druggability_score" in str(result)

    def test_handles_db_exception_gracefully(self):
        with patch("tools.genomeclaw.requests.get", side_effect=Exception("network error")), \
             patch("tools.genomeclaw.requests.post", side_effect=Exception("network error")):
            from tools.genomeclaw import query_genomeclaw_databases
            result = query_genomeclaw_databases("EGFR", databases=["gnomad"])
        # Should not raise; should return some error info
        assert isinstance(result, dict)


# ── cluster_scaffolds ─────────────────────────────────────────────────────────

ASPIRIN_SMILES  = "CC(=O)Oc1ccccc1C(=O)O"
ASPIRIN2_SMILES = "CC(=O)Oc1ccccc1C(=O)OC"   # similar to aspirin (Tanimoto ~ 0.8)
INDOLE_SMILES   = "c1ccc2[nH]ccc2c1"          # unrelated scaffold


def _make_similarity_response(hits):
    return make_response({"query": "", "hits": hits, "n_library": 3})


class TestClusterScaffolds:
    HITS_WITH_SMILES = [
        {"molecule_chembl_id": "CHEMBL25",   "smiles": ASPIRIN_SMILES,  "pIC50": 6.0},
        {"molecule_chembl_id": "CHEMBL999",  "smiles": ASPIRIN2_SMILES, "pIC50": 5.5},
        {"molecule_chembl_id": "CHEMBL941",  "smiles": INDOLE_SMILES,   "pIC50": 7.2},
    ]

    def _similar_router(self, url, json=None, **kwargs):
        """Return aspirin + aspirin2 as similar; indole gets only itself."""
        query = (json or {}).get("query_smiles", "")
        if ASPIRIN_SMILES in query:
            return _make_similarity_response([
                {"id": "CHEMBL25",  "smiles": ASPIRIN_SMILES,  "tanimoto": 1.0},
                {"id": "CHEMBL999", "smiles": ASPIRIN2_SMILES, "tanimoto": 0.82},
            ])
        if ASPIRIN2_SMILES in query:
            return _make_similarity_response([
                {"id": "CHEMBL999", "smiles": ASPIRIN2_SMILES, "tanimoto": 1.0},
                {"id": "CHEMBL25",  "smiles": ASPIRIN_SMILES,  "tanimoto": 0.82},
            ])
        # indole — no cross-cluster hits above threshold
        return _make_similarity_response([
            {"id": "CHEMBL941", "smiles": INDOLE_SMILES, "tanimoto": 1.0},
        ])

    def test_offline_returns_error(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=False):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        assert r["status"] == "error"
        assert "GenomeClaw" in r["note"]

    def test_no_smiles_returns_error(self):
        hits_no_smiles = [{"molecule_chembl_id": "CHEMBL25", "pIC50": 6.0}]
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(hits_no_smiles)
        assert r["status"] == "error"
        assert "SMILES" in r["note"]

    def test_groups_similar_compounds(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=self._similar_router):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        assert r["status"] == "ok"
        assert r["n_input"] == 3
        assert r["n_clusters"] == 2  # aspirin group + indole singleton

    def test_representative_is_highest_pic50(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=self._similar_router):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        # largest cluster contains aspirin + aspirin2; rep should be CHEMBL25 (pIC50=6.0)
        largest = max(r["clusters"], key=lambda c: c["cluster_size"])
        assert largest["representative_id"] == "CHEMBL25"

    def test_clusters_sorted_by_size_descending(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=self._similar_router):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        sizes = [c["cluster_size"] for c in r["clusters"]]
        assert sizes == sorted(sizes, reverse=True)

    def test_all_compounds_assigned(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=self._similar_router):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        all_members = [m for c in r["clusters"] for m in c["members"]]
        assert sorted(all_members) == ["CHEMBL25", "CHEMBL941", "CHEMBL999"]

    def test_session_updated(self):
        from tools.session import SESSION
        SESSION["scaffold_clusters"].clear()
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=self._similar_router):
            from tools.genomeclaw import cluster_scaffolds
            cluster_scaffolds(self.HITS_WITH_SMILES)
        assert len(SESSION["scaffold_clusters"]) == 1

    def test_api_error_falls_back_to_singleton(self):
        """If /api/screen/similar returns non-200, compound becomes its own cluster."""
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response({}, 500)):
            from tools.genomeclaw import cluster_scaffolds
            r = cluster_scaffolds(self.HITS_WITH_SMILES)
        assert r["status"] == "ok"
        # Each compound is its own singleton cluster
        assert r["n_clusters"] == 3


# ── dock_compound ─────────────────────────────────────────────────────────────

DOCK_RESPONSE = {
    "ligand_smiles": ASPIRIN_SMILES,
    "best_score": -6.5,
    "n_poses": 10,
    "pose_scores": [-6.5, -5.8, -5.1, -4.9, -4.2, -3.8, -3.1, -2.9, -2.1, -1.5],
}

DOCK_MODERATE = {**DOCK_RESPONSE, "best_score": -3.2,
                 "pose_scores": [-3.2, -2.8, -2.1]}
DOCK_WEAK     = {**DOCK_RESPONSE, "best_score": -0.5,
                 "pose_scores": [-0.5, -0.3, -0.1]}


class TestDockCompound:
    POCKET = [10.0, 20.0, 30.0]

    def test_offline_returns_error(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=False):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["status"] == "error"

    def test_wrong_pocket_length_returns_error(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, [10.0, 20.0])
        assert r["status"] == "error"
        assert "pocket_center" in r["note"]

    def test_returns_ok_with_score(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response(DOCK_RESPONSE)):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["status"] == "ok"
        assert r["best_score"] == -6.5
        assert r["ligand_smiles"] == ASPIRIN_SMILES

    def test_strong_tier_below_minus5(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response(DOCK_RESPONSE)):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["binding_tier"] == "STRONG"

    def test_moderate_tier(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response(DOCK_MODERATE)):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["binding_tier"] == "MODERATE"

    def test_weak_tier(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response(DOCK_WEAK)):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["binding_tier"] == "WEAK"

    def test_n_poses_passed_through(self):
        captured = {}
        def capture_post(url, json=None, **kwargs):
            captured.update(json or {})
            return make_response(DOCK_RESPONSE)
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=capture_post):
            from tools.genomeclaw import dock_compound
            dock_compound(ASPIRIN_SMILES, self.POCKET, n_poses=5)
        assert captured.get("n_poses") == 5

    def test_session_updated(self):
        from tools.session import SESSION
        SESSION["dock_scores"].clear()
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response(DOCK_RESPONSE)):
            from tools.genomeclaw import dock_compound
            dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert len(SESSION["dock_scores"]) == 1

    def test_api_error_returns_error(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", return_value=make_response({}, 400, text="bad request")):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["status"] == "error"

    def test_network_exception_returns_error(self):
        with patch("tools.genomeclaw._check_genomeclaw_health", return_value=True), \
             patch("tools.genomeclaw.requests.post", side_effect=Exception("timeout")):
            from tools.genomeclaw import dock_compound
            r = dock_compound(ASPIRIN_SMILES, self.POCKET)
        assert r["status"] == "error"
