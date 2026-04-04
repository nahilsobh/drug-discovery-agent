import requests
import xml.etree.ElementTree as ET

class LiteratureSpecialist:
    def __init__(self):
        self.epmc_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        self.ct_url = "https://clinicaltrials.gov/api/v2/studies"

    def discover_breakthroughs(self, drug_name, indication, limit=1):
        """Fetches linked data from Peer-Review, Pre-prints, and Clinical Registries."""
        evidence = {"peer_review": [], "preprints": [], "clinical_trials": []}
        query = f'("{drug_name}" AND "{indication}")'

        # 1. Peer-Reviewed (Europe PMC)
        try:
            params = {'query': query, 'format': 'json', 'pageSize': limit, 'resultType': 'lite'}
            r = requests.get(self.epmc_url, params=params, timeout=5)
            if r.status_code == 200:
                results = r.json().get('resultList', {}).get('result', [])
                for p in results:
                    evidence["peer_review"].append({
                        "title": p.get('title'), 
                        "doi": p.get('doi', 'N/A'),
                        "source": "Europe PMC"
                    })
        except: pass

        # 2. 2026 Pre-prints (ArXiv REST API)
        try:
            ar_url = f"http://export.arxiv.org/api/query?search_query=all:{drug_name}+AND+all:{indication}&max_results={limit}"
            r = requests.get(ar_url, timeout=5)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                # ArXiv uses Atom namespace
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                for entry in root.findall('atom:entry', ns):
                    title = entry.find('atom:title', ns).text.strip()
                    evidence["preprints"].append({"title": title, "source": "ArXiv"})
        except: pass

        # 3. Clinical Trial Protocol (ClinicalTrials.gov API v2)
        try:
            params = {"query.term": f"{drug_name} {indication}", "pageSize": 1}
            r = requests.get(self.ct_url, params=params, timeout=5)
            if r.status_code == 200:
                studies = r.json().get('studies', [])
                for s in studies:
                    id_mod = s.get('protocolSection', {}).get('identificationModule', {})
                    stat_mod = s.get('protocolSection', {}).get('statusModule', {})
                    evidence["clinical_trials"].append({
                        "nct_id": id_mod.get('nctId'),
                        "title": id_mod.get('briefTitle'),
                        "status": stat_mod.get('overallStatus')
                    })
        except: pass

        return evidence
