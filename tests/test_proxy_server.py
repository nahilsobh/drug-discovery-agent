"""
Tests for proxy_server.py — JSON ReAct parser and inline [TOOL CALL:] fallback.
"""
import unittest
from proxy_server import (
    _extract_first_json,
    _extract_inline_tool_call,
    _strip_tool_catalog,
    parse_claude_response,
    build_prompt,
    REACT_PREAMBLE,
)


class TestExtractFirstJson(unittest.TestCase):

    def test_valid_react_action(self):
        raw = '{"reasoning": "thinking", "action": "find_gaps", "action_input": {"therapeutic_area": "oncology"}, "final_answer": null}'
        data = _extract_first_json(raw)
        self.assertEqual(data["action"], "find_gaps")

    def test_prefers_react_over_first_json(self):
        # First JSON is a tool_input fragment, second is the ReAct object
        raw = '{"query": "KRAS"}\n{"reasoning": "r", "action": "find_hits", "action_input": {}, "final_answer": null}'
        data = _extract_first_json(raw)
        self.assertEqual(data["action"], "find_hits")

    def test_final_answer_pattern(self):
        raw = '{"reasoning": "done", "action": null, "action_input": {}, "final_answer": "CEO summary here"}'
        data = _extract_first_json(raw)
        self.assertEqual(data["final_answer"], "CEO summary here")

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"reasoning": "r", "action": "find_hits", "action_input": {}, "final_answer": null}\n```'
        data = _extract_first_json(raw)
        self.assertIsNotNone(data)
        self.assertEqual(data["action"], "find_hits")

    def test_no_json_returns_none(self):
        self.assertIsNone(_extract_first_json("no json here at all"))

    def test_broken_json_returns_none(self):
        self.assertIsNone(_extract_first_json('{"action": "find_gaps", broken'))


class TestExtractInlineToolCall(unittest.TestCase):

    def test_pattern1_block_label(self):
        raw = '[TOOL CALL: recall_longterm_memory]\n{"query_type": "negatives", "target_filter": "GHR"}'
        result = _extract_inline_tool_call(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "recall_longterm_memory")
        self.assertEqual(result["action_input"]["query_type"], "negatives")
        self.assertEqual(result["action_input"]["target_filter"], "GHR")

    def test_pattern2_inline_with_prose(self):
        raw = 'I need to check for prior hits first.\n[TOOL CALL: find_hits]\n{"target": "KRAS", "max_ic50_nm": 500}'
        result = _extract_inline_tool_call(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "find_hits")
        self.assertEqual(result["action_input"]["target"], "KRAS")
        self.assertIn("prior hits", result["reasoning"])

    def test_reasoning_before_marker_captured(self):
        raw = 'Checking memory before hitting ChEMBL.\n[TOOL CALL: recall_longterm_memory]\n{"query_type": "hits"}'
        result = _extract_inline_tool_call(raw)
        self.assertIn("Checking memory", result["reasoning"])

    def test_no_match_returns_none(self):
        self.assertIsNone(_extract_inline_tool_call("No tool call here."))

    def test_broken_json_after_marker_returns_none(self):
        raw = '[TOOL CALL: find_hits]\n{broken json'
        self.assertIsNone(_extract_inline_tool_call(raw))

    def test_multiline_json_body(self):
        raw = '[TOOL CALL: find_gaps]\n{\n  "therapeutic_area": "oncology",\n  "min_bio_score": 0.5\n}'
        result = _extract_inline_tool_call(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["action_input"]["therapeutic_area"], "oncology")

    def test_nested_braces_in_string_value(self):
        # Regression: ceo_summary with parentheses/braces caused non-greedy .*? to stop early
        raw = (
            '[TOOL CALL: generate_pdf_report]\n'
            '{"filename": "report.pdf", "ceo_summary": "Roche covers all targets (bio_score >0.70). '
            'Two TIER-1 hits: CHEMBL3337735 (IC50 990nM) and E-GUGGULSTERONE (IC50 1000nM)."}'
        )
        result = _extract_inline_tool_call(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "generate_pdf_report")
        self.assertEqual(result["action_input"]["filename"], "report.pdf")
        self.assertIn("bio_score", result["action_input"]["ceo_summary"])


class TestParseClaudeResponse(unittest.TestCase):

    def test_react_json_tool_call(self):
        raw = '{"reasoning": "step", "action": "find_gaps", "action_input": {"therapeutic_area": "oncology"}, "final_answer": null}'
        resp = parse_claude_response(raw, "claude-opus-4-6")
        self.assertEqual(resp["stop_reason"], "tool_use")
        tool_blocks = [b for b in resp["content"] if b["type"] == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0]["name"], "find_gaps")

    def test_react_json_final_answer(self):
        raw = '{"reasoning": "done", "action": null, "action_input": {}, "final_answer": "CEO summary"}'
        resp = parse_claude_response(raw, "claude-opus-4-6")
        self.assertEqual(resp["stop_reason"], "end_turn")
        text_blocks = [b for b in resp["content"] if b["type"] == "text"]
        self.assertTrue(any("CEO summary" in b["text"] for b in text_blocks))

    def test_inline_tool_call_fallback(self):
        # Model emitted [TOOL CALL:] prose instead of valid ReAct JSON
        raw = 'I need to check for prior hits.\n[TOOL CALL: recall_longterm_memory]\n{"query_type": "hits", "target_filter": "EGFR"}'
        resp = parse_claude_response(raw, "claude-opus-4-6")
        self.assertEqual(resp["stop_reason"], "tool_use")
        tool_blocks = [b for b in resp["content"] if b["type"] == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0]["name"], "recall_longterm_memory")
        self.assertEqual(tool_blocks[0]["input"]["target_filter"], "EGFR")

    def test_inline_tool_call_not_shadowed_by_react_json(self):
        # Valid ReAct JSON should take priority over inline pattern
        raw = (
            '{"reasoning": "r", "action": "find_hits", "action_input": {"target": "KRAS"}, "final_answer": null}\n'
            '[TOOL CALL: find_gaps]\n{"therapeutic_area": "oncology"}'
        )
        resp = parse_claude_response(raw, "claude-opus-4-6")
        tool_blocks = [b for b in resp["content"] if b["type"] == "tool_use"]
        self.assertEqual(tool_blocks[0]["name"], "find_hits")  # ReAct wins

    def test_raw_text_fallback(self):
        raw = "Some prose with no JSON and no tool call block."
        resp = parse_claude_response(raw, "claude-opus-4-6")
        self.assertEqual(resp["stop_reason"], "end_turn")
        self.assertEqual(resp["content"][0]["type"], "text")

    def test_response_has_required_fields(self):
        raw = '{"reasoning": "r", "action": "find_gaps", "action_input": {}, "final_answer": null}'
        resp = parse_claude_response(raw, "claude-opus-4-6")
        for field in ("id", "type", "role", "content", "model", "stop_reason", "usage"):
            self.assertIn(field, resp)

    def test_tool_use_id_is_unique(self):
        raw = '{"reasoning": "r", "action": "find_hits", "action_input": {}, "final_answer": null}'
        r1 = parse_claude_response(raw, "m")
        r2 = parse_claude_response(raw, "m")
        id1 = [b for b in r1["content"] if b["type"] == "tool_use"][0]["id"]
        id2 = [b for b in r2["content"] if b["type"] == "tool_use"][0]["id"]
        self.assertNotEqual(id1, id2)


class TestStripToolCatalog(unittest.TestCase):

    def test_strips_tool_catalog_section(self):
        system = (
            "You are the agent.\n\n"
            "You have 30 tools available:\n\n"
            "DISCOVERY\n- find_gaps → ...\n- get_biology → ...\n\n"
            "WORKFLOW GUIDANCE\n- For gap questions: find_gaps → ...\n"
        )
        cleaned = _strip_tool_catalog(system)
        self.assertNotIn("You have 30 tools available", cleaned)
        self.assertNotIn("DISCOVERY", cleaned)
        self.assertIn("WORKFLOW GUIDANCE", cleaned)

    def test_no_catalog_unchanged(self):
        system = "You are the agent.\n\nWORKFLOW GUIDANCE\n- step 1\n"
        cleaned = _strip_tool_catalog(system)
        self.assertIn("WORKFLOW GUIDANCE", cleaned)

    def test_prompt_size_reduced(self):
        big_system = (
            "You are the agent.\n\n"
            "You have 30 tools available:\n\n"
            "DISCOVERY\n" + "- tool → description\n" * 30 + "\n"
            "WORKFLOW GUIDANCE\n- For gap questions: find_gaps\n"
        )
        cleaned = _strip_tool_catalog(big_system)
        self.assertLess(len(cleaned), len(big_system))


class TestBuildPrompt(unittest.TestCase):

    def test_includes_react_preamble(self):
        prompt = build_prompt("sys", [{"role": "user", "content": "q"}], [])
        self.assertIn("JSON ReAct loop", prompt)

    def test_includes_workflow_guidance_not_tool_catalog(self):
        system = (
            "You are the agent.\n\n"
            "You have 30 tools available:\n\n"
            "DISCOVERY\n- find_gaps → ...\n\n"
            "WORKFLOW GUIDANCE\n- For gap questions: find_gaps\n"
        )
        prompt = build_prompt(system, [{"role": "user", "content": "q"}], [])
        self.assertNotIn("You have 30 tools available", prompt)
        self.assertIn("WORKFLOW GUIDANCE", prompt)

    def test_tool_list_section_present(self):
        tools = [{"name": "find_gaps", "description": "find gaps",
                  "input_schema": {"type": "object", "properties": {
                      "disease": {"type": "string"}}, "required": ["disease"]}}]
        prompt = build_prompt("sys", [{"role": "user", "content": "q"}], tools)
        self.assertIn("find_gaps", prompt)

    def test_user_message_present(self):
        prompt = build_prompt("sys", [{"role": "user", "content": "find EGFR gaps"}], [])
        self.assertIn("find EGFR gaps", prompt)


if __name__ == "__main__":
    unittest.main()
