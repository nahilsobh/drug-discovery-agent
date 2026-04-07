#!/bin/bash
#SBATCH --job-name=drug-discovery
#SBATCH --output=/home/sobhn/hk/drug-discovery-agent/logs/discovery_%j.out
#SBATCH --error=/home/sobhn/hk/drug-discovery-agent/logs/discovery_%j.out
#SBATCH --time=02:00:00
#SBATCH --mem=16G
#SBATCH --cpus-per-task=4
#SBATCH --partition=batch_gpu
#SBATCH --qos=3h
#SBATCH --gres=gpu:1

# Find and initialize micromamba
export MAMBA_ROOT_PREFIX=/apps/rocs/2024.04/common/x86-64-v4/software/Micromamba/2.0.7-0
eval "$($MAMBA_ROOT_PREFIX/bin/micromamba shell hook --shell bash)"
micromamba activate /gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery

# Set environment variables
export LD_LIBRARY_PATH=/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib:$LD_LIBRARY_PATH
export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset ANTHROPIC_API_KEY

# Per-job ports derived from job ID — avoids collisions when multiple jobs share a node
CLAWAPI_PORT=$(( 8083 + SLURM_JOB_ID % 900 ))
export CLAWAPI_URL="http://127.0.0.1:${CLAWAPI_PORT}"

# Start ona-claude
$HOME/.local/bin/ona-claude -p 8095 &
sleep 10

# Expose NVIDIA Vulkan ICD (not deployed by default on this cluster)
export VK_ICD_FILENAMES=/home/sobhn/hk/drug-discovery-agent/nvidia_icd.json

# Start GenomeClaw API
WGPU_BACKEND=vulkan \
CLAWAPI_WEIGHTS=/home/sobhn/hk/drug-discovery-agent/genomeclaw/weights/boltz-1/models--boltz-community--boltz-1/snapshots/7c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt \
CLAWAPI_ESM_WEIGHTS=/home/sobhn/hk/drug-discovery-agent/genomeclaw/weights/esm2/models--facebook--esm2_t33_650M_UR50D/snapshots/08e4846e537177426273712802403f7ba8261b6c/model.safetensors \
CLAWAPI_BIND=127.0.0.1:${CLAWAPI_PORT} \
/home/sobhn/hk/drug-discovery-agent/genomeclaw/target/release/clawapi &
sleep 5

# Verify API
curl -s ${CLAWAPI_URL}/health

# Run agent
cd /home/sobhn/hk/drug-discovery-agent
python3 run_agent.py "$QUERY"
