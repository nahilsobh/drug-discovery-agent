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
#   USE_GPU             — set to "1" to request a GPU via --nv flag (for future
#                         GPU-native Python tools; GenomeClaw uses run_genomeclaw_gpu.sh)

#SBATCH --job-name=drug-discovery-agent
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=08:00:00
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

exec singularity exec \
  --cleanenv \
  --pwd /app \
  ${GPU_FLAG} \
  --bind "${PROJECT}:/app" \
  --bind "${PROJECT}/knowledge_base:/app/knowledge_base" \
  --env PYTHONPATH=/app \
  --env PYTHONUNBUFFERED=1 \
  --env ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  --env ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}" \
  --env AGENT_MODEL="${AGENT_MODEL:-}" \
  --env AGENT_MAX_TURNS="${AGENT_MAX_TURNS:-}" \
  --env CLAWAPI_URL="${CLAWAPI_URL:-}" \
  --env LENS_API_KEY="${LENS_API_KEY:-}" \
  "$SIF" \
  "${@:-python3 run_agent.py}"
