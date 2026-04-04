#!/usr/bin/env bash
set -euo pipefail

# Run from the root of the hk repo:  bash setup.sh

echo "==> Checking prerequisites..."
command -v python3 >/dev/null || { echo "ERROR: python3 required"; exit 1; }
command -v cargo  >/dev/null || { echo "ERROR: Rust not found. Install at https://rustup.rs"; exit 1; }
command -v git    >/dev/null || { echo "ERROR: git required"; exit 1; }

echo "==> Installing pyyaml (needed to parse repos.yaml)..."
pip3 install pyyaml -q

echo "==> Cloning repos..."
python3 - <<'EOF'
import subprocess, yaml, os

cfg = yaml.safe_load(open("repos.yaml"))
for r in cfg["repos"]:
    path = r["path"]
    if not os.path.exists(path):
        print(f"  Cloning {r['url']} -> {path}")
        subprocess.run(["git", "clone", r["url"], path], check=True)
    else:
        print(f"  {path} already exists, skipping clone")
EOF

echo "==> Installing Python deps..."
pip3 install -r requirements.txt -q

echo "==> Downloading weights (requires huggingface-cli + HF login)..."
if ! command -v huggingface-cli >/dev/null; then
    echo "  huggingface-cli not found, installing..."
    pip3 install huggingface_hub -q
fi

python3 - <<'EOF'
import subprocess, yaml, os

cfg = yaml.safe_load(open("repos.yaml"))
for w in cfg.get("weights", []):
    dest = w["dest"]
    is_dir = dest.endswith("/")
    if is_dir:
        os.makedirs(dest, exist_ok=True)
        already = bool(os.listdir(dest))
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        already = os.path.exists(dest)

    if already:
        print(f"  {dest} already exists, skipping")
        continue

    src = w["source"].replace("hf://", "")
    parts = src.split("/")
    org_repo = "/".join(parts[:2])
    filename = "/".join(parts[2:]) if len(parts) > 2 else None
    local_dir = dest if is_dir else os.path.dirname(dest)

    cmd = ["huggingface-cli", "download", org_repo, "--local-dir", local_dir]
    if filename:
        cmd += [filename]
    print(f"  Downloading {w['source']} ({w['size_gb']} GB)...")
    subprocess.run(cmd, check=True)
EOF

echo "==> Building GenomeClaw API..."
(cd genomeclaw && cargo build --release -p genomeclaw-api)

echo "==> Copying Claude config..."
MEMORY_DEST="$HOME/.claude/projects/-Users-$(whoami)-hk/memory"
mkdir -p "$HOME/.claude" "$MEMORY_DEST"
cp claude-config/settings.json       "$HOME/.claude/settings.json"
cp claude-config/settings.local.json "$HOME/.claude/settings.local.json"
cp claude-config/memory/*.md         "$MEMORY_DEST/"

echo ""
echo "============================================="
echo " Setup complete. Remaining manual steps:"
echo "============================================="
echo ""
echo "  1. Authenticate Claude Code (one-time OAuth login):"
echo "     claude"
echo ""
echo "  2. Start GenomeClaw API:"
echo "     CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/boltz1.safetensors \\"
echo "     CLAWAPI_BIND=127.0.0.1:8083 \\"
echo "     ./genomeclaw/target/release/clawapi &"
echo ""
echo "  3. Test the agent:"
echo "     python3 run_agent.py 'list roche oncology trials'"
echo ""
