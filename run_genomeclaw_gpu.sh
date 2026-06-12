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
#SBATCH --qos=3h
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/agent_gpu_%j.log

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIF="$HOME/singularity-images/drug-discovery-agent.sif"
GENOMECLAW_BIN="${PROJECT}/genomeclaw/target/release/clawapi"
WEIGHTS_DIR="${PROJECT}/genomeclaw/weights"
JOB_ID="${SLURM_JOB_ID:-0}"
CLAWAPI_PORT=$(( 8083 + JOB_ID % 900 ))
CLAWAPI_BIND="127.0.0.1:${CLAWAPI_PORT}"

# ---------------------------------------------------------------------------
# 1. Start GenomeClaw API on this GPU node
# ---------------------------------------------------------------------------
if [ ! -f "${GENOMECLAW_BIN}" ]; then
  echo "ERROR: GenomeClaw binary not found at ${GENOMECLAW_BIN}"
  echo "Build with: cd genomeclaw && cargo build --release -p genomeclaw-api"
  exit 1
fi

CONDA_LIB="/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib"
export VK_ICD_FILENAMES="${PROJECT}/nvidia_icd.json"

echo "[gpu] Starting GenomeClaw API on ${CLAWAPI_BIND} (GPU: ${CUDA_VISIBLE_DEVICES:-auto})"
LD_LIBRARY_PATH="${CONDA_LIB}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
WGPU_BACKEND=vulkan \
CLAWAPI_WEIGHTS="${WEIGHTS_DIR}/boltz-1/models--boltz-community--boltz-1/snapshots/7c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt" \
CLAWAPI_ESM_WEIGHTS="${WEIGHTS_DIR}/esm2/models--facebook--esm2_t33_650M_UR50D/snapshots/08e4846e537177426273712802403f7ba8261b6c/model.safetensors" \
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
USE_GPU=1 \
CLAWAPI_URL="http://${CLAWAPI_BIND}" \
AGENT_MODEL="${AGENT_MODEL:-claude-opus-4-6}" \
AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-30}" \
AUDIT_SUMMARY="${AUDIT_SUMMARY:-1}" \
bash "${PROJECT}/run_singularity.sh" python3 run_agent.py "${@:-}"

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
