#!/usr/bin/env bash
# Run GenomeClaw API on a GPU compute node, then launch the discovery agent
# pointing at it via CLAWAPI_URL.
#
# Usage:
#   sbatch run_genomeclaw_gpu.sh "Find BRCA2 folding context"
#   srun --partition=batch_gpu --gres=gpu:a100:1 --ntasks=1 --time=04:00:00 \
#        bash run_genomeclaw_gpu.sh "your query"
#
# Two-node alternative (agent on CPU node, GenomeClaw on GPU node):
#   See CLAWAPI_URL note at the bottom of this script.

#SBATCH --job-name=drug-discovery-gpu
#SBATCH --partition=batch_gpu
#SBATCH --gres=gpu:a100:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=04:00:00
#SBATCH --output=logs/agent_gpu_%j.log

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIF="$HOME/singularity-images/drug-discovery-agent.sif"
GENOMECLAW_BIN="${PROJECT}/genomeclaw/target/release/clawapi"
WEIGHTS_DIR="${PROJECT}/genomeclaw/weights"
CLAWAPI_PORT=8083
CLAWAPI_BIND="127.0.0.1:${CLAWAPI_PORT}"

# ---------------------------------------------------------------------------
# 1. Start GenomeClaw API on this GPU node
# ---------------------------------------------------------------------------
if [ ! -f "${GENOMECLAW_BIN}" ]; then
  echo "ERROR: GenomeClaw binary not found at ${GENOMECLAW_BIN}"
  echo "Build with: cd genomeclaw && cargo build --release -p genomeclaw-api"
  exit 1
fi

echo "[gpu] Starting GenomeClaw API on ${CLAWAPI_BIND} (GPU: ${CUDA_VISIBLE_DEVICES:-auto})"
CLAWAPI_WEIGHTS="${WEIGHTS_DIR}/boltz-1/boltz1.safetensors" \
CLAWAPI_CONF_WEIGHTS="${WEIGHTS_DIR}/boltz-1/boltz1_conf.safetensors" \
CLAWAPI_ESM2_DIR="${WEIGHTS_DIR}/esm2" \
CLAWAPI_BIND="${CLAWAPI_BIND}" \
  "${GENOMECLAW_BIN}" &
CLAWAPI_PID=$!
trap "kill ${CLAWAPI_PID} 2>/dev/null || true" EXIT

# Wait for GenomeClaw to be healthy (up to 60 s)
echo "[gpu] Waiting for GenomeClaw health..."
for i in $(seq 1 30); do
  if curl -sf "http://${CLAWAPI_BIND}/health" >/dev/null 2>&1; then
    echo "[gpu] GenomeClaw ready after ${i}x2s"
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "ERROR: GenomeClaw did not become healthy in 60s"
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# 2. Run the agent inside Singularity, pointing at the local GenomeClaw
# ---------------------------------------------------------------------------
echo "[gpu] Launching agent (CLAWAPI_URL=http://${CLAWAPI_BIND})"
singularity exec \
  --cleanenv \
  --pwd /app \
  --bind "${PROJECT}:/app" \
  --bind "${PROJECT}/knowledge_base:/app/knowledge_base" \
  --env PYTHONPATH=/app \
  --env PYTHONUNBUFFERED=1 \
  --env ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  --env ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}" \
  --env AGENT_MODEL="${AGENT_MODEL:-}" \
  --env AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-}" \
  --env LENS_API_KEY="${LENS_API_KEY:-}" \
  --env CLAWAPI_URL="http://${CLAWAPI_BIND}" \
  "${SIF}" \
  python3 run_agent.py "${@:-}"

# GenomeClaw is stopped by the EXIT trap above.

# ---------------------------------------------------------------------------
# Two-node setup (GenomeClaw on dedicated GPU node, agent on CPU node):
#
#   1. Start GenomeClaw on the GPU node and note its hostname:
#        srun --partition=batch_gpu --gres=gpu:a100:1 --ntasks=1 \
#             bash run_genomeclaw_gpu.sh --server-only &
#        GPU_NODE=$(squeue -u $USER -h -o "%R" | head -1)
#
#   2. Run the agent on a CPU node pointing at the GPU node:
#        CLAWAPI_URL="http://${GPU_NODE}:8083" sbatch run_singularity.sh "query"
# ---------------------------------------------------------------------------
