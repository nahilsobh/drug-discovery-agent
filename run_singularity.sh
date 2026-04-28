#!/usr/bin/env bash
# Run the drug-discovery agent inside the Singularity container.
#
# Usage:
#   bash run_singularity.sh                        # interactive agent (default query)
#   bash run_singularity.sh python3 run_agent.py   # explicit command
#   sbatch run_singularity.sh                       # SLURM batch job
#   bash run_singularity.sh python3 -m pytest tests/ -q   # run test suite
#
# Environment variables (all optional):
#   ANTHROPIC_API_KEY   — API key for Claude (or ANTHROPIC_AUTH_TOKEN for OAuth)
#   AGENT_MODEL         — override model (default: claude-opus-4-6)
#   AGENT_MAX_TURNS     — max ReAct turns (default: 20)
#   CLAWAPI_URL         — GenomeClaw API URL (default: http://127.0.0.1:8083)
#                         Set to GPU node URL when using run_genomeclaw_gpu.sh
#   LENS_API_KEY        — Lens.org API key for global patent search (optional)
#   USE_GPU             — set to "1" to request a GPU via --nv flag
#   JOB_HOME            — per-job home dir mounted as /root inside the container
#                         (isolates claude config so concurrent jobs don't collide)
#   PROXY_PORT          — port for the local Anthropic proxy (default: 9797)
#                         Set to a unique value per job when running concurrently

#SBATCH --job-name=drug-discovery-agent
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=08:00:00
#SBATCH --partition=batch_gpu
#SBATCH --qos=3h
#SBATCH --gres=gpu:1
#SBATCH --output=logs/agent_%j.log

set -euo pipefail

SIF="$HOME/singularity-images/drug-discovery-agent.sif"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SIF" ]; then
  echo "ERROR: SIF image not found at $SIF"
  echo "Rebuild with:"
  echo "  srun --ntasks=1 singularity build \$HOME/singularity-images/drug-discovery-agent.sif \\"
  echo "    \$HOME/singularity-images/drug-discovery-sandbox"
  exit 1
fi

mkdir -p "${PROJECT}/logs"

# --nv passes NVIDIA GPU drivers into the container (needed if USE_GPU=1)
GPU_FLAG=""
if [ "${USE_GPU:-0}" = "1" ]; then
  GPU_FLAG="--nv"
fi

# Resolve the claude CLI binary (follows symlink → actual ELF).
# Bind-mounted to /usr/local/bin/claude so proxy_server.py can call it.
CLAUDE_BIN="$(readlink -f "${HOME}/.local/bin/claude" 2>/dev/null || true)"

# OpenSSL 3 is needed by GenomeClaw (clawapi binary built against it).
# Rocky Linux 8 ships OpenSSL 1.1; we use the conda env's libssl.so.3.
CONDA_LIB="/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib"

# Home directory inside the container.
# For single-job use: defaults to $HOME (real home, all auth state available).
# For concurrent jobs: pass JOB_HOME only when jobs are co-located on the
# same node (GPU partition normally gives dedicated nodes, so this is rare).
CONTAINER_HOME="${JOB_HOME:-${HOME}}"
mkdir -p "${CONTAINER_HOME}"

exec singularity exec \
  --cleanenv \
  --home "${CONTAINER_HOME}:/root" \
  --pwd /app \
  ${GPU_FLAG} \
  --bind "${PROJECT}:/app" \
  --bind "${PROJECT}/knowledge_base:/app/knowledge_base" \
  ${CLAUDE_BIN:+--bind "${CLAUDE_BIN}:/usr/local/bin/claude:ro"} \
  ${CONDA_LIB:+--bind "${CONDA_LIB}/libssl.so.3:/usr/lib64/libssl.so.3:ro"} \
  ${CONDA_LIB:+--bind "${CONDA_LIB}/libcrypto.so.3:/usr/lib64/libcrypto.so.3:ro"} \
  --env PYTHONPATH=/app \
  --env PYTHONUNBUFFERED=1 \
  --env PATH="/usr/local/bin:/usr/bin:/bin" \
  --env ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  --env ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}" \
  --env AGENT_MODEL="${AGENT_MODEL:-}" \
  --env AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-}" \
  --env CLAWAPI_URL="${CLAWAPI_URL:-}" \
  --env LENS_API_KEY="${LENS_API_KEY:-}" \
  --env AUDIT_SUMMARY="${AUDIT_SUMMARY:-}" \
  --env PROXY_PORT="${PROXY_PORT:-9797}" \
  --env COMPETITOR="${COMPETITOR:-}" \
  "$SIF" \
  "${@:-python3 run_agent.py}"
