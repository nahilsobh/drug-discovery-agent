#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Roche AI Factory — Full-suite cluster job
# Runs 3 queries that together exercise all 30 agent tools.
#
#   Query 1 — Pipeline gap + portfolio sweep (16 tools)
#   Query 2 — KRAS target deep dive: druggability, hits, IP (11 new tools)
#   Query 3 — Repurposing, combinations, adverse events (3 new tools)
#
# Submit:  sbatch run_full_suite.sh
# Monitor: squeue -u $USER
#          tail -f logs/full_suite_<jobid>.out
# ─────────────────────────────────────────────────────────────────────────────
#SBATCH --job-name=drug-discovery-full
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/full_suite_%j.out
#SBATCH --error=/home/sobhn/hk/drug-discovery-agent/logs/full_suite_%j.out
#SBATCH --time=09:00:00
#SBATCH --mem=24G
#SBATCH --cpus-per-task=4
#SBATCH --partition=batch_gpu
#SBATCH --qos=1d
#SBATCH --gres=gpu:1

set -euo pipefail

# ── Environment ───────────────────────────────────────────────────────────────
export MAMBA_ROOT_PREFIX=/apps/rocs/2024.04/common/x86-64-v4/software/Micromamba/2.0.7-0
eval "$($MAMBA_ROOT_PREFIX/bin/micromamba shell hook --shell bash)"
micromamba activate /gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery

export LD_LIBRARY_PATH=/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib:${LD_LIBRARY_PATH:-}
export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset ANTHROPIC_API_KEY

# Per-job port — avoids collisions when multiple jobs share a node
CLAWAPI_PORT=$(( 8083 + SLURM_JOB_ID % 900 ))
export CLAWAPI_URL="http://127.0.0.1:${CLAWAPI_PORT}"

AGENT_DIR=/home/sobhn/hk/drug-discovery-agent
LOG_DIR=${AGENT_DIR}/logs

# ── Start services ────────────────────────────────────────────────────────────
echo "[$(date)] Starting ona-claude on port 8095"
$HOME/.local/bin/ona-claude -p 8095 &
ONA_PID=$!
sleep 10

export VK_ICD_FILENAMES=${AGENT_DIR}/nvidia_icd.json

echo "[$(date)] Starting GenomeClaw API on port ${CLAWAPI_PORT}"
WGPU_BACKEND=vulkan \
CLAWAPI_WEIGHTS=${AGENT_DIR}/genomeclaw/weights/boltz-1/models--boltz-community--boltz-1/snapshots/7c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt \
CLAWAPI_ESM_WEIGHTS=${AGENT_DIR}/genomeclaw/weights/esm2/models--facebook--esm2_t33_650M_UR50D/snapshots/08e4846e537177426273712802403f7ba8261b6c/model.safetensors \
CLAWAPI_BIND=127.0.0.1:${CLAWAPI_PORT} \
${AGENT_DIR}/genomeclaw/target/release/clawapi &
CLAW_PID=$!
sleep 5

echo "[$(date)] GenomeClaw health: $(curl -s ${CLAWAPI_URL}/health)"

cd ${AGENT_DIR}

# ── Helper ────────────────────────────────────────────────────────────────────
run_query() {
    local idx=$1
    local query=$2
    local label=$3
    echo ""
    echo "════════════════════════════════════════════════════════════════════"
    echo "  QUERY ${idx}/3 — ${label}"
    echo "════════════════════════════════════════════════════════════════════"
    echo "[$(date)] Starting query ${idx}"
    python3 run_agent.py "${query}"
    local rc=$?
    echo "[$(date)] Query ${idx} finished (exit code ${rc})"
    return $rc
}

# ── Query 1: Pipeline gap sweep ───────────────────────────────────────────────
# Tools exercised:
#   find_gaps, list_pipeline_assets, rank_portfolio, query_competitive_intel,
#   monitor_competitive_signals, check_competitor_trials, search_roche_trials,
#   get_biology, scan_literature, bulk_scan_literature, scan_arxiv,
#   map_regulatory_path, check_orphan_eligibility, score_trial_outcome,
#   save_to_cache, generate_pdf_report
run_query 1 \
  "Perform a full Roche oncology pipeline gap analysis: list pipeline assets and rank the portfolio, identify targets with strong biology but no active Roche trial, run a competitive landscape across AstraZeneca and Pfizer, scan recent literature and arXiv for the top gap targets, map the regulatory pathway for the highest-confidence gap, check orphan drug eligibility, score trial success probability, save all findings to cache, and generate a PDF report." \
  "Pipeline gap sweep (16 tools)"

# ── Query 2: KRAS deep dive ───────────────────────────────────────────────────
# Tools exercised:
#   recall_longterm_memory, get_protein_structure_context, fold_target,
#   score_variant_effect (G12C), query_genomeclaw_databases,
#   find_hits, predict_admet, find_shared_targets (NSCLC + colorectal cancer),
#   find_phenocopiers, search_patents, get_patent_landscape, generate_pdf_report
run_query 2 \
  "Deep dive on KRAS in NSCLC: recall any prior hit or ADMET results from memory, assess druggability via protein structure context and fold the target with Boltz-1, score the G12C resistance variant with ESM-2, query gnomAD/ChemBL/BindingDB/ClinVar/STRING/cBioPortal databases, find KRAS inhibitor hits and run mandatory ADMET on the top candidates, find shared targets between NSCLC and colorectal cancer, find phenocopiers of KRAS, assess patent landscape and search for recent KRAS patents, then generate a PDF report." \
  "KRAS target deep dive (11 new tools)"

# ── Query 3: Repurposing, combinations, safety ────────────────────────────────
# Tools exercised:
#   find_repurposing_candidates, find_combinations, query_adverse_events,
#   generate_pdf_report
run_query 3 \
  "For atezolizumab: find repurposing candidates (approved drugs that could skip Phase I in a new indication), identify Roche drug combination partners for TNBC, and profile the adverse event landscape from FDA FAERS for checkpoint inhibitors. Run mandatory ADMET on any repurposing hit before advancing. Generate a PDF report." \
  "Repurposing, combinations, adverse events (3 new tools)"

# ── Cleanup ───────────────────────────────────────────────────────────────────
echo ""
echo "[$(date)] All 3 queries complete. Shutting down services."
kill ${ONA_PID} ${CLAW_PID} 2>/dev/null || true
echo "[$(date)] Job finished."
