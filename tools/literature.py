import time
import random
import datetime
import concurrent.futures
import requests
import arxiv
from tools.session import SESSION
from tools.constants import ARXIV_CATS

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
            r.raise_for_status()
            results = r.json().get("resultList", {}).get("result", [])
        except Exception as exc:
            return {"_epmc_error": str(exc)}
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
        epmc_result  = fut_epmc.result()
        arxiv_papers = fut_arxiv.result()

    # epmc_result is either a list of papers or an error dict
    epmc_error = None
    if isinstance(epmc_result, dict) and "_epmc_error" in epmc_result:
        epmc_error = epmc_result["_epmc_error"]
        epmc_papers = []
    else:
        epmc_papers = epmc_result

    all_papers = sorted(
        epmc_papers + arxiv_papers,
        key=lambda x: x.get("date", ""),
        reverse=True,
    )[:max_results]

    out = {
        "status":       "ok" if not epmc_error else "partial",
        "target":       target,
        "disease":      disease,
        "min_year":     min_year,
        "papers_found": len(all_papers),
        "papers":       all_papers,
    }
    if epmc_error:
        out["epmc_error"] = epmc_error
    SESSION["literature"].append(out)
    return out


def bulk_scan_literature(targets: list, months_back: int = 6) -> dict:
    """
    Scan literature for multiple targets in parallel.
    Answers: "Which RedClaw targets had new high-impact publications in the last N months?"
    targets: list of gene symbols or drug names.
    months_back: how far back to search (default 6 months).
    """
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
