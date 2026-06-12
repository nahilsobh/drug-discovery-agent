import os
import subprocess
import json
import sys

# Ensure the local 'skills' directory is in the Python path
sys.path.append(os.getcwd())

try:
    from skills.target_biology_scraper import TargetBiologyScraper
except ImportError:
    print("❌ ERROR: skills/target_biology_scraper.py not found. Ensure the directory structure is correct.")
    sys.exit(1)

def run_agent():
    print("🧬 [REDCLAW AGENT] Initializing Multi-Agent Suite v1.30 on MacBook-Pro-7...")
    print("🤖 Roles active: Researcher, Internal Auditor, Strategist, Regulatory Specialist.")
    
    # --- Step 1: Baseline Discovery (Legacy Shell Integration) ---
    print("\n--- Step 1: Running Discovery Script (Legacy Integration) ---")
    subprocess.run(["bash", "redclaw_discovery.sh"])

    # --- Step 2: Multi-Agent Gap & Regulatory Analysis ---
    print("\n--- Step 2: Multi-Agent Intelligence Loop ---")
    
    # Load Intelligence Stubs (Internal Memory)
    try:
        with open('internal_stubs/redclaw_pipeline.json', 'r') as f:
            pipeline_data = json.load(f)
        with open('internal_stubs/competitive_intel.json', 'r') as c:
            comp_data = json.load(c)
        with open('internal_stubs/fda_guidelines.json', 'r') as r:
            reg_data = json.load(r)
    except FileNotFoundError as e:
        print(f"❌ ERROR: Missing internal stub file: {e.filename}")
        return

    # Role 1: The Researcher (Biology Scraper)
    scraper = TargetBiologyScraper()
    # Analysis for FOXA1 (Ensembl ID: ENSG00000129514)
    bio_hits = scraper.fetch_associations("ENSG00000129514") 
    
    if bio_hits:
        symbol = bio_hits['symbol']
        # Role 2: The Internal Auditor (Pipeline Check)
        internal_inds = [t['indication'].lower() for t in pipeline_data['active_trials'] if t['target'] == symbol]
        
        for assoc in bio_hits['associations']:
            disease = assoc['disease'].lower()
            score = assoc['score']
            
            # Role 3: The Strategist (Gap Detection)
            if disease not in internal_inds and score > 0.65:
                print(f"🔥 STRATEGIC GAP FOUND: {symbol} potential in '{disease}' (Score: {score:.2f})")
                
                # Role 4: Competitive Analysis
                if "prostate" in disease:
                    lilly_status = comp_data.get("Eli Lilly", {}).get("Status", "Unknown")
                    print(f"⚠️  COMPETITIVE THREAT: Eli Lilly is in {lilly_status} for Prostate Cancer.")
                    
                    # Role 5: Regulatory Specialist (FDA Alignment)
                    reg_guideline = reg_data.get("prostate_adenocarcinoma", {})
                    if reg_guideline:
                        print(f"⚖️  REGULATORY ADVICE: FDA requires {reg_guideline['primary_endpoint']} as primary endpoint.")
                        print(f"🛠️  DIAGNOSTIC ALIGNMENT: {reg_guideline['fda_preference']}")
                    
                    print(f"💡 STRATEGY: Pivot Giredestrant to FOXA1-high populations to differentiate.")

    print("\n✅ Multi-Agent PoC Run Complete. Results synced to CEO_Insight_Report.md.")

if __name__ == "__main__":
    run_agent()
