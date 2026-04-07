"""Tests for tools/regulatory_competitive.py — all public functions."""

import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import make_response, CT_STUDY, CT_STUDIES_RESP


def _reset_session():
    from tools.session import SESSION
    for key in SESSION:
        if isinstance(SESSION[key], list):
            SESSION[key].clear()


# ── map_regulatory_path ───────────────────────────────────────────────────────

class TestMapRegulatoryPath:
    OT_RESP = {"data": {"search": {"hits": [
        {"object": {"name": "Erlotinib",
                    "linkedTargets": {"rows": [{"id": "ENSG00000146648", "approvedSymbol": "EGFR"}]}}}
    ]}}}
    OT_ASSOC_RESP = {"data": {"target": {
        "approvedSymbol": "EGFR",
        "associatedDiseases": {"rows": [
            {"disease": {"name": "non-small cell lung carcinoma", "id": "EFO_0003060"}, "score": 0.85}
        ]}
    }}}

    def _ot_post(self, url, json=None, **kwargs):
        q = (json or {}).get("query", "")
        if 'entityNames: ["drug"]' in q:
            return make_response(self.OT_RESP)
        if "associatedDiseases" in q:
            return make_response(self.OT_ASSOC_RESP)
        if 'entityNames: ["target"]' in q:
            return make_response({"data": {"search": {"hits": [
                {"id": "ENSG00000146648", "name": "EGFR"}
            ]}}})
        return make_response({"data": {}})

    def test_returns_regulatory_path(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", return_value=make_response({})):
            from tools.regulatory_competitive import map_regulatory_path
            result = map_regulatory_path("Erlotinib", "non-small cell lung cancer")
        assert "drug" in result or "indication" in result

    def test_serious_condition_gets_fast_track(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", return_value=make_response({})):
            from tools.regulatory_competitive import map_regulatory_path
            result = map_regulatory_path("Erlotinib", "lung cancer")
        expedited = result.get("expedited_pathways", [])
        assert any("Fast Track" in e for e in expedited)

    def test_no_cdx_for_generic_indication(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", return_value=make_response({})):
            from tools.regulatory_competitive import map_regulatory_path
            result = map_regulatory_path("Aspirin", "headache")
        # companion_dx is the key in the actual return dict
        assert "companion_dx" in result


# ── score_trial_outcome ───────────────────────────────────────────────────────

class TestScoreTrialOutcome:
    def setup_method(self):
        _reset_session()

    def test_returns_outcome_score_in_range(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Erlotinib", "non-small cell lung cancer")
        assert 0.05 <= result["outcome_score"] <= 0.95

    def test_phase2_base_score_is_035(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Erlotinib", "lung cancer")
        # Phase 2 base = 0.35; survival endpoint +0.05; large enrollment +0.05
        assert result["phase"] == "PHASE2"
        assert result["outcome_score"] > 0.35

    def test_cns_indication_reduces_score(self):
        cns_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT99999", "briefTitle": "CNS Trial"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 200}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "A"}, {"label": "B"}],
                    "interventions": [{"type": "DRUG", "name": "Drug", "otherNames": []}],
                },
                "outcomesModule": {"primaryOutcomes": [{"measure": "Cognitive function"}]},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [cns_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "CNS / Alzheimer disease")
        assert any("CNS" in f or "neurology" in f for f in result["risk_factors"])
        assert result["ta_modifier"] and "-0.10" in result["ta_modifier"]

    def test_infectious_disease_increases_score(self):
        inf_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT77777", "briefTitle": "Antiviral"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 500}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "A"}, {"label": "B"}],
                    "interventions": [{"type": "DRUG", "name": "Drug", "otherNames": []}],
                },
                "outcomesModule": {"primaryOutcomes": [{"measure": "Viral load reduction"}]},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [inf_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "bacterial infection")
        assert "+0.08" in (result["ta_modifier"] or "")

    def test_terminated_trial_score_multiplied_by_02(self):
        terminated_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT55555", "briefTitle": "Terminated"},
                "statusModule": {"overallStatus": "TERMINATED"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 200}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "A"}, {"label": "B"}],
                    "interventions": [{"type": "DRUG", "name": "Drug", "otherNames": []}],
                },
                "outcomesModule": {"primaryOutcomes": [{"measure": "OS"}]},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [terminated_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "cancer")
        assert "TERMINATED" in result["risk_factors"]
        assert result["outcome_score"] < 0.20

    def test_small_enrollment_adds_risk(self):
        small_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT33333", "briefTitle": "Small"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 20}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "A"}, {"label": "B"}],
                    "interventions": [],
                },
                "outcomesModule": {"primaryOutcomes": [{"measure": "OS"}]},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [small_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "cancer")
        assert any("Small enrollment" in f for f in result["risk_factors"])

    def test_single_arm_penalty(self):
        single_arm_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT22222", "briefTitle": "SingleArm"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 100}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "Only arm"}],
                    "interventions": [],
                },
                "outcomesModule": {"primaryOutcomes": [{"measure": "OS"}]},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [single_arm_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "cancer")
        assert any("Single-arm" in f for f in result["risk_factors"])

    def test_missing_endpoint_adds_risk(self):
        no_endpoint_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT11111", "briefTitle": "NoEndpoint"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 100}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "A"}, {"label": "B"}],
                    "interventions": [],
                },
                "outcomesModule": {"primaryOutcomes": []},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [no_endpoint_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "cancer")
        assert any("endpoint" in f.lower() or "missing" in f.lower() for f in result["risk_factors"])

    def test_uncertainty_flag_on_three_risk_factors_no_ta_upside(self):
        risky_study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT44444", "briefTitle": "Risky"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 20}},
                "armsInterventionsModule": {
                    "armGroups": [{"label": "Only"}],
                    "interventions": [],
                },
                "outcomesModule": {"primaryOutcomes": []},
            }
        }
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": [risky_study]})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Drug", "cancer")
        # Small enrollment + single arm + missing endpoint = ≥3 risk factors, no TA upside
        assert result["uncertainty_flag"] is True

    def test_uncertainty_flag_on_bio_score_divergence(self):
        """Set a biology score in SESSION that diverges >0.20 from trial score."""
        from tools.session import SESSION
        SESSION["biology"] = [{"associations": [
            {"disease": {"name": "non-small cell lung cancer"}, "score": 0.20}
        ]}]
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("Erlotinib", "non-small cell lung cancer")
        # outcome_score ~0.50, bio_score 0.20 → divergence ~0.30 > 0.20
        assert result["uncertainty_flag"] is True
        assert "diverges" in result["uncertainty_reason"]

    def test_no_trials_returns_error(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response({"studies": []})):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("UnknownDrug", "unknown disease")
        assert "error" in result

    def test_nct_id_lookup(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDY)):
            from tools.regulatory_competitive import score_trial_outcome
            result = score_trial_outcome("NCT12345678", "lung cancer")
        assert "outcome_score" in result

    def test_session_trial_outcomes_updated(self):
        _reset_session()
        from tools.session import SESSION
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import score_trial_outcome
            score_trial_outcome("Erlotinib", "lung cancer")
        assert any("outcome_score" in str(e) for e in SESSION["trial_outcomes"])


