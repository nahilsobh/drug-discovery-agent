import requests
import time
import json

# Roche 2026 Strategic Portfolio - Verified March 2026
portfolio = [
    {"name": "Giredestrant", "alias": "RG6171", "id": "ENSG00000091831"},
    {"name": "Trontinemab", "alias": "RG6102", "id": "ENSG00000154277"},
    {"name": "CT-388", "alias": "RG6640", "id": "ENSG00000112164"},
    {"name": "NXT007", "alias": "RG6512", "id": "ENSG00000185010"},
    {"name": "Fenebrutinib", "alias": "RG6046", "id": "ENSG00000165816"},
    {"name": "Inavolisib", "alias": "RG6114", "id": "ENSG00000121879"},
    {"name": "Divarasib", "alias": "RG6330", "id": "ENSG00000133703"},
    {"name": "Zilebesiran", "alias": "ALN-AGT", "id": "ENSG00000135744"},
    {"name": "Crovalimab", "alias": "RG6107", "id": "ENSG00000125730"},
    {"name": "Tiragolumab", "alias": "RG6058", "id": "ENSG00000163932"},
    {"name": "Gazyva", "alias": "RG7159", "id": "ENSG00000156738"},
    {"name": "Susvimo", "alias": "RG6321", "id": "ENSG00000112715"},
    {"name": "RVT-3101", "alias": "RG6633", "id": "ENSG00000181138"},
    {"name": "Prasinezumab", "alias": "RG7935", "id": "ENSG00000145335"},
    {"name": "Vamikibart", "alias": "RG6179", "id": "ENSG00000136244"},
    {"name": "Cevostamab", "alias": "RG6160", "id": "ENSG00000143549"},
    {"name": "Columvi", "alias": "RG6026", "id": "ENSG00000156738"},
    {"name": "Lunsumio", "alias": "RG7828", "id": "ENSG00000156738"},
    {"name": "Astegolimab", "alias": "RG6149", "id": "ENSG00000115602"},
    {"name": "Satralizumab", "alias": "RG6168", "id": "ENSG00000160359"}
]

def run_global_audit():
    print(f"🚀 [AI FACTORY] Initiating 20-by-30 Strategic Audit (Mar 2026)...")
    print(f"{'Asset':<15} | {'Primary Indication':<25} | {'Bio Score':<10} | {'Status'}")
    print("-" * 78)

    results = []
    ot_url = "https://api.platform.opentargets.org/api/v4/graphql"

    for asset in portfolio:
        # 1. Biology (Open Targets)
        # Using a safer query structure with aliases
        ot_query = """
        query targetDiseases($id: String!) {
          target(ensemblId: $id) {
            associatedDiseases(page: {size: 1, index: 0}) {
              rows {
                disease { name }
                score
              }
            }
          }
        }
        """
        try:
            r = requests.post(ot_url, json={"query": ot_query, "variables": {"id": asset['id']}}, timeout=10)
            data = r.json()
            
            if 'errors' in data or 'data' not in data or not data['data']['target']:
                print(f"⚠️  {asset['name']:<15} | Biology Data Unavailable     | N/A        | ⚪ SKIP")
                continue
                
            lead = data['data']['target']['associatedDiseases']['rows'][0]
            disease = lead['disease']['name']
            score = lead['score']
            
            # 2. Pipeline Check (ClinicalTrials.gov Fuzzy Search)
            ct_url = f"https://clinicaltrials.gov/api/v2/studies?query.term={asset['name']} OR {asset['alias']}&pageSize=0"
            trial_count = requests.get(ct_url).json().get('totalCount', 0)
            
            status = "✅ ACTIVE" if trial_count > 0 else "🔥 GAP"
            print(f"{asset['name']:<15} | {disease[:25]:<25} | {score:<10.2f} | {status} ({trial_count} trials)")
            
            results.append({**asset, "top_disease": disease, "score": score, "trials": trial_count})
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ {asset['name']:<15} | ERROR: {str(e)[:25]}")

    with open("ROCHE_GLOBAL_AUDIT_MAR2026.json", "w") as f:
        json.dump(results, f, indent=4)
    print("\n✅ Audit Complete. Global Strategy saved to ROCHE_GLOBAL_AUDIT_MAR2026.json")

if __name__ == "__main__":
    run_global_audit()
