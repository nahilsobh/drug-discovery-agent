#!/usr/bin/env bash
# Competitive CEO Briefing — per-company SLURM batch job
#
# Each job runs in its own Singularity container instance with fully
# isolated ports and a per-job home directory so concurrent jobs on
# the same node never collide.
#
# Port assignments (all derived from SLURM_JOB_ID to guarantee uniqueness):
#   ONA_PORT    = 8095 + (JOB_ID % 900)  — ona-claude OAuth proxy
#   CLAWAPI_PORT= 8083 + (JOB_ID % 900)  — GenomeClaw Rust API
#   PROXY_PORT  = 9797 + (JOB_ID % 200)  — Python Anthropic proxy (inside container)
#
# DO NOT submit directly — use:
#   bash submit_competitive_sweep.sh
#
# Required env vars (set by launcher):
#   COMPETITOR   — company name, e.g. "AstraZeneca"
#   COMPANY_SLUG — filesystem-safe slug, e.g. "astrazeneca"

#SBATCH --job-name=roche-vs-%x
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/competitive_%x_%j.out
#SBATCH --error=/home/sobhn/hk/drug-discovery-agent/logs/competitive_%x_%j.out
#SBATCH --time=03:00:00
#SBATCH --mem=16G
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

# ── Per-job isolated ports (no collisions on shared nodes) ────────────────────
JOB_ID="${SLURM_JOB_ID:-$$}"
ONA_PORT=$(( 8095 + JOB_ID % 900 ))
CLAWAPI_PORT=$(( 8083 + JOB_ID % 900 ))
PROXY_PORT=$(( 9797 + JOB_ID % 200 ))
export CLAWAPI_URL="http://127.0.0.1:${CLAWAPI_PORT}"

echo "============================================================"
echo "  ROCHE AI FACTORY — COMPETITIVE BRIEFING"
echo "  Competitor   : ${COMPETITOR}"
echo "  SLURM Job ID : ${JOB_ID}"
echo "  Node         : ${SLURMD_NODENAME:-$(hostname)}"
echo "  ONA port     : ${ONA_PORT}"
echo "  CLAW port    : ${CLAWAPI_PORT}"
echo "  Proxy port   : ${PROXY_PORT}"
echo "  Started      : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "============================================================"

# ── Start ona-claude OAuth proxy ─────────────────────────────────────────────
# Runs under the real home so authentication (SSH keys, cached tokens) works.
# GPU partition gives each job a dedicated node, so port 8095 won't conflict.
# ONA_PORT is still unique per job in case of co-location.
echo "[setup] Starting ona-claude on port ${ONA_PORT}..."
$HOME/.local/bin/ona-claude -p "${ONA_PORT}" &
ONA_PID=$!
sleep 10
echo "[setup] ona-claude PID=${ONA_PID} on port ${ONA_PORT}"

# ── Start GenomeClaw with OpenSSL 3 from conda env ────────────────────────────
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
    echo "[setup] WARNING: GenomeClaw not reachable — GPU tools will be offline"
fi

# ── Per-competitor CEO query ───────────────────────────────────────────────────
QUERY="Give me a full strategic briefing focused on ${COMPETITOR}: \
rank our portfolio against ${COMPETITOR}, find every gap where ${COMPETITOR} is \
moving faster than us, identify our three best first-mover white spaces versus \
${COMPETITOR}, score our top 5 pipeline assets for trial success in areas where \
${COMPETITOR} competes, flag the two biggest competitive threats from ${COMPETITOR} \
in the next 12 months, and tell me the single highest-ROI action Roche should take \
in the next 90 days to stay ahead of ${COMPETITOR}. Generate a PDF."

export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset  ANTHROPIC_API_KEY
export AGENT_MODEL="claude-opus-4-6"
export AGENT_MAX_TURNS=30
export AUDIT_SUMMARY=1
export PROXY_PORT

echo ""
echo "[agent] Launching briefing: Roche vs ${COMPETITOR} (ports: ona=${ONA_PORT} claw=${CLAWAPI_PORT} proxy=${PROXY_PORT})..."
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

# Move the newest PDF in the project root to reports/ with a canonical name.
# The agent generates PDFs with varied names — grab the most recently modified.
LATEST_PDF=$(ls -t "${PROJECT}"/*.pdf 2>/dev/null | grep -v "reports/" | grep -iv "Google\|AI agents\|KRAS\|Atezolizumab\|Oncology_Gap\|oncology_gap\|strategic_discovery\|Pipeline\|atezolizumab" | head -1 || true)
if [ -n "$LATEST_PDF" ]; then
    DEST="${PROJECT}/reports/Roche_vs_${COMPANY_SLUG}_$(date -u '+%Y%m%d').pdf"
    mv "$LATEST_PDF" "$DEST"
    echo "[output] PDF → ${DEST}"
fi

echo ""
echo "============================================================"
echo "  JOB COMPLETE — exit code ${EXIT_CODE}"
echo "  Competitor : ${COMPETITOR}"
echo "  Finished   : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  PDF        : ${PROJECT}/reports/Roche_vs_${COMPANY_SLUG}_*.pdf"
echo "  Audit      : ${PROJECT}/logs/audit_*.jsonl (latest)"
echo "============================================================"

exit "$EXIT_CODE"
