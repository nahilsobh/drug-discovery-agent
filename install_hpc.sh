#!/usr/bin/env bash
# =============================================================================
# drug-discovery-agent — RedClaw sHPC Linux Cluster Installer
# =============================================================================
# Usage:
#   1. SSH into the cluster
#   2. git clone https://github.com/nahilsobh/drug-discovery-agent.git
#   3. cd drug-discovery-agent
#   4. bash install_hpc.sh
#
# Weights are downloaded from public Hugging Face repos — no token required.
# Set HF_TOKEN if you hit rate limits.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Step 1: Rust ──────────────────────────────────────────────────────────────
info "Checking Rust..."
if ! command -v cargo &>/dev/null; then
    info "Installing Rust in user space..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
    source "$HOME/.cargo/env"
    grep -qxF 'source "$HOME/.cargo/env"' ~/.bashrc 2>/dev/null \
        || echo 'source "$HOME/.cargo/env"' >> ~/.bashrc
else
    source "$HOME/.cargo/env" 2>/dev/null || true
    info "Rust $(rustc --version) already installed."
fi

# ── Step 2: Python deps ───────────────────────────────────────────────────────
info "Installing Python dependencies..."
pip install --user -q -r "$REPO_ROOT/requirements.txt"
pip install --user -q huggingface_hub torch safetensors

# ── Step 3: Clone GenomeClaw ──────────────────────────────────────────────────
info "Cloning GenomeClaw from git.redclaw.dev..."
if [[ -d "$REPO_ROOT/genomeclaw/.git" ]]; then
    info "GenomeClaw already cloned, pulling latest..."
    git -C "$REPO_ROOT/genomeclaw" pull --ff-only
else
    git clone https://git.redclaw.dev/genomeclaw/genomeclaw "$REPO_ROOT/genomeclaw"
fi

# ── Step 4: Download weights ──────────────────────────────────────────────────
info "Downloading model weights from Hugging Face (public repos)..."
WEIGHTS_DIR="$REPO_ROOT/genomeclaw/weights"
mkdir -p "$WEIGHTS_DIR/boltz-1" "$WEIGHTS_DIR/esm2"

download_hf_file() {
    local repo="$1"
    local filename="$2"
    local dest="$3"
    if [[ -f "$dest" ]]; then
        info "  $(basename "$dest") already exists, skipping."
        return
    fi
    info "  Downloading $filename from $repo..."
    python3 - <<EOF
from huggingface_hub import hf_hub_download
import os, shutil
token = os.environ.get("HF_TOKEN", None)
path = hf_hub_download(repo_id="$repo", filename="$filename", token=token)
shutil.copy(path, "$dest")
print(f"  Saved to $dest")
EOF
}

# Boltz-1 checkpoints
download_hf_file "boltz-community/boltz-1" "boltz1.ckpt"      "$WEIGHTS_DIR/boltz-1/boltz1.ckpt"
download_hf_file "boltz-community/boltz-1" "boltz1_conf.ckpt" "$WEIGHTS_DIR/boltz-1/boltz1_conf.ckpt"

# ESM2 650M
download_hf_file "facebook/esm2_t33_650M_UR50D" "model.safetensors" "$WEIGHTS_DIR/esm2/esm2_t33_650m.safetensors"

# ── Step 5: Convert Boltz-1 .ckpt → .safetensors ─────────────────────────────
info "Converting Boltz-1 weights to SafeTensors format..."

convert_weight() {
    local input="$1"
    local output="$2"
    if [[ -f "$output" ]]; then
        info "  $(basename "$output") already converted, skipping."
        return
    fi
    info "  Converting $(basename "$input")..."
    python3 "$REPO_ROOT/genomeclaw/scripts/convert_weights.py" \
        --input "$input" \
        --output "$output"
    info "  Saved to $output"
}

convert_weight "$WEIGHTS_DIR/boltz-1/boltz1.ckpt"      "$WEIGHTS_DIR/boltz-1/boltz1.safetensors"
convert_weight "$WEIGHTS_DIR/boltz-1/boltz1_conf.ckpt" "$WEIGHTS_DIR/boltz-1/boltz1_conf.safetensors"

# ── Step 6: Build GenomeClaw API on compute node ──────────────────────────────
info "Building GenomeClaw API via srun (compute node)..."
srun --cpus-per-task=8 --mem=16G --time=01:00:00 \
    bash -c "source $HOME/.cargo/env && cd $REPO_ROOT/genomeclaw && cargo build --release -p genomeclaw-api"
info "Build complete: $REPO_ROOT/genomeclaw/target/release/clawapi"

# ── Step 7: Create SLURM job script ──────────────────────────────────────────
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

# ── Step 8: Submit GenomeClaw API job ─────────────────────────────────────────
info "Submitting GenomeClaw API SLURM job..."
JOB_ID=$(sbatch --parsable "$REPO_ROOT/start_clawapi.slurm")
info "GenomeClaw API job submitted — Job ID: $JOB_ID"

# ── Step 9: Create run wrapper ────────────────────────────────────────────────
info "Creating run.sh wrapper..."
cat > "$REPO_ROOT/run.sh" << 'EOF'
#!/usr/bin/env bash
# Usage: bash run.sh "your query here"
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -z "${ANTHROPIC_API_KEY:-}" ]] && { echo "ERROR: ANTHROPIC_API_KEY not set."; exit 1; }

echo "Waiting for GenomeClaw API..."
for i in $(seq 1 12); do
    curl -sf http://127.0.0.1:8083/health &>/dev/null && echo "GenomeClaw API ready." && break
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
echo "     export ANTHROPIC_API_KEY=<key from RedClaw IT>"
echo ""
echo "  2. Check GenomeClaw API job:"
echo "     squeue -j $JOB_ID"
echo ""
echo "  3. Run the agent:"
echo "     bash run.sh \"Find gaps in RedClaw neurology pipeline\""
echo ""
echo "  Logs: $REPO_ROOT/clawapi.log"
