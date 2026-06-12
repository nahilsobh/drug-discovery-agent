#!/usr/bin/env bash
# Repurposing Analysis — per-company SLURM batch job
#
# Identifies highest-probability repurposing candidates from each competitor's
# pipeline: find_repurposing_candidates → predict_admet → score_trial_outcome
# → check_orphan_eligibility → ranked PDF per company.
#
# Each job runs in its own Singularity container with isolated ports.
#
# DO NOT submit directly — use:
#   bash submit_repurposing_sweep.sh
#
# Required env vars (set by launcher):
#   COMPETITOR   — company name, e.g. "AstraZeneca"
#   COMPANY_SLUG — filesystem-safe slug, e.g. "astrazeneca"

#SBATCH --job-name=repurpose-competitive
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/repurposing_%x_%j.out
#SBATCH --error=/home/sobhn/hk/drug-discovery-agent/logs/repurposing_%x_%j.out
#SBATCH --time=03:00:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=4
#SBATCH --partition=batch_gpu
#SBATCH --qos=3h
#SBATCH --gres=gpu:1

set -euo pipefail

COMPETITOR="${COMPETITOR:?COMPETITOR env var required}"
COMPANY_SLUG="${COMPANY_SLUG:?COMPANY_SLUG env var required}"

PROJECT="/home/sobhn/hk/drug-discovery-agent"
cd "$PROJECT"
mkdir -p logs reports

# ── Per-job isolated ports ─────────────────────────────────────────────────────
JOB_ID="${SLURM_JOB_ID:-$$}"
ONA_PORT=$(( 8095 + JOB_ID % 900 ))
CLAWAPI_PORT=$(( 8083 + JOB_ID % 900 ))
PROXY_PORT=$(( 9797 + JOB_ID % 200 ))
export CLAWAPI_URL="http://127.0.0.1:${CLAWAPI_PORT}"

echo "============================================================"
echo "  REDCLAW AI FACTORY — REPURPOSING ANALYSIS"
echo "  Competitor   : ${COMPETITOR}"
echo "  SLURM Job ID : ${JOB_ID}"
echo "  Node         : ${SLURMD_NODENAME:-$(hostname)}"
echo "  ONA port     : ${ONA_PORT}"
echo "  CLAW port    : ${CLAWAPI_PORT}"
echo "  Proxy port   : ${PROXY_PORT}"
echo "  Started      : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "============================================================"

# ── Start ona-claude OAuth proxy ──────────────────────────────────────────────
echo "[setup] Starting ona-claude on port ${ONA_PORT}..."
$HOME/.local/bin/ona-claude -p "${ONA_PORT}" &
ONA_PID=$!

# Wait until ona-claude is accepting connections (up to 60s)
echo "[setup] Waiting for ona-claude to be ready..."
ONA_READY=0
for i in $(seq 1 30); do
    if ss -tlnp 2>/dev/null | grep -q ":${ONA_PORT} "; then
        echo "[setup] ona-claude ready after ${i}×2s (PID=${ONA_PID}, port=${ONA_PORT})"
        ONA_READY=1
        break
    fi
    sleep 2
done
if [ "${ONA_READY}" -eq 0 ]; then
    echo "[ERROR] ona-claude did not start within 60s — aborting"
    kill "$ONA_PID" 2>/dev/null || true
    exit 1
fi

export ANTHROPIC_BASE_URL="http://127.0.0.1:${ONA_PORT}"
echo "[setup] ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL}"

# ── Start GenomeClaw API ───────────────────────────────────────────────────────
echo "[setup] Starting GenomeClaw API on port ${CLAWAPI_PORT}..."
CONDA_LIB="/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib"
export VK_ICD_FILENAMES="${PROJECT}/nvidia_icd.json"
LD_LIBRARY_PATH="${CONDA_LIB}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
WGPU_BACKEND=vulkan \
CLAWAPI_WEIGHTS="${PROJECT}/genomeclaw/weights/boltz-1/models--boltz-community--boltz-1/snapshots/7c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt" \
CLAWAPI_ESM_WEIGHTS="${PROJECT}/genomeclaw/weights/esm2/models--facebook--esm2_t33_650M_UR50D/snapshots/08e4846e537177426273712802403f7ba8261b6c/model.safetensors" \
CLAWAPI_BIND="127.0.0.1:${CLAWAPI_PORT}" \
"${PROJECT}/genomeclaw/target/release/clawapi" &
CLAW_PID=$!
sleep 5

