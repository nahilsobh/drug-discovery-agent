import requests
import json
import time

# Roche and Genentech are the same company — treat as one entity
SPONSORS = ["Hoffmann-La Roche", "Genentech, Inc."]

CT_URL = "https://clinicaltrials.gov/api/v2/studies"
OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"

# RG / RO / GDC aliases are the fingerprint of a proprietary Roche/Genentech compound
PROPRIETARY_PREFIXES = ("RG", "RO", "GDC", "MTIG", "NXT", "RVT")

# Common noise: chemotherapy partners, comparators, placebos
NOISE_KEYWORDS = {
    "placebo", "paclitaxel", "carboplatin", "cisplatin", "docetaxel",
    "gemcitabine", "oxaliplatin", "capecitabine", "doxorubicin", "vincristine",
    "cyclophosphamide", "pemetrexed", "etoposide", "fluorouracil", "vinorelbine",
    "nab-paclitaxel", "pembrolizumab", "nivolumab", "durvalumab", "ipilimumab",
    "trastuzumab emtansine", "pertuzumab", "ramucirumab", "cetuximab",
    "bevacizumab", "axitinib", "sunitinib", "sorafenib", "cabozantinib",
    "erlotinib", "everolimus", "alpelisib", "niraparib", "olaparib",
    "palbociclib", "abemaciclib", "letrozole", "fulvestrant", "tamoxifen",
    "exemestane", "enzalutamide", "lenalidomide", "pomalidomide", "daratumumab",
    "venetoclax", "rituximab", "tocilizumab", "metformin", "dexamethasone",
    "methylprednisolone", "prednisone", "bupropion", "omeprazole", "midazolam",
    "acetaminophen", "diphenhydramine", "tacrolimus", "ranibizumab",
    "teriflunomide", "fingolimod", "rucaparib", "tirzepatide", "lurbinectedin",
}

OT_DRUG_QUERY = """
query drugLookup($name: String!) {
  search(queryString: $name, entityNames: ["drug"], page: {index: 0, size: 1}) {
    hits {
      object {
        ... on Drug {
          name
          linkedTargets {
            rows {
              approvedSymbol
              id
            }
          }
        }
      }
    }
  }
}
"""


def fetch_all_roche_studies():
    """Page through all Hoffmann-La Roche + Genentech studies on ClinicalTrials.gov."""
    studies = []
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)
    params = {"filter.advanced": sponsor_filter, "pageSize": 100}

    page = 1
    while True:
        print(f"  Fetching page {page}...", end=" ", flush=True)
        r = requests.get(CT_URL, params=params, timeout=20)
        data = r.json()
        batch = data.get("studies", [])
        studies.extend(batch)
        print(f"{len(batch)} studies")

        token = data.get("nextPageToken")
        if not token or not batch:
            break
        params["pageToken"] = token
        page += 1
        time.sleep(0.3)

    return studies


def is_proprietary(name, aliases):
    """Return True if this intervention looks like a proprietary Roche/Genentech drug."""
    name_lower = name.lower().strip()

    # Reject obvious noise
    if any(noise in name_lower for noise in NOISE_KEYWORDS):
        return False
    if "matching" in name_lower or "matched" in name_lower:
        return False

    # Accept if it has a known proprietary alias prefix
    for alias in aliases:
        if any(alias.upper().startswith(p) for p in PROPRIETARY_PREFIXES):
            return True

    # Accept if the name itself starts with a proprietary prefix
    if any(name.upper().startswith(p) for p in PROPRIETARY_PREFIXES):
        return True

    return False


def extract_drugs(studies):
    """Extract unique proprietary drug candidates from study interventions."""
    seen = {}  # name -> best alias

    for study in studies:
        arms = (study.get("protocolSection", {})
                     .get("armsInterventionsModule", {})
                     .get("interventions", []))
        for arm in arms:
            if arm.get("type") != "DRUG":
                continue

            name = arm.get("name", "").strip()
            aliases = arm.get("otherNames", [])

            if not name or not is_proprietary(name, aliases):
                continue

            # Prefer the entry that has the most informative alias
            rg_alias = next(
                (a for a in aliases if any(a.upper().startswith(p) for p in PROPRIETARY_PREFIXES)),
                None
            )
            if name not in seen or (rg_alias and not seen[name]):
                seen[name] = rg_alias

    return seen  # {drug_name: alias_or_None}


def lookup_ensembl(drug_name):
    """Query Open Targets to find the primary target Ensembl ID for a drug."""
    try:
        r = requests.post(
            OT_URL,
            json={"query": OT_DRUG_QUERY, "variables": {"name": drug_name}},
            timeout=10
        )
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if hits:
            targets = hits[0].get("object", {}).get("linkedTargets", {}).get("rows", [])
            if targets:
                return targets[0]["id"], targets[0]["approvedSymbol"]
    except Exception:
        pass
    return None, None


def run():
    print("=" * 60)
    print("ROCHE / GENENTECH ASSET DISCOVERY")
    print("Sources: ClinicalTrials.gov + Open Targets")
    print("=" * 60)

    # Step 1: Fetch all studies
    print("\n[1/3] Fetching all Roche + Genentech studies from ClinicalTrials.gov...")
    studies = fetch_all_roche_studies()
    print(f"      Total studies fetched: {len(studies)}")

    # Step 2: Extract proprietary drugs
    print("\n[2/3] Extracting proprietary drug candidates...")
    drugs = extract_drugs(studies)
    print(f"      Candidates found: {len(drugs)}")

    # Step 3: Map each drug to Ensembl ID via Open Targets
    print("\n[3/3] Mapping drugs to Ensembl IDs via Open Targets...\n")
    assets = []
    mapped, unmapped = 0, 0

    for name, alias in sorted(drugs.items()):
        ensembl_id, symbol = lookup_ensembl(name)
        status = f"✅ {symbol} ({ensembl_id})" if ensembl_id else "⚠️  not in Open Targets"
        alias_str = f" [{alias}]" if alias else ""
        print(f"  {name}{alias_str:<30} → {status}")

        entry = {"name": name}
        if alias:
            entry["alias"] = alias
        if ensembl_id:
            entry["id"] = ensembl_id
            entry["symbol"] = symbol
            mapped += 1
        else:
            unmapped += 1
        assets.append(entry)
        time.sleep(0.2)

    # Save
    output = {"assets": assets, "source": "ClinicalTrials.gov + Open Targets", "date": "2026-03"}
    out_path = "knowledge_base/roche_pipeline.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  Total assets discovered : {len(assets)}")
    print(f"  Mapped to Ensembl ID    : {mapped}")
    print(f"  Not in Open Targets     : {unmapped} (early-stage / acquisitions)")
    print(f"  Saved to                : {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    run()
