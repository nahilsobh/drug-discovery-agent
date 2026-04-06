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

## Running on Roche Internal Infrastructure

This repo runs entirely on open-source and public APIs out of the box — no Roche credentials required. The section below is a **recommendation guide only** for teams who want to deploy this agent inside the Roche network and connect it to internal data sources. No code in this repo needs to be modified to run publicly.

---

### Recommended Internal Deployment Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Roche Internal Network              │
│                                                      │
│  drug-discovery-agent                                │
│       │                                              │
│       ├── Public APIs (unchanged)                    │
│       │     Open Targets, ClinicalTrials.gov,        │
│       │     Europe PMC, ArXiv, UniProt, openFDA      │
│       │                                              │
│       ├── GenomeClaw API  ──► Roche AI Factory       │
│       │     (deploy to 3,500 GPU cluster             │
│       │      instead of localhost)                   │
│       │                                              │
│       └── Internal data sources (drop-in JSON)       │
│             Apollo  ──► knowledge_base/roche_pipeline.json
│             Flatiron ──► knowledge_base/intelligence_cache.json
│             Navify   ──► knowledge_base/cdx_registry.json
│             Competitive DB ──► knowledge_base/competitive_intel.json
└─────────────────────────────────────────────────────┘
```

The agent reads all internal data through the `knowledge_base/` JSON files. The recommended approach is to **replace these files with exports from internal systems** rather than modifying the agent code itself — keeping this repo stable and upgradeable.

---

### Step 1 — Deploy GenomeClaw on the Roche AI Factory

Roche operates 3,500+ NVIDIA Blackwell GPUs (as of March 2026) across US and EU data centers. Running GenomeClaw on this cluster instead of a local machine gives ~100× throughput for folding and ADMET screens.

1. Clone [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) onto the cluster
2. Build and start the API: `cargo build --release -p genomeclaw-api`
3. Point the agent at the cluster endpoint instead of localhost:
   ```bash
   export CLAWAPI_URL=https://genomeclaw.ai-factory.roche-internal.com
   ```
The agent requires no other changes — all 5 GenomeClaw tools (`fold_target`, `score_variant_effect`, `predict_admet`, `query_genomeclaw_databases`, `get_protein_structure_context`) will automatically route to the cluster.

---

### Step 2 — Replace Knowledge Base Files with Internal Data

Each `knowledge_base/` JSON file has a defined schema. Export from the corresponding internal system and drop the file in place — no code changes needed.

#### Pipeline data → Apollo / Planisware / Veeva Vault
Replace `knowledge_base/roche_pipeline.json` with a live export of your internal portfolio:
```json
{
  "assets": [
    {
      "drug": "Giredestrant",
      "target": "ESR1",
      "indication": "Breast Cancer",
      "phase": "III",
      "status": "Active",
      "modality": "Small Molecule",
      "therapeutic_area": "Oncology"
    }
  ]
}
```
Required fields: `drug`, `target`, `indication`, `phase`, `status`, `modality`, `therapeutic_area`.
Contact the **Apollo / D&A team** for API or export access.

#### Real-world patient data → Flatiron Health
[Flatiron Health](https://flatiron.com) (Roche subsidiary) holds 5M+ patient records and 1.5B oncology datapoints from EHR-integrated OncoEMR across US, UK, Germany, and Japan. Append Flatiron survival and treatment sequence data into `knowledge_base/intelligence_cache.json` to give `score_trial_outcome` real-world comparator arms.
Requires a Flatiron data access agreement — contact [flatiron.com/real-world-evidence](https://flatiron.com/real-world-evidence).

#### Companion diagnostics → Navify
[Navify](https://navify.roche.com) (Roche Diagnostics) integrates lab, imaging, and genomic data across care settings. Its Algorithm Suite exposes an HTTPS/JSON API. Export CDx registry data into `knowledge_base/cdx_registry.json` to improve `map_regulatory_path` accuracy with real Ventana/PATHWAY assay data.
Docs: [navify.roche.com/marketplace](https://navify.roche.com/marketplace/products/navify-algorithm-suite)

#### Competitive intelligence → Citeline / Cortellis / GlobalData
Replace `knowledge_base/competitive_intel.json` with a licensed export from Citeline (Pharma Intelligence), Cortellis, or GlobalData. This gives `query_competitive_intel` and `monitor_competitive_signals` access to unpublished pipeline intelligence beyond what ClinicalTrials.gov shows.

#### Regulatory data → Internal Regulatory Affairs
Supplement `knowledge_base/fda_guidelines.json` with internal regulatory team guidance, FDA meeting minutes, and EMA scientific advice letters to improve `map_regulatory_path` precision for active Roche programs.

---

### Step 3 — Claude API via Roche's Anthropic Gateway

For shared or multi-user internal deployments, route Claude API calls through Roche's corporate API gateway rather than individual subscriptions:

```bash
export ANTHROPIC_API_KEY=<key from Roche IT / API management portal>
export ANTHROPIC_BASE_URL=https://anthropic-gateway.roche-internal.com  # if applicable
export AGENT_MODEL=claude-opus-4-6
```

For individual use on a Roche-issued machine with a personal Claude subscription, the existing OAuth flow (`proxy_server.py`) works unchanged.

---

### Step 4 — Add NVIDIA BioNeMo for Generative Chemistry (Optional)

Genentech's "lab-in-the-loop" platform pairs [NVIDIA BioNeMo](https://www.nvidia.com/en-us/clara/bionemo/) generative AI with automated high-throughput labs. If your team has BioNeMo access, it can complement the existing GenomeClaw tools with generative molecule design and larger protein language models.

This would require adding a new tool to `run_agent.py` — recommended as a separate internal fork rather than a change to this repo.

---

### Step 5 — Secrets Management

Never store credentials in this repo. For internal deployment use:

```bash
# Inject at runtime via environment variables
export ANTHROPIC_API_KEY=...
export CLAWAPI_URL=...