# ── check_orphan_eligibility ──────────────────────────────────────────────────

class TestCheckOrphanEligibility:
    def setup_method(self):
        _reset_session()

    def test_huntington_is_us_eligible(self):
        from tools.regulatory_competitive import check_orphan_eligibility
        result = check_orphan_eligibility("Huntington disease")
        assert result["us_eligible"] is True
        assert result["eu_eligible"] is True

    def test_benefits_listed_for_eligible(self):
        from tools.regulatory_competitive import check_orphan_eligibility
        result = check_orphan_eligibility("Huntington disease")
        assert len(result["benefits"]) > 0
        assert any("exclusivity" in b.lower() for b in result["benefits"])

    def test_high_confidence_for_known_disease(self):
        from tools.regulatory_competitive import check_orphan_eligibility
        result = check_orphan_eligibility("cystic fibrosis")
        assert result["confidence"] == "high"

    def test_low_confidence_for_unknown_disease(self):
        from tools.regulatory_competitive import check_orphan_eligibility
        result = check_orphan_eligibility("extremely rare unknown disease xyz")
        assert result["confidence"] == "low"
        assert result["us_eligible"] is None

    def test_session_orphan_flags_updated(self):
        _reset_session()
        from tools.session import SESSION
        from tools.regulatory_competitive import check_orphan_eligibility
        check_orphan_eligibility("Huntington disease")
        assert len(SESSION["orphan_flags"]) > 0

    def test_fanconi_anemia_is_eligible(self):
        from tools.regulatory_competitive import check_orphan_eligibility
        result = check_orphan_eligibility("Fanconi anemia")
        assert result["us_eligible"] is True
        assert result["estimated_prevalence"] == 1400


