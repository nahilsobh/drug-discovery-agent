# Roche AI Factory — Strategic Discovery Agent

## What This Is

A 34-tool ReAct agent that answers pharmaceutical strategic intelligence questions
for Roche/Genentech. The agent runs a Claude-powered loop (`run_agent.py`) calling
Python tools that query ClinicalTrials.gov, Open Targets, Europe PMC, ArXiv,
ChEMBL, USPTO, FDA FAERS, UniProt, KEGG, Orphanet, and a local GPU-accelerated
GenomeClaw API (Boltz-1 protein folding + ESM-2 variant effects + ADMET prediction
+ Tanimoto scaffold clustering + geometry-based docking scoring).

Outputs: structured JSON audit trail + branded Roche PDF report per run.

---

## Architecture

```
run_agent.py          — ReAct loop: Claude ↔ tool calls ↔ results
proxy_server.py       — Routes Anthropic SDK calls through `claude -p` CLI
                        (for subscription auth via ona-claude)
tools/
  constants.py        — MODEL, MAX_TURNS, CLAWAPI_URL, COMPETITORS, etc.
  session.py          — In-memory SESSION dict accumulating all tool results
  audit.py            — JSON Lines audit trail (logs/audit_<session_id>.jsonl)
  discovery.py        — find_gaps, get_biology, search_roche_trials, find_combinations,
                        get_pathway_context (KEGG)
  regulatory_competitive.py — rank_portfolio, list_pipeline_assets,
                               map_regulatory_path, score_trial_outcome,
                               check_orphan_eligibility, monitor_competitive_signals,
                               get_disease_prevalence (Orphanet)
  literature.py       — scan_literature, scan_arxiv, bulk_scan_literature
  chemistry.py        — find_hits (+ SMILES/descriptors), find_repurposing_candidates,
                        query_adverse_events
  genomeclaw.py       — fold_target, score_variant_effect, predict_admet,
                        query_genomeclaw_databases, cluster_scaffolds, dock_compound
                        (calls local Rust API)
  patents.py          — search_patents, get_patent_landscape
  memory.py           — recall_longterm_memory, save_to_cache
  _allowlist.py       — Egress allowlist (patches requests.get/post at import time)
knowledge_base/
  intelligence_cache.json   — Persisted tool results (59KB, 66+ entries)
  agent_longterm_memory.json — Cross-session memory (47KB)
  roche_pipeline.json        — Roche/Genentech pipeline assets (59 assets)
  competitive_intel.json     — Offline competitor asset database
genomeclaw/
  target/release/clawapi    — Rust binary (Boltz-1 + ESM-2 + ADMET GPU API)
  weights/                   — Model weights (boltz-1, esm2_t33_650M_UR50D)
reports/                     — Generated PDFs
logs/                        — SLURM job logs + audit_*.jsonl files
```

---

## Authentication

Two modes — detected automatically in `make_client()`:

| Mode | Env var | How it works |
|------|---------|--------------|
| Direct API | `ANTHROPIC_API_KEY` | Standard Anthropic SDK |
| Subscription proxy | `ANTHROPIC_AUTH_TOKEN` | `proxy_server.py` → `claude -p` CLI → `ona-claude` SSH tunnel |

`ona-claude` must be running (`$HOME/.local/bin/ona-claude -p 8095`) before
the subscription proxy path will work.

---

## Running Locally

```bash
# Direct API
export ANTHROPIC_API_KEY=sk-ant-api03-...
python3.11 run_agent.py "Find gaps in Roche's oncology pipeline"

# Subscription (requires ona-claude running)
export ANTHROPIC_AUTH_TOKEN=ona-proxy
python3.11 run_agent.py "CEO strategic briefing — generate PDF"
```

Key env vars:
```bash
# Model selection — Anthropic benchmarks (Protocol QA, bioinformatics) show
# claude-sonnet-4-6 can match or exceed opus on life-sciences reasoning tasks
# at lower cost/latency. Use opus for complex multi-step strategic queries.
AGENT_MODEL=claude-opus-4-6      # default; try claude-sonnet-4-6 for speed
AGENT_MAX_TURNS=30               # default 20
CLAWAPI_URL=http://127.0.0.1:8083  # GenomeClaw API
AUDIT_SUMMARY=1                  # print audit table at end of run
PROXY_PORT=9797                  # internal proxy port (auto-selected if taken)
```

