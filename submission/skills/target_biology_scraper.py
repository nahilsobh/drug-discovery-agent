import requests
import json

class TargetBiologyScraper:
    def __init__(self):
        self.endpoint = "https://api.platform.opentargets.org/api/v4/graphql"

    def fetch_associations(self, ensembl_id):
        # FIX: The variables must be a dictionary, and the key must match the GraphQL query ($ensemblId)
        query = """
        query target($ensemblId: String!) {
          target(ensemblId: $ensemblId) {
            approvedSymbol
            associatedDiseases(page: {index: 0, size: 5}) {
              rows {
                disease { name }
                score
              }
            }
          }
        }
        """
        variables = {"ensemblId": ensembl_id}
        
        try:
            # Adding a User-Agent header makes the agent look like a real browser
            headers = {"User-Agent": "Roche-OpenClaw-PoC/1.3"}
            r = requests.post(self.endpoint, json={"query": query, "variables": variables}, headers=headers, timeout=10)
            
            if r.status_code != 200:
                print(f"❌ API Error: {r.status_code}")
                return None

            data = r.json()
            target_info = data.get("data", {}).get("target", {})
            
            if not target_info:
                print(f"⚠️ No data found for {ensembl_id}")
                return None

            return {
                "symbol": target_info.get("approvedSymbol", "Unknown"),
                "associations": [{"disease": row["disease"]["name"], "score": row["score"]} 
                               for row in target_info.get("associatedDiseases", {}).get("rows", [])]
            }
        except Exception as e:
            print(f"❌ Connection Failed: {e}")
            return None
