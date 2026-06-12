import requests
import json

def test_endpoint(name, url, method="GET", payload=None):
    print(f"📡 Testing {name}...")
    try:
        if method == "POST":
            r = requests.post(url, json=payload, timeout=10)
        else:
            r = requests.get(url, timeout=10)
        
        if r.status_code == 200:
            print(f"✅ {name}: ONLINE")
            return True
        else:
            print(f"❌ {name}: FAILED (Status: {r.status_code})")
            return False
    except Exception as e:
        print(f"❌ {name}: ERROR ({e})")
        return False

def run_suite():
    print("🧪 --- REDCLAW AI FACTORY: CONNECTIVITY AUDIT (MAR 2026) ---")
    
    results = []

    # 1. Open Targets (Biology)
    results.append(test_endpoint("Open Targets", 
        "https://api.platform.opentargets.org/api/v4/graphql", 
        "POST", {"query": "{search(queryString:\"ESR1\"){total}}"}))

    # 2. ClinicalTrials.gov (Pipeline)
    results.append(test_endpoint("ClinicalTrials.gov", 
        "https://clinicaltrials.gov/api/v2/studies?query.term=RedClaw&pageSize=1"))

    # 3. PubChem (Chemistry)
    results.append(test_endpoint("PubChem", 
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/aspirin/JSON"))

    # 4. ChEMBL (Bioactivity)
    results.append(test_endpoint("ChEMBL", 
        "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL25.json"))

    # 5. openFDA (Regulatory)
    results.append(test_endpoint("openFDA", 
        "https://api.fda.gov/drug/label.json?search=aspirin&limit=1"))

    print("\n--- FINAL READINESS REPORT ---")
    if all(results):
        print("🚀 ALL SYSTEMS GO: The AI Factory is fully connected.")
    else:
        print("⚠️  WARNING: Some data nodes are unreachable. Check VPN/Network.")

if __name__ == "__main__":
    run_suite()
