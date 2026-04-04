"""
Roche AI Factory — Strategic Discovery Agent
ReAct loop powered by Claude + tool_use.

Usage:
    python3 run_agent.py "Find gaps in Roche's neurology pipeline"
    python3 run_agent.py "Which oncology targets have strong biology but no active Roche trial?"
"""

import sys
import json
import time
import os
import random
import datetime
import concurrent.futures
import requests
import arxiv
import anthropic
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm

# ── Session state (accumulated across all tool calls) ──────────────────────────
SESSION: dict = {
    "question":           "",
    "gaps":               [],
    "portfolio":          [],
    "combinations":       [],
    "literature":         [],
    "regulatory":         [],
    "trials":             [],
    "biology":            [],
    "arxiv_papers":       [],
    "trial_outcomes":     [],
    "repurposing":        [],
    "orphan_flags":       [],
    "protein_structures":    [],
    "competitive_signals":   [],
    "fold_results":          [],
    "variant_effects":       [],
    "admet_profiles":        [],
    "mutation_landscapes":   [],
}

# ── Constants ──────────────────────────────────────────────────────────────────

SPONSORS    = ["Hoffmann-La Roche", "Genentech, Inc."]
CT_URL      = "https://clinicaltrials.gov/api/v2/studies"
OT_URL      = "https://api.platform.opentargets.org/api/v4/graphql"
CACHE_PATH  = "knowledge_base/intelligence_cache.json"
UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/search"
OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"
MODEL       = os.environ.get("AGENT_MODEL", "claude-opus-4-6")

CLAWAPI_URL   = os.environ.get("CLAWAPI_URL", "http://127.0.0.1:8083")
GENOMECLAW_DIR = os.path.join(os.path.dirname(__file__), "genomeclaw")

COMPETITORS = [
    "AstraZeneca", "Eli Lilly", "Novartis", "Pfizer",
    "Merck", "Bristol-Myers Squibb", "AbbVie", "Johnson & Johnson",
]

ARXIV_CATS = {
    "q-bio.BM", "q-bio.GN", "q-bio.QM", "q-bio.TO", "q-bio.NC", "q-bio.OT",
    "cs.LG", "cs.AI", "stat.ML", "physics.bio-ph",
}

PREVALENCE_MAP = {
    "fanconi anemia":                {"prevalence": 1400},
    "huntington":                    {"prevalence": 30000},
    "friedreich ataxia":             {"prevalence": 15000},
    "pompe disease":                 {"prevalence": 10000},
    "gaucher disease":               {"prevalence": 20000},
    "niemann-pick":                  {"prevalence": 1800},
    "fabry disease":                 {"prevalence": 50000},
    "cystic fibrosis":               {"prevalence": 35000},
    "duchenne muscular dystrophy":   {"prevalence": 15000},
    "spinal muscular atrophy":       {"prevalence": 20000},
    "amyotrophic lateral sclerosis": {"prevalence": 16000},
    "multiple myeloma":              {"prevalence": 160000},
    "hairy cell leukemia":           {"prevalence": 15000},
    "waldenstrom macroglobulinemia": {"prevalence": 17000},
    "primary sclerosing cholangitis":{"prevalence": 30000},
    "wilson disease":                {"prevalence": 10000},
    "phenylketonuria":               {"prevalence": 14000},
    "hereditary angioedema":         {"prevalence": 10000},
    "tuberous sclerosis":            {"prevalence": 50000},
    "transthyretin amyloidosis":     {"prevalence": 50000},
}

# ── ArXiv rate-limit-safe client ───────────────────────────────────────────────
# One module-level client instance so the 3-second inter-request delay is shared
# across ALL calls in a process (each new Client() resets the internal timer).
# ArXiv's official limit: 1 request / 3 seconds. Violations risk IP blocks.
_ARXIV_CLIENT = arxiv.Client(
    page_size=100,
    delay_seconds=3.0,   # enforced by the library between pages
    num_retries=3,
)

def _arxiv_search_with_backoff(query: str, max_results: int) -> list:
    """
    Execute an ArXiv search with exponential backoff + full jitter on HTTP 429.
    Caps at 5 attempts with a maximum wait of 64 seconds.
    Uses the shared module-level client to honour the 3-second inter-request gap.
    """
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )
    base_delay = 4.0   # first retry starts at 4s (on top of the client's own 3s)
    max_delay  = 64.0
    for attempt in range(5):
        try:
            return list(_ARXIV_CLIENT.results(search))
        except arxiv.HTTPError as e:
            if e.status == 429 and attempt < 4:
                # Full-jitter exponential backoff: sleep a random fraction of cap
                cap   = min(base_delay * (2 ** attempt), max_delay)
                sleep = random.uniform(0, cap)
                print(f"[ArXiv] HTTP 429 — backoff {sleep:.1f}s (attempt {attempt + 1}/5)")
                time.sleep(sleep)
            else:
                break   # non-429 error or final attempt — give up gracefully
        except Exception:
            break
    return []


# ── Tool implementations ────────────────────────────────────────────────────────

def search_roche_trials(therapeutic_area: str, phase: str = None) -> dict:
    """Query ClinicalTrials.gov for active Roche/Genentech trials in a therapeutic area."""
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)
    params = {
        "filter.advanced": sponsor_filter,
        "query.cond": therapeutic_area,
        "pageSize": 50,
    }
    if phase:
        params["aggFilters"] = f"phase:{phase}"

    r = requests.get(CT_URL, params=params, timeout=15)
    studies = r.json().get("studies", [])

    results = []
    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        arms = proto.get("armsInterventionsModule", {}).get("interventions", [])

        drugs = [
            {"name": a.get("name"), "aliases": a.get("otherNames", [])}
            for a in arms if a.get("type") == "DRUG"
        ]
        results.append({
            "nct_id":    ident.get("nctId"),
            "title":     ident.get("briefTitle"),
            "status":    status.get("overallStatus"),
            "phase":     proto.get("designModule", {}).get("phases", []),
            "drugs":     drugs,
        })

    out = {"therapeutic_area": therapeutic_area, "trial_count": len(results), "trials": results}
    SESSION["trials"].append(out)
    return out


def get_biology(target: str) -> dict:
    """Query Open Targets for disease associations of a drug or gene target."""
    # First try as a drug name → get linked target → get associations
    drug_query = """
    query($name: String!) {
      search(queryString: $name, entityNames: ["drug"], page: {index: 0, size: 1}) {
        hits {
          object {
            ... on Drug {
              name
              linkedTargets { rows { id approvedSymbol } }
            }
          }
        }
      }
    }"""
    r = requests.post(OT_URL, json={"query": drug_query, "variables": {"name": target}}, timeout=10)
    hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    ensembl_id = None
    symbol = None
    if hits:
        rows = hits[0].get("object", {}).get("linkedTargets", {}).get("rows", [])
        if rows:
            ensembl_id = rows[0]["id"]
            symbol = rows[0]["approvedSymbol"]

    # If not found as drug, try as gene symbol
    if not ensembl_id:
        gene_query = """
        query($name: String!) {
          search(queryString: $name, entityNames: ["target"], page: {index: 0, size: 1}) {
            hits { id name }
          }
        }"""
        r = requests.post(OT_URL, json={"query": gene_query, "variables": {"name": target}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if hits:
            ensembl_id = hits[0]["id"]
            symbol = hits[0]["name"]

    if not ensembl_id:
        return {"error": f"Target '{target}' not found in Open Targets"}

    # Get top disease associations
    assoc_query = """
    query($id: String!) {
      target(ensemblId: $id) {
        approvedSymbol
        associatedDiseases(page: {index: 0, size: 10}) {
          rows { disease { name id } score }
        }
      }
    }"""
    r = requests.post(OT_URL, json={"query": assoc_query, "variables": {"id": ensembl_id}}, timeout=10)
    data = r.json().get("data", {}).get("target", {})
    rows = data.get("associatedDiseases", {}).get("rows", [])

    associations = [
        {"disease": row["disease"]["name"], "score": round(row["score"], 3)}
        for row in rows
    ]

    out = {
        "target":       target,
        "ensembl_id":   ensembl_id,
        "symbol":       symbol or data.get("approvedSymbol"),
        "associations": associations,
    }
    SESSION["biology"].append(out)
    return out


def check_competitor_trials(disease: str, competitor: str) -> dict:
    """Check how many trials a competitor has for a given disease."""
    COMPETITOR_MAP = {
        "astrazeneca": "AstraZeneca",
        "lilly":       "Eli Lilly",
        "eli lilly":   "Eli Lilly",
        "novartis":    "Novartis",
        "pfizer":      "Pfizer",
        "merck":       "Merck",
        "bms":         "Bristol-Myers Squibb",
        "abbvie":      "AbbVie",
    }
    sponsor_name = COMPETITOR_MAP.get(competitor.lower(), competitor)
    params = {
        "query.cond": disease,
        "query.term": sponsor_name,
        "pageSize":   10,
    }
    r = requests.get(CT_URL, params=params, timeout=10)
    studies = r.json().get("studies", [])
    return {
        "competitor":    sponsor_name,
        "disease":       disease,
        "trial_count":   len(studies),
        "trials":        [s.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
                          for s in studies],
    }


def find_gaps(therapeutic_area: str, min_bio_score: float = 0.60) -> dict:
    """
    Core gap analysis: cross-references Open Targets biology with Roche's
    ClinicalTrials.gov pipeline to surface high-evidence, zero-trial opportunities.
    """
    # 1. Get Roche trials in this area
    trial_data = search_roche_trials(therapeutic_area)
    roche_diseases = set()
    for trial in trial_data["trials"]:
        title_lower = trial.get("title", "").lower()
        roche_diseases.add(title_lower)

    # 2. Get top biological targets for the therapeutic area
    target_query = """
    query($area: String!) {
      search(queryString: $area, entityNames: ["disease"], page: {index: 0, size: 5}) {
        hits {
          id
          name
          object {
            ... on Disease {
              name
              associatedTargets(page: {index: 0, size: 10}) {
                rows { target { approvedSymbol id } score }
              }
            }
          }
        }
      }
    }"""
    r = requests.post(OT_URL, json={"query": target_query, "variables": {"area": therapeutic_area}}, timeout=10)
    disease_hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    gaps = []
    for hit in disease_hits:
        disease_name = hit.get("name", "")
        assoc_targets = hit.get("object", {}).get("associatedTargets", {}).get("rows", [])

        for row in assoc_targets:
            score = row.get("score", 0)
            if score < min_bio_score:
                continue
            symbol = row["target"]["approvedSymbol"]
            ensembl = row["target"]["id"]

            # Check if Roche has a trial for this target
            ct_params = {
                "filter.advanced": ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS),
                "query.term": symbol,
                "pageSize": 5,
            }
            ct_r = requests.get(CT_URL, params=ct_params, timeout=10)
            roche_trials = ct_r.json().get("studies", [])
            time.sleep(0.2)

            if not roche_trials:
                gaps.append({
                    "disease":     disease_name,
                    "target":      symbol,
                    "ensembl_id":  ensembl,
                    "bio_score":   round(score, 3),
                    "roche_trials": 0,
                    "status":      "STRATEGIC GAP",
                })

    out = {
        "therapeutic_area": therapeutic_area,
        "roche_active_trials": trial_data["trial_count"],
        "gaps_found": len(gaps),
        "gaps": sorted(gaps, key=lambda x: x["bio_score"], reverse=True),
    }
    SESSION["gaps"].extend(out["gaps"])
    return out


def _load_pipeline_enrichment() -> dict:
    """Load pipeline_enrichment.json as a name→metadata lookup (case-insensitive)."""
    try:
        with open("knowledge_base/pipeline_enrichment.json") as f:
            raw = json.load(f)
        return {k.lower(): v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def rank_portfolio(assets: list = None) -> dict:
    """
    Score every asset in the Roche portfolio by composite opportunity:
    bio_score × unexplored_indications × (1 + competitive_vacuum).
    Loads assets from roche_pipeline.json if none provided.
    Merges pipeline_enrichment.json for phase/TA/indication context.
    """
    if not assets:
        try:
            with open("knowledge_base/roche_pipeline.json") as f:
                data = json.load(f)
            assets = data if isinstance(data, list) else data.get("assets", [])
        except Exception:
            return {"error": "Could not load knowledge_base/roche_pipeline.json"}

    enrichment = _load_pipeline_enrichment()

    ranked = []
    for asset in assets:
        name = asset.get("name", "Unknown")
        ensembl = asset.get("id") or asset.get("ensembl_id")
        if not ensembl:
            continue

        # Biology: top disease score
        try:
            q = """query($id:String!){target(ensemblId:$id){
                     associatedDiseases(page:{index:0,size:10}){
                       rows{disease{name} score}}}}"""
            r = requests.post(OT_URL, json={"query": q, "variables": {"id": ensembl}}, timeout=10)
            rows = r.json().get("data", {}).get("target", {}).get("associatedDiseases", {}).get("rows", [])
        except Exception:
            rows = []

        if not rows:
            continue

        top_score   = rows[0]["score"]
        top_disease = rows[0]["disease"]["name"]

        # Count indications with no Roche trial
        unexplored = 0
        for row in rows:
            if row["score"] < 0.5:
                continue
            disease = row["disease"]["name"]
            ct_params = {
                "filter.advanced": ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS),
                "query.term": disease,
                "pageSize": 1,
            }
            ct_r = requests.get(CT_URL, params=ct_params, timeout=8)
            if not ct_r.json().get("studies"):
                unexplored += 1
            time.sleep(0.15)

        # Competitive vacuum: 1 if no major competitor in top indication, 0 otherwise
        comp_params = {"query.cond": top_disease, "query.term": "AstraZeneca OR Eli Lilly OR Novartis", "pageSize": 1}
        comp_r = requests.get(CT_URL, params=comp_params, timeout=8)
        vacuum = 0 if comp_r.json().get("studies") else 1

        composite = round(top_score * unexplored * (1 + vacuum), 3)

        # Merge enrichment metadata
        enr = enrichment.get(name.lower(), {})
        # Also try alias match
        if not enr:
            alias_val = asset.get("alias", "")
            for alias_part in alias_val.replace(";", ",").split(","):
                enr = enrichment.get(alias_part.strip().lower(), {})
                if enr:
                    break

        entry = {
            "name":            name,
            "top_disease":     top_disease,
            "bio_score":       round(top_score, 3),
            "unexplored_inds": unexplored,
            "competitive_vacuum": bool(vacuum),
            "composite_score": composite,
        }
        if enr:
            entry["phase"]             = enr.get("phase")
            entry["status"]            = enr.get("status")
            entry["therapeutic_area"]  = enr.get("therapeutic_area")
            entry["primary_indication"] = enr.get("primary_indication")
            entry["modality"]          = enr.get("modality")
            entry["mechanism"]         = enr.get("mechanism")
            entry["safety_signals"]    = enr.get("safety_signals")

        ranked.append(entry)
        time.sleep(0.2)

    ranked.sort(key=lambda x: x["composite_score"], reverse=True)
    out = {"ranked_assets": ranked, "total": len(ranked)}
    SESSION["portfolio"] = ranked
    return out


def find_combinations(disease: str) -> dict:
    """
    Find Roche drugs that target complementary pathways in the same disease.
    Returns pairs of drugs that appear in combination arms or share a disease indication.
    """
    sponsor_filter = ' OR '.join(f'AREA[LeadSponsorName]"{s}"' for s in SPONSORS)
    params = {"filter.advanced": sponsor_filter, "query.cond": disease, "pageSize": 50}
    r = requests.get(CT_URL, params=params, timeout=15)
    studies = r.json().get("studies", [])

    # Collect drugs per study
    combinations = []
    all_drugs = {}  # drug_name → set of diseases from OT

    for study in studies:
        arms = study.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions", [])
        drug_names = [
            a["name"] for a in arms
            if a.get("type") == "DRUG" and any(
                a["name"].upper().startswith(p) or
                any(o.upper().startswith(p) for o in a.get("otherNames", []))
                for p in ("RG", "RO", "GDC", "MTIG")
            )
        ]
        if len(drug_names) >= 2:
            nct = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            combinations.append({"nct_id": nct, "drugs": drug_names})

    # Deduplicate drug pairs
    seen_pairs = set()
    unique_pairs = []
    for combo in combinations:
        drugs = sorted(combo["drugs"])
        for i in range(len(drugs)):
            for j in range(i + 1, len(drugs)):
                pair = (drugs[i], drugs[j])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    unique_pairs.append({"drug_a": pair[0], "drug_b": pair[1], "nct_id": combo["nct_id"]})

    out = {
        "disease":      disease,
        "combo_trials": len(combinations),
        "unique_pairs": unique_pairs[:20],
    }
    SESSION["combinations"].append(out)
    return out


def scan_arxiv(target: str, disease: str, max_results: int = 10, min_year: int = None) -> dict:
    """
    Search ArXiv for preprints linking a drug/gene target to a disease.
    Surfaces science 6-18 months before peer review.
    min_year: optional integer (e.g. 2024) to filter out older papers.
    """
    # Simple keyword query (no exact-phrase quoting) for broader recall
    query   = f"{target} {disease}"
    results = _arxiv_search_with_backoff(query, max_results)
    papers  = []
    for result in results:
        cats = set(result.categories)
        submitted = result.published.strftime("%Y-%m-%d") if result.published else ""
        if min_year and result.published and result.published.year < min_year:
            continue
        papers.append({
            "arxiv_id":   result.entry_id.split("/")[-1],
            "title":      result.title,
            "authors":    [str(a) for a in result.authors[:5]],
            "abstract":   result.summary[:400],
            "submitted":  submitted,
            "categories": list(cats),
            "pdf_url":    result.pdf_url,
            "source":     "ArXiv",
        })

    # Prefer biomedical/ML categories
    preferred = [p for p in papers if any(c in ARXIV_CATS for c in p["categories"])]
    out_papers = preferred if preferred else papers

    out = {
        "target":       target,
        "disease":      disease,
        "min_year":     min_year,
        "papers_found": len(out_papers),
        "papers":       out_papers,
    }
    SESSION["arxiv_papers"].extend(out_papers)
    return out


def scan_literature(target: str, disease: str, max_results: int = 10, min_year: int = None) -> dict:
    """
    Search Europe PMC + ArXiv in parallel for recent publications linking a target to a disease.
    Results are merged and sorted by date descending.
    min_year: optional integer (e.g. 2024) — filters out papers published before this year.
    """
    min_date_str = f"{min_year}-01-01" if min_year else None

    def fetch_epmc():
        query = f'"{target}" AND "{disease}"'
        if min_year:
            query += f' AND FIRST_PDATE:[{min_year}-01-01 TO 9999-12-31]'
        url   = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query":      query,
            "format":     "json",
            "pageSize":   max_results,
            "sort":       "date desc",
            "resultType": "core",
        }
        try:
            r = requests.get(url, params=params, timeout=12)
            results = r.json().get("resultList", {}).get("result", [])
        except Exception:
            return []
        return [
            {
                "title":   p.get("title", "").strip(),
                "journal": p.get("journalTitle", ""),
                "date":    p.get("firstPublicationDate", ""),
                "pmid":    p.get("pmid", ""),
                "doi":     p.get("doi", ""),
                "source":  "EuropePMC",
            }
            for p in results
            if not min_date_str or p.get("firstPublicationDate", "") >= min_date_str
        ]

    def fetch_arxiv_local():
        results = _arxiv_search_with_backoff(f"{target} {disease}", max_results)
        papers  = []
        for result in results:
            cats = set(result.categories)
            if min_year and result.published and result.published.year < min_year:
                continue
            if any(c in ARXIV_CATS for c in cats):
                papers.append({
                    "title":   result.title,
                    "journal": "ArXiv preprint",
                    "date":    result.published.strftime("%Y-%m-%d") if result.published else "",
                    "doi":     result.doi or result.entry_id,
                    "pdf_url": result.pdf_url,
                    "source":  "ArXiv",
                })
        return papers

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_epmc  = executor.submit(fetch_epmc)
        fut_arxiv = executor.submit(fetch_arxiv_local)
        epmc_papers  = fut_epmc.result()
        arxiv_papers = fut_arxiv.result()

    all_papers = sorted(
        epmc_papers + arxiv_papers,
        key=lambda x: x.get("date", ""),
        reverse=True,
    )[:max_results]

    out = {
        "target":       target,
        "disease":      disease,
        "min_year":     min_year,
        "papers_found": len(all_papers),
        "papers":       all_papers,
    }
    SESSION["literature"].append(out)
    return out


