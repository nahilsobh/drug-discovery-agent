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
import re
import subprocess
import threading
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

PROXY_PORT = 9797

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

Rules:
- Call ONE tool per turn. Do not batch tool calls.
- action_input keys must exactly match the tool's parameter names.
- NEVER fabricate [TOOL RESULT] or [USER] lines. The system will provide them.
- NEVER include [TOOL CALL:] or [TOOL RESULT:] labels in your output — only output the JSON object.
- When all analysis is done set action=null and write final_answer.

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


def build_prompt(system: str, messages: list, tools: list) -> str:
    parts = [REACT_PREAMBLE, _tool_list_section(tools)]

    if system:
        # Inject system prompt as context, stripped of tool-list sections
        # (we already provide tools above in our own format)
        parts.append(f"Context / Instructions:\n{system}\n\n---\n\n")

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


def parse_claude_response(raw: str, model: str) -> dict:
    """
    Convert `claude -p` text output into an Anthropic API response object.
    """
    data = _extract_first_json(raw)

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

        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--model", proxy_model],
                capture_output=True,
                text=True,
                timeout=300,
            )
            raw = result.stdout.strip()
            if not raw and result.stderr:
                raw = f"Error from claude: {result.stderr[:200]}"
        except subprocess.TimeoutExpired:
            raw = json.dumps({
                "reasoning": "Request timed out after 180s.",
                "action": None,
                "action_input": {},
                "final_answer": "Analysis timed out. Please try a narrower question.",
            })
        except FileNotFoundError:
            raw = json.dumps({
                "reasoning": "claude CLI not found in PATH.",
                "action": None,
                "action_input": {},
                "final_answer": "Error: claude binary not found.",
            })

        response = parse_claude_response(raw, model)
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
