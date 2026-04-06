#!/usr/bin/env bash
# =============================================================================
# drug-discovery-agent — Roche sHPC Linux Cluster Installer
# =============================================================================
# Usage:
#   1. SSH into the cluster
#   2. git clone https://github.com/nahilsobh/drug-discovery-agent.git
#   3. cd drug-discovery-agent
#   4. bash install_hpc.sh
#
# Requirements:
#   - HF_TOKEN env var set, or script will prompt for it
#   - Internet access to github.com, git.redclaw.dev, huggingface.co
#   - SLURM scheduler available (sbatch / srun)
# =============================================================================

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HF_REPO="nahilsobh/genomeclaw-weights"

# ── Step 0: Hugging Face token ────────────────────────────────────────────────
info "Checking Hugging Face token..."
if [[ -z "${HF_TOKEN:-}" ]]; then
    read -rsp "Enter your Hugging Face token (input hidden): " HF_TOKEN
    echo
fi
export HF_TOKEN
export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"   # support both env var names

# ── Step 1: Rust ──────────────────────────────────────────────────────────────
info "Checking Rust installation..."
if ! command -v cargo &>/dev/null; then
    info "Installing Rust in user space..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
    source "$HOME/.cargo/env"
    # Persist to shell rc
    grep -qxF 'source "$HOME/.cargo/env"' ~/.bashrc 2>/dev/null \
        || echo 'source "$HOME/.cargo/env"' >> ~/.bashrc
else
    source "$HOME/.cargo/env" 2>/dev/null || true
    info "Rust $(rustc --version) already installed."
fi

# ── Step 2: Python deps ───────────────────────────────────────────────────────
info "Installing Python dependencies..."
if command -v conda &>/dev/null && [[ -z "${CONDA_DEFAULT_ENV:-}" ]]; then
    warn "conda detected but no env active. Run: conda activate <env> then re-run this script."
    warn "Falling back to pip --user install."
fi
pip install --user -q -r "$REPO_ROOT/requirements.txt"
pip install --user -q huggingface_hub   # ensure hf cli available

# ── Step 3: Clone GenomeClaw ──────────────────────────────────────────────────
info "Cloning GenomeClaw..."
if [[ -d "$REPO_ROOT/genomeclaw/.git" ]]; then
    info "GenomeClaw already cloned, pulling latest..."
    git -C "$REPO_ROOT/genomeclaw" pull --ff-only
else
    git clone https://git.redclaw.dev/genomeclaw/genomeclaw "$REPO_ROOT/genomeclaw"
fi

# ── Step 4: Download weights from Hugging Face ────────────────────────────────
info "Downloading model weights from Hugging Face ($HF_REPO)..."
WEIGHTS_DIR="$REPO_ROOT/genomeclaw/weights"

download_weight() {
    local filename="$1"
    local dest="$2"
    mkdir -p "$(dirname "$dest")"
    if [[ -f "$dest" ]]; then
        info "  $filename already exists, skipping."
        return
    fi
    info "  Downloading $filename..."
    python3 - <<EOF
from huggingface_hub import hf_hub_download
import os, shutil
path = hf_hub_download(
    repo_id="$HF_REPO",
    filename="$filename",
    token=os.environ["HF_TOKEN"],
)
shutil.copy(path, "$dest")
print(f"  Saved to $dest")
EOF
}

download_weight "boltz1.safetensors"      "$WEIGHTS_DIR/boltz-1/boltz1.safetensors"
download_weight "boltz1_conf.safetensors" "$WEIGHTS_DIR/boltz-1/boltz1_conf.safetensors"

# ESM2 — download whole subfolder
ESM2_DIR="$WEIGHTS_DIR/esm2"
mkdir -p "$ESM2_DIR"
if [[ -z "$(ls -A "$ESM2_DIR" 2>/dev/null)" ]]; then
    info "  Downloading ESM2 weights..."
    python3 - <<EOF
from huggingface_hub import snapshot_download
import os
snapshot_download(
    repo_id="$HF_REPO",
    allow_patterns=["esm2/*"],
    local_dir="$WEIGHTS_DIR",
    token=os.environ["HF_TOKEN"],
)
print("  ESM2 weights downloaded.")
EOF
else
    info "  ESM2 weights already present, skipping."
fi

# ── Step 5: Build GenomeClaw API on compute node ──────────────────────────────
info "Building GenomeClaw API (submitting to compute node via srun)..."
srun --cpus-per-task=8 --mem=16G --time=01:00:00 \
    bash -c "source $HOME/.cargo/env && cd $REPO_ROOT/genomeclaw && cargo build --release -p genomeclaw-api"
info "Build complete: $REPO_ROOT/genomeclaw/target/release/clawapi"

# ── Step 6: Create SLURM job script for GenomeClaw API ───────────────────────
info "Creating SLURM job script..."
cat > "$REPO_ROOT/start_clawapi.slurm" << EOF
#!/bin/bash
#SBATCH --job-name=clawapi
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=24:00:00
#SBATCH --output=$REPO_ROOT/clawapi.log
#SBATCH --error=$REPO_ROOT/clawapi.err

cd $REPO_ROOT
CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/boltz1.safetensors \\
CLAWAPI_BIND=127.0.0.1:8083 \\
./genomeclaw/target/release/clawapi
EOF
chmod +x "$REPO_ROOT/start_clawapi.slurm"

# ── Step 7: Submit GenomeClaw API job ─────────────────────────────────────────
info "Submitting GenomeClaw API SLURM job..."
JOB_ID=$(sbatch --parsable "$REPO_ROOT/start_clawapi.slurm")
info "GenomeClaw API job submitted — Job ID: $JOB_ID"
info "Monitor with: squeue -j $JOB_ID"
info "Logs at:      $REPO_ROOT/clawapi.log"

# ── Step 8: Create run script ─────────────────────────────────────────────────
info "Creating run_agent wrapper..."
cat > "$REPO_ROOT/run.sh" << 'EOF'
#!/usr/bin/env bash
# Usage: bash run.sh "your query here"
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY not set. Export it before running."
    exit 1
fi

# Wait for GenomeClaw API to be ready (up to 60s)
echo "Waiting for GenomeClaw API..."
for i in $(seq 1 12); do
    if curl -sf http://127.0.0.1:8083/health &>/dev/null; then
        echo "GenomeClaw API ready."
        break
    fi
    sleep 5
done

export CLAWAPI_URL=http://127.0.0.1:8083
python3 "$REPO_ROOT/run_agent.py" "$@"
EOF
chmod +x "$REPO_ROOT/run.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN} Installation complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo ""
echo "  1. Set your Anthropic API key:"
echo "     export ANTHROPIC_API_KEY=<key from Roche IT>"
echo ""
echo "  2. Wait for GenomeClaw API job to start:"
echo "     squeue -j $JOB_ID"
echo ""
echo "  3. Run the agent:"
echo "     bash run.sh \"Find gaps in Roche neurology pipeline\""
echo ""
echo "  Logs: $REPO_ROOT/clawapi.log"
