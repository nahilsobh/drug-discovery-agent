#!/usr/bin/env bash
# Start the Whitespace web UI on the cluster.
# Access via: ssh -L 8088:localhost:8088 sobhn@<cluster-login-node>
#             then open http://localhost:8088
set -euo pipefail
cd /home/sobhn/hk/drug-discovery-agent

# Kill any stale process on the UI port
fuser -k 8088/tcp 2>/dev/null || true
sleep 1

source /apps/rocs/2024.04/common/x86-64-v4/software/Micromamba/2.0.7-0/etc/profile.d/mamba.sh
micromamba activate /gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery

python3.11 -m pip install -q "fastapi>=0.110" "uvicorn[standard]>=0.29" "websockets>=12"

# Start persistent ona-claude for the web UI chat feature
UI_ONA_PORT=8090
if ! ss -tlnp 2>/dev/null | grep -q ":${UI_ONA_PORT} "; then
    echo "[ui] Starting ona-claude on port ${UI_ONA_PORT} for chat..."
    $HOME/.local/bin/ona-claude -p "${UI_ONA_PORT}" &
    for i in $(seq 1 20); do
        ss -tlnp 2>/dev/null | grep -q ":${UI_ONA_PORT} " && break
        sleep 1
    done
    if ss -tlnp 2>/dev/null | grep -q ":${UI_ONA_PORT} "; then
        echo "[ui] ona-claude ready on port ${UI_ONA_PORT}"
    else
        echo "[ui] WARNING: ona-claude did not start — chat feature may be unavailable"
    fi
else
    echo "[ui] ona-claude already running on port ${UI_ONA_PORT}"
fi
export UI_ONA_PORT="${UI_ONA_PORT}"

export UI_PASSWORD="${UI_PASSWORD:-whitespace2026}"
NODE=$(hostname -f 2>/dev/null || hostname)
echo "[ui] Access URL : http://${NODE}:8088"
echo "[ui] Password   : ${UI_PASSWORD}"

exec python3.11 -m uvicorn web_ui.server:app --host 0.0.0.0 --port 8088 --reload
