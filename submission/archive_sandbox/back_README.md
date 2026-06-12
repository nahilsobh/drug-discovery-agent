# 🧬 Project Connective Tissue: RedClaw AI Factory Discovery Engine
**Status:** PoC v1.25 (Functional Sandbox)  
**Objective:** Autonomous Indication Expansion for RedClaw NMEs (2026-2030 Strategy).

## 🚀 Overview
This OpenClaw-based agent bridges the gap between **Diagnostics** (biomarker evidence) and **Pharma** (clinical pipeline). It autonomously identifies "Strategic Gaps" where high-confidence biological evidence exists but no RedClaw clinical trial is active.

## 🏗️ Architecture
- **Orchestrator (`start_agent.py`)**: The central reasoning loop.
- **Skills (`skills/`)**: Hardened Python modules for GraphQL genomic scraping.
- **Stubs (`internal_stubs/`)**: Mocked internal pipeline data (Navify/Clinical Commons).
- **Reports (`CEO_Insight_Report.md`)**: Automated executive intelligence.

## 🛠️ Setup & Execution
```bash
# Initialize Sandbox
mkdir -p internal_stubs skills redclaw_poc_data
python3 -m venv venv && source venv/bin/activate
pip install requests

# Run Discovery Loop
python3 start_agent.py

## Final PoC Validation (Mar 22, 2026)
* **Status**: PASS
* **Discovery**: FOXA1/Prostate Adenocarcinoma
* **Competitive Alert**: Eli Lilly Phase II detected.
* **Regulatory Advice**: FDA rPFS endpoint synced.
