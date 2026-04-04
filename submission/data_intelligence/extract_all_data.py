import requests
import json

def print_header(text):
    print(f"\n{'='*60}\n{text}\n{'='*60}")

def extract_data():
    # 1. Open Targets (Genomic Associations)
    print_header("1. OPEN TARGETS: FETCHING DISEASE ASSOCIATIONS")
    ot_url = "https://api.platform.opentargets.org/api/v4/graphql"
    ot_query = """
    query associatedDiseases($ensemblId: String!) {
      target(ensemblId: $ensemblId) {
        approvedSymbol
        associatedDiseases(page: {index: 0, size: 3}) {
          rows {
            disease { name }
            score
          }
        }
      }
    }
    """
    # ESR1 Ensembl ID (ENSG00000091831)
    variables = {"ensemblId": "ENSG00000091831"} 
    try:
        r = requests.post(ot_url, json={"query": ot_query, "variables": variables}, timeout=10)
        data = r.json()['data']['target']
        print(f"Target: {data['approvedSymbol']}")
        for row in data['associatedDiseases']['rows']:
            print(f" -> Disease: {row['disease']['name']} | Score: {row['score']:.4f}")
    except Exception as e:
        print(f"❌ Open Targets Error: {e}")

    # 2. ClinicalTrials.gov (Robust Pipeline Audit)
    print_header("2. CLINICALTRIALS.GOV: AUDITING ROCHE TRIALS")
    # FIX: Using 'Hoffmann-La Roche' ensures we catch the global legal entity trials
    ct_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.cond": "breast cancer",
        "query.term": "Hoffmann-La Roche",
        "pageSize": 2
    }
    try:
        r = requests.get(ct_url, params=params, timeout=10)
        studies = r.json().get('studies', [])
        for s in studies:
            nct = s['protocolSection']['identificationModule']['nctId']
            title = s['protocolSection']['identificationModule']['briefTitle']
            status = s['protocolSection']['statusModule']['overallStatus']
            print(f" -> [{nct}] {status}: {title[:55]}...")
    except Exception as e:
        print(f"❌ ClinicalTrials Error: {e}")

    # 3. PubChem (Molecular Properties)
    print_header("3. PUBCHEM: EXTRACTING CHEMICAL DATA")
    pc_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/aspirin/property/MolecularWeight,XLogP/JSON"
    try:
        r = requests.get(pc_url, timeout=10)
        prop = r.json()['PropertyTable']['Properties'][0]
        print(f"Compound: Aspirin | MW: {prop['MolecularWeight']} | XLogP: {prop.get('XLogP', 'N/A')}")
    except Exception as e:
        print(f"❌ PubChem Error: {e}")

    # 4. ChEMBL (Bioactivity Data)
    print_header("4. ChEMBL: FETCHING MOLECULAR METADATA")
    # Fetching details for Aspirin (CHEMBL25) using the specific ID endpoint
    ch_url = "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL25.json"
    try:
        r = requests.get(ch_url, timeout=10)
        mol = r.json()
        print(f"ChEMBL ID: {mol['molecule_chembl_id']} | Type: {mol['molecule_type']}")
        print(f"Max Phase: {mol['max_phase']} | First Approval: {mol['first_approval']}")
    except Exception as e:
        print(f"❌ ChEMBL Error: {e}")

    # 5. openFDA (Regulatory Labeling)
    print_header("5. openFDA: PULLING REGULATORY LABELS")
    # FIX: Using openfda.generic_name to bypass 404 on brand-specific searches
    fda_url = "https://api.fda.gov/drug/label.json?search=openfda.generic_name:aspirin&limit=1"
    try:
        r = requests.get(fda_url, timeout=10)
        label = r.json()['results'][0]
        brand = label.get('openfda', {}).get('brand_name', ['Generic/Unknown'])[0]
        # Purpose field can vary by label; we pull the first available section
        purpose = label.get('purpose', label.get('indications_and_usage', ['N/A']))[0]
        print(f"Brand: {brand} | Purpose: {purpose[:100]}...")
    except Exception as e:
        print(f"❌ openFDA Error: {e}")

if __name__ == "__main__":
    extract_data()
