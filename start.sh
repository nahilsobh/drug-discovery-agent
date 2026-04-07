#!/bin/bash
cd ~/hk/drug-discovery-agent

# Activate conda environment
eval "$(micromamba shell hook --shell bash)"
micromamba activate drug-discovery

# Set environment variables
export LD_LIBRARY_PATH=/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib:$LD_LIBRARY_PATH
export ANTHROPIC_AUTH_TOKEN=ona-proxy
unset ANTHROPIC_API_KEY

# Start ona-claude only if not already running
if ! pgrep -u sobhn -f "ona-claude" > /dev/null; then
    echo "Starting ona-claude..."
    $HOME/.local/bin/ona-claude -p 8095 &
    sleep 10
else
    echo "ona-claude already running, skipping..."
fi

# Start GenomeClaw API only if not already running
if ! pgrep -u sobhn -f "clawapi" > /dev/null; then
    echo "Starting GenomeClaw API..."
    CLAWAPI_WEIGHTS=/home/sobhn/hk/drug-discovery-agent/genomeclaw/weights/boltz-1/models--boltz-community--boltz-1/snapshots/7c1d83b779e4c65ecc37dfdf0c6b2788076f31e1/boltz1.ckpt \
    CLAWAPI_BIND=127.0.0.1:8083 \
    ./genomeclaw/target/release/clawapi &
    sleep 5
else
    echo "GenomeClaw API already running, skipping..."
fi

# Verify API
curl -s http://127.0.0.1:8083/health

# Run agent
python3 run_agent.py "$1"