def find_shared_targets(disease1: str, disease2: str, min_score: float = 0.70) -> dict:
    """
    Find gene targets shared between two diseases with high Open Targets confidence.
    Answers: "What targets are shared between Alzheimer's and Parkinson's with bio score > 0.7?"
    Uses OT search + disease association queries run in parallel.
    """
    def resolve_disease(name):
        q = """query($q: String!) {
          search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 1}) {
            hits { id name }
          }
        }"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"q": name}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        return (hits[0]["id"], hits[0]["name"]) if hits else (None, name)

    def fetch_targets(disease_id, min_s):
        q = """query($id: String!, $size: Int!) {
          disease(efoId: $id) {
            associatedTargets(page: {index: 0, size: $size}) {
              rows {
                target { id approvedSymbol approvedName }
                score
              }
            }
          }
        }"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"id": disease_id, "size": 200}}, timeout=15)
        rows = (r.json().get("data", {}).get("disease", {})
                        .get("associatedTargets", {}).get("rows", []))
        return {
            row["target"]["approvedSymbol"]: {
                "ensembl_id":    row["target"]["id"],
                "gene_name":     row["target"]["approvedName"],
                "score":         round(row["score"], 3),
            }
            for row in rows if row.get("score", 0) >= min_s
        }

    # Resolve both diseases and fetch targets in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(resolve_disease, disease1)
        f2 = ex.submit(resolve_disease, disease2)
        id1, name1 = f1.result()
        id2, name2 = f2.result()

    if not id1 or not id2:
        missing = disease1 if not id1 else disease2
        return {"status": "error", "note": f"Could not resolve disease: {missing}"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        t1 = ex.submit(fetch_targets, id1, min_score)
        t2 = ex.submit(fetch_targets, id2, min_score)
        targets1 = t1.result()
        targets2 = t2.result()

    # Intersect
    shared_symbols = set(targets1.keys()) & set(targets2.keys())
    shared = []
    for sym in sorted(shared_symbols, key=lambda s: -(targets1[s]["score"] + targets2[s]["score"]) / 2):
        shared.append({
            "gene_symbol":  sym,
            "gene_name":    targets1[sym]["gene_name"],
            "ensembl_id":   targets1[sym]["ensembl_id"],
            f"score_{name1[:20]}": targets1[sym]["score"],
            f"score_{name2[:20]}": targets2[sym]["score"],
            "mean_score":   round((targets1[sym]["score"] + targets2[sym]["score"]) / 2, 3),
        })

    return {
        "disease1":         name1,
        "disease2":         name2,
        "min_score":        min_score,
        "targets_in_d1":    len(targets1),
        "targets_in_d2":    len(targets2),
        "shared_count":     len(shared),
        "shared_targets":   shared[:30],
    }


def bulk_scan_literature(targets: list, months_back: int = 6) -> dict:
    """
    Scan literature for multiple targets in parallel.
    Answers: "Which Roche targets had new high-impact publications in the last N months?"
    targets: list of gene symbols or drug names.
    months_back: how far back to search (default 6 months).
    """
    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(days=months_back * 30)
    min_year = cutoff.year
    min_date = cutoff.strftime("%Y-%m-%d")

    def scan_one(target):
        # Use the gene symbol as both target and a broad disease query
        q = target
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query":      f'"{q}" AND FIRST_PDATE:[{min_date} TO 9999-12-31]',
            "format":     "json",
            "pageSize":   5,
            "sort":       "date desc",
            "resultType": "core",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            results = r.json().get("resultList", {}).get("result", [])
            papers = [
                {
                    "title":   p.get("title", "").strip(),
                    "journal": p.get("journalTitle", ""),
                    "date":    p.get("firstPublicationDate", ""),
                    "doi":     p.get("doi", ""),
                    "pmid":    p.get("pmid", ""),
                    "source":  "EuropePMC",
                }
                for p in results
            ]
        except Exception:
            papers = []
        return {"target": target, "papers_found": len(papers), "papers": papers}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(scan_one, t): t for t in targets}
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    results.sort(key=lambda x: x["papers_found"], reverse=True)
    total_papers = sum(r["papers_found"] for r in results)
    active = [r for r in results if r["papers_found"] > 0]

    out = {
        "targets_scanned":  len(targets),
        "months_back":      months_back,
        "cutoff_date":      min_date,
        "total_papers":     total_papers,
        "targets_with_hits": len(active),
        "results":          results,
    }
    SESSION["literature"].append(out)
    return out


def map_regulatory_path(drug: str, indication: str) -> dict:
    """
    Map the regulatory pathway for a drug + indication:
    checks local FDA guidelines knowledge base and Open Targets for biomarker requirements.
    Also loads pipeline_enrichment for known drug phase/status context.
    """
    # Load local FDA guidelines (comprehensive — 30+ indication keys)
    guidelines = {}
    fda_path = "knowledge_base/fda_guidelines.json"
    if os.path.exists(fda_path):
        with open(fda_path) as f:
            guidelines = json.load(f)
    # Remove metadata key
    guidelines.pop("_meta", None)
    guidelines.pop("_designations", None)

    # Normalise indication for lookup — try progressively broader keys
    def _normalise(s):
        return s.lower().replace(" ", "_").replace("-", "_").replace("/", "_")

    key = _normalise(indication)
    local_guideline = guidelines.get(key, {})
    # Fuzzy fallback: try partial match on any guideline key
    # Exclude stop words that are too generic (disease, cancer, syndrome, etc.) to avoid false matches
    _FUZZY_STOPWORDS = {"disease", "cancer", "syndrome", "disorder", "condition",
                        "failure", "infection", "carcinoma", "positive", "negative"}
    if not local_guideline:
        ind_lower = indication.lower()
        for gk, gv in guidelines.items():
            meaningful_words = [w for w in gk.split("_")
                                if len(w) > 5 and w not in _FUZZY_STOPWORDS]
            if meaningful_words and any(word in ind_lower for word in meaningful_words):
                local_guideline = gv
                break

    # Drug enrichment (phase/status/modality)
    enrichment = _load_pipeline_enrichment()
    drug_enr = enrichment.get(drug.lower(), {})

    # Get biomarker from Open Targets
    bio = get_biology(drug)
    biomarker = None
    if "associations" in bio:
        for assoc in bio["associations"]:
            if indication.lower() in assoc["disease"].lower():
                biomarker = bio.get("symbol")
                break

    # Determine likely expedited pathways
    designations = guidelines.get("_designations", {})
    expedited = []
    ind_lower = indication.lower()
    serious_conditions = ["cancer", "leukemia", "lymphoma", "glioblastoma", "alzheimer",
                          "parkinson", "als", "multiple sclerosis", "hemophilia", "sma",
                          "dmd", "huntington", "pnh", "rare", "orphan"]
    if any(c in ind_lower for c in serious_conditions):
        expedited.append("Fast Track (serious condition + potential unmet need)")
    if local_guideline.get("typical_designations"):
        expedited.extend(local_guideline["typical_designations"])
    expedited = list(dict.fromkeys(expedited))  # deduplicate

    # CDx requirement
    cdx_required = local_guideline.get("cdx_required", False)
    cdx_examples = local_guideline.get("cdx_examples", [])
    cdx_note = ""
    if cdx_required:
        cdx_note = "CDx required. Examples: " + (", ".join(cdx_examples) if cdx_examples else "see cdx_registry.json")
    elif biomarker:
        cdx_note = f"Consider CDx for {biomarker} selection (FoundationOne CDx or equivalent)"
    else:
        cdx_note = "No CDx requirement identified for this indication"

    # Load CDx registry for approved test platforms matching this biomarker/indication
    cdx_registry_hits = []
    cdx_registry_path = "knowledge_base/cdx_registry.json"
    if os.path.exists(cdx_registry_path):
        try:
            with open(cdx_registry_path) as f:
                cdx_db = json.load(f)
            cdx_db.pop("_meta", None)
            req_biomarker_lower = (local_guideline.get("required_biomarker") or biomarker or "").lower()
            ind_lower_short = indication.lower()
            for bm_key, bm_data in cdx_db.items():
                # Match if biomarker keyword or indication appears in CDx entry
                bm_str = (bm_key + " " + bm_data.get("biomarker", "")).lower()
                ind_match = any(ind_lower_short in str(i).lower() for i in bm_data.get("indications", []))
                bm_match = any(w in bm_str for w in req_biomarker_lower.split() if len(w) > 4)
                if ind_match or bm_match:
                    for entry in bm_data.get("approved_cdx", []):
                        cdx_registry_hits.append({
                            "biomarker":        bm_key,
                            "drug":             entry.get("drug"),
                            "platform":         entry.get("platform"),
                            "assay_type":       entry.get("assay_type"),
                            "fda_approval_date": entry.get("fda_approval_date", ""),
                            "approval_type":    entry.get("approval_type"),
                            "notes":            entry.get("notes", ""),
                        })
        except Exception:
            pass

    out = {
        "drug":              drug,
        "indication":        indication,
        "primary_endpoint":  local_guideline.get("primary_endpoint", "Consult FDA guidance for specific indication"),
        "key_secondary_endpoints": local_guideline.get("key_secondary_endpoints", []),
        "required_biomarker": local_guideline.get("required_biomarker") or biomarker or "Not specified",
        "fda_notes":         local_guideline.get("fda_notes", "Standard regulatory package"),
        "fda_preference":    local_guideline.get("fda_preference", ""),
        "companion_dx":      cdx_note,
        "cdx_required":      cdx_required,
        "cdx_approved_platforms": cdx_registry_hits[:5],  # top 5 matching platforms
        "expedited_pathways": expedited or ["Breakthrough Therapy / Fast Track likely eligible if unmet need confirmed"],
        "drug_phase":        drug_enr.get("phase", "Unknown"),
        "drug_status":       drug_enr.get("status", "Unknown"),
        "drug_modality":     drug_enr.get("modality", "Unknown"),
    }
    SESSION["regulatory"].append(out)
    return out


