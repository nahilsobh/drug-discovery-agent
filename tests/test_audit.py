"""Tests for tools/audit.py — structured JSON Lines audit trail."""

import json
import os
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger(tmp_path, model="claude-opus-4-6", query="test query"):
    """Create an AuditLogger whose log file lands in tmp_path."""
    from tools.audit import AuditLogger
    with patch("tools.audit._LOGS_DIR", Path(tmp_path)):
        logger = AuditLogger(model=model, query=query)
    return logger


def _read_records(log_path):
    with open(log_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# AuditLogger — initialisation
# ---------------------------------------------------------------------------

class TestAuditLoggerInit:
    def test_creates_log_file(self, tmp_path):
        logger = _make_logger(tmp_path)
        assert os.path.exists(logger.log_path)

    def test_session_id_is_uuid4_format(self, tmp_path):
        import re
        logger = _make_logger(tmp_path)
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            logger.session_id,
        )

    def test_writes_session_start_record(self, tmp_path):
        logger = _make_logger(tmp_path, model="test-model", query="hello world")
        records = _read_records(logger.log_path)
        assert records[0]["record_type"] == "session_start"
        assert records[0]["model"] == "test-model"
        assert records[0]["query_digest"] == "hello world"
        assert records[0]["session_id"] == logger.session_id

    def test_query_digest_truncated_to_200_chars(self, tmp_path):
        long_query = "x" * 500
        logger = _make_logger(tmp_path, query=long_query)
        records = _read_records(logger.log_path)
        assert len(records[0]["query_digest"]) == 200

    def test_slurm_job_id_captured(self, tmp_path):
        with patch.dict(os.environ, {"SLURM_JOB_ID": "99999"}):
            logger = _make_logger(tmp_path)
        assert logger.slurm_job_id == "99999"
        records = _read_records(logger.log_path)
        assert records[0]["slurm_job_id"] == "99999"

    def test_slurm_job_id_none_outside_slurm(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "SLURM_JOB_ID"}
        with patch.dict(os.environ, env, clear=True):
            logger = _make_logger(tmp_path)
        assert logger.slurm_job_id is None

    def test_in_container_detected_via_singularity_env(self, tmp_path):
        with patch.dict(os.environ, {"SINGULARITY_CONTAINER": "/images/agent.sif"}):
            logger = _make_logger(tmp_path)
        assert logger.in_container is True

    def test_in_container_false_outside_singularity(self, tmp_path):
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("SINGULARITY_CONTAINER", "SINGULARITY_NAME")}
        with patch.dict(os.environ, clean_env, clear=True), \
             patch("os.path.exists", return_value=False):
            logger = _make_logger(tmp_path)
        assert logger.in_container is False


# ---------------------------------------------------------------------------
# AuditLogger.log — tool call records
# ---------------------------------------------------------------------------

