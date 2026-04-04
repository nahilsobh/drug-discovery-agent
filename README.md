# drug-discovery-agent

An autonomous strategic drug discovery agent for pharmaceutical portfolio analysis. Built as part of the **Roche AI Factory "20 by 30" initiative**, it runs a JSON ReAct loop powered by Claude and 24 tools that query live biomedical databases and a local [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) API for protein structure prediction, ADMET filtering, and variant effect scoring.

> **Core capability:** Cross-references Open Targets genetic evidence with live ClinicalTrials.gov data to surface "blue ocean" gaps — indications where human genetics is strong but no active Roche Phase II/III trial exists.

---

## Architecture

```
run_agent.py                  # JSON ReAct orchestrator — 24 tools, Claude claude-opus-4-6
orchestrator_agent.py         # Secondary agent for parallel sub-tasks
proxy_server.py               # Auth bridge for Claude subscription (OAuth Bearer)
skills/                       # Modular Python skill modules
  researcher.py               # Open Targets + ClinicalTrials.gov queries
  auditor.py                  # Portfolio gap analysis
  lit_agent.py                # Europe PMC + ArXiv literature synthesis
  pdf_generator.py            # ReportLab CEO-ready PDF output
  pipeline.py                 # Roche pipeline enrichment
  target_biology_scraper.py   # Ensembl → disease association resolver
knowledge_base/               # Pre-populated JSON intelligence caches
  intelligence_cache.json     # 100+ drug-target records with scores + trial IDs
  roche_pipeline.json         # Roche/Genentech pipeline assets
  competitive_intel.json      # AZ, Lilly, Novartis, Pfizer programs
  fda_guidelines.json         # Endpoint, biomarker, CDx requirements
  asset_timelines.json        # Phase transition timelines
  cdx_registry.json           # Companion diagnostic registry
repos.yaml                    # Manifest — declares repos and weights to pull
setup.sh                      # Bootstrap script for new machine setup
```

GenomeClaw is cloned separately into `genomeclaw/` by `setup.sh` and is not tracked in this repo.

---

## Live Database Integrations