def list_pipeline_assets(therapeutic_area: str = None, phase: str = None,
                          status: str = None, modality: str = None) -> dict:
    """
    List Roche/Genentech pipeline assets from enriched knowledge base.
    Filters by therapeutic_area, phase (approved/3/2/1), status (active/approved/discontinued),
    or modality (mAb/bispecific/small_molecule/ADC/ASO/mRNA/etc.).
    Returns matching assets with full metadata. Fast — no API calls.
    """
    enrichment = _load_pipeline_enrichment()
    results = []
    for name, meta in enrichment.items():
        if name.startswith("_"):
            continue
        if therapeutic_area and therapeutic_area.lower() not in meta.get("therapeutic_area", "").lower():
            continue
        if phase and str(phase).lower() != str(meta.get("phase", "")).lower():
            continue
        if status and status.lower() != meta.get("status", "").lower():
            continue
        if modality and modality.lower() not in meta.get("modality", "").lower():
            continue
        results.append({
            "name":              name,
            "alias":             meta.get("alias", ""),
            "phase":             meta.get("phase"),
            "status":            meta.get("status"),
            "therapeutic_area":  meta.get("therapeutic_area"),
            "primary_indication": meta.get("primary_indication"),
            "modality":          meta.get("modality"),
            "mechanism":         meta.get("mechanism"),
            "safety_signals":    meta.get("safety_signals"),
        })

    results.sort(key=lambda x: (
        {"approved": 0, "3": 1, "2": 2, "1": 3, "discontinued": 4, "partner_licensed": 5}.get(
            str(x.get("phase", "")), 9
        ),
        x.get("name", "")
    ))

    out = {
        "filters_applied": {
            "therapeutic_area": therapeutic_area,
            "phase": phase,
            "status": status,
            "modality": modality,
        },
        "total": len(results),
        "assets": results,
    }
    return out