class TestAuditLoggerLog:
    def test_appends_tool_call_record(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log(
            turn=1, call_num=1, tool_name="find_gaps",
            category="DISCOVERY", inputs={"therapeutic_area": "oncology"},
            result_str='{"gaps": []}', elapsed=2.34,
        )
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert tc["tool_name"] == "find_gaps"
        assert tc["category"] == "DISCOVERY"
        assert tc["turn"] == 1
        assert tc["call_num"] == 1
        assert tc["elapsed_secs"] == 2.34
        assert tc["session_id"] == logger.session_id

    def test_result_digest_truncated_to_500_chars(self, tmp_path):
        logger = _make_logger(tmp_path)
        long_result = '{"data": "' + "x" * 1000 + '"}'
        logger.log(turn=1, call_num=1, tool_name="t", category="C",
                   inputs={}, result_str=long_result, elapsed=0.1)
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert len(tc["result_digest"]) == 500

    def test_result_len_is_full_length(self, tmp_path):
        logger = _make_logger(tmp_path)
        result = '{"x": "' + "y" * 2000 + '"}'
        logger.log(turn=1, call_num=1, tool_name="t", category="C",
                   inputs={}, result_str=result, elapsed=0.0)
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert tc["result_len"] == len(result)

    def test_large_inputs_truncated(self, tmp_path):
        logger = _make_logger(tmp_path)
        big_inputs = {"seq": "A" * 10_000}
        logger.log(turn=1, call_num=1, tool_name="fold_target", category="GENOMECLAW",
                   inputs=big_inputs, result_str="{}", elapsed=1.0)
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert tc["inputs"].get("_truncated") is True

    def test_small_inputs_stored_verbatim(self, tmp_path):
        logger = _make_logger(tmp_path)
        inputs = {"gene": "EGFR", "variant": "T790M"}
        logger.log(turn=2, call_num=2, tool_name="score_variant_effect",
                   category="GENOMECLAW", inputs=inputs, result_str="{}", elapsed=0.5)
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert tc["inputs"]["gene"] == "EGFR"
        assert tc["inputs"]["variant"] == "T790M"

    def test_multiple_calls_all_appended(self, tmp_path):
        logger = _make_logger(tmp_path)
        for i in range(5):
            logger.log(turn=i + 1, call_num=i + 1, tool_name=f"tool_{i}",
                       category="TEST", inputs={}, result_str="{}", elapsed=float(i))
        records = _read_records(logger.log_path)
        tool_calls = [r for r in records if r["record_type"] == "tool_call"]
        assert len(tool_calls) == 5
        assert [r["call_num"] for r in tool_calls] == list(range(1, 6))

    def test_elapsed_rounded_to_3dp(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log(turn=1, call_num=1, tool_name="t", category="C",
                   inputs={}, result_str="{}", elapsed=1.23456789)
        records = _read_records(logger.log_path)
        tc = next(r for r in records if r["record_type"] == "tool_call")
        assert tc["elapsed_secs"] == 1.235


# ---------------------------------------------------------------------------
# AuditLogger.log_session_end
# ---------------------------------------------------------------------------

class TestAuditLoggerSessionEnd:
    def test_writes_session_end_record(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log_session_end(total_turns=10, total_calls=7)
        records = _read_records(logger.log_path)
        end = next(r for r in records if r["record_type"] == "session_end")
        assert end["total_turns"] == 10
        assert end["total_calls"] == 7
        assert end["session_id"] == logger.session_id

    def test_session_end_timestamp_is_utc_iso(self, tmp_path):
        import re
        logger = _make_logger(tmp_path)
        logger.log_session_end(total_turns=1, total_calls=1)
        records = _read_records(logger.log_path)
        end = next(r for r in records if r["record_type"] == "session_end")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", end["timestamp"])


# ---------------------------------------------------------------------------
# AuditLogger.close / __del__
# ---------------------------------------------------------------------------

class TestAuditLoggerClose:
    def test_close_is_idempotent(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.close()
        logger.close()  # second call must not raise

    def test_log_path_accessible_after_close(self, tmp_path):
        logger = _make_logger(tmp_path)
        log_path = logger.log_path
        logger.close()
        assert os.path.exists(log_path)


# ---------------------------------------------------------------------------
# print_audit_summary
# ---------------------------------------------------------------------------

class TestPrintAuditSummary:
    def _make_full_log(self, tmp_path):
        logger = _make_logger(tmp_path)
        logger.log(turn=1, call_num=1, tool_name="find_gaps", category="DISCOVERY",
                   inputs={}, result_str='{"gaps": []}', elapsed=1.23)
        logger.log(turn=2, call_num=2, tool_name="generate_pdf_report", category="REPORT",
                   inputs={}, result_str='{"status": "success"}', elapsed=0.05)
        logger.log_session_end(total_turns=2, total_calls=2)
        return logger.log_path

    def test_prints_without_error(self, tmp_path, capsys):
        from tools.audit import print_audit_summary
        log_path = self._make_full_log(tmp_path)
        print_audit_summary(log_path)
        out = capsys.readouterr().out
        assert "find_gaps" in out
        assert "generate_pdf_report" in out

    def test_prints_tool_count_and_total_time(self, tmp_path, capsys):
        from tools.audit import print_audit_summary
        log_path = self._make_full_log(tmp_path)
        print_audit_summary(log_path)
        out = capsys.readouterr().out
        assert "2 tool calls" in out

    def test_handles_missing_file_gracefully(self, tmp_path, capsys):
        from tools.audit import print_audit_summary
        print_audit_summary(str(tmp_path / "does_not_exist.jsonl"))
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "Log not found" in out

    def test_handles_empty_log_no_tool_calls(self, tmp_path, capsys):
        from tools.audit import print_audit_summary
        # Log with only a session_start record
        logger = _make_logger(tmp_path)
        log_path = logger.log_path
        logger.close()
        print_audit_summary(log_path)
        out = capsys.readouterr().out
        assert "No tool calls" in out

    def test_shows_session_id_in_footer(self, tmp_path, capsys):
        from tools.audit import print_audit_summary
        log_path = self._make_full_log(tmp_path)
        print_audit_summary(log_path)
        out = capsys.readouterr().out
        records = _read_records(log_path)
        session_id = records[0]["session_id"]
        assert session_id in out


# ---------------------------------------------------------------------------
# _now_utc helper
# ---------------------------------------------------------------------------

class TestNowUtc:
    def test_returns_utc_iso_string(self):
        import re
        from tools.audit import _now_utc
        ts = _now_utc()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", ts)

    def test_timestamps_increase_monotonically(self):
        from tools.audit import _now_utc
        t1 = _now_utc()
        time.sleep(0.01)
        t2 = _now_utc()
        assert t2 >= t1
