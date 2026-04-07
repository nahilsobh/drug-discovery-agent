# drug-discovery-agent

An autonomous strategic drug discovery agent for pharmaceutical portfolio analysis. Built as part of the **Roche AI Factory "20 by 30" initiative**, it runs a JSON ReAct loop powered by Claude and 30 tools that query live biomedical databases and a local [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) API for protein structure prediction, ADMET filtering, and variant effect scoring.

> **Core capability:** Cross-references Open Targets genetic evidence with live ClinicalTrials.gov data to surface "blue ocean" gaps — indications where human genetics is strong but no active Roche Phase II/III trial exists.

---

## Architecture

```
run_agent.py                  # JSON ReAct orchestrator — 30 tools, Claude claude-opus-4-6
orchestrator_agent.py         # 20-by-30 turbospeed audit agent (20 fixed pipeline assets)
proxy_server.py               # Auth bridge for Claude subscription (OAuth Bearer)
main.py                       # PDF synthesis orchestrator (reads intelligence_cache.json)
skills/
  researcher.py               # Open Targets + ClinicalTrials.gov queries
  auditor.py                  # Portfolio gap analysis
  lit_agent.py                # Europe PMC + ArXiv literature synthesis
  pdf_generator.py            # ReportLab CEO-ready PDF output
  pipeline.py                 # Roche pipeline enrichment
  target_biology_scraper.py   # Ensembl → disease association resolver
knowledge_base/               # Pre-populated JSON intelligence caches (see below)
genomeclaw/                   # Rust-native biomedical compute (cloned by setup.sh)
start.sh                      # Full startup: proxy + GenomeClaw API + agent
setup.sh                      # Bootstrap script for new machine setup
repos.yaml                    # Manifest — declares repos and weights to pull
```

---

## Live Database Integrations