def query_competitive_intel(therapeutic_area: str = None, competitor: str = None,
                             indication: str = None) -> dict:
    """
    Query the static competitive intelligence knowledge base (competitive_intel.json).
    Returns key competitor assets filtered by therapeutic area, competitor name, or indication.
    Fast — no API calls. Use before monitor_competitive_signals for initial context,
    or when you need specific competitor mechanism/phase details.
    """
    ci_path = "knowledge_base/competitive_intel.json"
    if not os.path.exists(ci_path):
        return {"error": "competitive_intel.json not found"}

    with open(ci_path) as f:
        db = json.load(f)
    db.pop("_meta", None)

    results = []
    ta_lower = therapeutic_area.lower() if therapeutic_area else None
    comp_lower = competitor.lower() if competitor else None
    ind_lower = indication.lower() if indication else None

    def _search(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _search(v, path + "/" + k)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and "asset" in item:
                    # Check filters
                    asset_str = json.dumps(item).lower()
                    path_lower = path.lower()

                    if ta_lower and ta_lower.replace(" ", "_") not in path_lower and ta_lower not in asset_str:
                        return
                    if comp_lower:
                        path_parts = [p.lower() for p in path.split("/")]
                        if not any(comp_lower in p for p in path_parts):
                            return
                    if ind_lower and ind_lower not in asset_str and ind_lower.replace(" ", "_") not in path_lower:
                        return

                    competitor_name = path.split("/")[-2] if "/" in path else "Unknown"
                    results.append({
                        "competitor":  competitor_name,
                        "area":        path.split("/")[1] if "/" in path else "Unknown",
                        "sub_area":    path.split("/")[2] if path.count("/") >= 2 else "Unknown",
                        "asset":       item.get("asset"),
                        "type":        item.get("type"),
                        "target":      item.get("target"),
                        "phase":       item.get("phase"),
                        "indication":  item.get("indication"),
                        "notes":       item.get("notes", ""),
                    })

    _search(db)
    return {
        "filters": {"therapeutic_area": therapeutic_area, "competitor": competitor, "indication": indication},
        "total": len(results),
        "assets": results,
    }


def generate_pdf_report(filename: str = None, ceo_summary: str = "") -> dict:
    """
    Generate a full structured PDF report from all findings accumulated in this session.
    Includes: cover page, executive summary, gap analysis, portfolio ranking,
    combination opportunities, literature evidence, and regulatory pathways.
    """
    if not filename:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"Roche_AI_Factory_Report_{ts}.pdf"

    doc = SimpleDocTemplate(
        filename, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    styles = getSampleStyleSheet()

    # Custom styles
    NAVY   = colors.HexColor("#003087")
    BLUE   = colors.HexColor("#0066CC")
    LIGHT  = colors.HexColor("#EEF4FB")
    YELLOW = colors.HexColor("#FFF3CD")

    styles.add(ParagraphStyle("Cover",    fontSize=26, textColor=NAVY,  spaceAfter=6,  fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("SubCover", fontSize=13, textColor=BLUE,  spaceAfter=4,  fontName="Helvetica"))
    styles.add(ParagraphStyle("SectionH", fontSize=14, textColor=NAVY,  spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("SubH",     fontSize=11, textColor=BLUE,  spaceBefore=8,  spaceAfter=4, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Body",     fontSize=9,  leading=13, spaceAfter=4))
    styles.add(ParagraphStyle("Cell",     fontSize=8,  leading=11))
    styles.add(ParagraphStyle("CellB",    fontSize=8,  leading=11, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Tag",      fontSize=8,  textColor=colors.white, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("Ref",      fontSize=7.5, leading=10, textColor=NAVY, leftIndent=10, firstLineIndent=-10))

    story = []
    date_str = datetime.datetime.now().strftime("%B %d, %Y")

    def hr(color=NAVY, thick=1.5):
        return HRFlowable(width="100%", thickness=thick, color=color, spaceAfter=8)

    def section(title):
        story.append(Spacer(1, 4*mm))
        story.append(hr())
        story.append(Paragraph(title, styles["SectionH"]))

    def tbl(data, col_widths, header=True):
        t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
        style = [
            ("GRID",        (0,0), (-1,-1), 0.4, colors.grey),
            ("VALIGN",      (0,0), (-1,-1), "TOP"),
            ("TOPPADDING",  (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
        ]
        if header:
            style += [
                ("BACKGROUND", (0,0), (-1,0), NAVY),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ]
        for i in range(1, len(data)):
            bg = LIGHT if i % 2 == 0 else colors.white
            style.append(("BACKGROUND", (0,i), (-1,i), bg))
        t.setStyle(TableStyle(style))
        return t

    # ── COVER PAGE ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 30*mm))
    story.append(Paragraph("ROCHE AI FACTORY", styles["Cover"]))
    story.append(Paragraph("Strategic Discovery Report", styles["SubCover"]))
    story.append(Spacer(1, 6*mm))
    story.append(hr(NAVY, 2))
    story.append(Spacer(1, 4*mm))

    query_text = SESSION.get("question", "Strategic Portfolio Analysis")
    story.append(Paragraph(f"<b>Query:</b> {query_text}", styles["Body"]))
    story.append(Paragraph(f"<b>Generated:</b> {date_str}", styles["Body"]))
    story.append(Paragraph("<b>Sources:</b> ClinicalTrials.gov · Open Targets · Europe PMC · ArXiv · UniProt · openFDA", styles["Body"]))
    story.append(Paragraph("<b>Agent:</b> Roche AI Factory v2.0 — ReAct Loop (Claude + Tool Use)", styles["Body"]))

    story.append(Spacer(1, 10*mm))

    # Summary stats box
    n_gaps   = len(SESSION["gaps"])
    n_assets = len(SESSION["portfolio"])
    n_papers = sum(l.get("papers_found", 0) for l in SESSION["literature"])
    n_reg    = len(SESSION["regulatory"])

    stats = Table(
        [[Paragraph(f"<b>{n_gaps}</b><br/>Strategic Gaps", styles["Cell"]),
          Paragraph(f"<b>{n_assets}</b><br/>Assets Ranked", styles["Cell"]),
          Paragraph(f"<b>{n_papers}</b><br/>Papers Reviewed", styles["Cell"]),
          Paragraph(f"<b>{n_reg}</b><br/>Regulatory Paths", styles["Cell"])]],
        colWidths=[38*mm]*4,
    )
    stats.setStyle(TableStyle([
        ("BOX",        (0,0), (-1,-1), 1, NAVY),
        ("INNERGRID",  (0,0), (-1,-1), 0.5, BLUE),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("BACKGROUND", (0,0), (-1,-1), LIGHT),
    ]))
    story.append(stats)
    story.append(PageBreak())

    # ── EXECUTIVE SUMMARY ───────────────────────────────────────────────────────
    section("EXECUTIVE SUMMARY")
    if ceo_summary:
        story.append(Paragraph(ceo_summary, styles["Body"]))
    else:
        top_gaps = sorted(SESSION["gaps"], key=lambda x: x.get("bio_score", 0), reverse=True)[:3]
        bullets  = "".join(
            f"<br/>• <b>{g['target']}</b> in <i>{g['disease']}</i> (Bio Score: {g['bio_score']}) — {g['status']}"
            for g in top_gaps
        ) if top_gaps else "<br/>• No gaps identified in this session."
        top_asset = SESSION["portfolio"][0] if SESSION["portfolio"] else None
        asset_txt = (
            f"<br/>• Top ranked asset: <b>{top_asset['name']}</b> "
            f"(Composite Score: {top_asset['composite_score']})"
        ) if top_asset else ""
        story.append(Paragraph(
            f"This report summarises the AI Factory's autonomous discovery session for: "
            f"<i>{query_text}</i><br/><br/>"
            f"<b>Key Findings:</b>{bullets}{asset_txt}",
            styles["Body"],
        ))

    # ── SECTION 1: GAP ANALYSIS ─────────────────────────────────────────────────
    if SESSION["gaps"]:
        section("SECTION 1 — STRATEGIC GAP ANALYSIS")
        story.append(Paragraph(
            "Gaps are indications where Open Targets biological evidence is strong (score ≥ 0.60) "
            "but Roche/Genentech has no active clinical trial.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        header = [
            Paragraph("Target", styles["CellB"]),
            Paragraph("Disease", styles["CellB"]),
            Paragraph("Bio Score", styles["CellB"]),
            Paragraph("Ensembl ID", styles["CellB"]),
            Paragraph("Status", styles["CellB"]),
        ]
        rows = [header]
        for g in sorted(SESSION["gaps"], key=lambda x: x.get("bio_score", 0), reverse=True):
            score = g.get("bio_score", 0)
            score_color = colors.red if score >= 0.75 else (BLUE if score >= 0.60 else colors.grey)
            rows.append([
                Paragraph(g.get("target", ""), styles["Cell"]),
                Paragraph(g.get("disease", ""), styles["Cell"]),
                Paragraph(f"<font color='#{score_color.hexval()[2:] if hasattr(score_color, 'hexval') else '003087'}'><b>{score}</b></font>", styles["Cell"]),
                Paragraph(g.get("ensembl_id", ""), styles["Cell"]),
                Paragraph(g.get("status", "STRATEGIC GAP"), styles["Cell"]),
            ])
        story.append(tbl(rows, [30*mm, 55*mm, 25*mm, 38*mm, 32*mm]))

    # ── SECTION 2: PORTFOLIO RANKING ────────────────────────────────────────────
    if SESSION["portfolio"]:
        section("SECTION 2 — PORTFOLIO RANKING")
        story.append(Paragraph(
            "Assets ranked by composite score = Bio Score × Unexplored Indications × (1 + Competitive Vacuum).",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        header = [
            Paragraph("#",               styles["CellB"]),
            Paragraph("Asset",           styles["CellB"]),
            Paragraph("Top Disease",     styles["CellB"]),
            Paragraph("Bio Score",       styles["CellB"]),
            Paragraph("Unexplored Inds", styles["CellB"]),
            Paragraph("Comp. Vacuum",    styles["CellB"]),
            Paragraph("Composite",       styles["CellB"]),
        ]
        rows = [header]
        for i, a in enumerate(SESSION["portfolio"], 1):
            rows.append([
                Paragraph(str(i), styles["Cell"]),
                Paragraph(f"<b>{a['name']}</b>", styles["Cell"]),
                Paragraph(a.get("top_disease", ""), styles["Cell"]),
                Paragraph(str(a.get("bio_score", "")), styles["Cell"]),
                Paragraph(str(a.get("unexplored_inds", "")), styles["Cell"]),
                Paragraph("Yes" if a.get("competitive_vacuum") else "No", styles["Cell"]),
                Paragraph(f"<b>{a.get('composite_score', '')}</b>", styles["Cell"]),
            ])
        story.append(tbl(rows, [8*mm, 32*mm, 45*mm, 20*mm, 22*mm, 22*mm, 21*mm]))

    # ── SECTION 3: COMBINATION OPPORTUNITIES ────────────────────────────────────
    if SESSION["combinations"]:
        section("SECTION 3 — COMBINATION THERAPY OPPORTUNITIES")
        for combo in SESSION["combinations"]:
            story.append(Paragraph(f"Disease: <b>{combo['disease']}</b> — {combo['combo_trials']} combination trials found", styles["SubH"]))
            if combo["unique_pairs"]:
                header = [
                    Paragraph("Drug A",  styles["CellB"]),
                    Paragraph("Drug B",  styles["CellB"]),
                    Paragraph("NCT ID",  styles["CellB"]),
                ]
                rows = [header] + [
                    [Paragraph(p["drug_a"], styles["Cell"]),
                     Paragraph(p["drug_b"], styles["Cell"]),
                     Paragraph(p.get("nct_id",""), styles["Cell"])]
                    for p in combo["unique_pairs"]
                ]
                story.append(tbl(rows, [65*mm, 65*mm, 40*mm]))
                story.append(Spacer(1, 3*mm))

    # ── SECTION 4: LITERATURE EVIDENCE ──────────────────────────────────────────
    if SESSION["literature"]:
        section("SECTION 4 — LITERATURE EVIDENCE")
        for lit in SESSION["literature"]:
            story.append(Paragraph(
                f"<b>{lit['target']}</b> in <i>{lit['disease']}</i> — {lit['papers_found']} papers",
                styles["SubH"],
            ))
            for p in lit.get("papers", [])[:5]:
                ref = f"<b>{p.get('title','')}</b><br/>"
                if p.get("journal"):
                    ref += f"<i>{p['journal']}</i>"
                if p.get("date"):
                    ref += f" ({p['date']})"
                if p.get("doi"):
                    ref += f" · DOI: {p['doi']}"
                story.append(Paragraph(ref, styles["Ref"]))
                story.append(Spacer(1, 2*mm))

    # ── SECTION 5: REGULATORY PATHWAYS ──────────────────────────────────────────
    if SESSION["regulatory"]:
        section("SECTION 5 — REGULATORY PATHWAYS")
        for reg in SESSION["regulatory"]:
            cdx_platforms = reg.get("cdx_approved_platforms", [])
            cdx_str = "; ".join(
                f"{p['platform']} ({p['assay_type']}, {p['fda_approval_date'][:4]})"
                for p in cdx_platforms[:3]
            ) if cdx_platforms else reg.get("companion_dx", "—")

            reg_rows = [
                [Paragraph("Field",              styles["CellB"]), Paragraph("Value", styles["CellB"])],
                [Paragraph("Primary Endpoint",   styles["Cell"]), Paragraph(reg.get("primary_endpoint",""),  styles["Cell"])],
                [Paragraph("Required Biomarker", styles["Cell"]), Paragraph(reg.get("required_biomarker",""),styles["Cell"])],
                [Paragraph("Companion Dx",       styles["Cell"]), Paragraph(cdx_str,                         styles["Cell"])],
                [Paragraph("FDA Preference",     styles["Cell"]), Paragraph(reg.get("fda_preference",""),    styles["Cell"])],
                [Paragraph("Expedited Path",     styles["Cell"]), Paragraph(
                    ", ".join(reg.get("expedited_pathways", [reg.get("expedited_pathway","")])),
                    styles["Cell"])],
            ]
            story.append(KeepTogether([
                Paragraph(f"{reg['drug']} → {reg['indication']}", styles["SubH"]),
                tbl(reg_rows, [50*mm, 120*mm]),
                Spacer(1, 4*mm),
            ]))

    # ── SECTION 6: ARXIV INTELLIGENCE ───────────────────────────────────────────
    if SESSION["arxiv_papers"]:
        section("SECTION 6 — ARXIV INTELLIGENCE (Pre-Publication Signal)")
        story.append(Paragraph(
            "ArXiv preprints surface cutting-edge science 6–18 months before peer review. "
            "Includes ML-assisted drug design, AlphaFold structure predictions, and resistance mechanism papers.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        header = [
            Paragraph("Title",      styles["CellB"]),
            Paragraph("Categories", styles["CellB"]),
            Paragraph("Submitted",  styles["CellB"]),
            Paragraph("PDF Link",   styles["CellB"]),
        ]
        rows = [header]
        for p in SESSION["arxiv_papers"][:20]:
            cats = ", ".join(p.get("categories", [])[:3])
            rows.append([
                Paragraph(p.get("title", "")[:120], styles["Cell"]),
                Paragraph(cats, styles["Cell"]),
                Paragraph(p.get("submitted", ""), styles["Cell"]),
                Paragraph(p.get("pdf_url", ""), styles["Cell"]),
            ])
        story.append(tbl(rows, [90*mm, 35*mm, 22*mm, 33*mm]))

    # ── SECTION 7: REPURPOSING CANDIDATES ───────────────────────────────────────
    if SESSION["repurposing"]:
        section("SECTION 7 — DRUG REPURPOSING OPPORTUNITIES")
        story.append(Paragraph(
            "Approved drugs (Phase 4) that could be repositioned into a new indication. "
            "These skip Phase I entirely — the fastest regulatory path to clinic.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))
        header = [
            Paragraph("Drug",               styles["CellB"]),
            Paragraph("Approved Indication", styles["CellB"]),
            Paragraph("Target Disease",      styles["CellB"]),
            Paragraph("Year Approved",       styles["CellB"]),
        ]
        rows = [header]
        for c in SESSION["repurposing"][:20]:
            rows.append([
                Paragraph(f"<b>{c.get('drug_name','')}</b>", styles["Cell"]),
                Paragraph(c.get("approved_indication", ""), styles["Cell"]),
                Paragraph(c.get("target_disease", ""),      styles["Cell"]),
                Paragraph(str(c.get("year_approved", "")),  styles["Cell"]),
            ])
        story.append(tbl(rows, [40*mm, 55*mm, 55*mm, 30*mm]))

    # ── SECTION 8: TARGET DRUGGABILITY & ORPHAN FLAGS ──────────────────────────
    has_prot  = bool(SESSION["protein_structures"])
    has_orph  = bool(SESSION["orphan_flags"])
    if has_prot or has_orph:
        section("SECTION 8 — TARGET DRUGGABILITY & ORPHAN DISEASE FLAGS")
        story.append(Spacer(1, 2*mm))

        if has_prot:
            story.append(Paragraph("Druggability Assessment (UniProt + Open Targets Tractability)", styles["SubH"]))
            header = [
                Paragraph("Gene",          styles["CellB"]),
                Paragraph("Protein",       styles["CellB"]),
                Paragraph("Druggability",  styles["CellB"]),
                Paragraph("Modality",      styles["CellB"]),
                Paragraph("Evidence",      styles["CellB"]),
            ]
            rows = [header]
            for p in SESSION["protein_structures"]:
                sm = ", ".join(p.get("tractability_evidence", {}).get("sm", [])[:2])
                rows.append([
                    Paragraph(f"<b>{p.get('gene_symbol','')}</b>", styles["Cell"]),
                    Paragraph(p.get("protein_name", "")[:50],      styles["Cell"]),
                    Paragraph(p.get("druggability", ""),            styles["Cell"]),
                    Paragraph(p.get("recommended_modality", ""),    styles["Cell"]),
                    Paragraph(sm or "—",                            styles["Cell"]),
                ])
            story.append(tbl(rows, [25*mm, 55*mm, 28*mm, 30*mm, 42*mm]))
            story.append(Spacer(1, 4*mm))

        if has_orph:
            story.append(Paragraph("Orphan Drug Designation Eligibility", styles["SubH"]))
            header = [
                Paragraph("Disease",      styles["CellB"]),
                Paragraph("US Eligible",  styles["CellB"]),
                Paragraph("EU Eligible",  styles["CellB"]),
                Paragraph("Prevalence",   styles["CellB"]),
                Paragraph("Key Benefits", styles["CellB"]),
            ]
            rows = [header]
            for o in SESSION["orphan_flags"]:
                benefits_short = "; ".join(o.get("benefits", [])[:2])
                rows.append([
                    Paragraph(o.get("disease", ""),                                 styles["Cell"]),
                    Paragraph("YES" if o.get("us_eligible") else "No",              styles["Cell"]),
                    Paragraph("YES" if o.get("eu_eligible") else "No",              styles["Cell"]),
                    Paragraph(f"~{o.get('estimated_prevalence','?'):,}" if o.get("estimated_prevalence") else "Unknown", styles["Cell"]),
                    Paragraph(benefits_short or "—",                                styles["Cell"]),
                ])
            story.append(tbl(rows, [45*mm, 22*mm, 22*mm, 25*mm, 66*mm]))

    # ── SECTION 9: GENOMECLAW STRUCTURAL ANALYSIS ──────────────────────────────
    has_folds  = bool(SESSION["fold_results"])
    has_admet  = bool(SESSION["admet_profiles"])
    has_vars   = bool(SESSION["variant_effects"])
    if has_folds or has_admet or has_vars:
        section("SECTION 9 — GENOMECLAW STRUCTURAL & ADMET ANALYSIS")
        story.append(Paragraph(
            "3D structures predicted by GenomeClaw Boltz-1 (Rust-native, GPU-accelerated). "
            "ADMET profiles generated by genomeclaw-admet. Variant effects scored by ESM-2.",
            styles["Body"],
        ))
        story.append(Spacer(1, 3*mm))

        if has_folds:
            story.append(Paragraph("Protein Structure Predictions (Boltz-1 pLDDT)", styles["SubH"]))
            header = [
                Paragraph("Gene / Sequence",    styles["CellB"]),
                Paragraph("Residues",            styles["CellB"]),
                Paragraph("pLDDT mean",          styles["CellB"]),
                Paragraph("Confidence",          styles["CellB"]),
                Paragraph("Elapsed (s)",         styles["CellB"]),
            ]
            rows = [header]
            for f in SESSION["fold_results"]:
                rows.append([
                    Paragraph(f"<b>{f.get('gene','')}</b>",          styles["Cell"]),
                    Paragraph(str(f.get("residues", "")),             styles["Cell"]),
                    Paragraph(str(f.get("plddt_mean", "—")),          styles["Cell"]),
                    Paragraph(f.get("confidence_label", "")[:60],     styles["Cell"]),
                    Paragraph(str(f.get("elapsed_secs", "—")),        styles["Cell"]),
                ])
            story.append(tbl(rows, [45*mm, 22*mm, 25*mm, 60*mm, 28*mm]))
            story.append(Spacer(1, 4*mm))

        if has_vars:
            story.append(Paragraph("Variant Effect Scores (ESM-2 Delta Log-Likelihood)", styles["SubH"]))
            header = [
                Paragraph("Gene",          styles["CellB"]),
                Paragraph("Variant",       styles["CellB"]),
                Paragraph("ΔLL",           styles["CellB"]),
                Paragraph("Interpretation",styles["CellB"]),
            ]
            rows = [header]
            for v in SESSION["variant_effects"]:
                rows.append([
                    Paragraph(f"<b>{v.get('gene','')}</b>",           styles["Cell"]),
                    Paragraph(v.get("variant", ""),                    styles["Cell"]),
                    Paragraph(str(v.get("delta_log_likelihood", "—")), styles["Cell"]),
                    Paragraph(v.get("interpretation", "")[:80],        styles["Cell"]),
                ])
            story.append(tbl(rows, [30*mm, 25*mm, 20*mm, 105*mm]))
            story.append(Spacer(1, 4*mm))

        if has_admet:
            story.append(Paragraph("ADMET Profiles (genomeclaw-admet)", styles["SubH"]))
            header = [
                Paragraph("Drug",            styles["CellB"]),
                Paragraph("Tier",            styles["CellB"]),
                Paragraph("hERG",            styles["CellB"]),
                Paragraph("BBB",             styles["CellB"]),
                Paragraph("Half-Life",       styles["CellB"]),
                Paragraph("Flags",           styles["CellB"]),
            ]
            rows = [header]
            for a in SESSION["admet_profiles"]:
                flags = "; ".join(a.get("red_flags", []) + a.get("minor_flags", []))
                rows.append([
                    Paragraph(f"<b>{a.get('drug','')}</b>",  styles["Cell"]),
                    Paragraph(a.get("tier", ""),             styles["Cell"]),
                    Paragraph(a.get("hERG", "—"),            styles["Cell"]),
                    Paragraph(a.get("BBB", "—"),             styles["Cell"]),
                    Paragraph(a.get("HalfLife", "—"),        styles["Cell"]),
                    Paragraph(flags[:80] or "—",             styles["Cell"]),
                ])
            story.append(tbl(rows, [40*mm, 18*mm, 38*mm, 30*mm, 22*mm, 32*mm]))

    # ── FOOTER ──────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Spacer(1, 20*mm))
    story.append(hr(NAVY, 2))
    story.append(Paragraph("CONFIDENTIAL — Roche AI Factory Internal Use Only", styles["Body"]))
    story.append(Paragraph(
        f"Generated by Roche AI Factory Strategic Discovery Agent · {date_str} · "
        "Sources: ClinicalTrials.gov, Open Targets Platform, Europe PMC",
        styles["Ref"],
    ))

    doc.build(story)
    return {"status": "success", "file": filename, "pages_estimated": len(story) // 10}


def save_to_cache(data: dict) -> dict:
    """Persist a discovery to the intelligence cache for future use."""
    cache = []
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            try:
                cache = json.load(f)
                if isinstance(cache, dict):
                    cache = cache.get("assets", [])
            except json.JSONDecodeError:
                cache = []

    # Upsert by name
    names = {e.get("name") for e in cache}
    if isinstance(data, list):
        for item in data:
            if item.get("name") not in names:
                cache.append(item)
                names.add(item.get("name"))
    else:
        if data.get("name") not in names:
            cache.append(data)

    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

    return {"status": "saved", "cache_size": len(cache)}


# ── New analytical tools ───────────────────────────────────────────────────────

def score_trial_outcome(nct_id_or_drug: str, indication: str) -> dict:
    """
    Estimate the likelihood of trial success (0.0–1.0) using a weighted heuristic
    derived from DiMasi/BIO meta-analyses. Returns score + risk factors.
    """
    PHASE_BASE = {"PHASE1": 0.65, "PHASE2": 0.35, "PHASE3": 0.58, "PHASE4": 0.85}

    if nct_id_or_drug.upper().startswith("NCT"):
        r = requests.get(f"{CT_URL}/{nct_id_or_drug.upper()}", timeout=10)
        study = r.json() if r.status_code == 200 else {}
        studies = [study] if study else []
    else:
        params = {"query.term": nct_id_or_drug, "query.cond": indication, "pageSize": 5}
        r = requests.get(CT_URL, params=params, timeout=10)
        studies = r.json().get("studies", [])

    if not studies:
        return {"error": f"No trials found for '{nct_id_or_drug}'"}

    proto  = studies[0].get("protocolSection", {})
    design = proto.get("designModule", {})
    phases = design.get("phases", [])
    top_phase = phases[-1] if phases else ""
    score  = PHASE_BASE.get(top_phase, 0.40)
    risk_factors = []

    # Enrollment modifier
    enroll = (proto.get("designModule", {}).get("enrollmentInfo") or {}).get("count")
    if isinstance(enroll, (int, float)):
        if enroll < 30:
            score -= 0.05
            risk_factors.append(f"Small enrollment ({enroll})")
        elif enroll > 100:
            score += 0.05

    # Primary endpoint
    outcomes = proto.get("outcomesModule", {}).get("primaryOutcomes", [])
    if outcomes:
        measure = (outcomes[0].get("measure") or "").lower()
        if "survival" in measure:
            score += 0.05
        elif not measure:
            score -= 0.05
            risk_factors.append("No primary endpoint specified")
    else:
        score -= 0.05
        risk_factors.append("Primary endpoint missing")

    # Single-arm penalty
    arms = proto.get("armsInterventionsModule", {}).get("armGroups", [])
    if len(arms) == 1:
        score -= 0.05
        risk_factors.append("Single-arm study")

    # Terminated
    status = proto.get("statusModule", {}).get("overallStatus", "")
    if status == "TERMINATED":
        score *= 0.2
        risk_factors.append("TERMINATED")

    score = round(max(0.05, min(0.95, score)), 3)
    result = {
        "trial_id":      nct_id_or_drug,
        "indication":    indication,
        "phase":         top_phase,
        "enrollment":    enroll,
        "outcome_score": score,
        "risk_factors":  risk_factors,
    }
    SESSION["trial_outcomes"].append(result)
    return result


def find_repurposing_candidates(target_or_disease: str) -> dict:
    """
    Find approved drugs that could be repositioned into a new disease indication.
    Skips Phase I entirely — fastest path to clinic.
    Uses Open Targets drugAndClinicalCandidates with maxClinicalStage == 'APPROVAL'.
    """
    disease_q = """
    query($q: String!) {
      search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 1}) {
        hits { id name }
      }
    }"""
    r = requests.post(OT_URL, json={"query": disease_q, "variables": {"q": target_or_disease}}, timeout=10)
    hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    candidates = []
    search_disease = target_or_disease

    if hits:
        disease_id     = hits[0]["id"]
        search_disease = hits[0]["name"]

        drugs_q = """
        query($id: String!) {
          disease(efoId: $id) {
            drugAndClinicalCandidates {
              rows {
                drug { name drugType }
                maxClinicalStage
              }
            }
          }
        }"""
        r = requests.post(OT_URL, json={"query": drugs_q, "variables": {"id": disease_id}}, timeout=10)
        drug_rows = (
            r.json().get("data", {}).get("disease", {})
                    .get("drugAndClinicalCandidates", {}).get("rows", [])
        )
        for row in drug_rows:
            if row.get("maxClinicalStage") == "APPROVAL":
                drug = row.get("drug", {})
                candidates.append({
                    "drug_name":      drug.get("name", ""),
                    "drug_type":      drug.get("drugType", ""),
                    "target_disease": search_disease,
                    "stage":          "APPROVAL",
                })

    # Enrich up to 5 APPROVAL candidates with ChemBL + BindingDB binding data
    for cand in candidates[:5]:
        db_info = query_genomeclaw_databases(cand["drug_name"], ["chembl", "bindingdb"])
        if db_info.get("status") not in ("genomeclaw_not_built", "genomeclaw_offline"):
            cand["chembl_best_pIC50"]    = db_info.get("summary", {}).get("chembl_best_pIC50")
            cand["bindingdb_best_Ki_nM"] = db_info.get("summary", {}).get("bindingdb_best_Ki_nM")

    # Rank by best pIC50 when available
    candidates.sort(
        key=lambda c: (c.get("chembl_best_pIC50") or 0),
        reverse=True,
    )

    out = {
        "query":            target_or_disease,
        "candidates_found": len(candidates),
        "candidates":       candidates,
    }
    SESSION["repurposing"].extend(candidates)
    return out


def check_orphan_eligibility(disease: str) -> dict:
    """
    Check if a disease qualifies for Orphan Drug Designation (US < 200K, EU < 165K patients).
    Returns eligibility, benefits (7yr exclusivity, tax credits, fee waivers), and confidence.
    """
    key = disease.lower().strip()
    prevalence = None
    confidence = "low"

    for map_key, data in PREVALENCE_MAP.items():
        if map_key in key or key in map_key:
            prevalence = data["prevalence"]
            confidence = "high"
            break

    us_eligible = (prevalence < 200000) if prevalence is not None else None
    eu_eligible = (prevalence < 165000) if prevalence is not None else None

    benefits = []
    if us_eligible:
        benefits += [
            "7 years market exclusivity (US FDA)",
            "Tax credits up to 25% on clinical trial costs",
            "Reduced FDA filing fees (PDUFA waiver)",
            "Smaller trial size requirements accepted",
        ]
    if eu_eligible:
        benefits += [
            "10 years market exclusivity (EMA)",
            "EMA protocol assistance and scientific advice",
            "Reduced regulatory fees",
        ]

    result = {
        "disease":              disease,
        "us_eligible":          us_eligible,
        "eu_eligible":          eu_eligible,
        "estimated_prevalence": prevalence,
        "benefits":             benefits,
        "confidence":           confidence,
        "note":                 "Verify with current NORD/Orphanet databases before IND filing" if confidence == "low" else "",
    }
    SESSION["orphan_flags"].append(result)
    return result


def get_protein_structure_context(gene_symbol: str) -> dict:
    """
    Validates target druggability before committing R&D resources.
    Queries UniProt REST for protein function + binding sites, and Open Targets
    tractability scores to recommend modality (small molecule / antibody / PROTAC).
    """
    # 1. UniProt REST
    uniprot_fields = "protein_name,cc_function,ft_binding,ft_domain,length"
    r = requests.get(
        UNIPROT_URL,
        params={"query": f"gene:{gene_symbol} AND organism_id:9606",
                "fields": uniprot_fields, "format": "json", "size": 1},
        timeout=10,
    )
    protein_name = ""
    function_txt = ""
    if r.status_code == 200:
        entries = r.json().get("results", [])
        if entries:
            e = entries[0]
            protein_name = (
                e.get("proteinDescription", {})
                 .get("recommendedName", {})
                 .get("fullName", {})
                 .get("value", "")
            )
            for c in e.get("comments", []):
                if c.get("commentType") == "FUNCTION":
                    texts = c.get("texts", [])
                    if texts:
                        function_txt = texts[0].get("value", "")[:300]
                        break

    # 2. Open Targets tractability
    gene_q = """
    query($q: String!) {
      search(queryString: $q, entityNames: ["target"], page: {index: 0, size: 1}) {
        hits { id name }
      }
    }"""
    r = requests.post(OT_URL, json={"query": gene_q, "variables": {"q": gene_symbol}}, timeout=10)
    hits = r.json().get("data", {}).get("search", {}).get("hits", [])

    druggability = "Unknown"
    modality     = "Unknown"
    tractability = {}

    if hits:
        ensembl_id = hits[0]["id"]
        tract_q = """
        query($id: String!) {
          target(ensemblId: $id) {
            tractability { label modality value }
          }
        }"""
        r = requests.post(OT_URL, json={"query": tract_q, "variables": {"id": ensembl_id}}, timeout=10)
        tract_rows = r.json().get("data", {}).get("target", {}).get("tractability", []) or []

        sm_labels = [t["label"] for t in tract_rows if t.get("modality") == "SM"  and t.get("value")]
        ab_labels = [t["label"] for t in tract_rows if t.get("modality") == "AB"  and t.get("value")]
        oc_labels = [t["label"] for t in tract_rows if t.get("modality") == "OC"  and t.get("value")]

        HIGH_SM = {"Approved Drug", "Advanced Clinical"}
        HIGH_AB = {"Approved Drug", "Advanced Clinical", "Antibodies in clinical phase"}
        MED_SM  = {"Phase 1 Clinical", "Structure with Ligand", "High-Quality Pocket", "Druggable Family"}

        if any(l in HIGH_SM for l in sm_labels):
            druggability, modality = "High", "Small Molecule"
        elif any(l in HIGH_AB for l in ab_labels):
            druggability, modality = "High", "Antibody"
        elif any(l in MED_SM for l in sm_labels) or oc_labels:
            druggability, modality = "Medium", "Small Molecule"
        elif ab_labels:
            druggability, modality = "Medium", "Antibody"
        else:
            druggability = "Low"

        tractability = {"sm": sm_labels[:3], "ab": ab_labels[:3], "other": oc_labels[:2]}

    # 3. GenomeClaw fold — upgrade druggability verdict with real 3D pLDDT
    fold = fold_target(gene_symbol)
    plddt = fold.get("plddt_mean") if fold.get("status") not in ("genomeclaw_offline", "error", "timeout") else None
    if plddt is not None:
        if plddt > 0.70 and druggability in ("Unknown", "Low", "Medium"):
            druggability = "High (3D structure confirmed)"
        elif plddt > 0.50 and druggability in ("Unknown", "Low"):
            druggability = "Medium (partial 3D structure)"
        elif plddt <= 0.50 and druggability == "Unknown":
            druggability = "Low (disordered)"

    result = {
        "gene_symbol":           gene_symbol,
        "protein_name":          protein_name,
        "function_summary":      function_txt,
        "druggability":          druggability,
        "recommended_modality":  modality,
        "tractability_evidence": tractability,
        "plddt_mean":            plddt,
        "fold_confidence":       fold.get("confidence_label"),
    }
    SESSION["protein_structures"].append(result)
    return result


def monitor_competitive_signals(disease: str, competitors: list = None) -> dict:
    """
    Live 8-competitor activity dashboard for a disease.
    Fires all ClinicalTrials.gov queries in parallel, then checks openFDA for recent approvals.
    """
    comps = competitors if competitors else COMPETITORS

    def query_competitor(comp):
        try:
            params = {
                "query.cond": disease,
                "query.term": comp,
                "pageSize":   20,
                "aggFilters": "status:act",
            }
            resp   = requests.get(CT_URL, params=params, timeout=10)
            studies = resp.json().get("studies", [])
            phases  = []
            for s in studies:
                phases.extend(
                    s.get("protocolSection", {}).get("designModule", {}).get("phases", [])
                )
            phase_order = {"PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}
            max_phase = max(phases, key=lambda p: phase_order.get(p, 0), default="") if phases else ""
            return {"competitor": comp, "trial_count": len(studies), "max_phase": max_phase}
        except Exception:
            return {"competitor": comp, "trial_count": 0, "max_phase": ""}

    def query_openfda():
        try:
            resp = requests.get(
                OPENFDA_URL,
                params={"search": f'indications_and_usage:"{disease}"', "limit": 5},
                timeout=10,
            )
            if resp.status_code == 200:
                return [item.get("application_number", "") for item in resp.json().get("results", [])]
        except Exception:
            pass
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
        comp_futures = {executor.submit(query_competitor, c): c for c in comps}
        fda_future   = executor.submit(query_openfda)
        signals      = [f.result() for f in concurrent.futures.as_completed(comp_futures)]
        fda_approvals = fda_future.result()

    signals.sort(key=lambda x: x["trial_count"], reverse=True)

    out = {
        "disease":               disease,
        "competitors_checked":   len(comps),
        "signals":               signals,
        "recent_fda_approvals":  len(fda_approvals),
    }
    SESSION["competitive_signals"].append(out)
    return out


# ── GenomeClaw helpers ──────────────────────────────────────────────────────────

# Boltz-1 maximum sequence length (residues)
BOLTZ_MAX_SEQ = 2048

# Known functional domains for proteins that exceed BOLTZ_MAX_SEQ.
# Used by fold_target() as an automatic fallback when the full sequence is too long.
# Format: gene_upper → (start_1based, end_1based_inclusive, domain_name)
#
# GPU constraint note: AMD Radeon R9 M370X + Metal (macOS) limits dispatch_workgroups_x
# to 65535. Boltz-1 PairFormer dispatches (2×seq_len², 1, 1) at its largest kernel,
# so the safe limit is seq_len ≤ 181 (2×181² = 65522 ≤ 65535).
# On CUDA hardware the practical limit is the Boltz-1 model max (2048 residues).
# All entries below are ≤181 aa so they fold on both GPU types.
# Verified: 181-residue fold of LRRK2 kinase domain completes in ~21 min on CPU.
KNOWN_DOMAINS: dict = {
    # LRRK2: kinase catalytic core centered on G2019S (activation loop)
    "LRRK2":   (1929, 2109, "kinase catalytic core (G2019S at 2019)"),   # 181 aa
    # BRCA2: OB3 domain hotspot (frameshift/nonsense variants cluster 2800-2970)
    "BRCA2":   (2800, 2980, "OB3 DNA-binding hotspot"),                   # 181 aa
    # ATM: C-terminal kinase activation loop; D2870 catalytic Asp
    "ATM":     (2876, 3056, "kinase catalytic loop"),                     # 181 aa
    # RYR1: N-terminal hot-spot 1; MH mutations R163C, G248R
    "RYR1":    (83,   263,  "N-terminal hot-spot 1 (MH mutations)"),      # 181 aa
    # NF1: Ras-GAP catalytic core; R1276, R1391 arginine finger mutations
    "NF1":     (1263, 1443, "GRD Ras-GAP catalytic core"),               # 181 aa
    # TTN: Z-disc proximal Ig1-2 repeats; DCM frameshift hotspot
    "TTN":     (1,    181,  "Z-disc Ig1-2 repeats"),                      # 181 aa
    # DNAPKCS: kinase activation loop + FATC domain; S2056/T2609 cluster
    "DNAPKCS": (3948, 4128, "kinase activation loop + FATC"),             # 181 aa
    # MUC16: CA-125 epitope core (used as ovarian cancer biomarker)
    "MUC16":   (14327, 14507, "CA-125 epitope core"),                     # 181 aa
}


def _fetch_uniprot_sequence(gene_symbol: str) -> str:
    """Return the canonical human protein sequence for a gene symbol, or '' on failure."""
    try:
        r = requests.get(
            UNIPROT_URL,
            params={"query": f"gene_exact:{gene_symbol} AND organism_id:9606 AND reviewed:true",
                    "fields": "sequence", "format": "json", "size": 1},
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                return results[0].get("sequence", {}).get("value", "")
    except Exception:
        pass
    return ""


def fold_target(gene_symbol_or_sequence: str) -> dict:
    """
    Predict the 3D structure of a protein using GenomeClaw Boltz-1.
    Accepts a gene symbol (looks up canonical sequence) or a raw amino-acid sequence.
    Returns pLDDT confidence, PAE, and a druggability interpretation.
    API: POST http://127.0.0.1:8083/api/fold  (async — submits then polls)
    """
    # Resolve sequence
    seq = gene_symbol_or_sequence.strip()
    is_gene = not all(c in "ACDEFGHIKLMNPQRSTVWYacdefghiklmnpqrstvwy" for c in seq)
    gene_label = seq if is_gene else f"seq[{len(seq)}aa]"
    if is_gene:
        seq = _fetch_uniprot_sequence(seq)
        if not seq:
            return {"status": "error", "note": f"Could not resolve UniProt sequence for {gene_label}"}

    # Domain fallback: if sequence exceeds Boltz-1 limit, trim to known functional domain
    domain_note = None
    if len(seq) > BOLTZ_MAX_SEQ:
        domain_info = KNOWN_DOMAINS.get(gene_label.upper()) if is_gene else None
        if domain_info:
            d_start, d_end, d_name = domain_info
            seq = seq[d_start - 1 : d_end]          # slice is 0-based, coords are 1-based inclusive
            domain_note = f"{d_name} (residues {d_start}–{d_end})"
            gene_label = f"{gene_label}[{d_name}]"
        else:
            return {
                "status": "error",
                "code":   422,
                "note":   (
                    f"Sequence too long ({len(seq)} > {BOLTZ_MAX_SEQ} residues). "
                    f"Add '{gene_label}' to KNOWN_DOMAINS or pass a domain subsequence directly."
                ),
            }

    # Submit fold job
    try:
        r = requests.post(
            f"{CLAWAPI_URL}/api/fold",
            json={"sequence": seq, "sampler": "edm", "diffusion_steps": 50, "num_recycles": 1},
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        return {"status": "genomeclaw_offline", "note": "GenomeClaw API not reachable at 127.0.0.1:8083"}

    if r.status_code not in (200, 202):
        return {"status": "error", "code": r.status_code, "body": r.text[:200]}

    job_id = r.json().get("job_id") or r.json().get("id")
    if not job_id:
        return {"status": "error", "note": "No job_id in fold response"}

    # Poll for completion — domain folds on CPU can take 10-15 min for ~180 aa
    fold_timeout = 900 if domain_note else 120
    deadline = time.time() + fold_timeout
    while time.time() < deadline:
        time.sleep(5)
        poll = requests.get(f"{CLAWAPI_URL}/api/fold/{job_id}", timeout=10)
        # Use strict=False because PDB content contains embedded newlines
        data = json.loads(poll.text, strict=False)
        if data.get("status") == "completed":
            # API fields: mean_plddt, pdb (not plddt_mean / pdb_str)
            plddt   = data.get("mean_plddt") or data.get("plddt_mean") or 0.0
            elapsed = data.get("elapsed_secs") or data.get("elapsed_ms", 0)
            pdb_preview = (data.get("pdb") or data.get("pdb_str") or "")[:200]

            # pLDDT = 0.0 means confidence scoring not yet available in this model build
            if plddt > 0.70:
                confidence_label = "Confident structure / likely druggable"
            elif plddt > 0.50:
                confidence_label = "Moderate confidence / partially disordered"
            elif plddt > 0.0:
                confidence_label = "Disordered / difficult target"
            else:
                confidence_label = "Structure predicted (pLDDT not available)"

            result = {
                "gene":             gene_label,
                "residues":         len(seq),
                "plddt_mean":       round(plddt, 3),
                "elapsed_secs":     elapsed,
                "confidence_label": confidence_label,
                "pdb_preview":      pdb_preview,
            }
            if domain_note:
                result["domain_used"] = domain_note
            SESSION["fold_results"].append(result)
            return result
        elif data.get("status") == "failed":
            return {"status": "failed", "gene": gene_label, "error": data.get("error", "")}

    return {"status": "timeout", "gene": gene_label, "note": f"Fold exceeded {fold_timeout}s — try a shorter sequence or fewer diffusion steps"}


def score_variant_effect(gene_symbol: str, variant_notation: str) -> dict:
    """
    Score the functional effect of a protein variant using GenomeClaw ESM-2.
    variant_notation: e.g. 'G12C', 'L858R', 'G2019S'
    Returns delta_log_likelihood and an interpretation (resistance risk / tolerated).
    API: POST http://127.0.0.1:8083/api/variant
    """
    import re
    m = re.match(r"^([A-Z])(\d+)([A-Z])$", variant_notation.strip().upper())
    if not m:
        return {"status": "error", "note": f"Cannot parse variant notation '{variant_notation}'. Use format: G12C"}
    wt_aa, position, mut_aa = m.group(1), int(m.group(2)), m.group(3)

    full_seq = _fetch_uniprot_sequence(gene_symbol)
    if not full_seq:
        return {"status": "error", "note": f"Could not resolve sequence for {gene_symbol}"}

    if position < 1 or position > len(full_seq):
        return {"status": "error", "note": f"Position {position} out of range for {gene_symbol} ({len(full_seq)} residues)"}

    # For large proteins (>500 res), extract a ±150 residue window around the mutation.
    # ESM-2 masked marginal scoring uses local context; the window captures the domain.
    MAX_SEQ = 500
    WINDOW = 150
    windowed = False
    seq = full_seq
    seq_position = position  # position relative to seq (may be adjusted after windowing)
    if len(full_seq) > MAX_SEQ:
        start = max(0, position - 1 - WINDOW)
        end   = min(len(full_seq), position - 1 + WINDOW + 1)
        seq = full_seq[start:end]
        seq_position = position - start  # 1-based position within the window
        windowed = True

    try:
        # /api/score uses masked marginal scoring → delta_log_likelihood
        # /api/variant uses embedding distance → cosine_similarity (different metric)
        # Timeout scales with sequence length: ~0.1s/residue for CPU streaming ESM-2
        esm_timeout = max(60, len(seq) // 5)
        r = requests.post(
            f"{CLAWAPI_URL}/api/score",
            json={"sequence": seq, "position": seq_position, "alt": mut_aa},
            timeout=esm_timeout,
        )
    except requests.exceptions.ConnectionError:
        return {"status": "genomeclaw_offline", "note": "GenomeClaw API not reachable at 127.0.0.1:8083"}

    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "body": r.text[:200]}

    data = r.json()
    # delta_log_likelihood can be None/null (NaN serialized as null in Rust serde_json).
    # This consistently occurs on AMD GPU + Metal backend (macOS) — all ESM-2 embeddings
    # are NaN due to a WGSL shader compatibility issue with this GPU/driver combination.
    # The tool is functional on CUDA (Linux) hardware.
    delta_raw = data.get("delta_log_likelihood")
    if delta_raw is None or (isinstance(delta_raw, float) and delta_raw != delta_raw):
        return {
            "status": "gpu_incompatible",
            "gene": gene_symbol,
            "variant": variant_notation.upper(),
            "note": (
                "ESM-2 GPU forward pass returned NaN — WGSL shaders are incompatible with "
                "AMD Radeon R9 M370X + Metal (macOS). Variant scoring requires CUDA hardware. "
                "Use query_genomeclaw_databases to retrieve ClinVar pathogenicity evidence "
                "for this variant from public databases instead."
            ),
        }
    delta = float(delta_raw)
    assessment = data.get("assessment", "")

    if delta < -2.0:
        interpretation = "Likely damaging — high resistance risk"
    elif delta < -0.5:
        interpretation = "Moderate effect — monitor for resistance"
    else:
        interpretation = "Tolerated — low resistance risk"

    result = {
        "gene":                  gene_symbol,
        "variant":               variant_notation.upper(),
        "wt_aa":                 wt_aa,
        "position":              position,
        "mutant_aa":             mut_aa,
        "delta_log_likelihood":  round(delta, 4),
        "api_assessment":        assessment,
        "interpretation":        interpretation,
        "wt_probability":        data.get("wildtype_probability"),
        "mut_probability":       data.get("mutant_probability"),
        "full_protein_length":   len(full_seq),
        "scored_window":         f"{position - seq_position + 1}-{position - seq_position + len(seq)}" if windowed else "full",
    }
    SESSION["variant_effects"].append(result)
    return result


def predict_admet(smiles_or_drug_name: str) -> dict:
    """
    Predict ADMET (absorption, distribution, metabolism, excretion, toxicity) properties.
    Accepts a drug name (auto-resolves SMILES from PubChem) or a SMILES string directly.
    Uses the clawadmet CLI binary from the genomeclaw workspace.
    Returns TIER-1 (all clear) / TIER-2 (minor flags) / TIER-3 (red flags).
    """
    import subprocess, shutil

    smiles = smiles_or_drug_name.strip()
    drug_label = smiles

    # Resolve SMILES from PubChem if input looks like a drug name (not SMILES)
    if not any(c in smiles for c in "()=[]#@"):
        drug_label = smiles
        try:
            pub_r = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{requests.utils.quote(smiles)}/property/IsomericSMILES/JSON",
                timeout=10,
            )
            if pub_r.status_code == 200:
                props = pub_r.json().get("PropertyTable", {}).get("Properties", [])
                if props:
                    smiles = props[0].get("IsomericSMILES") or props[0].get("SMILES") or smiles
        except Exception:
            pass

    # If SMILES still looks like a drug name (no SMILES characters), try one more lookup
    if not any(c in smiles for c in "()=[]#@\\/"):
        return {
            "status": "smiles_not_resolved",
            "drug": drug_label,
            "note": f"Could not resolve SMILES for '{drug_label}' from PubChem. Provide SMILES directly.",
        }

    # Locate clawadmet binary
    binary = os.path.join(GENOMECLAW_DIR, "target", "release", "clawadmet")
    if not os.path.isfile(binary):
        return {
            "status": "genomeclaw_not_built",
            "drug": drug_label,
            "note": "Run: cd genomeclaw && cargo build --release -p genomeclaw-cli",
        }

    try:
        proc = subprocess.run(
            [binary, "predict", "--smiles", smiles, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return {"status": "error", "drug": drug_label, "stderr": proc.stderr[:300]}

        output = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "drug": drug_label}
    except json.JSONDecodeError:
        return {"status": "error", "drug": drug_label, "note": "Could not parse clawadmet JSON output"}
    except Exception as e:
        return {"status": "error", "drug": drug_label, "note": str(e)}

    # Map results list → dict keyed by model name
    model_map = {r["model"].lower(): r for r in output.get("results", [])}
    summary_score = output.get("summary_score", 1.0)
    summary_label = output.get("summary_label", "UNKNOWN")

    def _label(model_key):
        return model_map.get(model_key, {}).get("label", "Unknown")

    def _val(model_key):
        return model_map.get(model_key, {}).get("value")

    # Tier assignment based on clawadmet labels
    # Note: label values are "hERG blocker" (bad) vs "hERG non-blocker" (good)
    red_flags = []
    herg_label = _label("herg").lower()
    if "blocker" in herg_label and "non-blocker" not in herg_label:
        red_flags.append("hERG blocker (cardiac QTc risk)")
    ames_label = _label("ames").lower()
    if "positive" in ames_label and "non-mutagenic" not in ames_label and "negative" not in ames_label:
        red_flags.append("Ames positive (mutagenic)")
    if summary_label == "RISKY":
        red_flags.append(f"Low summary score ({summary_score:.2f})")

    minor_flags = []
    herg_val = _val("herg") or 0
    if 0.3 < herg_val <= 0.5:
        minor_flags.append("Moderate hERG signal")
    bbb_label = _label("bbb")
    if "non-penetrant" in bbb_label.lower() and any(w in drug_label.lower() for w in ["neuro", "brain", "cns", "alzheimer", "parkinson"]):
        minor_flags.append("Low BBB penetration (consider for CNS targets)")
    solubility_label = _label("solubility")
    if "poorly" in solubility_label.lower():
        minor_flags.append("Poor aqueous solubility")

    if red_flags:
        tier = "TIER-3"
    elif minor_flags:
        tier = "TIER-2"
    else:
        tier = "TIER-1"

    result = {
        "drug":           drug_label,
        "smiles_used":    smiles[:80],
        "tier":           tier,
        "summary_score":  round(summary_score, 3),
        "summary_label":  summary_label,
        "hERG":           _label("herg"),
        "BBB":            _label("bbb"),
        "Ames_mutagenicity": _label("ames"),
        "Solubility":     _label("solubility"),
        "HalfLife":       _label("halflife"),
        "CYP3A4":         _label("cyp3a4"),
        "CYP2D6":         _label("cyp2d6"),
        "Pgp_efflux":     _label("pgp"),
        "PPB":            _label("ppb"),
        "red_flags":      red_flags,
        "minor_flags":    minor_flags,
        "all_predictions": [{"model": r["model"], "label": r["label"], "confidence": r["confidence"]}
                            for r in output.get("results", [])],
    }
    SESSION["admet_profiles"].append(result)
    return result


def query_genomeclaw_databases(gene_or_drug: str, databases: list = None) -> dict:
    """
    Query multiple biomedical databases for a gene symbol or drug name.
    Supported: gnomad (pLI/LoF intolerance), chembl (bioactivity), clinvar (pathogenic variants),
               string (protein interactions), cbioportal (cancer alteration frequency),
               opentargets (disease associations).
    Returns per-DB results and a composite target-richness score.
    """
    import concurrent.futures

    dbs = [d.lower() for d in databases] if databases else ["gnomad", "chembl", "clinvar", "string", "cbioportal", "opentargets"]
    db_results = {}

    def _query_gnomad(target):
        """gnomAD v4 constraint metrics via GraphQL."""
        q = """{ gene(gene_symbol: "%s", reference_genome: GRCh38) {
                   gnomad_constraint { pLI exp_lof obs_lof lof_z }
                   symbol chrom start stop } }""" % target
        r = requests.post("https://gnomad.broadinstitute.org/api", json={"query": q}, timeout=12)
        gene = r.json().get("data", {}).get("gene") or {}
        c = gene.get("gnomad_constraint") or {}
        if not c:
            return {"status": "no_data", "note": "Gene not found in gnomAD or no constraint data"}
        pli = c.get("pLI")
        return {
            "pLI":     round(pli, 3) if pli is not None else None,
            "obs_lof": c.get("obs_lof"),
            "exp_lof": c.get("exp_lof"),
            "lof_z":   round(c.get("lof_z") or 0, 3),
            "lof_intolerant": pli > 0.9 if pli else None,
            "interpretation": "High LoF intolerance — loss-of-function likely pathogenic" if pli and pli > 0.9
                else ("Moderate LoF constraint" if pli and pli > 0.5 else "LoF tolerant — unlikely essential gene"),
        }

    def _query_chembl(target):
        """ChEMBL REST API — compound bioactivity for gene target."""
        # First resolve target to ChEMBL ID
        url = f"https://www.ebi.ac.uk/chembl/api/data/target/search?q={target}&format=json&limit=1"
        r = requests.get(url, timeout=10)
        targets_data = r.json().get("targets", [])
        if not targets_data:
            return {"status": "no_target", "note": f"{target} not found in ChEMBL target list"}
        chembl_id = targets_data[0]["target_chembl_id"]
        target_name = targets_data[0].get("pref_name", target)

        # Get bioactivities
        act_url = f"https://www.ebi.ac.uk/chembl/api/data/activity?target_chembl_id={chembl_id}&standard_type=IC50&format=json&limit=10&order_by=standard_value"
        act_r = requests.get(act_url, timeout=10)
        activities = act_r.json().get("activities", [])
        best_ic50 = None
        if activities:
            vals = [a.get("standard_value") for a in activities if a.get("standard_value")]
            if vals:
                best_ic50 = float(min(vals))

        # Count total assays
        count_url = f"https://www.ebi.ac.uk/chembl/api/data/activity?target_chembl_id={chembl_id}&format=json&limit=1"
        count_r = requests.get(count_url, timeout=10)
        total = count_r.json().get("page_meta", {}).get("total_count", 0)

        return {
            "chembl_id":     chembl_id,
            "target_name":   target_name,
            "total_assays":  total,
            "best_IC50_nM":  round(best_ic50, 2) if best_ic50 else None,
            "best_pIC50":    round(9 - (best_ic50 / 1e9 if best_ic50 else 0), 2) if best_ic50 else None,
            "druggability_comment": "Well-validated target" if total > 100 else ("Some chemical matter" if total > 10 else "Limited bioactivity data"),
        }

    def _query_clinvar(target):
        """NCBI ClinVar E-utilities — pathogenic variants for a gene."""
        search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term={target}[gene]+AND+pathogenic[clinical_significance]&retmode=json&retmax=1"
        r = requests.get(search_url, timeout=10)
        data = r.json().get("esearchresult", {})
        count = int(data.get("count", 0))
        return {
            "pathogenic_variants": count,
            "interpretation": f"{count} pathogenic/likely-pathogenic variants — {'high' if count > 50 else 'moderate' if count > 10 else 'low'} disease-gene burden",
        }

    def _query_string(target):
        """STRING-DB v12 — protein interaction partners."""
        url = f"https://string-db.org/api/json/network?identifiers={target}&species=9606&limit=5&caller_identity=roche_ai_factory"
        r = requests.get(url, timeout=12)
        interactions = r.json() if r.status_code == 200 else []
        if not interactions:
            return {"status": "no_interactions", "note": "No STRING interactions found"}
        partners = []
        seen = set()
        for item in interactions[:10]:
            for field in ["preferredName_A", "preferredName_B"]:
                name = item.get(field, "")
                if name and name.upper() != target.upper() and name not in seen:
                    partners.append({"protein": name, "score": item.get("score", 0)})
                    seen.add(name)
        partners.sort(key=lambda x: -x["score"])
        return {
            "top_interactors": partners[:5],
            "interaction_count": len(interactions),
            "note": "High-confidence STRING v12 interactions (score ≥400)",
        }

    def _query_cbioportal(target):
        """cBioPortal REST API — mutation/CNA frequency across TCGA studies."""
        url = f"https://www.cbioportal.org/api/genes/{target}?projection=SUMMARY"
        r = requests.get(url, timeout=12)
        if r.status_code != 200:
            return {"status": "not_found", "note": f"{target} not in cBioPortal gene list"}
        gene_info = r.json()
        entrez_id = gene_info.get("entrezGeneId")

        # Get mutation counts across all studies
        mut_url = f"https://www.cbioportal.org/api/genes/{target}/mutations?projection=SUMMARY&pageSize=1"
        mut_r = requests.get(mut_url, timeout=12)
        # Use cancer type hotspot as proxy
        return {
            "hugo_symbol":   gene_info.get("hugoGeneSymbol", target),
            "entrez_id":     entrez_id,
            "gene_type":     gene_info.get("type", "Unknown"),
            "note":          "cBioPortal gene confirmed. Run monitor_competitive_signals for alteration frequency by cancer type.",
        }

    def _query_opentargets(target):
        """Open Targets — disease associations (already used by get_biology, here for cross-reference)."""
        q = """query($s:String!){search(queryString:$s,entityNames:["target"],page:{index:0,size:1}){
                 hits{id score object{... on Target{id approvedSymbol associatedDiseases(page:{index:0,size:5}){
                   rows{score disease{name}}}}}}}}"""
        r = requests.post(OT_URL, json={"query": q, "variables": {"s": target}}, timeout=10)
        hits = r.json().get("data", {}).get("search", {}).get("hits", [])
        if not hits:
            return {"status": "not_found"}
        obj = hits[0].get("object", {})
        rows = obj.get("associatedDiseases", {}).get("rows", [])
        return {
            "ensembl_id":     obj.get("id"),
            "top_diseases":   [{"disease": row["disease"]["name"], "score": round(row["score"], 3)} for row in rows[:5]],
            "max_score":      round(rows[0]["score"], 3) if rows else 0,
        }

    # Execute queries in parallel
    query_map = {
        "gnomad":       _query_gnomad,
        "chembl":       _query_chembl,
        "clinvar":      _query_clinvar,
        "string":       _query_string,
        "cbioportal":   _query_cbioportal,
        "opentargets":  _query_opentargets,
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(query_map[db], gene_or_drug): db for db in dbs if db in query_map}
        for fut in concurrent.futures.as_completed(futures):
            db = futures[fut]
            try:
                db_results[db] = fut.result()
            except Exception as e:
                db_results[db] = {"status": "error", "note": str(e)[:150]}

    evidence_sources = sum(1 for v in db_results.values()
                           if isinstance(v, dict) and v.get("status") not in ("error", "not_found", "no_data", "no_target", "no_interactions"))

    # Derive summary
    summary = {}
    if "gnomad" in db_results:
        summary["pLI"] = db_results["gnomad"].get("pLI")
        summary["lof_intolerant"] = db_results["gnomad"].get("lof_intolerant")
    if "chembl" in db_results:
        summary["chembl_assays"]   = db_results["chembl"].get("total_assays")
        summary["chembl_best_IC50_nM"] = db_results["chembl"].get("best_IC50_nM")
    if "clinvar" in db_results:
        summary["pathogenic_variants"] = db_results["clinvar"].get("pathogenic_variants")
    if "string" in db_results:
        summary["top_interactors"] = [i["protein"] for i in db_results["string"].get("top_interactors", [])]
    if "opentargets" in db_results:
        summary["ot_max_score"] = db_results["opentargets"].get("max_score")
        summary["top_disease"]  = (db_results["opentargets"].get("top_diseases") or [{}])[0].get("disease")

    result = {
        "target":                gene_or_drug,
        "databases_queried":     list(db_results.keys()),
        "evidence_sources":      evidence_sources,
        "target_richness_score": round(evidence_sources / max(len(dbs), 1), 2),
        "summary":               summary,
        "details":               db_results,
    }
    SESSION.setdefault("variant_effects", [])
    return result


# ── Tool definitions for Claude ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_roche_trials",
        "description": "Search ClinicalTrials.gov for active Roche/Genentech trials in a therapeutic area. Returns trial count, NCT IDs, drugs, and phases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Disease or therapeutic area to search (e.g. 'breast cancer', 'Alzheimer', 'neurology')"},
                "phase": {"type": "string", "description": "Optional phase filter: '1', '2', '3', '4'"},
            },
            "required": ["therapeutic_area"],
        },
    },
    {
        "name": "get_biology",
        "description": "Query Open Targets for the top disease associations of a drug name or gene symbol. Returns bio-confidence scores per disease.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Drug name (e.g. 'Giredestrant') or gene symbol (e.g. 'ESR1')"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "check_competitor_trials",
        "description": "Check how many trials a competitor (AstraZeneca, Eli Lilly, Novartis, Pfizer, etc.) has for a given disease.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease":    {"type": "string", "description": "Disease name"},
                "competitor": {"type": "string", "description": "Competitor name"},
            },
            "required": ["disease", "competitor"],
        },
    },
    {
        "name": "find_gaps",
        "description": "Core strategic analysis: cross-references Open Targets biology with Roche's clinical pipeline to surface high-evidence indications with zero Roche trials.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Therapeutic area to analyse"},
                "min_bio_score":    {"type": "number",  "description": "Minimum Open Targets confidence score (0.0–1.0, default 0.60)"},
            },
            "required": ["therapeutic_area"],
        },
    },
    {
        "name": "save_to_cache",
        "description": "Save a discovery or gap finding to the intelligence cache for future retrieval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "Discovery data to persist"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "rank_portfolio",
        "description": "Score and rank all Roche portfolio assets by composite opportunity: bio_score × unexplored indications × competitive vacuum. Loads from roche_pipeline.json if no assets provided.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "description": "Optional list of assets to rank. Each item needs 'name' and 'id' (Ensembl ID). Omit to use the full portfolio.",
                    "items": {"type": "object"},
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_combinations",
        "description": "Find Roche drug pairs that target complementary pathways in the same disease by analysing combination arms on ClinicalTrials.gov.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease": {"type": "string", "description": "Disease to search for combination trials (e.g. 'breast cancer', 'NSCLC')"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "scan_literature",
        "description": "Search Europe PMC + ArXiv in parallel for recent publications linking a drug or gene target to a specific disease. Returns titles, journals, and dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target":   {"type": "string",  "description": "Drug name or gene symbol (e.g. 'Giredestrant' or 'ESR1')"},
                "disease":  {"type": "string",  "description": "Disease name (e.g. 'breast cancer')"},
                "min_year": {"type": "integer", "description": "Optional: only return papers published in this year or later (e.g. 2024)"},
            },
            "required": ["target", "disease"],
        },
    },
    {
        "name": "generate_pdf_report",
        "description": "Generate a full structured PDF report from all findings in this session. Call this as the FINAL step after all analysis is complete. The report includes gap analysis, portfolio ranking, combination opportunities, literature, and regulatory pathways.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename":    {"type": "string", "description": "Output filename (optional, auto-generated if omitted)"},
                "ceo_summary": {"type": "string", "description": "2-4 sentence executive summary written by the agent summarising the key findings and top recommended actions"},
            },
            "required": [],
        },
    },
    {
        "name": "map_regulatory_path",
        "description": "Map the regulatory pathway for a drug + indication: primary endpoint, required biomarker, companion diagnostic, and expedited pathway eligibility.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drug":       {"type": "string", "description": "Drug name"},
                "indication": {"type": "string", "description": "Target indication"},
            },
            "required": ["drug", "indication"],
        },
    },
    {
        "name": "scan_arxiv",
        "description": "Search ArXiv preprints for a target + disease. Surfaces science 6-18 months before peer review, including ML-assisted drug design and AlphaFold papers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target":      {"type": "string",  "description": "Drug name or gene symbol"},
                "disease":     {"type": "string",  "description": "Disease name"},
                "max_results": {"type": "integer", "description": "Max papers to return (default 10)"},
                "min_year":    {"type": "integer", "description": "Optional: only return papers from this year or later (e.g. 2024)"},
            },
            "required": ["target", "disease"],
        },
    },
    {
        "name": "score_trial_outcome",
        "description": "Estimate the likelihood of trial success (0.0–1.0) based on phase, enrollment, endpoint, and trial design. Helps filter 120 gaps down to the ~30 worth pursuing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nct_id_or_drug": {"type": "string", "description": "NCT ID (e.g. NCT04567890) or drug name to look up"},
                "indication":     {"type": "string", "description": "Target indication"},
            },
            "required": ["nct_id_or_drug", "indication"],
        },
    },
    {
        "name": "find_repurposing_candidates",
        "description": "Find approved drugs (Phase 4) that could be repositioned into a new indication — skipping Phase I entirely. Fastest path to clinic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_or_disease": {"type": "string", "description": "Disease name or gene target to search repurposing opportunities for"},
            },
            "required": ["target_or_disease"],
        },
    },
    {
        "name": "check_orphan_eligibility",
        "description": "Check if a disease qualifies for Orphan Drug Designation (US: <200K patients, EU: <165K). Returns eligibility, 7yr exclusivity, tax credits, and fee waiver benefits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease": {"type": "string", "description": "Disease name to check for rare disease / orphan eligibility"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "get_protein_structure_context",
        "description": "Validate target druggability before committing R&D resources. Queries UniProt for protein function + binding sites and Open Targets for tractability scores. Returns recommended modality (small molecule / antibody / PROTAC).",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol": {"type": "string", "description": "HGNC gene symbol (e.g. LRRK2, ESR1, KRAS)"},
            },
            "required": ["gene_symbol"],
        },
    },
    {
        "name": "monitor_competitive_signals",
        "description": "Live 8-competitor activity dashboard for a disease. Fires all ClinicalTrials.gov queries in parallel and checks openFDA for recent approvals. Replaces static competitive_intel.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease":     {"type": "string", "description": "Disease to monitor competitive activity for"},
                "competitors": {"type": "array", "items": {"type": "string"}, "description": "Optional custom competitor list (defaults to 8 major pharma companies)"},
            },
            "required": ["disease"],
        },
    },
    {
        "name": "find_shared_targets",
        "description": "Find gene targets shared between two diseases above a confidence threshold. Answers: 'What targets are shared between Alzheimer's and Parkinson's with bio score > 0.7?' Uses Open Targets in parallel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease1":   {"type": "string", "description": "First disease name (e.g. 'Alzheimer disease')"},
                "disease2":   {"type": "string", "description": "Second disease name (e.g. 'Parkinson disease')"},
                "min_score":  {"type": "number", "description": "Minimum Open Targets confidence score (default 0.70)"},
            },
            "required": ["disease1", "disease2"],
        },
    },
    {
        "name": "bulk_scan_literature",
        "description": "Scan Europe PMC for recent publications across multiple targets in parallel. Answers: 'Which Roche targets had new publications in the last 6 months?' Much faster than calling scan_literature for each target individually.",
        "input_schema": {
            "type": "object",
            "properties": {
                "targets":     {"type": "array", "items": {"type": "string"}, "description": "List of gene symbols or drug names to scan"},
                "months_back": {"type": "integer", "description": "How many months back to search (default 6)"},
            },
            "required": ["targets"],
        },
    },
    {
        "name": "fold_target",
        "description": "Predict the 3D protein structure of a gene target using GenomeClaw Boltz-1. Returns pLDDT confidence score and druggability interpretation. Use BEFORE committing R&D to a target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol_or_sequence": {"type": "string", "description": "HGNC gene symbol (e.g. LRRK2) or raw amino-acid sequence"},
            },
            "required": ["gene_symbol_or_sequence"],
        },
    },
    {
        "name": "score_variant_effect",
        "description": "Score the functional effect of a protein variant using GenomeClaw ESM-2. Returns delta log-likelihood and resistance risk interpretation. Use for known resistance mutations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_symbol":       {"type": "string", "description": "HGNC gene symbol (e.g. KRAS, EGFR, LRRK2)"},
                "variant_notation":  {"type": "string", "description": "Variant in standard notation (e.g. G12C, L858R, G2019S)"},
            },
            "required": ["gene_symbol", "variant_notation"],
        },
    },
    {
        "name": "predict_admet",
        "description": "Predict ADMET properties (hERG, BBB, hepatotoxicity, oral bioavailability) for a drug. Assigns TIER-1 (all clear) / TIER-2 (minor flags) / TIER-3 (red flags). Use to filter repurposing candidates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "smiles_or_drug_name": {"type": "string", "description": "Drug name (e.g. levodopa) or SMILES string"},
            },
            "required": ["smiles_or_drug_name"],
        },
    },
    {
        "name": "query_genomeclaw_databases",
        "description": "Query gnomAD, ChemBL, BindingDB, ClinVar, STRING, and cBioPortal via genomeclaw-data. Returns binding constants, pLI scores, variant counts, and a target-richness score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gene_or_drug": {"type": "string", "description": "Gene symbol or drug name to query"},
                "databases":    {"type": "array", "items": {"type": "string"}, "description": "Databases to query (default: all 6). Options: gnomad, chembl, bindingdb, clinvar, string, cbioportal"},
            },
            "required": ["gene_or_drug"],
        },
    },
    {
        "name": "list_pipeline_assets",
        "description": "Fast lookup of Roche/Genentech pipeline assets from enriched knowledge base. Returns phase, status, therapeutic area, indication, modality, mechanism, and safety signals. No API calls. Use this before rank_portfolio for context, or to answer 'what does Roche have in neurology/oncology/phase 3?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Filter by therapeutic area (e.g. 'Oncology', 'Neurology', 'Hematology', 'Immunology', 'Dermatology', 'Ophthalmology', 'Rare')"},
                "phase":            {"type": "string", "description": "Filter by phase: 'approved', '3', '2', '1', 'discontinued', 'partner_licensed'"},
                "status":           {"type": "string", "description": "Filter by status: 'active', 'approved', 'discontinued', 'partner_licensed'"},
                "modality":         {"type": "string", "description": "Filter by modality: 'mAb', 'bispecific', 'small_molecule', 'ADC', 'ASO', 'mRNA', 'protein', 'other'"},
            },
            "required": [],
        },
    },
    {
        "name": "query_competitive_intel",
        "description": "Query static competitive intelligence knowledge base. Returns competitor assets by therapeutic area, competitor name, or indication. Covers AZ, Lilly, Novartis, BMS, Pfizer, MSD, AbbVie, J&J across oncology/neurology/immunology/rare/ophthalmology. No API calls — use for initial context before monitor_competitive_signals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "therapeutic_area": {"type": "string", "description": "Filter by area (e.g. 'oncology', 'neurology', 'immunology', 'rare_disease', 'ophthalmology', 'metabolic')"},
                "competitor":       {"type": "string", "description": "Filter by competitor name (e.g. 'AstraZeneca', 'Eli Lilly', 'Novartis', 'AbbVie', 'Pfizer', 'Merck', 'Johnson & Johnson', 'Bristol-Myers Squibb')"},
                "indication":       {"type": "string", "description": "Filter by indication keyword (e.g. 'breast cancer', 'multiple sclerosis', 'atopic dermatitis')"},
            },
            "required": [],
        },
    },
]

