"""
Tests for tools/patents.py — USPTO PatentsView + Lens.org patent search.
All HTTP calls are mocked.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

from tools.session import SESSION


def make_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.raise_for_status = MagicMock()
    return resp


# Fixture: PatentsView response
PV_RESPONSE = {
    "patents": [
        {
            "patent_number": "US10123456",
            "patent_title": "EGFR inhibitor compounds and methods of use",
            "patent_date": "2022-03-15",
            "patent_abstract": "Novel EGFR inhibitor compounds for treating lung cancer.",
            "assignees": [{"assignee_organization": "Roche AG"}],
            "patent_type": "utility",
        },
        {
            "patent_number": "US10234567",
            "patent_title": "EGFR antagonist formulations",
            "patent_date": "2021-07-20",
            "patent_abstract": "Formulation improvements for EGFR antagonist therapy.",
            "assignees": [{"assignee_organization": "AstraZeneca PLC"}],
            "patent_type": "utility",
        },
    ]
}

# Fixture: Lens.org response
LENS_RESPONSE = {
    "data": [
        {
            "lens_id": "001-234-567-890",
            "title": "EGFR tyrosine kinase inhibitor resistance mechanisms",
            "abstract": "Study of resistance mutations in EGFR targeted therapy.",
            "date_published": "2023-01-10",
            "jurisdiction": "EP",
            "applicant": [{"name": "Novartis AG"}],
            "patent_citations_count": 12,
        }
    ]
}


def _mock_post(url, *args, **kwargs):
    if "patentsview" in url:
        return make_response(PV_RESPONSE)
    if "lens.org" in url:
        return make_response(LENS_RESPONSE)
    return make_response({})


class TestSearchPatents(unittest.TestCase):

    def setup_method(self, _):
        SESSION.pop("patents", None)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_returns_patents_list(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        self.assertIn("patents", result)
        self.assertIsInstance(result["patents"], list)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_query_echoed_in_result(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        self.assertEqual(result["query"], "EGFR inhibitor")

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_patentsview_patents_included(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        titles = [p["title"] for p in result["patents"]]
        self.assertTrue(any("EGFR inhibitor" in t for t in titles))

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_patent_has_required_fields(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        p = result["patents"][0]
        self.assertIn("title", p)
        self.assertIn("date", p)
        self.assertIn("source", p)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_total_reflects_deduplicated_count(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        self.assertEqual(result["total"], len(result["patents"]))

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_session_patents_updated(self, _mock):
        from tools.patents import search_patents
        SESSION.pop("patents", None)
        search_patents("EGFR inhibitor")
        self.assertIn("patents", SESSION)
        self.assertGreater(len(SESSION["patents"]), 0)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_sources_list_present(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        self.assertIn("sources", result)
        self.assertIn("patentsview", result["sources"])

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_lens_included_when_api_key_set(self, _mock):
        from tools import patents as pm
        original_key = pm.LENS_API_KEY
        pm.LENS_API_KEY = "test-key-123"
        try:
            result = pm.search_patents("EGFR inhibitor")
            self.assertIn("lens.org", result["sources"])
        finally:
            pm.LENS_API_KEY = original_key

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_lens_skipped_without_api_key(self, _mock):
        from tools import patents as pm
        original_key = pm.LENS_API_KEY
        pm.LENS_API_KEY = ""
        try:
            result = pm.search_patents("EGFR inhibitor")
            self.assertNotIn("lens.org", result["sources"])
        finally:
            pm.LENS_API_KEY = original_key

    @patch("tools.patents.requests.post", side_effect=Exception("network error"))
    def test_patentsview_network_error_returns_gracefully(self, _mock):
        from tools.patents import search_patents
        result = search_patents("EGFR inhibitor")
        # Should not raise — returns empty or error patent
        self.assertIn("patents", result)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_assignee_filter_passed_to_query(self, mock_post):
        from tools.patents import search_patents
        search_patents("EGFR", assignee="Roche")
        call_kwargs = mock_post.call_args
        body = call_kwargs[1].get("json") or call_kwargs[0][1]
        body_str = json.dumps(body)
        self.assertIn("Roche", body_str)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_deduplication_by_title(self, _mock):
        from tools.patents import search_patents
        # Same title from two sources should be deduplicated
        result = search_patents("EGFR inhibitor")
        titles = [p["title"].lower() for p in result["patents"]]
        self.assertEqual(len(titles), len(set(titles)))


class TestGetPatentLandscape(unittest.TestCase):

    def setup_method(self, _):
        SESSION.pop("patent_landscapes", None)
        SESSION.pop("patents", None)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_returns_landscape_dict(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("EGFR")
        self.assertIn("target", result)
        self.assertIn("total_patents", result)
        self.assertIn("top_assignees", result)
        self.assertIn("recent_patents", result)
        self.assertIn("freedom_to_operate_note", result)
        self.assertIn("white_space_note", result)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_target_echoed(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("KRAS")
        self.assertEqual(result["target"], "KRAS")

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_top_assignees_ranked_by_count(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("EGFR")
        assignees = result["top_assignees"]
        if len(assignees) > 1:
            counts = [a["count"] for a in assignees]
            self.assertEqual(counts, sorted(counts, reverse=True))

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_roche_fto_note_when_roche_patents_present(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("EGFR")
        # Roche AG is an assignee in our fixture
        if result["total_patents"] > 0:
            self.assertIsInstance(result["freedom_to_operate_note"], str)
            self.assertGreater(len(result["freedom_to_operate_note"]), 0)

    @patch("tools.patents.requests.post", return_value=make_response({"patents": []}))
    def test_empty_landscape_fto_note(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("NOVEL_TARGET_XYZ")
        self.assertIn("white space", result["freedom_to_operate_note"].lower())

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_recent_patents_max_10(self, _mock):
        from tools.patents import get_patent_landscape
        result = get_patent_landscape("EGFR")
        self.assertLessEqual(len(result["recent_patents"]), 10)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_session_patent_landscapes_updated(self, _mock):
        from tools.patents import get_patent_landscape
        get_patent_landscape("EGFR")
        self.assertIn("patent_landscapes", SESSION)
        entry = SESSION["patent_landscapes"][-1]
        self.assertEqual(entry["target"], "EGFR")
        self.assertIn("total", entry)

    @patch("tools.patents.requests.post", side_effect=_mock_post)
    def test_queries_inhibitor_antagonist_therapy_variants(self, mock_post):
        from tools.patents import get_patent_landscape
        get_patent_landscape("EGFR")
        # Should make 3 search_patents calls (inhibitor, antagonist, therapy) × 1 post each
        self.assertGreaterEqual(mock_post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
