#!/usr/bin/env python3
"""
Local Anthropic API proxy that routes requests through the authenticated
`claude -p` CLI. Allows run_agent.py to use a Claude subscription without
separate API billing credits.

Usage (automatic — called from make_client() in run_agent.py):
    from proxy_server import start_proxy
    port = start_proxy()
    client = anthropic.Anthropic(base_url=f"http://127.0.0.1:{port}", api_key="proxy")

Protocol:
  - Accepts POST /v1/messages in Anthropic API format
  - Converts messages + tools into a JSON-ReAct prompt for `claude -p`
  - Parses the first JSON block from the response
  - Returns a valid Anthropic API response object
"""

import json
import os
import time
import re
import subprocess
import threading
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

PROXY_PORT = int(os.environ.get("PROXY_PORT", 9797))

# ── Prompt builder ─────────────────────────────────────────────────────────────

REACT_PREAMBLE = """\
You are operating as a strategic drug discovery agent in a JSON ReAct loop.
Each turn you MUST respond with ONLY a single valid JSON object — no prose, \
no markdown fences, no explanations outside the JSON.

Response format (pick ONE pattern per turn):

Pattern A — call a tool:
{"reasoning": "<step-by-step thinking>", "action": "<tool_name>", \
"action_input": {<parameters>}, "final_answer": null}

Pattern B — final answer (no more tools needed):
{"reasoning": "<final synthesis>", "action": null, \
"action_input": {}, "final_answer": "<complete CEO-ready answer>"}

CRITICAL RULES — read before every response:
- You MUST call tools to gather real data. NEVER fabricate results.
- For ANY analysis question, call at least 3 data-gathering tools before final_answer.
- Call ONE tool per turn. Wait for the [TOOL RESULT] before calling the next tool.
- action_input keys must exactly match the tool's parameter names.
- NEVER invent [TOOL RESULT] lines — the system provides them after each tool call.
- Only set final_answer after you have received real tool results in this conversation.
- Violation: writing final_answer without tool results = hallucination and is FORBIDDEN.

"""

def _tool_list_section(tools: list) -> str:
    if not tools:
        return ""
    lines = ["Available tools:\n"]
    for t in tools:
        props = t.get("input_schema", {}).get("properties", {})
        req   = t.get("input_schema", {}).get("required", [])
        params = ", ".join(
            f"{k}{'*' if k in req else '?'}: {v.get('type','any')}"
            for k, v in props.items()
        )
        lines.append(f"  {t['name']}({params}) — {t.get('description','')[:120]}")
    return "\n".join(lines) + "\n\n"