TOOL_FN_MAP = {
    "search_roche_trials":         search_roche_trials,
    "get_biology":                 get_biology,
    "check_competitor_trials":     check_competitor_trials,
    "find_gaps":                   find_gaps,
    "save_to_cache":               save_to_cache,
    "rank_portfolio":              rank_portfolio,
    "find_combinations":           find_combinations,
    "scan_literature":             scan_literature,
    "map_regulatory_path":         map_regulatory_path,
    "generate_pdf_report":         generate_pdf_report,
    "scan_arxiv":                  scan_arxiv,
    "score_trial_outcome":         score_trial_outcome,
    "find_repurposing_candidates": find_repurposing_candidates,
    "check_orphan_eligibility":    check_orphan_eligibility,
    "get_protein_structure_context": get_protein_structure_context,
    "monitor_competitive_signals":  monitor_competitive_signals,
    "find_shared_targets":          find_shared_targets,
    "bulk_scan_literature":         bulk_scan_literature,
    "fold_target":                  fold_target,
    "score_variant_effect":         score_variant_effect,
    "predict_admet":                predict_admet,
    "query_genomeclaw_databases":   query_genomeclaw_databases,
    "list_pipeline_assets":         list_pipeline_assets,
    "query_competitive_intel":      query_competitive_intel,
}


