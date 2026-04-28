#!/usr/bin/env bash
# CEO Strategic Briefing — SLURM batch script
#
# Runs the "Single Most Valuable CEO Query" through the Roche AI Factory agent
# inside a Singularity container on a GPU node.  Produces:
#   1. Full PDF report (reports/CEO_Briefing_<date>.pdf)
#   2. Structured JSON Lines audit trail (logs/audit_<session_id>.jsonl)
#   3. Console audit summary (printed at end of job log)
#
# Submit with:
#   sbatch ceo_query_slurm.sh
#
# Dry-run validation:
#   sbatch --test-only ceo_query_slurm.sh

#SBATCH --job-name=roche-ceo-briefing
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/ceo_briefing_%j.out
#SBATCH --error=/home/sobhn/hk/drug-discovery-agent/logs/ceo_briefing_%j.out
#SBATCH --time=03:00:00
#SBATCH --mem=16G
#SBATCH --cpus-per-task=4
#SBATCH --partition=batch_gpu
#SBATCH --qos=3h
#SBATCH --gres=gpu:1

set -euo pipefail

PROJECT="/home/sobhn/hk/drug-discovery-agent"
cd "$PROJECT"
mkdir -p logs

echo "============================================================"
echo "  ROCHE AI FACTORY — CEO STRATEGIC BRIEFING"
echo "  SLURM Job ID : ${SLURM_JOB_ID:-<local>}"
echo "  Node         : ${SLURMD_NODENAME:-$(hostname)}"
echo "  Started      : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "============================================================"

# ── Per-job port: avoids collisions when multiple jobs share a node ────────────
CLAWAPI_PORT=$(( 8083 + ${SLURM_JOB_ID:-0} % 900 ))
export CLAWAPI_URL="http://127.0.0.1:${CLAWAPI_PORT}"

# ── Start ona-claude OAuth proxy (Roche subscription → Claude API) ─────────────
echo "[setup] Starting ona-claude on port 8095..."
$HOME/.local/bin/ona-claude -p 8095 &
ONA_PID=$!
sleep 10
echo "[setup] ona-claude PID=${ONA_PID}"

# ── Start GenomeClaw API (Boltz-1 protein folding + ESM-2 variant effects) ─────
# clawapi is built against OpenSSL 3; Rocky Linux 8 ships 1.1.
# Point LD_LIBRARY_PATH to the conda env that has libssl.so.3.
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

# Verify GenomeClaw is healthy
if curl -sf "${CLAWAPI_URL}/health" > /dev/null; then
    echo "[setup] GenomeClaw healthy at ${CLAWAPI_URL}"
else
    echo "[setup] WARNING: GenomeClaw not reachable at ${CLAWAPI_URL} — GPU tools will be offline"
fi

# ── CEO Query ──────────────────────────────────────────────────────────────────
export QUERY="Give me a full strategic briefing: rank our portfolio, find every gap \
where competitors are moving faster than us, identify our three best first-mover \
white spaces, score our top 5 pipeline assets for trial success, flag the two biggest \
competitive threats in the next 12 months, and tell me the single highest-ROI action \
Roche should take in the next 90 days. Generate a PDF."

# Agent settings
export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset  ANTHROPIC_API_KEY
export AGENT_MODEL="claude-opus-4-6"
export AGENT_MAX_TURNS=30
export AUDIT_SUMMARY=1          # print human-readable audit table at end of run

# ── Run inside Singularity container ──────────────────────────────────────────
echo ""
echo "[agent] Launching CEO briefing query (MAX_TURNS=${AGENT_MAX_TURNS})..."
echo ""

USE_GPU=1 \
CLAWAPI_URL="${CLAWAPI_URL}" \
ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN}" \
AGENT_MODEL="${AGENT_MODEL}" \
AGENT_MAX_TURNS="${AGENT_MAX_TURNS}" \
AUDIT_SUMMARY="${AUDIT_SUMMARY}" \
bash "${PROJECT}/run_singularity.sh" python3 run_agent.py "$QUERY"

EXIT_CODE=$?

# ── Cleanup ────────────────────────────────────────────────────────────────────
echo ""
echo "[cleanup] Stopping GenomeClaw (PID=${CLAW_PID}) and ona-claude (PID=${ONA_PID})..."
kill "$CLAW_PID" 2>/dev/null || true
kill "$ONA_PID"  2>/dev/null || true

# Move newest PDF to reports/ with canonical name
mkdir -p "${PROJECT}/reports"
LATEST_PDF=$(ls -t "${PROJECT}"/*.pdf 2>/dev/null \
  | grep -iv "Google\|AI agents\|KRAS\|Atezolizumab\|Oncology_Gap\|oncology_gap\|strategic_discovery\|Pipeline\|atezolizumab" \
  | head -1 || true)
if [ -n "$LATEST_PDF" ]; then
    DEST="${PROJECT}/reports/CEO_Briefing_$(date -u '+%Y%m%d').pdf"
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
echo "  Finished : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  PDF      : ${PROJECT}/reports/CEO_Briefing_$(date -u '+%Y%m%d').pdf"
echo "  Audit    : ${PROJECT}/logs/audit_*.jsonl  (latest file)"
echo "============================================================"

exit "$EXIT_CODE"
