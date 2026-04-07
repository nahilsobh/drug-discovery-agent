#!/bin/bash
#SBATCH --job-name=drug-discovery
#SBATCH --output=logs/discovery_%j.out
#SBATCH --error=logs/discovery_%j.err
#SBATCH --time=02:00:00
#SBATCH --mem=32G
#SBATCH --cpus-per-task=8
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1

# Activate environment
conda activate drug-discovery

# Set env vars
export ANTHROPIC_AUTH_TOKEN=ona-proxy
export ANTHROPIC_BASE_URL=http://127.0.0.1:8095

# Start ona-claude
$HOME/.local/bin/ona-claude -p 8095 &
sleep 10

# Start GenomeClaw API
cd ~/hk/drug-discovery-agent
CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/models--boltz-community--boltz-1/snapshots/c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt \
CLAWAPI_BIND=127.0.0.1:8083 \
./genomeclaw/target/release/clawapi &
sleep 10

# Run the agent
python3 run_agent.py "$QUERY"
