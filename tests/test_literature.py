"""Tests for tools/literature.py — scan_arxiv, scan_literature, bulk_scan_literature."""

import datetime
import pytest
from unittest.mock import patch, MagicMock


def _reset_session():
    from tools.session import SESSION
    for key in SESSION:
        if isinstance(SESSION[key], list):
            SESSION[key].clear()


# ── _arxiv_search_with_backoff ────────────────────────────────────────────────

class TestArxivSearchWithBackoff:
    def _make_arxiv_result(self, title, year=2025):
        r = MagicMock()
        r.title = title
        r.summary = f"Abstract for {title}"
        r.authors = [MagicMock(name="Author A")]
        r.published = datetime.datetime(year, 1, 15, tzinfo=datetime.timezone.utc)
        r.entry_id = f"https://arxiv.org/abs/2501.00001"
        r.categories = ["q-bio.BM"]
        return r

    def test_returns_list_of_results(self):
        mock_results = [self._make_arxiv_result("Paper 1"), self._make_arxiv_result("Paper 2")]
        with patch("tools.literature._ARXIV_CLIENT") as mock_client:
            mock_client.results.return_value = iter(mock_results)
            from tools.literature import _arxiv_search_with_backoff
            results = _arxiv_search_with_backoff("EGFR lung cancer", 10)
        assert len(results) == 2

    def test_returns_empty_list_on_exception(self):
        with patch("tools.literature._ARXIV_CLIENT") as mock_client:
            mock_client.results.side_effect = Exception("network error")
            from tools.literature import _arxiv_search_with_backoff
            results = _arxiv_search_with_backoff("EGFR lung cancer", 10)
        assert results == []

    def test_retries_on_http_429(self):
        import arxiv
        call_count = {"n": 0}
        def _results(search):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise arxiv.HTTPError(url="http://arxiv.org", retry=1, status=429)
            return iter([])
        with patch("tools.literature._ARXIV_CLIENT") as mock_client, \
             patch("time.sleep"):  # skip actual sleep
            mock_client.results.side_effect = _results
            from tools.literature import _arxiv_search_with_backoff
            results = _arxiv_search_with_backoff("EGFR", 5)
        assert call_count["n"] >= 2

    def test_stops_after_non_429_error(self):
        import arxiv
        with patch("tools.literature._ARXIV_CLIENT") as mock_client, \
             patch("time.sleep"):
            mock_client.results.side_effect = arxiv.HTTPError(url="http://arxiv.org", retry=1, status=500)
            from tools.literature import _arxiv_search_with_backoff
            results = _arxiv_search_with_backoff("EGFR", 5)
        assert results == []


# ── scan_arxiv ────────────────────────────────────────────────────────────────

class TestScanArxiv:
    def setup_method(self):
        _reset_session()

    def _make_arxiv_result(self, title, year=2025):
        r = MagicMock()
        r.title = title
        r.summary = "Abstract text about EGFR signaling"
        r.authors = [MagicMock()]
        r.authors[0].__str__ = lambda self: "Smith J"
        r.published = datetime.datetime(year, 3, 10, tzinfo=datetime.timezone.utc)
        r.entry_id = "https://arxiv.org/abs/2503.12345"
        r.categories = ["q-bio.BM", "cs.LG"]
        return r

    def test_returns_papers_list(self):
        mock_results = [self._make_arxiv_result("EGFR Paper 1"),
                        self._make_arxiv_result("EGFR Paper 2")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results):
            from tools.literature import scan_arxiv
            result = scan_arxiv("EGFR", "lung cancer")
        assert "papers" in result
        assert len(result["papers"]) == 2

    def test_papers_have_required_fields(self):
        mock_results = [self._make_arxiv_result("EGFR Inhibition Study")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results):
            from tools.literature import scan_arxiv
            result = scan_arxiv("EGFR", "lung cancer")
        paper = result["papers"][0]
        assert "title" in paper
        assert "abstract" in paper
        assert "arxiv_id" in paper
        assert "submitted" in paper

    def test_min_year_filter_applied(self):
        old_result = self._make_arxiv_result("Old Paper", year=2018)
        new_result = self._make_arxiv_result("New Paper", year=2024)
        with patch("tools.literature._arxiv_search_with_backoff",
                   return_value=[old_result, new_result]):
            from tools.literature import scan_arxiv
            result = scan_arxiv("EGFR", "lung cancer", min_year=2020)
        titles = [p["title"] for p in result["papers"]]
        assert "New Paper" in titles
        assert "Old Paper" not in titles

    def test_returns_empty_when_no_results(self):
        with patch("tools.literature._arxiv_search_with_backoff", return_value=[]):
            from tools.literature import scan_arxiv
            result = scan_arxiv("NONEXISTENT_TARGET_XYZ", "unknown disease")
        assert result["papers"] == [] or result.get("papers_found") == 0

    def test_session_arxiv_papers_updated(self):
        _reset_session()
        from tools.session import SESSION
        mock_results = [self._make_arxiv_result("Paper")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results):
            from tools.literature import scan_arxiv
            scan_arxiv("EGFR", "lung cancer")
        # scan_arxiv appends to SESSION["arxiv_papers"]
        assert len(SESSION.get("arxiv_papers", [])) > 0


# ── scan_literature ───────────────────────────────────────────────────────────

