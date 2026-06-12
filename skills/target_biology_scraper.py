import requests

class TargetBiologyScraper:
    def __init__(self):
        self.endpoint = "https://api.platform.opentargets.org/api/v4/graphql"
        self.headers = {"User-Agent": "RedClaw-OpenClaw-PoC/2026.3"}

    def fetch_associations(self, ensembl_id):
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
        try:
            r = requests.post(self.endpoint, 
                             json={"query": query, "variables": {"ensemblId": ensembl_id}}, 
                             headers=self.headers, timeout=10)
            data = r.json().get("data", {}).get("target", {})
            if not data: return None
            return {
                "symbol": data.get("approvedSymbol"),
                "associations": [{"disease": row["disease"]["name"], "score": row["score"]} 
                               for row in data["associatedDiseases"]["rows"]]
            }
        except Exception as e:
            print(f"❌ Scraper Error: {e}")
            return None