def make_client() -> anthropic.Anthropic:
    """
    Build an Anthropic client from the best available credential, in priority order:

    1. ANTHROPIC_API_KEY     — Standard API key (env var or config file) — direct API
    2. ANTHROPIC_AUTH_TOKEN  — OAuth subscription token → routed via local proxy
    3. No credentials        — error with instructions

    The local proxy (proxy_server.py) converts Anthropic SDK calls into `claude -p`
    subprocess calls, allowing subscription users to run the agent without separate
    API billing credits.
    """
    config_path = "configs/api_keys.json"
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)

    api_key    = os.environ.get("ANTHROPIC_API_KEY")    or cfg.get("ANTHROPIC_API_KEY")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or cfg.get("ANTHROPIC_AUTH_TOKEN")

    if api_key and not api_key.startswith("sk-ant-YOUR"):
        print("[Auth] Using API key → direct Anthropic API")
        return anthropic.Anthropic(api_key=api_key)

    if auth_token:
        print("[Auth] Subscription token detected → starting local proxy")
        from proxy_server import start_proxy
        port = start_proxy()
        # SDK points to local proxy; api_key value is ignored by the proxy
        return anthropic.Anthropic(
            base_url=f"http://127.0.0.1:{port}",
            api_key="proxy-auth",
        )

    print("ERROR: No Anthropic credentials found.")
    print()
    print("Set one of the following:")
    print("  API key (direct):    export ANTHROPIC_API_KEY=sk-ant-api03-...")
    print("  Subscription proxy:  export ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...")
    print(f"  Config file:         add either key to {config_path}")
    sys.exit(1)

