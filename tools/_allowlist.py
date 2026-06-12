"""
Application-level egress allowlist for the drug-discovery agent.

Monkey-patches requests.get / requests.post / requests.Session.request so that
any outbound call to a domain not on the approved list raises PermissionError
immediately — before a TCP connection is opened.

This is a defence-in-depth measure against prompt-injection attacks that could
attempt to exfiltrate proprietary SMILES strings or compound data to an
attacker-controlled host.

Import this module once, early in run_agent.py, before any tool is imported:

    import tools._allowlist   # noqa: F401  — activates allowlist

The patch is idempotent; importing multiple times is safe.
"""

import urllib.parse
import requests
from typing import List

# ---------------------------------------------------------------------------
# Approved egress domains
# ---------------------------------------------------------------------------
# Wildcards: a leading "*." matches any subdomain (e.g. "*.ebi.ac.uk" matches
# "www.ebi.ac.uk" and "rest.ebi.ac.uk" but not "ebi.ac.uk" itself).
# Exact entries must match the hostname exactly (case-insensitive).

ALLOWED_DOMAINS = frozenset([
    # Anthropic inference
    "api.anthropic.com",
    # Open Targets — genetic evidence, disease associations
    "api.platform.opentargets.org",
    # ClinicalTrials.gov — trial status, competitor programs
    "clinicaltrials.gov",
    # FDA FAERS — adverse event signals
    "api.fda.gov",
    # EBI services: ChEMBL (bioactivity), EuropePMC (literature)
    "*.ebi.ac.uk",
    # ArXiv preprints
    "arxiv.org",
    "export.arxiv.org",
    # STRING protein interaction networks
    "string-db.org",
    # gnomAD population variant frequency
    "gnomad.broadinstitute.org",
    # PubChem — SMILES canonicalization
    "pubchem.ncbi.nlm.nih.gov",
    # NCBI eUtils — ClinVar variant searches (used by query_genomeclaw_databases)
    "eutils.ncbi.nlm.nih.gov",
    # cBioPortal — cancer genomics (used by query_genomeclaw_databases)
    "www.cbioportal.org",
    # USPTO PatentsView — IP landscape
    "api.patentsview.org",
    # Lens.org — patent search
    "api.lens.org",
    # UniProt — canonical protein sequences (fold_target, score_variant_effect)
    "rest.uniprot.org",
    # KEGG — pathway context (get_pathway_context)
    "rest.kegg.jp",
    # Orphanet — rare disease prevalence (get_disease_prevalence)
    "api.orphacode.org",
    # GenomeClaw local REST API (Boltz-1, ESM-2, ADMET)
    "127.0.0.1",
    "localhost",
])

_WILDCARD_DOMAINS = [  # type: List[str]
    d[2:] for d in ALLOWED_DOMAINS if d.startswith("*.")
]
_EXACT_DOMAINS = frozenset(
    d for d in ALLOWED_DOMAINS if not d.startswith("*.")
)


def _is_allowed(url: str) -> bool:
    """Return True if *url* targets an approved domain."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    if host in _EXACT_DOMAINS:
        return True
    for suffix in _WILDCARD_DOMAINS:
        if host.endswith("." + suffix) or host == suffix:
            return True
    return False


def _checked_request(original_fn):
    """Wrap a requests function to enforce the allowlist."""
    def wrapper(url, *args, **kwargs):
        if not _is_allowed(url):
            host = urllib.parse.urlparse(url).hostname or url
            raise PermissionError(
                f"[allowlist] Outbound request to '{host}' is not permitted. "
                f"Add it to tools._allowlist.ALLOWED_DOMAINS if this domain "
                f"is intentionally used by the agent."
            )
        return original_fn(url, *args, **kwargs)
    wrapper.__wrapped__ = original_fn
    return wrapper


# ---------------------------------------------------------------------------
# Patch — idempotent
# ---------------------------------------------------------------------------
_PATCHED = False


def _apply():
    global _PATCHED
    if _PATCHED:
        return
    # Patch module-level convenience functions
    requests.get  = _checked_request(requests.get)
    requests.post = _checked_request(requests.post)
    requests.put  = _checked_request(requests.put)
    # Patch Session.request so tools that instantiate a Session are also covered
    _orig_session_request = requests.Session.request

    def _session_request(self, method, url, *args, **kwargs):
        if not _is_allowed(url):
            host = urllib.parse.urlparse(url).hostname or url
            raise PermissionError(
                f"[allowlist] Outbound request to '{host}' is not permitted."
            )
        return _orig_session_request(self, method, url, *args, **kwargs)

    requests.Session.request = _session_request
    _PATCHED = True


_apply()