def _format_content(content) -> str:
    """Flatten a content field (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        btype = block.get("type", "")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_use":
            parts.append(
                f"[TOOL CALL: {block['name']}]\n"
                + json.dumps(block.get("input", {}), indent=2)
            )
        elif btype == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, list):
                rc = "\n".join(b.get("text", "") for b in rc if b.get("type") == "text")
            parts.append(f"[TOOL RESULT]:\n{rc}")
    return "\n".join(parts)


def _strip_tool_catalog(system: str) -> str:
    """Remove the 30-tool catalog block from the system prompt.
    The proxy already injects tools via _tool_list_section — including them
    again in the system prompt doubles the prompt size and causes the model
    to skip tool calls in favour of a hallucinated final_answer.
    Keep the WORKFLOW GUIDANCE section which describes how to chain tools.
    """
    # Drop everything between "You have N tools available:" and "WORKFLOW GUIDANCE"
    cleaned = re.sub(
        r"You have \d+ tools available:.*?(?=WORKFLOW GUIDANCE)",
        "",
        system,
        flags=re.DOTALL,
    )
    return cleaned.strip()


def build_prompt(system: str, messages: list, tools: list) -> str:
    parts = [REACT_PREAMBLE, _tool_list_section(tools)]

    if system:
        # Strip the tool catalog from the system prompt — tools are already listed
        # above via _tool_list_section; duplicating them inflates the prompt and
        # causes the model to skip tool calls on complex multi-step queries.
        clean_system = _strip_tool_catalog(system)
        parts.append(f"Context / Instructions:\n{clean_system}\n\n---\n\n")

    for msg in messages:
        role    = msg["role"].upper()
        content = _format_content(msg["content"])
        parts.append(f"[{role}]:\n{content}\n\n")

    parts.append("[ASSISTANT]:\n")
    return "".join(parts)


# ── Response parser ────────────────────────────────────────────────────────────

def _extract_all_json(text: str) -> list[dict]:
    """Extract and parse ALL top-level {...} JSON objects from text."""
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)

    results = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                try:
                    results.append(json.loads(candidate))
                except json.JSONDecodeError:
                    pass
                start = None
    return results


def _extract_first_json(text: str) -> Optional[dict]:
    """Extract the most relevant ReAct JSON from text.
    Prefers an object with 'action' or 'final_answer' keys (ReAct format)
    over the first JSON found (which might be a hallucinated tool_input or result).
    """
    candidates = _extract_all_json(text)
    if not candidates:
        return None
    # Prefer a ReAct-shaped object
    for c in candidates:
        if "action" in c or "final_answer" in c:
            return c
    return candidates[0]


def _extract_inline_tool_call(text: str) -> Optional[dict]:
    """
    Detect inline [TOOL CALL: name] blocks that the model occasionally emits
    instead of valid ReAct JSON.  Handles two observed patterns:

    Pattern 1 — block label followed by JSON body:
        [TOOL CALL: recall_longterm_memory]
        {"query_type": "negatives", "target_filter": "GHR"}

    Pattern 2 — reasoning prose with embedded action label:
        I need to call find_hits now.
        [TOOL CALL: find_hits]
        {"target": "KRAS"}

    Returns a ReAct-compatible dict or None.
    """
    m = re.search(r"\[TOOL CALL:\s*(\w+)\]", text)
    if not m:
        return None
    tool_name = m.group(1).strip()
    # Use balanced-brace extraction on the text after the marker to avoid
    # the non-greedy .*? regex stopping inside nested string values.
    after_marker = text[m.end():]
    json_objects = _extract_all_json(after_marker)
    if not json_objects:
        return None
    tool_input = json_objects[0]
    # Treat everything before the [TOOL CALL:] marker as reasoning
    reasoning = text[: m.start()].strip()
    return {
        "reasoning": reasoning,
        "action": tool_name,
        "action_input": tool_input,
        "final_answer": None,
    }


def parse_claude_response(raw: str, model: str) -> dict:
    """
    Convert `claude -p` text output into an Anthropic API response object.

    Parsing priority:
    1. Valid ReAct JSON  {"reasoning":..., "action":..., ...}
    2. Inline [TOOL CALL: name] block  (model deviated from JSON format)
    3. Raw text fallback  (treated as end_turn / reasoning only)
    """
    data = _extract_first_json(raw)

    # If no valid ReAct JSON found, check for inline [TOOL CALL: ...] pattern
    if not (data and ("action" in data or "final_answer" in data)):
        inline = _extract_inline_tool_call(raw)
        if inline:
            data = inline

    content   = []
    stop_reason = "end_turn"

    if data and data.get("action") and data.get("action") != "null":
        # Tool call turn
        tool_name  = data["action"]
        tool_input = data.get("action_input") or {}
        reasoning  = data.get("reasoning", "")

        if reasoning:
            content.append({"type": "text", "text": reasoning})

        content.append({
            "type":  "tool_use",
            "id":    f"toolu_{uuid.uuid4().hex[:16]}",
            "name":  tool_name,
            "input": tool_input,
        })
        stop_reason = "tool_use"

    elif data and data.get("final_answer"):
        # Final answer
        answer = data["final_answer"]
        reasoning = data.get("reasoning", "")
        text = (f"{reasoning}\n\n{answer}" if reasoning else answer).strip()
        content.append({"type": "text", "text": text})

    else:
        # Fallback: return raw text as-is
        content.append({"type": "text", "text": raw.strip()})

    return {
        "id":            f"msg_{uuid.uuid4().hex[:24]}",
        "type":          "message",
        "role":          "assistant",
        "content":       content,
        "model":         model,
        "stop_reason":   stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens":  max(1, len(raw) // 6),
            "output_tokens": max(1, len(raw) // 6),
        },
    }


# ── HTTP handler ───────────────────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Only log errors, not every request
        if args and str(args[1]) != "200":
            print(f"[proxy] {fmt % args}", file=sys.stderr)

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send_json({"status": "ok", "proxy": "claude-code-proxy"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length))

        system   = body.get("system", "")
        messages = body.get("messages", [])
        tools    = body.get("tools", [])
        model    = body.get("model", "claude-opus-4-6")
        max_tok  = body.get("max_tokens", 4096)

        prompt = build_prompt(system, messages, tools)

        # Use the model from the request unless PROXY_MODEL env var overrides it.
        # Default fallback is Haiku (higher turn limits on Pro subscription).
        req_model   = body.get("model", "claude-opus-4-6")
        proxy_model = os.environ.get("PROXY_MODEL", req_model)

        print(f"[proxy] turn {len(messages)} — calling claude -p ({len(prompt)} chars) via {proxy_model}", file=sys.stderr)

        _FORCE_TOOL_PREAMBLE = (
            "IMPORTANT: You must respond ONLY with a JSON object in this exact format:\n"
            '{"reasoning": "<your thinking>", "action": "<tool_name>", "action_input": {<args>}}\n'
            "Do NOT write prose, markdown, or a final_answer unless you have collected all required data.\n\n"
        )
        _MAX_PROXY_RETRIES = 2
        raw = ""
        response = None

        for _attempt in range(_MAX_PROXY_RETRIES + 1):
            current_prompt = (_FORCE_TOOL_PREAMBLE + prompt) if _attempt > 0 else prompt
            _t0 = time.monotonic()
            timed_out = False
            try:
                result = subprocess.run(
                    ["claude", "-p", current_prompt, "--model", proxy_model],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                raw = result.stdout.strip()
                rc  = result.returncode
                if not raw and result.stderr:
                    raw = f"Error from claude: {result.stderr[:200]}"
            except subprocess.TimeoutExpired:
                timed_out = True
                rc  = -1
                raw = json.dumps({
                    "reasoning": "Request timed out after 600s.",
                    "action": None,
                    "action_input": {},
                    "final_answer": "Analysis timed out. Please try a narrower question.",
                })
            except FileNotFoundError:
                rc  = -1
                raw = json.dumps({
                    "reasoning": "claude CLI not found in PATH.",
                    "action": None,
                    "action_input": {},
                    "final_answer": "Error: claude binary not found.",
                })

            elapsed = time.monotonic() - _t0
            response = parse_claude_response(raw, model)
            stop     = response.get("stop_reason", "?")
            preview  = raw[:120].replace("\n", " ")

            print(
                f"[proxy] attempt {_attempt + 1}/{_MAX_PROXY_RETRIES + 1} "
                f"took {elapsed:.1f}s — rc={rc} "
                f"{'TIMEOUT ' if timed_out else ''}"
                f"stdout={len(raw)}chars → {stop} | {preview!r}",
                file=sys.stderr,
            )

            # If tools were requested and we got a tool_use back, we're done.
            if not tools or stop == "tool_use":
                break

            # Got text instead of a tool call — retry with forcing preamble if budget remains.
            if _attempt < _MAX_PROXY_RETRIES:
                print(
                    f"[proxy] STALL retry {_attempt + 1}/{_MAX_PROXY_RETRIES} — "
                    f"forcing tool-call format (raw preview: {raw[:80]!r})",
                    file=sys.stderr,
                )

        self._send_json(response)


# ── Public API ─────────────────────────────────────────────────────────────────

_server_instance = None
_server_lock     = threading.Lock()


def _port_is_our_proxy(port: int) -> bool:
    """Return True if port is already serving our proxy (survives across processes)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=1) as r:
            data = json.loads(r.read())
            return data.get("proxy") == "claude-code-proxy"
    except Exception:
        return False