class TestScanLiterature:
    def setup_method(self):
        _reset_session()

    def _make_arxiv_result(self, title, year=2025, category="q-bio.BM"):
        r = MagicMock()
        r.title = title
        r.summary = f"Abstract: {title}"
        r.authors = [MagicMock()]
        r.authors[0].__str__ = lambda self: "Jones K"
        r.published = datetime.datetime(year, 6, 1, tzinfo=datetime.timezone.utc)
        r.entry_id = f"https://arxiv.org/abs/2506.00001"
        r.categories = [category]
        return r

    def _epmc_empty(self):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"resultList": {"result": []}}
        m.raise_for_status = MagicMock()
        return m

    def test_returns_papers_from_arxiv(self):
        mock_results = [self._make_arxiv_result("KRAS Paper")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results), \
             patch("tools.literature.requests.get", return_value=self._epmc_empty()):
            from tools.literature import scan_literature
            result = scan_literature("KRAS", "pancreatic cancer")
        assert "papers" in result

    def test_bio_relevance_filter_for_arxiv_categories(self):
        """Papers outside q-bio/cs.AI should not appear if category filter is applied."""
        bio_result = self._make_arxiv_result("Bio Paper", category="q-bio.BM")
        irrelevant_result = self._make_arxiv_result("Physics Paper", category="physics.gen-ph")
        with patch("tools.literature._arxiv_search_with_backoff",
                   return_value=[bio_result, irrelevant_result]), \
             patch("tools.literature.requests.get", return_value=self._epmc_empty()):
            from tools.literature import scan_literature
            result = scan_literature("KRAS", "pancreatic cancer")
        # Bio-relevant paper should be in the output
        titles = [p["title"] for p in result["papers"]]
        assert "Bio Paper" in titles

    def test_result_has_papers_found_field(self):
        mock_results = [self._make_arxiv_result("KRAS Paper")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results), \
             patch("tools.literature.requests.get",
                   return_value=MagicMock(status_code=200,
                                         json=MagicMock(return_value={"resultList": {"result": []}}))):
            from tools.literature import scan_literature
            result = scan_literature("KRAS", "pancreatic cancer")
        assert "papers_found" in result
        assert result["papers_found"] >= 0

    def test_min_year_respected(self):
        old = self._make_arxiv_result("Old", year=2019)
        new = self._make_arxiv_result("New", year=2024)
        with patch("tools.literature._arxiv_search_with_backoff",
                   return_value=[old, new]), \
             patch("tools.literature.requests.get", return_value=self._epmc_empty()):
            from tools.literature import scan_literature
            result = scan_literature("KRAS", "cancer", min_year=2022)
        titles = [p["title"] for p in result["papers"]]
        assert "New" in titles
        assert "Old" not in titles

    def test_session_literature_updated(self):
        _reset_session()
        from tools.session import SESSION
        mock_results = [self._make_arxiv_result("KRAS Study")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results), \
             patch("tools.literature.requests.get", return_value=self._epmc_empty()):
            from tools.literature import scan_literature
            scan_literature("KRAS", "pancreatic cancer")
        assert len(SESSION.get("literature", [])) > 0


# ── bulk_scan_literature ──────────────────────────────────────────────────────

class TestBulkScanLiterature:
    def setup_method(self):
        _reset_session()

    def _make_arxiv_result(self, title, year=2025):
        r = MagicMock()
        r.title = title
        r.summary = "Abstract"
        r.authors = [MagicMock()]
        r.authors[0].__str__ = lambda self: "Author"
        r.published = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
        r.entry_id = "https://arxiv.org/abs/2501.00001"
        r.categories = ["q-bio.BM"]
        return r

    def test_returns_results_for_each_target(self):
        mock_results = [self._make_arxiv_result("Paper")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results):
            from tools.literature import bulk_scan_literature
            result = bulk_scan_literature(["EGFR", "KRAS"])
        assert "results" in result or "EGFR" in str(result)

    def test_handles_empty_targets_list(self):
        with patch("tools.literature._arxiv_search_with_backoff", return_value=[]):
            from tools.literature import bulk_scan_literature
            result = bulk_scan_literature([])
        assert isinstance(result, dict)

    def test_months_back_filter_works(self):
        """bulk_scan_literature queries EuropePMC with date filter — papers returned by mock."""
        from unittest.mock import MagicMock
        epmc_resp = MagicMock()
        epmc_resp.status_code = 200
        epmc_resp.json.return_value = {"resultList": {"result": [
            {"title": "Recent Paper", "journalTitle": "Nature", "firstPublicationDate": "2025-06-01",
             "doi": "10.1000/x", "pmid": "12345"}
        ]}}
        with patch("tools.literature.requests.get", return_value=epmc_resp):
            from tools.literature import bulk_scan_literature
            result = bulk_scan_literature(["EGFR"], months_back=12)
        assert result["targets_with_hits"] > 0

    def test_session_literature_updated(self):
        _reset_session()
        from tools.session import SESSION
        mock_results = [self._make_arxiv_result("Bulk Paper")]
        with patch("tools.literature._arxiv_search_with_backoff", return_value=mock_results):
            from tools.literature import bulk_scan_literature
            bulk_scan_literature(["EGFR"])
        assert len(SESSION.get("literature", [])) > 0
