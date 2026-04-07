"""
Patent landscape tools for the drug-discovery agent.

Two data sources (both free, no API key required for basic use):
  - USPTO PatentsView REST API  — US patents, assignee/inventor search
  - Lens.org API                — global patents (US, EP, WO, CN, JP, AU)
    Requires LENS_API_KEY env var for >10 req/day; free tier registration at lens.org

Functions exposed to the agent:
  search_patents(query, assignee, years_back)
  get_patent_landscape(target_or_compound, years_back)
"""

import os
import datetime
import requests
from typing import List, Dict, Any
from tools.session import SESSION

PATENTSVIEW_URL = "https://api.patentsview.org/patents/query"
LENS_URL        = "https://api.lens.org/patent/search"
LENS_API_KEY    = os.environ.get("LENS_API_KEY", "")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _patentsview_search(query_str: str, assignee: str = "", years_back: int = 10) -> List[Dict]:
    """Search USPTO PatentsView. Returns list of patent dicts."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=365 * years_back)).isoformat()

    and_clauses = [
        {"_gte": {"patent_date": cutoff}},
        {"_or": [
            {"_text_phrase": {"patent_title":    query_str}},
            {"_text_phrase": {"patent_abstract": query_str}},
        ]},
    ]
    if assignee:
        and_clauses.append({"_contains": {"assignee_organization": assignee}})

    payload = {
        "q":  {"_and": and_clauses},
        "f":  ["patent_number", "patent_title", "patent_date",
               "patent_abstract", "assignee_organization",
               "inventor_last_name", "patent_type"],
        "o":  {"patent_date": "desc"},
        "per_page": 25,
    }
    try:
        r = requests.post(PATENTSVIEW_URL, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("patents") or []
    except Exception as e:
        return [{"error": str(e), "source": "patentsview"}]


def _lens_search(query_str: str, years_back: int = 10) -> List[Dict]:
    """Search Lens.org global patent database."""
    if not LENS_API_KEY:
        return []  # skip silently if no key configured

    cutoff_year = datetime.date.today().year - years_back
    payload = {
        "query": {
            "bool": {
                "must": [{"query_string": {"query": query_str, "fields": ["title", "abstract"]}}],
                "filter": [{"range": {"date_published": {"gte": f"{cutoff_year}-01-01"}}}],
            }
        },
        "size": 20,
        "sort": [{"date_published": "desc"}],
        "include": ["lens_id", "title", "abstract", "date_published",
                    "applicant", "inventor", "jurisdiction", "patent_citations_count"],
    }
    headers = {"Authorization": f"Bearer {LENS_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.post(LENS_URL, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        hits = r.json().get("data", [])
        return [
            {
                "lens_id":      h.get("lens_id"),
                "title":        h.get("title", ""),
                "abstract":     (h.get("abstract") or "")[:400],
                "date":         h.get("date_published", ""),
                "jurisdiction": h.get("jurisdiction", ""),
                "applicants":   [a.get("name", "") for a in (h.get("applicant") or [])],
                "citations":    h.get("patent_citations_count", 0),
                "source":       "lens",
            }
            for h in hits
        ]
    except Exception as e:
        return [{"error": str(e), "source": "lens"}]


def _fmt_patents(patents: List[Dict]) -> List[Dict]:
    """Normalise PatentsView records to a common schema."""
    out = []
    for p in patents:
        if "error" in p:
            continue
        orgs = p.get("assignees") or []
        assignees = [
            a.get("assignee_organization") or ""
            for a in (orgs if isinstance(orgs, list) else [orgs])
        ] if orgs else []
        out.append({
            "patent_number": p.get("patent_number", ""),
            "title":         p.get("patent_title", ""),
            "date":          p.get("patent_date", ""),
            "abstract":      (p.get("patent_abstract") or "")[:400],
            "assignees":     assignees,
            "source":        "patentsview",
        })
    return out


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def search_patents(
    query: str,
    assignee: str = "",
    years_back: int = 10,
) -> dict:
    """
    Search US and global patents for a compound, target, or technology keyword.

    Args:
        query:      Free-text search term (e.g. "EGFR inhibitor erlotinib",
                    "KRAS G12C covalent", "Boltz protein folding").
        assignee:   Filter by organisation name fragment (e.g. "Roche", "AstraZeneca").
        years_back: How many years of patent history to include (default 10).

    Returns dict with:
        total         — total patents found
        patents       — list of {patent_number, title, date, abstract, assignees}
        query         — the search term used
        sources       — data sources queried
    """
    pv_raw   = _patentsview_search(query, assignee, years_back)
    lens_raw = _lens_search(query, years_back)

    pv_patents   = _fmt_patents(pv_raw)
    lens_patents = lens_raw  # already normalised

    # Deduplicate by title similarity (simple: exact title match)
    seen_titles = set()
    combined = []
    for p in pv_patents + lens_patents:
        key = (p.get("title") or "").lower().strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            combined.append(p)

    SESSION.setdefault("patents", [])
    SESSION["patents"].extend(combined[:30])

    sources = ["patentsview"]
    if LENS_API_KEY:
        sources.append("lens.org")

    return {
        "query":   query,
        "total":   len(combined),
        "patents": combined[:30],
        "sources": sources,
    }


def get_patent_landscape(
    target_or_compound: str,
    years_back: int = 10,
) -> dict:
    """
    Build an IP landscape for a drug target or compound name.

    Queries both "target_or_compound inhibitor" and "target_or_compound antagonist"
    to capture the full IP space, then ranks assignees by filing volume.

    Args:
        target_or_compound: Gene symbol or compound name (e.g. "EGFR", "sotorasib",
                            "CDK4/6 inhibitor", "KRAS G12C").
        years_back:         Patent lookback window (default 10).

    Returns dict with:
        target          — input term
        total_patents   — total unique patents found
        top_assignees   — [{name, count}] ranked by filing volume
        recent_patents  — 10 most recent patents
        freedom_to_operate_note — high-level FTO flag
        white_space_note        — areas with thin coverage
    """
    term = target_or_compound.strip()

    r1 = search_patents(f"{term} inhibitor",  years_back=years_back)
    r2 = search_patents(f"{term} antagonist", years_back=years_back)
    r3 = search_patents(f"{term} therapy",    years_back=years_back)

    # Merge and deduplicate
    seen = set()
    all_patents = []
    for p in r1["patents"] + r2["patents"] + r3["patents"]:
        key = (p.get("patent_number") or p.get("lens_id") or p.get("title", "")).lower()
        if key not in seen:
            seen.add(key)
            all_patents.append(p)

    # Rank assignees by volume
    from collections import Counter
    assignee_counts: Counter = Counter()
    for p in all_patents:
        for a in (p.get("assignees") or []):
            name = (a or "").strip()
            if name:
                assignee_counts[name] += 1
    top_assignees = [{"name": k, "count": v} for k, v in assignee_counts.most_common(10)]

    # Sort by date descending for recency
    def _date_key(p):
        return p.get("date") or ""
    recent = sorted(all_patents, key=_date_key, reverse=True)[:10]

    # Simple FTO flag
    roche_count = sum(1 for p in all_patents
                      if any("roche" in (a or "").lower() or "genentech" in (a or "").lower()
                             for a in (p.get("assignees") or [])))
    if len(all_patents) == 0:
        fto_note = "No patents found — possible white space or search too specific."
    elif roche_count > 0:
        fto_note = (f"Roche/Genentech holds {roche_count} of {len(all_patents)} patents "
                    f"in this space. Internal FTO review recommended.")
    elif len(all_patents) < 5:
        fto_note = f"Thin patent landscape ({len(all_patents)} patents) — likely low IP barrier."
    else:
        fto_note = (f"{len(all_patents)} patents found. "
                    f"Review top assignees for blocking claims before advancing.")

    white_space = (
        "Patent coverage appears dense — consider novel formulation, combination, "
        "or biomarker-defined patient population angles."
        if len(all_patents) > 20
        else "Moderate filing volume — novel mechanism or delivery route may offer white space."
    )

    SESSION.setdefault("patent_landscapes", [])
    SESSION["patent_landscapes"].append({"target": term, "total": len(all_patents)})

    return {
        "target":                   term,
        "total_patents":            len(all_patents),
        "top_assignees":            top_assignees,
        "recent_patents":           recent,
        "freedom_to_operate_note":  fto_note,
        "white_space_note":         white_space,
    }
