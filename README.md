# drug-discovery-agent

A strategic drug discovery agent built on a 20-tool JSON ReAct loop, integrating live biomedical databases with the GenomeClaw Rust-native platform for protein structure prediction, ADMET analysis, and variant effect scoring.

## Setup

```bash
git clone https://github.com/nahilsobh/drug-discovery-agent.git && cd drug-discovery-agent
huggingface-cli login   # one-time, for model weights
bash setup.sh
claude                  # one-time OAuth login
```

`setup.sh` will:
1. Clone [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) into `genomeclaw/`
2. Install Python dependencies
3. Download model weights (~4.8 GB) from Hugging Face
4. Build the GenomeClaw API (`cargo build --release`)
5. Copy Claude Code config to `~/.claude/`

## Running

Start the GenomeClaw API:
```bash
CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/boltz1.safetensors \
CLAWAPI_BIND=127.0.0.1:8083 \
./genomeclaw/target/release/clawapi &
```

Run the agent:
```bash
python3 run_agent.py "your query here"
```

## Requirements

- Python 3.9+
- Rust 1.94+ — [rustup.rs](https://rustup.rs)
- Claude Code CLI — [claude.ai/code](https://claude.ai/code)
- Hugging Face CLI — `pip install huggingface_hub`

## Structure

```
run_agent.py          # 20-tool ReAct orchestrator
orchestrator_agent.py # secondary agent
proxy_server.py       # subscription auth bridge
skills/               # researcher, auditor, lit_agent, pdf_generator, pipeline
knowledge_base/       # cached intelligence (trials, FDA, pipeline, competitive)
claude-config/        # portable Claude Code settings and memory
repos.yaml            # manifest — declares repos and weights to pull
setup.sh              # bootstrap script for new machine setup
```