def start_proxy(port: int = PROXY_PORT) -> int:
    """
    Start the proxy server in a background daemon thread (idempotent).
    If the port is already bound by a previous run of this proxy, reuse it.
    Returns the port it's listening on.
    """
    global _server_instance
    with _server_lock:
        if _server_instance is not None:
            return port

        # Check if a previous process already has our proxy on this port
        if _port_is_our_proxy(port):
            print(f"[proxy] Reusing existing proxy on http://127.0.0.1:{port}", file=sys.stderr)
            return port

        # Try to bind; if port is taken by something else, pick the next free one
        class _ReusableServer(HTTPServer):
            allow_reuse_address = True

        for candidate in range(port, port + 10):
            try:
                server = _ReusableServer(("127.0.0.1", candidate), ProxyHandler)
                t = threading.Thread(target=server.serve_forever, daemon=True, name="anthropic-proxy")
                t.start()
                _server_instance = server
                print(f"[proxy] Anthropic API proxy started on http://127.0.0.1:{candidate}", file=sys.stderr)
                return candidate
            except OSError:
                continue

        raise RuntimeError("Could not bind proxy on ports 9797-9806")
    return port


if __name__ == "__main__":
    # Run standalone for testing
    p = start_proxy()
    print(f"Proxy running on port {p}. Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nProxy stopped.")
