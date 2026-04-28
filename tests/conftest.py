"""Shared pytest fixtures for drug-discovery-agent tool tests."""

import json
import pytest
from unittest.mock import MagicMock, patch


# ── HTTP mock helpers ─────────────────────────────────────────────────────────

def make_response(json_data=None, status_code=200, text=""):
    """Create a mock requests.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.text = text
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        resp.json = MagicMock(side_effect=ValueError("no json"))
    resp.raise_for_status = MagicMock()
    return resp


# ── ChEMBL fixtures ───────────────────────────────────────────────────────────

CHEMBL_TARGET_RESP = {
    "targets": [
        {"target_chembl_id": "CHEMBL203", "pref_name": "Epidermal growth factor receptor erbB1",
         "target_type": "SINGLE PROTEIN"}
    ]
}

CHEMBL_ACTIVITY_RESP = {
    "activities": [
        {"molecule_chembl_id": "CHEMBL553",  "molecule_pref_name": "Erlotinib",
         "standard_value": "0.5",  "standard_type": "IC50",
         "pchembl_value": "9.3",   "assay_description": "Enzymatic EGFR inhibition assay"},
        {"molecule_chembl_id": "CHEMBL940",  "molecule_pref_name": "Gefitinib",
         "standard_value": "1.2",  "standard_type": "IC50",
         "pchembl_value": "8.9",   "assay_description": "Cell-based EGFR phosphorylation"},
        {"molecule_chembl_id": "CHEMBL553",  "molecule_pref_name": "Erlotinib",
         "standard_value": "2.5",  "standard_type": "IC50",
         "pchembl_value": "8.6",   "assay_description": "Orthogonal EGFR binding assay"},
    ]
}

FAERS_META_RESP  = {"meta": {"results": {"total": 4201}}}
FAERS_REACT_RESP = {"results": [
    {"term": "Diarrhoea", "count": 800},
    {"term": "Rash",      "count": 600},
]}
FAERS_SERIOUS_RESP = {"meta": {"results": {"total": 1197}}}
FAERS_FATAL_RESP   = {"meta": {"results": {"total": 126}}}

# ── Open Targets fixtures ─────────────────────────────────────────────────────

OT_DRUG_SEARCH_RESP = {"data": {"search": {"hits": []}}}

OT_GENE_SEARCH_RESP = {"data": {"search": {"hits": [
    {"id": "ENSG00000146648", "name": "EGFR"}
]}}}

OT_ASSOC_RESP = {"data": {"target": {
    "approvedSymbol": "EGFR",
    "associatedDiseases": {"rows": [
        {"disease": {"name": "non-small cell lung carcinoma", "id": "EFO_0003060"}, "score": 0.85},
        {"disease": {"name": "glioblastoma multiforme", "id": "EFO_0000519"},       "score": 0.72},
    ]}
}}}

# ── ClinicalTrials fixtures ───────────────────────────────────────────────────

CT_STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT12345678", "briefTitle": "Phase II EGFR Study"},
        "statusModule":         {"overallStatus": "RECRUITING"},
        "designModule":         {"phases": ["PHASE2"],
                                 "enrollmentInfo": {"count": 150}},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Erlotinib", "otherNames": []}],
            "armGroups":     [{"label": "Active"}, {"label": "Placebo"}],
        },
        "outcomesModule": {"primaryOutcomes": [{"measure": "Overall survival at 12 months"}]},
    }
}

CT_STUDIES_RESP = {"studies": [CT_STUDY]}

# ── Open Targets disease search ───────────────────────────────────────────────

OT_DISEASE_SEARCH_RESP = {"data": {"search": {"hits": [
    {"id": "EFO_0003060", "name": "non-small cell lung carcinoma"}
]}}}

OT_DRUG_CANDIDATES_RESP = {"data": {"disease": {"drugAndClinicalCandidates": {"rows": [
    {"drug": {"name": "Erlotinib", "drugType": "Small molecule"}, "maxClinicalStage": "APPROVAL"},
    {"drug": {"name": "Gefitinib", "drugType": "Small molecule"}, "maxClinicalStage": "PHASE3"},
]}}}}