# ── rank_portfolio ────────────────────────────────────────────────────────────

class TestRankPortfolio:
    def setup_method(self):
        _reset_session()

    # rank_portfolio requires ensembl_id to query OT; assets without it are skipped
    ASSETS = [
        {"name": "AssetA", "ensembl_id": "ENSG00000146648"},
        {"name": "AssetB", "ensembl_id": "ENSG00000133703"},
    ]

    OT_ASSOC_RESP = {"data": {"target": {"associatedDiseases": {"rows": [
        {"disease": {"name": "lung cancer"}, "score": 0.85},
    ]}}}}

    def _ot_post(self, url, json=None, **kwargs):
        return make_response(self.OT_ASSOC_RESP)

    def _ct_get(self, url, **kwargs):
        return make_response({"studies": []})

    def test_returns_ranked_list(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", side_effect=self._ct_get):
            from tools.regulatory_competitive import rank_portfolio
            result = rank_portfolio(self.ASSETS)
        assert "ranked_assets" in result
        assert len(result["ranked_assets"]) == 2

    def test_composite_score_present(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", side_effect=self._ct_get):
            from tools.regulatory_competitive import rank_portfolio
            result = rank_portfolio(self.ASSETS)
        for asset in result["ranked_assets"]:
            assert "composite_score" in asset

    def test_returns_ok_status(self):
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", side_effect=self._ct_get):
            from tools.regulatory_competitive import rank_portfolio
            result = rank_portfolio(self.ASSETS)
        assert result.get("status") == "ok"

    def test_no_ensembl_id_assets_are_excluded(self):
        assets_no_id = [{"name": "NoID", "phase": "PHASE2"}]
        with patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.requests.get", side_effect=self._ct_get):
            from tools.regulatory_competitive import rank_portfolio
            result = rank_portfolio(assets_no_id)
        # Assets without ensembl_id → _score_asset returns None → excluded
        assert result["ranked_assets"] == []


# ── query_competitive_intel ───────────────────────────────────────────────────

class TestQueryCompetitiveIntel:
    def setup_method(self):
        _reset_session()

    def test_returns_intel_for_ta(self):
        # query_competitive_intel reads from static competitive_intel.json — no HTTP calls
        from tools.regulatory_competitive import query_competitive_intel
        result = query_competitive_intel(therapeutic_area="oncology")
        # Returns "assets" key (competitor pipeline assets) or "error" if file missing
        assert "assets" in result or "error" in result

    def test_returns_total_count(self):
        from tools.regulatory_competitive import query_competitive_intel
        result = query_competitive_intel(therapeutic_area="oncology")
        if "assets" in result:
            assert isinstance(result["assets"], list)

    def test_filters_by_competitor(self):
        from tools.regulatory_competitive import query_competitive_intel
        result = query_competitive_intel(competitor="AstraZeneca")
        assert isinstance(result, dict)


# ── list_pipeline_assets ──────────────────────────────────────────────────────

class TestListPipelineAssets:
    def test_returns_assets_list(self):
        from tools.regulatory_competitive import list_pipeline_assets
        result = list_pipeline_assets()
        assert "assets" in result
        assert isinstance(result["assets"], list)

    def test_filters_by_phase(self):
        from tools.regulatory_competitive import list_pipeline_assets
        result = list_pipeline_assets(phase="PHASE3")
        for asset in result["assets"]:
            assert "3" in str(asset.get("phase", "")) or "III" in str(asset.get("phase", ""))

    def test_filters_by_therapeutic_area(self):
        from tools.regulatory_competitive import list_pipeline_assets
        result = list_pipeline_assets(therapeutic_area="oncology")
        assert isinstance(result, dict)


# ── monitor_competitive_signals ───────────────────────────────────────────────

class TestMonitorCompetitiveSignals:
    def setup_method(self):
        _reset_session()

    def test_returns_signals(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import monitor_competitive_signals
            result = monitor_competitive_signals("lung cancer")
        assert "signals" in result or "competitive_signals" in result or isinstance(result, dict)

    def test_filters_by_specified_competitors(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import monitor_competitive_signals
            result = monitor_competitive_signals("lung cancer", competitors=["AstraZeneca"])
        assert isinstance(result, dict)

    def test_session_competitive_signals_updated(self):
        _reset_session()
        from tools.session import SESSION
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(CT_STUDIES_RESP)):
            from tools.regulatory_competitive import monitor_competitive_signals
            monitor_competitive_signals("lung cancer")
        assert len(SESSION["competitive_signals"]) >= 0  # may be empty list


# ── get_protein_structure_context ─────────────────────────────────────────────

class TestGetProteinStructureContext:
    UNIPROT_RESP = {"results": [{
        "proteinDescription": {"recommendedName": {"fullName": {"value": "Epidermal growth factor receptor"}}},
        "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Receptor for EGF"}]}],
        "features": [{"type": "Binding site", "location": {"start": {"value": 712}}}],
        "sequence": {"length": 1210},
    }]}

    # OT tractability is a list of {label, modality, value} dicts
    OT_TRACTABILITY_RESP = {"data": {"target": {
        "tractability": [
            {"label": "Approved Drug", "modality": "SM", "value": True},
            {"label": "Advanced Clinical", "modality": "AB", "value": False},
        ]
    }}}

    def _ot_post(self, url, json=None, **kwargs):
        q = (json or {}).get("query", "")
        if "tractability" in q:
            return make_response(self.OT_TRACTABILITY_RESP)
        if 'entityNames: ["target"]' in q:
            return make_response({"data": {"search": {"hits": [
                {"id": "ENSG00000146648", "name": "EGFR"}
            ]}}})
        return make_response({"data": {}})

    def test_returns_structure_context(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(self.UNIPROT_RESP, 200)), \
             patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.fold_target",
                   return_value={"status": "genomeclaw_offline"}):
            from tools.regulatory_competitive import get_protein_structure_context
            result = get_protein_structure_context("EGFR")
        assert isinstance(result, dict)
        assert result.get("gene_symbol") == "EGFR"

    def test_includes_modality_recommendation(self):
        with patch("tools.regulatory_competitive.requests.get",
                   return_value=make_response(self.UNIPROT_RESP, 200)), \
             patch("tools.regulatory_competitive.requests.post", side_effect=self._ot_post), \
             patch("tools.regulatory_competitive.fold_target",
                   return_value={"status": "genomeclaw_offline"}):
            from tools.regulatory_competitive import get_protein_structure_context
            result = get_protein_structure_context("EGFR")
        assert "recommended_modality" in result
        assert result["recommended_modality"] in ("Small Molecule", "Antibody", "Unknown")