| Database | Purpose |
|---|---|
| [Open Targets GraphQL v4](https://platform.opentargets.org) | Genetic evidence scores per target/disease |
| [ClinicalTrials.gov v2 API](https://clinicaltrials.gov/api/v2) | Active trial search by sponsor/phase |
| [Europe PMC](https://europepmc.org) | Peer-reviewed literature (2018–present) |
| [ArXiv](https://arxiv.org) | Preprints (q-bio, cs.LG, stat.ML) |
| [UniProt REST](https://rest.uniprot.org) | Protein function + binding site data |
| [ChEMBL REST](https://www.ebi.ac.uk/chembl) | IC50/Ki bioactivities for hit identification |
| [openFDA / FAERS](https://api.fda.gov) | Adverse event reports + drug approval lookup |
| GenomeClaw REST API (`127.0.0.1:8083`) | Boltz-1 folding, ESM-2 variants, ADMET, gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal |

---

## 30 Agent Tools

### Discovery
| Tool | Description |
|---|---|
| `search_roche_trials` | Active Roche/Genentech trials by therapeutic area |
| `get_biology` | Open Targets disease associations for a gene/drug |
| `find_gaps` | Core analysis: high-evidence targets with no Roche trial. Returns `translational_confidence` (LOW/MODERATE/HIGH) per gap |
| `find_hits` | Hit identification via ChEMBL — ranked actives by IC50/pIC50 with assay provenance quality flag |
| `find_repurposing_candidates` | Approved drugs repositionable into new indications (includes strategic caution note) |
| `find_combinations` | Roche drug pairs targeting complementary pathways |
| `find_shared_targets` | Gene targets shared between two diseases above a confidence threshold |

### Competitive & Portfolio
| Tool | Description |
|---|---|
| `check_competitor_trials` | Competitor trial count for a given disease |
| `monitor_competitive_signals` | Live 8-competitor dashboard (parallel CT.gov queries) |
| `query_competitive_intel` | Offline competitor asset database (AZ, Lilly, Novartis, Pfizer, BMS, Merck, AbbVie, J&J) |
| `rank_portfolio` | Score all assets by bio_score × unexplored indications × competitive vacancy |
| `list_pipeline_assets` | Fast offline Roche pipeline lookup by TA/phase/modality |

### Evidence & Regulatory
| Tool | Description |
|---|---|
| `scan_literature` | Europe PMC + ArXiv parallel search with `min_year` filter |
| `scan_arxiv` | ArXiv preprints only (6–18 months ahead of peer review) |
| `bulk_scan_literature` | Parallel literature scan across multiple targets |
| `map_regulatory_path` | FDA endpoint, biomarker, CDx, and expedited pathway (30+ indications) |
| `score_trial_outcome` | Trial success likelihood (0.0–1.0) with TA-adjusted priors (see below) |
| `check_orphan_eligibility` | Orphan Drug Designation eligibility + 7yr exclusivity + tax credits |
| `query_adverse_events` | FDA FAERS: total reports, serious/fatal rates, top MedDRA reactions, safety signal rating |

### Target Intelligence (GenomeClaw)
| Tool | Description |
|---|---|
| `get_protein_structure_context` | UniProt + OT tractability + 3D fold druggability |
| `fold_target` | Boltz-1 3D structure prediction + pLDDT confidence score |
| `score_variant_effect` | ESM-2 delta log-likelihood — resistance risk for known mutations |
| `predict_admet` | **MANDATORY gate** — hERG, BBB, hepatotoxicity, oral bioavailability (TIER-1/2/3) |
| `query_genomeclaw_databases` | gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal in one call |

### IP / Patents
| Tool | Description |
|---|---|
| `search_patents` | Search US + global patents by keyword or assignee (USPTO PatentsView + Lens.org). Set `LENS_API_KEY` env var for global coverage |
| `get_patent_landscape` | Full IP landscape for a target/compound: filing volume, top assignees ranked by volume, FTO flag, white-space note. Call after `find_hits` before `map_regulatory_path` |

### Memory & Output
| Tool | Description |
|---|---|
| `save_to_cache` | Persist findings to `knowledge_base/intelligence_cache.json` |
| `generate_pdf_report` | Full structured PDF report from session findings (always last step) |

---

## Enforced Workflow Rules

The following rules are hard-coded into the agent's system prompt and cannot be overridden:

1. **`predict_admet` is a mandatory gate** after both `find_hits` and `find_repurposing_candidates`. No compound advances to `map_regulatory_path` or `score_trial_outcome` without TIER-1 ADMET clearance.

2. **`generate_pdf_report` is always the final step** on any report-type query.

3. **Translational confidence weighting:** `find_gaps` returns `translational_confidence` (LOW / MODERATE / HIGH) per gap — HIGH gaps are prioritised. CNS gaps are flagged LOW regardless of bio score.

4. **TA-adjusted success priors in `score_trial_outcome`:**
   | Therapeutic Area | Prior Modifier | Rationale |
   |---|---|---|
   | CNS / neurology | −0.10 | Near-absent predictive animal models |
   | Anti-infectives / viral | +0.08 | Best cell→animal→human concordance |
   | Metabolic / diabetes | +0.05 | Db/Db + Ob/Ob models are predictive |
   | Oncology | 0.00 | Reflected in phase base rates |

5. **Assay provenance:** `find_hits` flags `provenance_quality` — single-assay results require orthogonal confirmation before advancing.

6. **Bio score threshold:** Gaps with `bio_score < 0.70` are deprioritised.

---

## Standard Workflows

**Gap analysis:**
```
find_gaps → monitor_competitive_signals → scan_literature → map_regulatory_path → save_to_cache → generate_pdf_report
```

**Hit identification:**
```
find_hits → predict_admet (TIER-1 only) → score_variant_effect on key mutations → map_regulatory_path
```

**Repurposing:**
```
find_repurposing_candidates → predict_admet (TIER-1 only) → map_regulatory_path → generate_pdf_report
```

**New target validation:**
```
get_protein_structure_context → fold_target → score_variant_effect → query_genomeclaw_databases
```

**Competitive landscape:**
```
query_competitive_intel → monitor_competitive_signals → check_competitor_trials
```

**Safety profiling:**
```
query_adverse_events → compare serious/fatal rates across drug class → score_trial_outcome
```

---

## Knowledge Base

All files live in `knowledge_base/`. Replace with internal system exports for production deployment (see Roche Internal Infrastructure below).

| File | Size | Contents |
|---|---|---|
| `roche_pipeline.json` | 27K | 200+ Roche/Genentech pipeline assets with gene symbol, alias, Ensembl ID |
| `pipeline_enrichment.json` | 25K | Per-asset metadata: phase, status, TA, indication, modality, mechanism, safety signals |
| `competitive_intel.json` | 14K | 30+ competitor programs across AZ, Lilly, Novartis, Pfizer, BMS, Merck, AbbVie, J&J |
| `fda_guidelines.json` | 54K | FDA endpoint, biomarker, CDx, and expedited pathway requirements (30+ indications) |
| `cdx_registry.json` | 26K | Companion diagnostic registry by indication |
| `asset_timelines.json` | 19K | SoTD → FiH phase transition timelines (used by orchestrator) |
| `intelligence_cache.json` | 41K | Accumulating cache of all agent discoveries and findings |
| `thin_layer_mdm.json` | 14K | Master data management: verified sites, investigators, CROs |
| `ihb_organoid_data.json` | 12K | In Vitro Human Biology organoid concordance data |
| `bionemo_cache.json` | 12K | Cached NVIDIA BioNeMo molecular simulation results |
| `rde_levers.json` | 9.6K | R&D acceleration tactics by phase (preclinical, IND, recruitment, etc.) |

---

## 20-by-30 Portfolio (Orchestrator)

The `orchestrator_agent.py` runs a dedicated audit against these 20 priority assets:

| # | Drug | Alias | Target | TA |
|---|---|---|---|---|
| 1 | Giredestrant | RG6171 | ESR1 | Oncology |
| 2 | Trontinemab | RG6102 | APP | Neurology |
| 3 | CT-388 | RG6640 | GLP1R | Metabolic |
| 4 | NXT007 | RG6512 | F8 | Haematology |
| 5 | Fenebrutinib | RG6046 | BTK | Immunology |
| 6 | Inavolisib | RG6114 | PIK3CA | Oncology |
| 7 | Divarasib | RG6330 | KRAS | Oncology |
| 8 | Zilebesiran | ALN-AGT | AGT | Cardiovascular |
| 9 | Crovalimab | RG6107 | C5 | Haematology |
| 10 | Tiragolumab | RG6058 | TIGIT | Oncology |
| 11 | Gazyva | RG7159 | MS4A1 | Oncology |
| 12 | Susvimo | RG6321 | VEGFA | Ophthalmology |
| 13 | RVT-3101 | RG6633 | TNFSF15 | Gastroenterology |
| 14 | Prasinezumab | RG7935 | SNCA | Neurology |
| 15 | Vamikibart | RG6179 | IL6 | Immunology |
| 16 | Cevostamab | RG6160 | FCRL5 | Haematology |
| 17 | Columvi | RG6026 | MS4A1 | Oncology |
| 18 | Lunsumio | RG7828 | MS4A1 | Oncology |
| 19 | Astegolimab | RG6149 | IL33 | Pulmonology |
| 20 | Satralizumab | RG6168 | IL6R | Neurology |

---

## Prerequisites

| Dependency | Version | Install |
|---|---|---|
| Python | 3.9+ | [python.org](https://www.python.org) |
| Rust + Cargo | 1.94+ (nightly) | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Claude Code CLI | latest | [claude.ai/code](https://claude.ai/code) |
| Hugging Face CLI | latest | `pip install huggingface_hub` |
| Git | any | system package manager |

**On HPC clusters (SLURM):** Python and Singularity are the only requirements — no root, no Docker needed. See [Running on HPC / SLURM](#running-on-hpc--slurm) below.

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

### 5. Start everything
```bash
bash start.sh "your query here"
```

`start.sh` handles the full startup sequence: OAuth proxy → GenomeClaw API health check → agent query.

Or manually:
```bash
# Start GenomeClaw API
CLAWAPI_WEIGHTS=genomeclaw/weights/boltz-1/boltz1.safetensors \
CLAWAPI_BIND=127.0.0.1:8083 \
./genomeclaw/target/release/clawapi &

# Verify
curl http://127.0.0.1:8083/health

# Run agent
python3 run_agent.py "Find gaps in Roche's neurology pipeline"
```

---

## Usage

```bash
python3 run_agent.py "Find gaps in Roche's neurology pipeline"
python3 run_agent.py "Which oncology targets have strong biology but no active Roche trial?"
python3 run_agent.py "Find EGFR hits below 10nM and check adverse events for erlotinib vs osimertinib"
python3 run_agent.py "Run a competitive landscape analysis for KRAS inhibitors"
python3 run_agent.py "What repurposing candidates exist for Parkinson's disease?"
```

The agent runs a JSON ReAct loop — it reasons, calls tools, observes results, and iterates until it produces a final CEO-ready answer. Findings are saved to `knowledge_base/intelligence_cache.json` and exported as a PDF report.

### Authentication

```bash
# Claude subscription (OAuth Bearer — recommended)
export ANTHROPIC_AUTH_TOKEN=<token from ~/.claude/.credentials.json>

# API billing credits
export ANTHROPIC_API_KEY=sk-ant-api03-...

# Model override
export AGENT_MODEL=claude-opus-4-6   # default

# Max turns (default 20)
export AGENT_MAX_TURNS=30
```

---

## GenomeClaw

[GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) is a Rust-native biomedical compute platform (59 crates) providing:

- **Boltz-1** — protein structure prediction (pLDDT confidence scoring, up to 400 residues)
- **ESM-2 650M** — protein language model for variant effect scoring (delta log-likelihood)
- **ADMET** — hERG, BBB, hepatotoxicity, Ames mutagenicity, CYP3A4/2D6, oral bioavailability
- **Database clients** — gnomAD, ChemBL, BindingDB, ClinVar, STRING, cBioPortal

The agent communicates with GenomeClaw via a local REST API at `http://127.0.0.1:8083`.

**Model weights required:**

| File | Size | Purpose |
|---|---|---|
| `genomeclaw/weights/boltz-1/boltz1.safetensors` | 1.6 GB | Structure prediction |
| `genomeclaw/weights/boltz-1/boltz1_conf.safetensors` | 2.2 GB | Confidence model |
| `genomeclaw/weights/esm2/` | ~1.0 GB | Variant effect scoring |

**ADMET tiers:**
- **TIER-1** — All clear. Eligible to advance.
- **TIER-2** — Minor flags (moderate hERG, poor BBB, poor solubility). Review required.
- **TIER-3** — Red flags (hERG blocker, Ames+, low safety score). Do not advance.

---

## Session State

Each agent run accumulates findings in a session dict across all tool calls:

```
question, gaps, portfolio, combinations, literature, regulatory,
trials, biology, arxiv_papers, trial_outcomes, repurposing,
orphan_flags, protein_structures, competitive_signals,
fold_results, variant_effects, admet_profiles, mutation_landscapes
```

All session data is available to `generate_pdf_report` at the end of the run.

---

## Running on HPC / SLURM

The agent runs inside a [Singularity](https://sylabs.io/singularity/) container on SLURM clusters — no root, no Docker, no module loads required. The container image is pre-built with all Python dependencies; the project directory and `knowledge_base/` are bind-mounted at runtime so code changes take effect immediately without rebuilding.

### Container layout

```
~/singularity-images/
  drug-discovery-agent.sif      # immutable production image (65 MB)
  drug-discovery-sandbox/       # writable sandbox — rebuild after requirements.txt changes
run_singularity.sh              # launcher — interactive and SLURM batch
```

### Running interactively

```bash
# Request a compute node and start the agent
srun --ntasks=1 --time=08:00:00 bash run_singularity.sh \
  "Find gaps in Roche's neurology pipeline"

# Or open a shell inside the container
srun --ntasks=1 --time=02:00:00 bash run_singularity.sh bash
```

### Running as a SLURM batch job

```bash
# Default: runs python3 run_agent.py (SLURM headers are in run_singularity.sh)
sbatch run_singularity.sh

# Custom query via env var
AGENT_QUERY="Which oncology targets have strong biology but no active Roche trial?" \
  sbatch run_singularity.sh

# Logs → logs/agent_<jobid>.log
```

### Running the test suite

```bash
# Full pytest suite with coverage (runs in ~4 seconds — all HTTP mocked)
srun --ntasks=1 bash run_singularity.sh \
  python3 -m pytest tests/ -q --tb=short

# Coverage report
srun --ntasks=1 bash run_singularity.sh \
  python3 -m pytest tests/ --cov=tools --cov-report=term-missing

# Single module
srun --ntasks=1 bash run_singularity.sh \
  python3 -m pytest tests/test_chemistry.py -v
```

### Authentication inside the container

The container never stores credentials — pass them at job submission time:

```bash
# API key (preferred for SLURM)
ANTHROPIC_API_KEY=sk-ant-api03-... sbatch run_singularity.sh

# OAuth token (Claude subscription)
ANTHROPIC_AUTH_TOKEN=<token> sbatch run_singularity.sh

# Model and turn overrides
AGENT_MODEL=claude-opus-4-6 AGENT_MAX_TURNS=30 sbatch run_singularity.sh
```

### GPU-accelerated GenomeClaw (Boltz-1 protein folding)

GenomeClaw's Boltz-1 folds take ~21 min on CPU. On an A100 it drops to ~1–2 min, unlocking full-length folds for large proteins (BRCA2, TTN) currently blocked by sequence-length limits.

```bash
# Submit agent + GenomeClaw together on a GPU node
sbatch run_genomeclaw_gpu.sh "Fold BRCA2 and score resistance variants"

# Interactive GPU session
srun --partition=interactive_gpu --gres=gpu:l40s:1 --ntasks=1 --time=04:00:00 \
  bash run_genomeclaw_gpu.sh "your query"
```

`run_genomeclaw_gpu.sh` starts the GenomeClaw API on the allocated GPU node, waits for it to be healthy, then launches the agent in Singularity with `CLAWAPI_URL` pointing at it. GenomeClaw is stopped automatically when the job exits.

**Available GPU partitions on this cluster:**
| Partition | GPU | Use case |
|---|---|---|
| `interactive_gpu` | L40S | Interactive sessions |
| `batch_gpu` | A100 | Overnight batch runs |

### Rebuilding the image

Only needed when `requirements.txt` changes. Code changes under `drug-discovery-agent/` are live immediately (bind-mounted — no rebuild required).

```bash
# 1. Install new deps into the writable sandbox
srun --ntasks=1 singularity exec --writable --cleanenv \
  --bind /tmp:/tmp \
  ~/singularity-images/drug-discovery-sandbox \
  pip install --no-cache-dir -r /tmp/requirements.txt

# 2. Freeze to a new immutable .sif
srun --ntasks=1 singularity build \
  ~/singularity-images/drug-discovery-agent.sif \
  ~/singularity-images/drug-discovery-sandbox
```

### Deploying on Roche Internal Infrastructure

The agent runs entirely on public APIs out of the box. For internal deployment:

| Component | Change |
|---|---|
| GenomeClaw API | `export CLAWAPI_URL=https://genomeclaw.ai-factory.roche-internal.com` (routes all 5 GenomeClaw tools to the 3,500-GPU cluster) |
| Claude API | `export ANTHROPIC_BASE_URL=https://anthropic-gateway.roche-internal.com` |
| Pipeline data | Replace `knowledge_base/roche_pipeline.json` with Apollo / Planisware export |
| Patient data | Replace `knowledge_base/intelligence_cache.json` with Flatiron Health export |
| CDx registry | Replace `knowledge_base/cdx_registry.json` with Navify Algorithm Suite export |
| Competitive intel | Replace `knowledge_base/competitive_intel.json` with Citeline / Cortellis export |

No agent code changes needed — all internal data surfaces through the `knowledge_base/` JSON files.

---

## Runtime Alternatives — ZeroClaw vs OpenClaw

| | Python + OpenClaw | ZeroClaw |
|---|---|---|
| Runtime | Node.js / TypeScript | Single Rust binary |
| Binary size | 1 GB+ footprint | 3.4 MB |
| Idle RAM | ~394 MB | < 5 MB |
| Boot time | Seconds | < 10 ms |
| Best for | Development / complex workflows | Production / cluster / edge |

ZeroClaw is the natural production companion to GenomeClaw — both compile to single static binaries. On the Roche AI Factory cluster, the difference between 394 MB and 5 MB per agent instance is substantial at scale.

---

## Extending the Agent — OpenClaw Medical Skills

[OpenClaw Medical Skills](https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills) provides 869 pre-built agent skills compatible with this framework.

| Skill | Extends | What it adds |
|---|---|---|
| `tooluniverse-drug-repurposing` | `find_repurposing_candidates` | Multi-database repurposing with bioactivity + safety profiles |
| `tooluniverse-target-research` | `get_protein_structure_context` | Protein interactions, pathways, expression, variant landscape |
| `tooluniverse-drug-target-validation` | `find_gaps` | Validates targets across 10 dimensions incl. druggability + clinical precedent |
| `tooluniverse-gwas-trait-to-gene` | `get_biology` | 500k+ GWAS Catalog associations — broader genetic evidence than Open Targets alone |
| `tooluniverse-clinical-trial-design` | `map_regulatory_path` | Trial feasibility scoring — patient population, endpoints, regulatory pathway |
| `tooluniverse-clinical-trial-matching` | `score_trial_outcome` | Patient-to-trial matching by molecular eligibility and biomarker alignment |
| `tooluniverse-rare-disease-diagnosis` | `check_orphan_eligibility` | Phenotype + genetic differential diagnosis for rare disease gap analysis |
| `tooluniverse-adverse-event-detection` | `query_adverse_events` | FDA FAERS disproportionality analysis — surfaces safety signals early |
| `tooluniverse-precision-oncology` | `find_gaps` | Actionable treatment recommendations from molecular profiles |
| `tooluniverse-network-pharmacology` | `find_combinations` | Compound-target-disease network analysis for polypharmacology discovery |
| `tooluniverse-chemical-safety` | `predict_admet` | ADMET-AI + FDA label integration for deeper safety profiling |
| `patents-search` | *(new capability)* | Global patent landscape and prior art — currently missing from this agent |

The `patents-search` skill is the highest-priority addition — IP landscape analysis is not currently covered and is critical for competitive white-space assessment.

---

## Future Extensions

Based on *"AI Agents in Drug Discovery: Applications and Case Studies"* (Huynh, Seal, Bender, Spjuth et al., *Drug Discovery Today*, 2026) and the Sandbox AQ / UCSF Bhatt lab results (5.5M molecules screened computationally in 1 month vs 250K in 1 year, 30× higher hit rate).

### 1. Supervisor Architecture (highest priority)

Split the single ReAct loop into a supervisor + specialist sub-agents running in parallel:

- **Supervisor agent** — decomposes tasks and delegates
- **Biology sub-agent** — Open Targets, UniProt, GWAS
- **Chemistry sub-agent** — GenomeClaw ADMET, ChEMBL, SMILES
- **Clinical sub-agent** — ClinicalTrials.gov, Flatiron RWD
- **Regulatory sub-agent** — FDA guidelines, orphan designation, CDx

**Proven benchmarks:**

| Use case | Manual time | Agent time | Speedup |
|---|---|---|---|
| Literature analysis (BTK inhibitor) | Weeks | Hours | >100× |
| Assay protocol design | Months | <2 hours | >400× |
| IPF drug discovery program | 2–3 weeks | <2 hours | >50× |
| Rare disease repurposing (SMA) | Weeks | Hours | >20× |

### 2. GraphRAG for Rare Disease Literature (medium priority)

Replace keyword search in `scan_literature` with entity-graph traversal for sparse evidence areas (NEU1, DEPDC5, GLB1, GSN). Reference: [Microsoft GraphRAG](https://github.com/microsoft/graphrag).

### 3. Focal Graph Search for Novel Target Discovery (medium priority)

Extend `find_shared_targets` and `find_gaps` with focal graph queries on the Open Targets knowledge graph — finds genes with similar perturbation profiles (Plex Research approach for Wnt pathway oncology targets).

### 4. Probability-of-Success Scoring (lower priority)

Extend `score_trial_outcome` with:
- Market size and pricing scenarios
- IP landscape (freedom-to-operate) via `patents-search`
- Historical PoS rates by indication + modality (Convexia Bio approach)

### 5. Action Tools — Wet Lab Integration (long-term)

Add interfaces to robotic liquid handlers (Opentrons, Hamilton), HTS plate readers, and NGS library prep systems to close the DMTA loop toward self-driving laboratory capability.

---

## Project Context

Built for the **Roche AI Factory "20 by 30"** strategy — identifying 20 new indication opportunities by 2030 by eliminating innovation silos between Diagnostics and Pharma divisions. The agent autonomously senses global genomic and clinical data, reasons over gaps, and proposes strategic pivots for assets like Giredestrant (ESR1) and Trontinemab.

*Created for the 2026 Roche Global AI Hackathon.*
