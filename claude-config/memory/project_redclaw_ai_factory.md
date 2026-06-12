---
name: RedClaw AI Factory Project
description: 20-tool ReAct agent for pharmaceutical gap analysis + GenomeClaw integration
type: project
---

Main file: `/Users/sobhn/hk/run_agent.py` (~2,100 lines, 20 tools). GenomeClaw cloned to `/Users/sobhn/hk/genomeclaw/`.

**Why:** Strategic drug discovery agent for RedClaw portfolio. Identifies indication gaps, competitive white space, repurposing candidates, orphan opportunities.

**How to apply:** Always treat RedClaw as one entity. Tools call ClinicalTrials.gov, Open Targets GraphQL v4, Europe PMC, ArXiv, UniProt, openFDA, and GenomeClaw REST API.

**Current state (2026-03-31):**
- 20 tools implemented and syntax-verified
- GenomeClaw API running locally at `http://127.0.0.1:8083` (Boltz-1 fold validated)
- Auth updated to support ANTHROPIC_AUTH_TOKEN (subscription) + ANTHROPIC_API_KEY
- CLI binaries (clawadmet, clawdata) not yet built — need `cargo build --release`
- ESM-2 weights not downloaded — variant/score endpoints will fail until fetched
- Key OT API facts: use `drugAndClinicalCandidates` (not `knownDrugs`), `maxClinicalStage` is string ("APPROVAL" not 4), tractability labels are "Approved Drug"/"Advanced Clinical"
- GenomeClaw fold response fields: `mean_plddt` (not `plddt_mean`), `pdb` (not `pdb_str`), use `json.loads(strict=False)`
- API startup: `CLAWAPI_WEIGHTS=weights/boltz-1/boltz1.safetensors CLAWAPI_BIND=127.0.0.1:8083 PATH="$PWD/target/release:$PATH" ./target/release/clawapi &`