SYSTEM_PROMPT = """You are the Roche AI Factory Strategic Discovery Agent.

Roche and Genentech are the same company. Always treat them as one entity.

You have 24 tools available:

DISCOVERY
- search_roche_trials    → What trials does Roche/Genentech have active in an area?
- get_biology            → What diseases does a drug or gene target have strong evidence for?
- find_gaps              → Where is biology strong but Roche has no trial? (core analysis)

COMPETITIVE & PORTFOLIO
- check_competitor_trials     → Is AZ / Lilly / Novartis already in a gap? (live CT.gov query)
- monitor_competitive_signals → Live 8-competitor activity table for a disease (parallel queries)
- query_competitive_intel     → Fast offline: competitor assets, mechanisms, phase for AZ/Lilly/Novartis/etc. (30+ programs)
- rank_portfolio               → Score all portfolio assets by composite opportunity (OT + CT.gov)
- list_pipeline_assets         → Fast offline: Roche pipeline by TA/phase/modality with enriched metadata (no API calls)
- find_combinations            → Which Roche drugs target complementary pathways in the same disease?

EVIDENCE & REGULATORY
- scan_literature             → Recent peer-reviewed papers (Europe PMC + ArXiv in parallel); supports min_year filter
- scan_arxiv                  → ArXiv preprints only (6-18mo ahead of peer review); supports min_year filter
- bulk_scan_literature        → Scan all portfolio targets for recent papers in parallel (use for "last 6 months" questions)
- map_regulatory_path         → What endpoint, biomarker, CDx, and expedited pathway does FDA require? (30+ indications)
- score_trial_outcome         → Likelihood of trial success (0.0–1.0 score + risk factors)

TARGET INTELLIGENCE
- get_protein_structure_context → Is this target actually druggable? (UniProt + OT tractability + 3D fold)
- find_repurposing_candidates   → Approved drugs that could skip Phase I into a new indication
- check_orphan_eligibility      → Orphan Drug Designation eligibility + 7yr exclusivity + tax credits
- find_shared_targets           → Gene targets shared between two diseases above a confidence threshold

MEMORY
- save_to_cache               → Persist findings to intelligence_cache.json
- generate_pdf_report         → Final step — full structured PDF report

GENOMECLAW TOOLS (local Boltz-1/ESM-2 API at 127.0.0.1:8083)
- fold_target                 → Predict 3D protein structure + pLDDT confidence (actual binding pocket geometry)
- score_variant_effect        → Does this mutation damage drug binding? (delta log-likelihood, resistance risk)
- predict_admet               → Filter repurposing candidates by toxicity + PK (TIER-1/2/3)
- query_genomeclaw_databases  → gnomAD/ChemBL/BindingDB/ClinVar/STRING/cBioPortal in one call

WORKFLOW GUIDANCE
- For gap questions: find_gaps → monitor_competitive_signals → scan_literature → map_regulatory_path → save_to_cache
- For portfolio overview: list_pipeline_assets(therapeutic_area=...) → rank_portfolio → find_gaps on top assets
- For competitive landscape: query_competitive_intel(therapeutic_area=...) → monitor_competitive_signals(disease) → check_competitor_trials
- For new target gaps: get_protein_structure_context → fold_target → score_variant_effect → query_genomeclaw_databases
- For cross-disease targets: find_shared_targets(disease1, disease2) → get_biology on top hits → find_gaps
- For portfolio literature pulse: bulk_scan_literature(all_targets, months_back=6) → scan_literature on top hits
- For date-filtered literature: scan_literature(target, disease, min_year=2024) or scan_arxiv(..., min_year=2024)
- For repurposing: find_repurposing_candidates → predict_admet → filter TIER-1 only → map_regulatory_path
- For combination questions: find_combinations → get_biology on each drug → scan_literature
- For regulatory questions: map_regulatory_path returns full FDA endpoint/biomarker/CDx/expedited pathway guidance
- For competitive urgency: monitor_competitive_signals → score_trial_outcome → score_variant_effect on known resistance mutations
- Always save high-value findings before ending
- ALWAYS call generate_pdf_report as the very last step with a concise ceo_summary

Reason step by step. Never guess tool results. Prioritise gaps with bio score > 0.70.
Final output must be concise and CEO-ready with clear action items."""


