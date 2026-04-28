"""
Structured audit trail for the Roche AI Factory Strategic Discovery Agent.

Every tool call is appended as a JSON Lines record to:
    logs/audit_<session_id>.jsonl

Each record contains:
    session_id   — UUID4 stable for the entire run
    timestamp    — ISO 8601 UTC
    turn         — ReAct loop turn number
    call_num     — monotonically increasing call counter (global for session)
    tool_name    — e.g. "find_gaps"
    category     — e.g. "DISCOVERY", "COMPETITIVE", etc.
    inputs       — full input dict (or truncated if > 8 KB)
    result_digest— first 500 chars of JSON result
    result_len   — full byte length of original result_str
    elapsed_secs — float, wall-clock time for the tool call
    slurm_job_id — $SLURM_JOB_ID (None if not in SLURM)
    in_container — True if running inside a Singularity container
    model        — Claude model ID used in this session
    query_digest — first 200 chars of the user query
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

_MAX_INPUTS_BYTES = 8_000
_RESULT_DIGEST_LEN = 500
_QUERY_DIGEST_LEN  = 200


class AuditLogger:
    """
    One instance per agent run.  Thread-safe via line-buffered writes
    (each .write() call emits a single newline-terminated JSON object).

    Usage
    -----
    logger = AuditLogger(model="claude-opus-4-6", query="CEO briefing ...")
    logger.log(turn=1, call_num=1, tool_name="find_gaps",
               category="DISCOVERY", inputs={...},
               result_str='{"gaps": [...]}', elapsed=2.34)
    logger.close()          # optional — file is flushed on each write
    """

    def __init__(self, model: str = "", query: str = "") -> None:
        self.session_id  = str(uuid.uuid4())
        self.model       = model
        self.query_digest = query[:_QUERY_DIGEST_LEN]

        # Environment metadata (stable for the session)
        self.slurm_job_id = os.environ.get("SLURM_JOB_ID")
        # Singularity sets SINGULARITY_CONTAINER or SINGULARITYENV_* variables;
        # also check the common .singularity_bind mount-point sentinel.
        self.in_container = (
            "SINGULARITY_CONTAINER" in os.environ
            or "SINGULARITY_NAME"    in os.environ
            or os.path.exists("/.singularity.d")
        )

        log_path = _LOGS_DIR / f"audit_{self.session_id}.jsonl"
        self._fh = open(log_path, "w", buffering=1, encoding="utf-8")  # line-buffered
        self.log_path = str(log_path)

        # Write a session-start header record
        self._write({
            "record_type":  "session_start",
            "session_id":   self.session_id,
            "timestamp":    _now_utc(),
            "model":        self.model,
            "query_digest": self.query_digest,
            "slurm_job_id": self.slurm_job_id,
            "in_container": self.in_container,
            "log_path":     self.log_path,
        })
        print(f"[Audit] Session {self.session_id} → {self.log_path}")

    # ------------------------------------------------------------------
    def log(
        self,
        turn:        int,
        call_num:    int,
        tool_name:   str,
        category:    str,
        inputs:      dict[str, Any],
        result_str:  str,
        elapsed:     float,
    ) -> None:
        """Append one tool-call record."""
        inputs_str = json.dumps(inputs)
        if len(inputs_str) > _MAX_INPUTS_BYTES:
            inputs_str = inputs_str[:_MAX_INPUTS_BYTES] + "... [TRUNCATED]"
            inputs_logged: Any = {"_truncated": True, "_raw": inputs_str}
        else:
            inputs_logged = inputs

        record = {
            "record_type":    "tool_call",
            "session_id":     self.session_id,
            "timestamp":      _now_utc(),
            "turn":           turn,
            "call_num":       call_num,
            "tool_name":      tool_name,
            "category":       category,
            "inputs":         inputs_logged,
            "result_digest":  result_str[:_RESULT_DIGEST_LEN],
            "result_len":     len(result_str),
            "elapsed_secs":   round(elapsed, 3),
            "slurm_job_id":   self.slurm_job_id,
            "in_container":   self.in_container,
            "model":          self.model,
        }
        self._write(record)

    # ------------------------------------------------------------------
    def log_session_end(self, total_turns: int, total_calls: int) -> None:
        """Write a session-end summary record."""
        self._write({
            "record_type":  "session_end",
            "session_id":   self.session_id,
            "timestamp":    _now_utc(),
            "total_turns":  total_turns,
            "total_calls":  total_calls,
            "slurm_job_id": self.slurm_job_id,
            "in_container": self.in_container,
        })

    # ------------------------------------------------------------------
    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    # ------------------------------------------------------------------
    def _write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, default=str) + "\n")

    # ------------------------------------------------------------------
    def __del__(self) -> None:
        self.close()


# ── Convenience: pretty-print an audit log ─────────────────────────────────────

def print_audit_summary(log_path: str) -> None:
    """
    Print a human-readable summary table from a .jsonl audit log.
    Useful for post-run review or CI checks.
    """
    records = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except FileNotFoundError:
        print(f"[Audit] Log not found: {log_path}")
        return

    tool_calls = [r for r in records if r.get("record_type") == "tool_call"]
    if not tool_calls:
        print("[Audit] No tool calls found in log.")
        return

    header = (
        f"{'#':>4}  {'Turn':>4}  {'Category':<14}  {'Tool':<32}  "
        f"{'Elapsed':>7}  {'Result':>8}"
    )
    print("\n" + "=" * len(header))
    print(" AUDIT TRAIL SUMMARY")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in tool_calls:
        print(
            f"{r['call_num']:>4}  "
            f"{r['turn']:>4}  "
            f"{r['category']:<14}  "
            f"{r['tool_name']:<32}  "
            f"{r['elapsed_secs']:>7.2f}s  "
            f"{r['result_len']:>7}B"
        )
    print("-" * len(header))
    total_time = sum(r["elapsed_secs"] for r in tool_calls)
    print(f"  {len(tool_calls)} tool calls  |  {total_time:.1f}s total tool time")
    end_records = [r for r in records if r.get("record_type") == "session_end"]
    if end_records:
        e = end_records[-1]
        print(f"  {e['total_turns']} turns  |  session {e['session_id']}")
    print("=" * len(header) + "\n")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