# Or use Roche's approved secrets manager
# (HashiCorp Vault / Azure Key Vault — contact Roche IT Security)
```

`configs/api_keys.json` is in `.gitignore` and must never be committed.

---

## Runtime Alternatives — ZeroClaw vs OpenClaw

This agent uses a Python orchestrator (`run_agent.py`) which works well on a developer machine. For production or cluster deployments where many agent instances run concurrently, [ZeroClaw](https://zeroclaw.net) is a significant upgrade in efficiency.

| | Python + OpenClaw | ZeroClaw |
|---|---|---|
| Runtime | Node.js / TypeScript | Single Rust binary |
| Binary size | 1 GB+ footprint | 3.4 MB |
| Idle RAM | ~394 MB | < 5 MB |
| Boot time | Seconds | < 10 ms |
| Plugin ecosystem | 869 skills (rich) | Smaller, OpenClaw-compatible |
| Best for | Development / complex workflows | Production / cluster / edge |

Since [GenomeClaw](https://git.redclaw.dev/genomeclaw/genomeclaw) is already Rust-native, ZeroClaw is the natural runtime companion — both compile to single static binaries with no external dependencies. On the Roche AI Factory cluster where hundreds of agent instances may run in parallel, the difference between 394 MB and 5 MB per agent is substantial.

**To migrate the orchestrator to ZeroClaw:**
1. Install ZeroClaw from [zeroclaw.net](https://zeroclaw.net)
2. Port the 24 tool definitions from `run_agent.py` to ZeroClaw's skill format (OpenClaw migration support is built in)
3. Point ZeroClaw at the same GenomeClaw API endpoint (`CLAWAPI_URL`)
4. The `knowledge_base/` JSON files and all public API integrations remain unchanged

This is recommended as a separate internal fork rather than a change to this repo — the Python orchestrator is kept here for portability and ease of contribution.

---

## Extending the Agent — OpenClaw Medical Skills

[OpenClaw Medical Skills](https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills) is a community library of **869 pre-built agent skills** for the OpenClaw/NanoClaw framework — the same ecosystem as GenomeClaw. Skills are drop-in tool definitions that can be added to `run_agent.py` to extend its capabilities without building from scratch.

The following skills directly complement or extend what this agent already does:

| Skill | Extends | What it adds |
|---|---|---|
| `tooluniverse-drug-repurposing` | `find_repurposing_candidates` | Multi-database repurposing with bioactivity + safety profiles |
| `tooluniverse-target-research` | `get_protein_structure_context` | Protein interactions, pathways, expression, variant landscape |
| `tooluniverse-drug-target-validation` | `find_gaps` | Validates targets across 10 dimensions incl. druggability + clinical precedent |
| `tooluniverse-gwas-trait-to-gene` | `get_biology` | 500k+ GWAS Catalog associations — broader genetic evidence than Open Targets alone |
| `tooluniverse-clinical-trial-design` | `map_regulatory_path` | Trial feasibility scoring — patient population, endpoints, regulatory pathway |
| `tooluniverse-clinical-trial-matching` | `score_trial_outcome` | Patient-to-trial matching by molecular eligibility and biomarker alignment |
| `tooluniverse-rare-disease-diagnosis` | `check_orphan_eligibility` | Phenotype + genetic differential diagnosis for rare disease gap analysis |
| `tooluniverse-adverse-event-detection` | `score_trial_outcome` | FDA FAERS disproportionality analysis — surfaces safety signals early |
| `tooluniverse-precision-oncology` | `find_gaps` | Actionable treatment recommendations from molecular profiles |
| `tooluniverse-network-pharmacology` | `find_combinations` | Compound-target-disease network analysis for polypharmacology discovery |
| `tooluniverse-chemical-safety` | `predict_admet` | ADMET-AI + FDA label integration for deeper safety profiling |
| `patents-search` | *(new capability)* | Global patent landscape and prior art — currently missing from this agent |

**Getting started:**
```bash
git clone https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills
# Browse skills/ directory and copy relevant tool definitions into run_agent.py
```

The `patents-search` skill is the highest-priority addition — IP landscape analysis is not currently covered by any of the 24 tools and is critical for competitive white-space assessment before committing to a new indication.

---

## Future Extensions

Based on *"AI Agents in Drug Discovery: Applications and Case Studies"* (Huynh, Seal, Bender, Spjuth et al., *Drug Discovery Today*, 2026 — [DOI: 10.1016/j.drudis.2026.104650](https://doi.org/10.1016/j.drudis.2026.104650)), the following extensions are identified as the highest-value upgrades to this agent. They are noted here as a roadmap — not yet implemented.

### 1. Supervisor Architecture (highest priority)

The current agent uses a single ReAct loop. The paper validates that a **supervisor + specialist sub-agent** architecture compresses literature analysis from weeks to hours (>100× speedup, Coincidence Labs BTK inhibitor case study). The upgrade would split `orchestrator_agent.py` into:

- **Supervisor agent** — decomposes tasks and delegates
- **Biology sub-agent** — Open Targets, UniProt, GWAS
- **Chemistry sub-agent** — GenomeClaw ADMET, ChEMBL, SMILES
- **Clinical sub-agent** — ClinicalTrials.gov, Flatiron RWD
- **Regulatory sub-agent** — FDA guidelines, orphan designation, CDx

Each sub-agent runs in parallel, solving the context-window bottleneck of a single long ReAct loop.

**Proven benchmarks from the paper:**

| Use case | Manual time | Agent time | Speedup |
|---|---|---|---|
| Literature analysis (BTK inhibitor) | Weeks | Hours | >100× |
| Assay protocol design | Months | <2 hours | >400× |
| IPF drug discovery program | 2–3 weeks | <2 hours | >50× |
| Rare disease repurposing (SMA) | Weeks | Hours | >20× |

### 2. GraphRAG for Rare Disease Literature (medium priority)

The current `scan_literature` tool uses keyword search on Europe PMC + ArXiv. **GraphRAG** organises publications into a knowledge graph of entities (genes, drugs, diseases, pathways) and retrieves by traversing entity relationships rather than matching keywords.

**When keyword search fails:** A paper linking *DEPDC5* → *mTOR* → *focal epilepsy* via a non-obvious pathway won't surface if it never uses the exact search term. GraphRAG finds it.

**When to add it:** For rare/orphan disease programs (NEU1, DEPDC5, GLB1, GSN) where evidence is sparse and non-obvious connections are the most valuable signal. For high-profile targets (PCSK9, LDLR) keyword search is already sufficient.

**Reference implementation:** [Microsoft GraphRAG](https://github.com/microsoft/graphrag) or [AgenticRAG](https://github.com/agentic-rag/agentic-rag) — both open source, can be layered on top of the existing `scan_literature` tool.

### 3. Focal Graph Search for Novel Target Discovery (medium priority)

Used by Plex Research to identify novel Wnt pathway oncology targets (including the eIF2 complex) by finding genes with similar RNA-seq perturbation profiles. Focal graphs extract relevant subgraphs from large knowledge graphs for a specific query — reducing compute cost while preserving meaningful relationships.

**Integration point:** Extend `find_shared_targets` and `find_gaps` with focal graph queries on the Open Targets knowledge graph.

### 4. Probability-of-Success Scoring (lower priority)

Convexia Bio's system includes a PoS module that predicts clinical trial outcomes using historical trial data, real-world evidence, and market/IP analysis. Currently missing from this agent — `score_trial_outcome` estimates likelihood based on phase/enrollment/design but does not integrate:
- Market size and pricing scenarios
- IP landscape (freedom-to-operate)
- Historical PoS rates by indication + modality

**Integration point:** Add `patents-search` from OpenClaw Medical Skills (see above) as the first step toward IP-aware PoS scoring.

### 5. Action Tools — Wet Lab Integration (long-term)

The paper categorises agent tools into four types: Perception, Computation, **Action**, and Memory. This agent covers the first three but has no Action tools — interfaces to physical lab systems such as:
- Robotic liquid handlers (Opentrons, Hamilton)
- High-throughput screening plate readers
- NGS library preparation systems

This closes the DMTA loop and moves toward self-driving laboratory capability. Relevant for Roche's AI Factory vision of continuous closed-loop experimentation.

---

## Project Context

Built for the **Roche AI Factory "20 by 30"** strategy — identifying 20 new indication opportunities by 2030 by eliminating innovation silos between Diagnostics and Pharma divisions. The agent autonomously senses global genomic and clinical data, reasons over gaps, and proposes strategic pivots for assets like Giredestrant (ESR1) and Trontinemab.

*Created for the 2026 Roche Global AI Hackathon.*