---

## Running on sHPC Cluster (Singularity + GPU)

All SLURM scripts run the agent in a Singularity container on the `batch_gpu`
partition (NVIDIA A100-SXM4-40GB). Each job has **fully isolated ports** derived
from `SLURM_JOB_ID` to avoid collisions when multiple jobs share a node:

```
ONA_PORT    = 8095 + (JOB_ID % 900)
CLAWAPI_PORT= 8083 + (JOB_ID % 900)
PROXY_PORT  = 9797 + (JOB_ID % 200)
```

### CEO Strategic Briefing (single job)
```bash
sbatch ceo_query_slurm.sh
```

### Competitive Sweep (one job per competitor, 8 companies)
```bash
bash submit_competitive_sweep.sh   # submits 8 jobs staggered 30s apart
```
Per-job template: `competitive_briefing_slurm.sh`
Output: `reports/Roche_vs_<company>_<date>.pdf`

### Repurposing Sweep (one job per competitor, 8 companies)
```bash
bash submit_repurposing_sweep.sh   # submits 8 jobs staggered 30s apart
```
Per-job template: `repurposing_slurm.sh`
Output: `reports/Repurposing_<company>_<date>.pdf`

### Monitor
```bash
squeue -u sobhn
tail -f logs/competitive_<slug>_<JOBID>.out
tail -f logs/repurposing_<slug>_<JOBID>.out
ls -lh reports/
```

### Singularity container
The `run_singularity.sh` script handles:
- `--home $HOME:/root` — uses real home for auth (ona-claude SSH keys/tokens)
- `--bind $HOME/.local/lib:/root/.local/lib:ro` — Python packages
- `--bind <claude_binary>:/usr/local/bin/claude:ro` — claude CLI for proxy
- `--bind libssl.so.3 + libcrypto.so.3` from conda env — OpenSSL 3 for clawapi
- `--nv` when `USE_GPU=1` — NVIDIA GPU passthrough

Rebuild SIF after changing `drug-discovery-agent.def`:
```bash
srun --ntasks=1 singularity build \
  $HOME/singularity-images/drug-discovery-agent.sif \
  drug-discovery-agent.def
```

---

## Audit Trail

Every tool call is logged to `logs/audit_<session_id>.jsonl`:
```jsonc
{"record_type":"session_start","session_id":"...","model":"claude-opus-4-6",...}
{"record_type":"tool_call","turn":1,"call_num":1,"tool_name":"find_gaps",
 "category":"DISCOVERY","inputs":{...},"result_digest":"...","elapsed_secs":2.3,
 "slurm_job_id":"29718382","in_container":true}
{"record_type":"session_end","total_turns":9,"total_calls":7,...}
```

Print a human-readable summary:
```python
from tools.audit import print_audit_summary
print_audit_summary("logs/audit_<session_id>.jsonl")
```

Set `AUDIT_SUMMARY=1` in env to auto-print at end of every run.

---

## Tool Workflow Patterns

```
Gap analysis:
  find_gaps → monitor_competitive_signals → scan_literature → map_regulatory_path → save_to_cache

Portfolio overview:
  list_pipeline_assets → rank_portfolio → find_gaps

Repurposing:
  recall_longterm_memory → find_repurposing_candidates → predict_admet (TIER-1 only)
  → score_trial_outcome → check_orphan_eligibility → generate_pdf_report

Hit identification (with scaffold diversity):
  recall_longterm_memory → find_hits → cluster_scaffolds(hits)
  → dock_compound(rep_smiles, pocket_center) → predict_admet (TIER-1 only)
  → score_variant_effect → map_regulatory_path

Combination synergy check:
  get_pathway_context(gene_A) + get_pathway_context(gene_B)
  → find_combinations → get_biology on each drug → scan_literature

Orphan / rare disease:
  get_disease_prevalence(disease) → check_orphan_eligibility → map_regulatory_path

CEO briefing:
  list_pipeline_assets → rank_portfolio → find_gaps → monitor_competitive_signals
  → scan_literature → save_to_cache → generate_pdf_report
```

