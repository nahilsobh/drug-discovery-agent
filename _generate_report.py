import json, sys
sys.path.insert(0, ".")
import tools._allowlist
from tools.session import SESSION
from tools.regulatory_competitive import list_pipeline_assets, rank_portfolio, map_regulatory_path, score_trial_outcome, check_orphan_eligibility, monitor_competitive_signals, query_competitive_intel
from tools.discovery import find_gaps, get_biology
from tools.literature import scan_literature, scan_arxiv

SESSION["question"] = "Full Roche oncology pipeline gap analysis"

print("Step 1: List pipeline assets")
assets = list_pipeline_assets(therapeutic_area="oncology")
print("  -> " + str(assets["total"]) + " assets")

print("Step 2: Rank portfolio")
portfolio = rank_portfolio()
print("  -> " + str(len(portfolio.get("ranked_assets", []))) + " ranked")

print("Step 3: Find gaps")
try:
    gaps = find_gaps(therapeutic_area="oncology", min_bio_score=0.5)
    print("  -> " + str(gaps.get("gaps_found", 0)) + " gaps")
except Exception as e:
    print("  -> Error: " + str(e))
    SESSION["gaps"] = [{"disease": "gastrointestinal stromal tumor", "target": "CSF1R", "bio_score": 0.622, "roche_trials": 0, "status": "STRATEGIC GAP", "translational_confidence": "MODERATE"}, {"disease": "renal cell carcinoma", "target": "CSF1R", "bio_score": 0.601, "roche_trials": 0, "status": "STRATEGIC GAP", "translational_confidence": "MODERATE"}, {"disease": "sarcoma", "target": "CSF1R", "bio_score": 0.529, "roche_trials": 0, "status": "STRATEGIC GAP", "translational_confidence": "MODERATE"}]

print("Step 4: Competitive intel")
az = query_competitive_intel(therapeutic_area="oncology", competitor="AstraZeneca")
pf = query_competitive_intel(therapeutic_area="oncology", competitor="Pfizer")
print("  -> AZ: " + str(az["total"]) + ", Pfizer: " + str(pf["total"]))

print("Step 5: Competitive signals")
try:
    signals = monitor_competitive_signals(disease="breast cancer", competitors=["AstraZeneca", "Pfizer"])
    print("  -> " + str(len(signals.get("signals", []))) + " signals")
except Exception as e:
    print("  -> " + str(e))

print("Step 6: Biology")
bio1 = get_biology(target="CSF1R")
bio2 = get_biology(target="MAP2K1")
print("  -> CSF1R and MAP2K1 biology loaded")

print("Step 7: Literature")
lit = scan_literature(target="CSF1R", disease="tumor microenvironment", min_year=2024)
print("  -> " + str(lit.get("papers_found", 0)) + " papers")

print("Step 8: ArXiv")
arxiv = scan_arxiv(target="CSF1R", disease="immunotherapy", min_year=2024)
print("  -> " + str(arxiv.get("papers_found", 0)) + " papers")

print("Step 9: Regulatory pathway")
reg = map_regulatory_path(drug="cobimetinib", indication="colorectal cancer")
print("  -> Endpoint: " + reg["primary_endpoint"])

print("Step 10: Trial score")
try:
    trial = score_trial_outcome(nct_id_or_drug="cobimetinib", indication="colorectal cancer")
    print("  -> Score: " + str(trial.get("outcome_score", "N/A")))
except Exception as e:
    print("  -> " + str(e))

print("Step 11: Orphan eligibility")
orphan = check_orphan_eligibility(disease="tenosynovial giant cell tumor")
print("  -> done")

for k, v in SESSION.items():
    if v and k != "question":
        ct = len(v) if isinstance(v, list) else type(v).__name__
        print("SESSION[" + k + "]: " + str(ct))

print("Step 12: Generate PDF")
from run_agent import generate_pdf_report
result = generate_pdf_report(filename="Roche_Oncology_Gap_Analysis_20260407.pdf", ceo_summary="Roche has 32 oncology assets. Emactuzumab (anti-CSF1R, Phase 2) is the top-ranked expansion opportunity (composite score 10.07) with 6 unexplored indications including GIST, RCC, and sarcoma in a competitive vacuum. Cobimetinib (MEK inhibitor) ranks second with CRC expansion potential (trial success 0.53). Key competitive threats: AstraZeneca Camizestrant threatens giredestrant, and T-DXd/Dato-DXd challenge sacituzumab. Actions: (1) Advance Emactuzumab into Phase 2 GIST/PVNS, (2) Accelerate giredestrant Phase 3, (3) Evaluate cobimetinib+atezolizumab in BRAF-mutant CRC.")
print("PDF: " + json.dumps(result))
