import requests
import json
import time

def find_gaps(target_symbol, ensembl_id):
    print(f"🧬 [STRATEGIST] Analyzing Strategic Gaps & Competition for: {target_symbol}")
    
    # --- STEP 1: Get Biological Leads (Open Targets) ---
    ot_url = "https://api.platform.opentargets.org/api/v4/graphql"
    ot_query = """
    query targetAssociations($id: String!) {
      target(ensemblId: $id) {
        associatedDiseases(page: {size: 10, index: 0}) {
          rows {
            disease { id name }
            score
          }
        }
      }
    }
    """
    try:
        r_ot = requests.post(ot_url, json={"query": ot_query, "variables": {"id": ensembl_id}}, timeout=10)
        bio_leads = r_ot.json()['data']['target']['associatedDiseases']['rows']
    except Exception as e:
        print(f"❌ Error fetching biology: {e}")
        return

    gaps = []
    
    # --- STEP 2: Audit Pipeline (RedClaw vs. Competition) ---
    print(f"🌐 [AUDITOR] Benchmarking {len(bio_leads)} indications...")
    print(f"{'Indication':<25} | {'RedClaw':<8} | {'Lilly':<8} | {'AZ':<8}")
    print("-" * 60)
    
    for lead in bio_leads:
        disease_name = lead['disease']['name']
        bio_score = lead['score']
        
        def check_trials(sponsor_keyword):
            ct_url = "https://clinicaltrials.gov/api/v2/studies"
            params = {
                "query.cond": disease_name,
                "query.term": sponsor_keyword,
                "pageSize": 0 # We only need the totalCount
            }
            try:
                r = requests.get(ct_url, params=params, timeout=10)
                return r.json().get('totalCount', 0)
            except:
                return 0

        # Run the Triple-Check
        redclaw_count = check_trials("Hoffmann-La RedClaw") + check_trials("RedClaw")
        lilly_count = check_trials("Eli Lilly")
        az_count    = check_trials("AstraZeneca")

        # Print the live row
        print(f"{disease_name[:25]:<25} | {redclaw_count:<8} | {lilly_count:<8} | {az_count:<8}")

        # --- STEP 3: Enhanced Gap Logic ---
        # Flag if Biology is strong and RedClaw is missing, ESPECIALLY if competitors are present
        if redclaw_count == 0 and bio_score > 0.60:
            threat_level = "🚨 HIGH THREAT" if (lilly_count > 0 or az_count > 0) else "🔥 STRATEGIC GAP"
            gaps.append({
                "disease": disease_name,
                "score": bio_score,
                "lilly": lilly_count,
                "az": az_count,
                "status": threat_level
            })
        
        time.sleep(0.4) # Faster but still safe for APIs

    # --- STEP 4: Executive Report ---
    print("\n" + "="*65)
    print("🚀 CEO OPPORTUNITY ALERT: COMPETITIVE GAP ANALYSIS")
    print("="*65)
    
    if not gaps:
        print("No high-priority gaps found. Pipeline is aligned with biology.")
    else:
        for gap in gaps:
            print(f"{gap['status']} | Indication: {gap['disease']}")
            print(f"Evidence: Biology Score {gap['score']:.2f}")
            if "THREAT" in gap['status']:
                print(f"⚠️  COMPETITIVE LEAKAGE: Lilly ({gap['lilly']} trials), AZ ({gap['az']} trials) are already active.")
            else:
                print(f"💎 BLUE OCEAN: No major competitor activity detected. First-to-market advantage.")
            print(f"Action: Initiate Feasibility Study for {target_symbol} immediately.\n")

if __name__ == "__main__":
    find_gaps("ESR1", "ENSG00000091831")