# ── Agent loop ──────────────────────────────────────────────────────────────────

def run_agent(question: str, model: str = MODEL):
    print("\n" + "=" * 65)
    print(f"  ROCHE AI FACTORY — STRATEGIC DISCOVERY AGENT")
    print(f"  Query: {question}")
    print("=" * 65 + "\n")

    SESSION["question"] = question
    client   = make_client()
    messages = [{"role": "user", "content": question}]
    turn     = 0

    while True:
        turn += 1
        print(f"── Turn {turn} ──────────────────────────────────────────────")

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Collect text reasoning and tool calls
        tool_calls = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[Reasoning]\n{block.text.strip()}\n")
            elif block.type == "tool_use":
                tool_calls.append(block)

        # If no tool calls, agent is done
        if response.stop_reason == "end_turn" or not tool_calls:
            print("\n" + "=" * 65)
            print("  AGENT COMPLETE")
            print("=" * 65)
            break

        # Execute tool calls and collect results
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []

        for call in tool_calls:
            fn   = TOOL_FN_MAP.get(call.name)
            args = call.input
            print(f"[Tool] {call.name}({json.dumps(args, separators=(',', ':'))})")

            if fn:
                try:
                    result = fn(**args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"Unknown tool: {call.name}"}

            result_str = json.dumps(result)
            print(f"       → {result_str[:200]}{'...' if len(result_str) > 200 else ''}\n")
            if call.name == "generate_pdf_report" and result.get("file"):
                print(f"\n📄 PDF REPORT SAVED → {result['file']}\n")
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": call.id,
                "content":     json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})


# ── Entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Roche AI Factory Strategic Discovery Agent")
    parser.add_argument("question", help="Strategic question to answer")
    parser.add_argument("--model", default=MODEL,
                        help="Claude model ID (default: %(default)s)")
    args = parser.parse_args()
    run_agent(args.question, model=args.model)