**Critical constraints:**
- `predict_admet` is a **mandatory gate** — never advance a compound without TIER-1 clearance
- Always call `recall_longterm_memory` before `find_hits` or `predict_admet`
- `generate_pdf_report` must be the **last tool call** in any PDF-producing run
- Roche and Genentech are the same company — treat them as one entity

---

## PDF Report Formatting

Generated by `generate_pdf_report()` in `run_agent.py` using ReportLab.

**Roche brand palette:**
```python
NAVY   = HexColor("#003087")
BLUE   = HexColor("#0066CC")
LIGHT  = HexColor("#E8F0FB")
SILVER = HexColor("#D0E4F7")
```

**Known ReportLab gotchas:**
- `spaceAfter`/`spaceBefore` on `ParagraphStyle` are ignored between Table rows — use explicit `Spacer()` rows or `TOPPADDING`/`BOTTOMPADDING`
- `TableStyle TEXTCOLOR` does NOT affect `Paragraph` objects inside cells — set `textColor` on the `ParagraphStyle` instead
- `CellB` style (white text) is exclusively for header rows with NAVY background

---

## Knowledge Base

| File | Contents | Size |
|------|----------|------|
| `intelligence_cache.json` | Persisted tool results | ~60KB |
| `agent_longterm_memory.json` | Cross-session hits, negatives, ADMET | ~47KB |
| `roche_pipeline.json` | 59 Roche/Genentech pipeline assets | ~26KB |
| `competitive_intel.json` | Competitor asset database (AZ, Lilly, etc.) | ~14KB |

---

## Tests

```bash
python3.11 -m pytest tests/ -q          # full suite (288 tests)
python3.11 -m pytest tests/test_genomeclaw.py -v    # includes cluster_scaffolds + dock_compound
python3.11 -m pytest tests/test_chemistry.py -v     # includes find_hits SMILES enrichment
python3.11 -m pytest tests/test_proxy_server.py -v  # includes proxy stall-retry integration tests
```

All 288 tests run offline via mocked HTTP — no live API or GPU required.
clawapi-dependent tests mock `_check_genomeclaw_health` and `requests.post`.
Proxy retry tests spin up a real `ProxyHandler` HTTPServer in a thread and mock `subprocess.run`.

---

## Known Issues / Gotchas

- **Proxy stall (mitigated)**: `claude -p` occasionally returns plain text instead of ReAct JSON. `proxy_server.py` now retries up to 2× with a JSON-forcing preamble; `run_agent.py` hard-aborts after 3 consecutive stall turns and fires a mid-run guard whenever any tools have been called but `generate_pdf_report` has not. Stall probability significantly reduced — resubmit if it still occurs.
- **Job completion signal**: All SLURM scripts emit `[result] SUCCESS` or `[result] STALLED` at the end of the log and set `EXIT_CODE=1` on stall. Quick status check: `grep "\[result\]" logs/<script>_*.out`.
- **SC1 storage issue**: INC16190297 — `fork: retry` errors in SLURM login banners. Jobs run through it but may be slower.
- **MAX_TURNS default is 20** — use `AGENT_MAX_TURNS=45` for complex multi-tool queries (repurposing, CEO briefing).
- **PDF lands in project root** — `generate_pdf_report()` writes to CWD (`/app` inside container = project root). SLURM scripts `mv` it to `reports/` at end of job.
- **clawapi needs OpenSSL 3**: Rocky Linux 8 ships 1.1. Workaround: `LD_LIBRARY_PATH=/gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery/lib` before launching clawapi.
- **cluster_scaffolds / dock_compound untested on live GPU** — both tools pass offline mocked tests but have not yet been exercised in a live agent run with clawapi running. Validate with a KRAS G12C hit-finding query.

---

## Gap Integrations (implemented)

Previously identified gaps, now closed:

- **KEGG pathway context** (`get_pathway_context(gene)` in `tools/discovery.py`)
  - Calls KEGG REST API (`https://rest.kegg.jp`) — no auth required
  - Resolves gene symbol → KEGG human gene ID via exact alias matching (avoids `/find` fuzzy-match false positives)
  - Returns up to 30 pathways in batches via `/get/hsa04010+hsa04012+...`
  - Use before `find_combinations` to check whether two drugs share pathways (shared = synergy, single = redundancy)