| Database | Purpose |
|---|---|
| [Open Targets GraphQL v4](https://platform.opentargets.org) | Genetic evidence scores per target/disease |
| [ClinicalTrials.gov v2 API](https://clinicaltrials.gov/api/v2) | Active trial search by sponsor/phase |
| [Europe PMC](https://europepmc.org) | Peer-reviewed literature (2018–present) |
| [ArXiv](https://arxiv.org) | Preprints (q-bio, cs.LG, stat.ML) |
| [UniProt REST](https://rest.uniprot.org) | Protein function + binding site data |
| [openFDA](https://api.fda.gov) | Approved drug + safety signal lookup |
| GenomeClaw REST API (`127.0.0.1:8083`) | Boltz-1 folding, ESM-2 variants, ADMET, gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal |

---

## 24 Agent Tools

### Discovery
| Tool | Description |
|---|---|
| `search_roche_trials` | Active Roche/Genentech trials by therapeutic area |
| `get_biology` | Open Targets disease associations for a gene/drug |
| `find_gaps` | Core analysis: high-evidence targets with no Roche Phase II/III |
| `find_repurposing_candidates` | Approved drugs repositionable into new indications |
| `find_combinations` | Roche drug pairs targeting complementary pathways |
| `find_shared_targets` | Gene targets shared between two diseases |

### Competitive & Portfolio
| Tool | Description |
|---|---|
| `check_competitor_trials` | Competitor trial count for a given disease |
| `monitor_competitive_signals` | Live 8-competitor dashboard (parallel CT.gov queries) |
| `query_competitive_intel` | Offline competitor asset database (AZ, Lilly, Novartis, Pfizer, etc.) |
| `rank_portfolio` | Score all assets by bio_score × unexplored indications × competitive vacancy |
| `list_pipeline_assets` | Fast offline Roche pipeline lookup by TA/phase/modality |

### Evidence & Regulatory
| Tool | Description |
|---|---|
| `scan_literature` | Europe PMC + ArXiv parallel search with `min_year` filter |
| `scan_arxiv` | ArXiv preprints only (6–18 months ahead of peer review) |
| `bulk_scan_literature` | Parallel literature scan across multiple targets |
| `map_regulatory_path` | FDA endpoint, biomarker, CDx, and expedited pathway (30+ indications) |
| `score_trial_outcome` | Trial success likelihood (0.0–1.0) based on phase/enrollment/design |
| `check_orphan_eligibility` | Orphan Drug Designation eligibility + 7yr exclusivity + tax credits |

### Target Intelligence (GenomeClaw)
| Tool | Description |
|---|---|
| `get_protein_structure_context` | UniProt + OT tractability + 3D fold druggability |
| `fold_target` | Boltz-1 3D structure prediction + pLDDT confidence score |
| `score_variant_effect` | ESM-2 delta log-likelihood — resistance risk for known mutations |
| `predict_admet` | hERG, BBB, hepatotoxicity, oral bioavailability (TIER-1/2/3) |
| `query_genomeclaw_databases` | gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal in one call |

### Memory & Output
| Tool | Description |
|---|---|
| `save_to_cache` | Persist findings to `knowledge_base/intelligence_cache.json` |
| `generate_pdf_report` | Full structured PDF report from session findings |

---

## Prerequisites

| Dependency | Version | Install |
|---|---|---|
| Python | 3.9+ | [python.org](https://www.python.org) |
| Rust + Cargo | 1.94+ (nightly) | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Claude Code CLI | latest | [claude.ai/code](https://claude.ai/code) |
| Hugging Face CLI | latest | `pip install huggingface_hub` |
| Git | any | system package manager |

---

## Setup

### 1. Clone this repo
```bash
git clone https://github.com/nahilsobh/drug-discovery-agent.git
cd drug-discovery-agent
```

### 2. Authenticate Hugging Face (for model weights)
```bash
huggingface-cli login
```

### 3. Run the bootstrap script
```bash
bash setup.sh
```

This will automatically:
- Clone [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) into `genomeclaw/`
- Install Python dependencies (`requirements.txt`)
- Download Boltz-1 and ESM-2 model weights (~4.8 GB) from Hugging Face
- Build the GenomeClaw API binary (`cargo build --release -p genomeclaw-api`)
- Copy Claude Code settings and memory files to `~/.claude/`

### 4. Authenticate Claude Code
```bash
claude
```
Follow the OAuth browser login on first run.

### 5. Start the GenomeClaw API
```bash
cd drug-discovery-agent
CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/boltz1.safetensors \
CLAWAPI_BIND=127.0.0.1:8083 \
./genomeclaw/target/release/clawapi &
```

Verify it's running:
```bash
curl http://127.0.0.1:8083/health
```

---

## Usage

```bash
python3 run_agent.py "Find gaps in Roche's neurology pipeline"
python3 run_agent.py "Which oncology targets have strong biology but no active Roche trial?"
python3 run_agent.py "Run a Phase 1 portfolio screen across metabolic disease and neurology"
```

The agent runs a JSON ReAct loop — it reasons, calls tools, observes results, and iterates until it produces a final CEO-ready answer. Findings are saved to `knowledge_base/intelligence_cache.json` and optionally exported as a PDF report.

### Authentication

The agent supports two auth modes (set via environment variable):

```bash
# Claude subscription (OAuth Bearer — recommended)
export ANTHROPIC_AUTH_TOKEN=<token from ~/.claude/.credentials.json>

# API billing credits
export ANTHROPIC_API_KEY=sk-ant-api03-...
```

To override the model:
```bash
export AGENT_MODEL=claude-opus-4-6   # default
```

---

## GenomeClaw

[GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) is a Rust-native biomedical compute platform (40+ crates) providing:

- **Boltz-1** — protein structure prediction (pLDDT confidence scoring)
- **ESM-2** — protein language model for variant effect scoring
- **ADMET** — hERG, BBB, hepatotoxicity, oral bioavailability prediction
- **Database clients** — gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal

The agent communicates with GenomeClaw via a local REST API at `http://127.0.0.1:8083`. Model weights are stored separately in `genomeclaw/weights/` and are not tracked in this repo.

**Model weights required:**

| File | Size | Purpose |
|---|---|---|
| `genomeclaw/weights/boltz-1/boltz1.safetensors` | 1.6 GB | Structure prediction |
| `genomeclaw/weights/boltz-1/boltz1_conf.safetensors` | 2.2 GB | Confidence model |
| `genomeclaw/weights/esm2/` | ~1.0 GB | Variant effect scoring |

---

## Example Workflows

**Gap analysis:**
```
find_gaps → monitor_competitive_signals → scan_literature → map_regulatory_path → save_to_cache → generate_pdf_report
```

**Repurposing:**
```
find_repurposing_candidates → predict_admet (TIER-1 only) → map_regulatory_path
```

**New target validation:**
```
get_protein_structure_context → fold_target → score_variant_effect → query_genomeclaw_databases
```

**Competitive landscape:**
```
query_competitive_intel → monitor_competitive_signals → check_competitor_trials
```

---

## Project Context

Built for the **Roche AI Factory "20 by 30"** strategy — identifying 20 new indication opportunities by 2030 by eliminating innovation silos between Diagnostics and Pharma divisions. The agent autonomously senses global genomic and clinical data, reasons over gaps, and proposes strategic pivots for assets like Giredestrant (ESR1) and Trontinemab.

*Created for the 2026 Roche Global AI Hackathon.*