if curl -sf "${CLAWAPI_URL}/health" > /dev/null; then
    echo "[setup] GenomeClaw healthy at ${CLAWAPI_URL}"
else
    echo "[setup] WARNING: GenomeClaw not reachable — ADMET/variant tools offline"
fi

# ── Repurposing query ──────────────────────────────────────────────────────────
QUERY="For ${COMPETITOR}, identify the highest-probability drugs that can be \
repurposed into new indications. Follow this exact workflow: \
(1) recall_longterm_memory to check prior repurposing hits for ${COMPETITOR} assets, \
(2) find_repurposing_candidates for the top ${COMPETITOR} pipeline targets across oncology, \
immunology, and neurology, \
(3) predict_admet on every candidate — advance TIER-1 only, \
(4) score_trial_outcome for each TIER-1 candidate in its repurposed indication, \
(5) check_orphan_eligibility for any rare-disease repurposing opportunities, \
(6) check_competitor_trials to confirm the repurposed indication is not already crowded, \
(7) rank all candidates by composite repurposing probability score \
(ADMET tier × trial success score × competitive vacuum × orphan bonus), \
(8) save top findings to cache, \
(9) generate_pdf_report with a CEO-ready ranked repurposing table. \
Focus on identifying the single highest-ROI repurposing move ${COMPETITOR} could make \
in the next 18 months."

export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset  ANTHROPIC_API_KEY
export AGENT_MODEL="claude-opus-4-6"
export AGENT_MAX_TURNS=45
export AUDIT_SUMMARY=1
export PROXY_PORT

echo ""
echo "[agent] Repurposing analysis: ${COMPETITOR} (MAX_TURNS=${AGENT_MAX_TURNS})..."
echo ""

USE_GPU=1 \
CLAWAPI_URL="${CLAWAPI_URL}" \
PROXY_PORT="${PROXY_PORT}" \
ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN}" \
AGENT_MODEL="${AGENT_MODEL}" \
AGENT_MAX_TURNS="${AGENT_MAX_TURNS}" \
AUDIT_SUMMARY="${AUDIT_SUMMARY}" \
COMPETITOR="${COMPETITOR}" \
bash "${PROJECT}/run_singularity.sh" python3 run_agent.py "$QUERY"

EXIT_CODE=$?

# ── Cleanup ────────────────────────────────────────────────────────────────────
kill "$CLAW_PID" 2>/dev/null || true
kill "$ONA_PID"  2>/dev/null || true

# Move newest PDF to reports/ with canonical name
LATEST_PDF=$(ls -t "${PROJECT}"/*.pdf 2>/dev/null \
  | grep -iv "Google\|AI agents\|KRAS\|Atezolizumab\|Oncology_Gap\|oncology_gap\|strategic_discovery\|Pipeline\|atezolizumab\|CEO_of\|RedClaw_AI_Factory_Report" \
  | head -1 || true)
if [ -n "$LATEST_PDF" ]; then
    DEST="${PROJECT}/reports/Repurposing_${COMPANY_SLUG}_$(date -u '+%Y%m%d').pdf"
    mv "$LATEST_PDF" "$DEST"
    echo "[output] PDF → ${DEST}"
    echo "[result] SUCCESS — PDF generated: ${DEST}"
else
    echo "[result] STALLED — no PDF generated (proxy stall or tool-call failure)"
    echo "[result] Check audit log: ${PROJECT}/logs/audit_*.jsonl"
    EXIT_CODE=1
fi

echo ""
echo "============================================================"
echo "  JOB COMPLETE — exit code ${EXIT_CODE}"
echo "  Competitor : ${COMPETITOR}"
echo "  Finished   : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  PDF        : ${PROJECT}/reports/Repurposing_${COMPANY_SLUG}_*.pdf"
echo "  Audit      : ${PROJECT}/logs/audit_*.jsonl (latest)"
echo "============================================================"

exit "$EXIT_CODE"