- **Live disease prevalence** (`get_disease_prevalence(disease)` in `tools/regulatory_competitive.py`)
  - Priority order: curated `PREVALENCE_MAP` (high confidence) → Orphanet REST API (medium, requires API key — graceful 401 fallback) → low-confidence note
  - `check_orphan_eligibility` now calls this instead of reading `PREVALENCE_MAP` directly
  - Orphanet API (`api.orphacode.org`) requires registration to activate; until then, the curated map (20+ rare diseases) + low-confidence fallback covers most cases

- **GBD / IHME** — live global burden of disease API returned HTTP 403 (access restricted). Not integrated; static `PREVALENCE_MAP` remains the fallback for non-Orphanet cases.

- **Scaffold clustering + docking** (`cluster_scaffolds`, `dock_compound` in `tools/genomeclaw.py`)
  - `cluster_scaffolds`: groups `find_hits` output by Tanimoto similarity (≥ 0.5) using clawapi `/api/screen/similar` (Morgan fingerprints). Returns cluster representatives for diverse ADMET screening.
  - `dock_compound`: geometry-based ligand pose scoring via clawapi `/api/dock/score`. Takes `ligand_smiles` + `pocket_center [x,y,z]` from `fold_target`. Scores are relative within the same pocket — use to rank-order compounds, not predict absolute affinity.
  - `find_hits` now batch-fetches canonical SMILES from ChEMBL and adds MW/LogP/TPSA/fsp3 descriptors via clawapi `/api/chem/describe`. Both enrichments are graceful no-ops when clawapi is offline.
  - Inspired by: Deep Origin/Balto article on domain-specific chemistry AI — the key insight being that general LLMs score ~25–30% on chemistry literature QA without tools, while tool-using domain agents reach 86% (LitQA2 benchmark).

---

## Security & Data Governance

- **No PHI**: Do not use Protected Health Information in any query or tool input. Use anonymized compound IDs, gene names, and indication names only.
- All data sources accessed (ClinicalTrials.gov, Open Targets, ChEMBL, Europe PMC, FDA FAERS) are public APIs. No patient-level data flows through this system.
- `knowledge_base/` files contain Roche-internal strategic analysis — treat as confidential. Do not log or transmit outside the sHPC environment.
- Audit logs in `logs/audit_*.jsonl` capture all tool inputs/outputs and are subject to the same confidentiality as the knowledge base.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-04-07 | Initial pipeline: gap analysis, competitive intel, PDF generation |
| 2026-04-07 | Added structured audit trail (`tools/audit.py`) |
| 2026-04-07 | Added Singularity container support + GPU SLURM scripts |
| 2026-04-08 | Competitive sweep (8 companies): all PDFs in `reports/` |
| 2026-04-09 | Fixed proxy auth: bind-mount `claude` binary + OpenSSL 3 into container |
| 2026-04-15 | Repurposing sweep (8 companies): `submit_repurposing_sweep.sh` |
| 2026-04-28 | Added `CLAUDE.md` |
| 2026-04-28 | Closed KEGG gap: `get_pathway_context` tool (exact alias match, batch pathway resolution) |
| 2026-04-28 | Closed prevalence gap: `get_disease_prevalence` with Orphanet fallback; refactored `check_orphan_eligibility` |
| 2026-04-28 | Added `cluster_scaffolds` (Tanimoto/Morgan via clawapi), `dock_compound` (geometry scoring via clawapi) |
| 2026-04-28 | Enriched `find_hits` with ChEMBL SMILES batch-fetch + clawapi descriptors (MW/LogP/TPSA/fsp3) |
| 2026-04-28 | 34 tools total; 282 tests passing; fixed `conftest.py` `resp.ok` latent bug |
| 2026-04-28 | Fixed proxy stall: retry loop in `proxy_server.py` (2× with JSON-forcing preamble) |
| 2026-04-28 | Fixed ReAct guard: stall counter + mid-run stall detection in `run_agent.py` |
| 2026-04-28 | Added `[result] SUCCESS/STALLED` signal to all SLURM scripts; `EXIT_CODE=1` on stall |
| 2026-04-28 | 6 new proxy retry integration tests; 288 tests total |
| 2026-04-28 | Repurposing sweep 8/8 confirmed; root PDFs cleaned up |
