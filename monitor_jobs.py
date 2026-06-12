#!/usr/bin/env python3
"""
monitor_jobs.py — SLURM job monitor and diagnostic agent for the Whitespace pipeline.

Usage:
    python3 monitor_jobs.py                   # one-shot status snapshot
    python3 monitor_jobs.py --watch           # poll every 60s until all jobs finish
    python3 monitor_jobs.py --watch --resubmit # auto-resubmit stalled jobs
    python3 monitor_jobs.py --type competitive # filter by job type
    python3 monitor_jobs.py --since 2h        # only show jobs from last 2 hours
"""

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).parent
LOGS_DIR = PROJECT / "logs"
REPORTS_DIR = PROJECT / "reports"
USER = os.environ.get("USER", "sobhn")

# ── Competitor → slug mapping ──────────────────────────────────────────────────
COMPETITORS: dict[str, str] = {
    "AstraZeneca":          "astrazeneca",
    "Eli Lilly":            "eli_lilly",
    "Novartis":             "novartis",
    "Pfizer":               "pfizer",
    "Merck":                "merck",
    "Bristol-Myers Squibb": "bms",
    "AbbVie":               "abbvie",
    "Johnson & Johnson":    "jnj",
}
SLUG_TO_COMPETITOR = {v: k for k, v in COMPETITORS.items()}

# ── Stall cause classifiers ────────────────────────────────────────────────────
# Each entry: (log_pattern, cause_code, explanation, recommendation)
STALL_PATTERNS = [
    (
        r"ona-claude did not start within",
        "ONA_TIMEOUT",
        "ona-claude health check timed out (>60s). SSH tunnel may be stale or token expired.",
        "Run: $HOME/.local/bin/ona-claude status\nThen resubmit.",
    ),
    (
        r"Unable to connect to API \(ConnectionRefused\)",
        "ONA_UNREACHABLE",
        "claude -p could not reach the ona-claude proxy. "
        "Either proxy crashed mid-run or ANTHROPIC_BASE_URL pointed at wrong port.",
        "Resubmit — scripts now export ANTHROPIC_BASE_URL per-job correctly.",
    ),
    (
        r"STALL ABORT",
        "PROXY_TEXT_STALL",
        "Agent guard triggered: 3 consecutive turns with no tool calls. "
        "Claude returned plain text instead of ReAct JSON.",
        "Transient model behaviour — resubmit.",
    ),
    (
        r"plain text instead of tool.use",
        "PROXY_TEXT_STALL",
        "Proxy detected plain-text response instead of tool_use block.",
        "Transient — resubmit.",
    ),
    (
        r"WARNING: GenomeClaw not reachable",
        "GENOMECLAW_OFFLINE",
        "clawapi failed to start or GPU was unavailable. GPU tools ran in offline fallback.",
        "Check nvidia_icd.json path and GPU allocation. Resubmit.",
    ),
    (
        r"[Dd]isk quota exceeded|No space left on device",
        "DISK_QUOTA",
        "Filesystem quota exceeded. PDF or log could not be written.",
        "Free space: empty ~/.local/share/Trash/ or scratchfs cache. Then resubmit.",
    ),
    (
        r"^Killed",
        "OOM",
        "Process killed by kernel — likely out of memory.",
        "Increase #SBATCH --mem (currently 32G) and resubmit.",
    ),
    (
        r"fork: retry",
        "SC1_STORAGE",
        "SC1 storage slowdown (INC16190297). Transient infrastructure issue.",
        "Resubmit — usually resolves automatically.",
    ),
    (
        r"Error from claude:|claude CLI not found",
        "CLAUDE_CLI_ERROR",
        "The claude CLI binary failed or was not found inside the Singularity container.",
        "Verify run_singularity.sh bind-mounts the claude binary correctly.",
    ),
    (
        r"SIF image not found",
        "CONTAINER_MISSING",
        "Singularity .sif image not found at expected path.",
        "Rebuild: srun singularity build $HOME/singularity-images/drug-discovery-agent.sif ...",
    ),
]


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class JobInfo:
    log_path: Path
    job_id: str
    job_type: str        # competitive | repurposing | ceo | full_suite | unknown
    competitor: str      # "Eli Lilly" or "" for CEO
    slug: str            # "eli_lilly" or ""
    node: str
    start_time: str
    ona_port: str
    claw_port: str
    proxy_port: str
    result: str          # SUCCESS | STALLED | RUNNING | UNKNOWN
    stall_cause: str     # cause code or ""
    stall_explanation: str
    stall_recommendation: str
    live_checks: dict = field(default_factory=dict)
    pdf_path: str = ""

    @property
    def label(self) -> str:
        if self.job_type == "competitive":
            return f"Roche vs {self.competitor}"
        if self.job_type == "repurposing":
            return f"Repurposing: {self.competitor}"
        if self.job_type == "ceo":
            return "CEO Strategic Briefing"
        if self.job_type == "full_suite":
            return "Full Suite (3 queries)"
        return f"Job {self.job_id}"


# ── SLURM queue ────────────────────────────────────────────────────────────────

def get_running_jobs() -> dict[str, dict]:
    """Return {jobid: {name, partition, state, time, node}} for current user."""
    try:
        out = subprocess.check_output(
            ["squeue", "-u", USER, "--format=%i|%j|%P|%T|%M|%R"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return {}
    jobs = {}
    for line in out.strip().splitlines()[1:]:
        parts = line.split("|")
        if len(parts) >= 6:
            jid, name, part, state, elapsed, reason = parts[:6]
            jobs[jid.strip()] = {
                "name": name.strip(),
                "partition": part.strip(),
                "state": state.strip(),
                "elapsed": elapsed.strip(),
                "reason": reason.strip(),
            }
    return jobs


# ── Log parsing ────────────────────────────────────────────────────────────────

def _grep(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(0) if m else None


def _grep_value(pattern: str, text: str, group: int = 1) -> str:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else ""


def parse_log(log_path: Path, running_ids: set[str]) -> JobInfo:
    text = log_path.read_text(errors="replace")

    # Job type from log filename
    name = log_path.name
    if name.startswith("competitive_"):
        job_type = "competitive"
    elif name.startswith("repurposing_"):
        job_type = "repurposing"
    elif name.startswith("ceo_briefing_"):
        job_type = "ceo"
    elif name.startswith("full_suite_"):
        job_type = "full_suite"
    else:
        job_type = "unknown"

    # Extract job ID from filename (last numeric segment before .out)
    m = re.search(r"_(\d+)\.out$", name)
    job_id = m.group(1) if m else "?"

    competitor  = _grep_value(r"Competitor\s+:\s+(.+)", text)
    node        = _grep_value(r"Node\s+:\s+(\S+)", text)
    start_time  = _grep_value(r"Started\s+:\s+(\S+)", text)
    ona_port    = _grep_value(r"ONA port\s+:\s+(\d+)", text)
    claw_port   = _grep_value(r"CLAW port\s+:\s+(\d+)", text)
    proxy_port  = _grep_value(r"Proxy port\s+:\s+(\d+)", text)

    slug = COMPETITORS.get(competitor, "")
    if not slug and competitor:
        # Try reverse lookup by slug embedded in filename
        for s, c in SLUG_TO_COMPETITOR.items():
            if s in name:
                slug = s
                competitor = c
                break

    # Result
    if "[result] SUCCESS" in text:
        result = "SUCCESS"
        pdf_m = re.search(r"\[result\] SUCCESS — PDF generated: (.+)", text)
        pdf_path = pdf_m.group(1).strip() if pdf_m else ""
    elif "[result] STALLED" in text:
        result = "STALLED"
        pdf_path = ""
    elif job_id in running_ids:
        result = "RUNNING"
        pdf_path = ""
    else:
        result = "UNKNOWN"
        pdf_path = ""

    # Stall diagnosis
    stall_cause = stall_explanation = stall_recommendation = ""
    if result == "STALLED":
        for pattern, code, explanation, recommendation in STALL_PATTERNS:
            if re.search(pattern, text, re.MULTILINE | re.IGNORECASE):
                stall_cause = code
                stall_explanation = explanation
                stall_recommendation = recommendation
                break
        if not stall_cause:
            stall_cause = "UNKNOWN"
            stall_explanation = (
                "No known error pattern matched. Could be an API-side issue, "
                "network timeout, or cluster resource problem."
            )
            stall_recommendation = (
                "Review full log: tail -100 " + str(log_path) + "\n"
                "Check Anthropic API status. Resubmit if transient."
            )

    return JobInfo(
        log_path=log_path,
        job_id=job_id,
        job_type=job_type,
        competitor=competitor,
        slug=slug,
        node=node,
        start_time=start_time,
        ona_port=ona_port,
        claw_port=claw_port,
        proxy_port=proxy_port,
        result=result,
        stall_cause=stall_cause,
        stall_explanation=stall_explanation,
        stall_recommendation=stall_recommendation,
        pdf_path=pdf_path,
    )


# ── Live diagnostics ───────────────────────────────────────────────────────────

def run_live_checks(job: JobInfo) -> dict:
    checks = {}

    # Is ona-claude port still bound?
    if job.ona_port:
        try:
            out = subprocess.check_output(
                ["ss", "-tlnp"], text=True, stderr=subprocess.DEVNULL
            )
            checks["ona_port_bound"] = f":{job.ona_port} " in out
        except Exception:
            checks["ona_port_bound"] = None

    # Is clawapi healthy?
    if job.claw_port:
        try:
            r = subprocess.run(
                ["curl", "-sf", "--max-time", "3",
                 f"http://127.0.0.1:{job.claw_port}/health"],
                capture_output=True, text=True
            )
            checks["clawapi_healthy"] = r.returncode == 0
        except Exception:
            checks["clawapi_healthy"] = None

    # Disk usage on homefs
    try:
        out = subprocess.check_output(
            ["df", "-h", str(Path.home())], text=True, stderr=subprocess.DEVNULL
        )
        lines = out.strip().splitlines()
        if len(lines) >= 2:
            checks["homefs_disk"] = " ".join(lines[1].split())
    except Exception:
        checks["homefs_disk"] = None

    # Disk usage on scratchfs
    scratchfs = "/gpfs/scratchfs01/site/u/sobhn"
    if os.path.exists(scratchfs):
        try:
            out = subprocess.check_output(
                ["df", "-h", scratchfs], text=True, stderr=subprocess.DEVNULL
            )
            lines = out.strip().splitlines()
            if len(lines) >= 2:
                checks["scratchfs_disk"] = " ".join(lines[1].split())
        except Exception:
            checks["scratchfs_disk"] = None

    return checks


# ── Resubmission ───────────────────────────────────────────────────────────────

def resubmit_job(job: JobInfo, delay_s: int = 0) -> str:
    """Submit the same job type again. Returns new job ID string."""
    env = os.environ.copy()
    cmd = ["sbatch"]
    if delay_s:
        cmd += [f"--begin=now+{delay_s}seconds"]

    if job.job_type == "competitive":
        if not job.competitor or not job.slug:
            raise ValueError(f"Cannot resubmit: competitor/slug not found in log {job.log_path}")
        env["COMPETITOR"]   = job.competitor
        env["COMPANY_SLUG"] = job.slug
        cmd += ["--job-name", job.slug]  # ensures %x in --output resolves to slug correctly
        cmd.append(str(PROJECT / "competitive_briefing_slurm.sh"))
    elif job.job_type == "repurposing":
        if not job.competitor or not job.slug:
            raise ValueError(f"Cannot resubmit: competitor/slug not found in log {job.log_path}")
        env["COMPETITOR"]   = job.competitor
        env["COMPANY_SLUG"] = job.slug
        cmd += ["--job-name", job.slug]  # ensures %x in --output resolves to slug correctly
        cmd.append(str(PROJECT / "repurposing_slurm.sh"))
    elif job.job_type == "ceo":
        cmd.append(str(PROJECT / "ceo_query_slurm.sh"))
    elif job.job_type == "full_suite":
        cmd.append(str(PROJECT / "run_full_suite.sh"))
    else:
        raise ValueError(f"Don't know how to resubmit job type '{job.job_type}'")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(PROJECT))
    if result.returncode != 0:
        raise RuntimeError(f"sbatch failed: {result.stderr.strip()}")
    m = re.search(r"(\d+)", result.stdout)
    return m.group(1) if m else "?"


# ── Display ────────────────────────────────────────────────────────────────────

STATUS_ICONS = {
    "SUCCESS": "✓",
    "STALLED": "✗",
    "RUNNING": "⟳",
    "UNKNOWN": "?",
}

CAUSE_COLORS = {
    "ONA_TIMEOUT":       "\033[33m",   # yellow
    "ONA_UNREACHABLE":   "\033[33m",
    "PROXY_TEXT_STALL":  "\033[33m",
    "GENOMECLAW_OFFLINE":"\033[35m",   # magenta
    "DISK_QUOTA":        "\033[31m",   # red
    "OOM":               "\033[31m",
    "SC1_STORAGE":       "\033[36m",   # cyan
    "CLAUDE_CLI_ERROR":  "\033[31m",
    "CONTAINER_MISSING": "\033[31m",
    "UNKNOWN":           "\033[31m",
}
RESET = "\033[0m"
BOLD  = "\033[1m"
GREEN = "\033[32m"
RED   = "\033[31m"
CYAN  = "\033[36m"


def _col(text: str, width: int) -> str:
    return text[:width].ljust(width)


def print_report(jobs: list[JobInfo], running_ids: set[str]) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n{BOLD}{'='*72}{RESET}")
    print(f"{BOLD}  Whitespace Pipeline Monitor  —  {now}{RESET}")
    print(f"{'='*72}")

    # Summary counts
    counts = {"SUCCESS": 0, "STALLED": 0, "RUNNING": 0, "UNKNOWN": 0}
    for j in jobs:
        counts[j.result] = counts.get(j.result, 0) + 1

    print(
        f"  {GREEN}✓ {counts['SUCCESS']} succeeded{RESET}  "
        f"{RED}✗ {counts['STALLED']} stalled{RESET}  "
        f"{CYAN}⟳ {counts['RUNNING']} running{RESET}  "
        f"? {counts.get('UNKNOWN', 0)} unknown"
    )
    print()

    # Table header
    w_label, w_status, w_node, w_cause = 32, 8, 14, 20
    hdr = (
        f"  {_col('Job', w_label)}"
        f"{_col('Status', w_status)}"
        f"{_col('Node', w_node)}"
        f"{_col('Cause', w_cause)}"
    )
    print(f"{BOLD}{hdr}{RESET}")
    print("  " + "-" * (w_label + w_status + w_node + w_cause))

    for j in sorted(jobs, key=lambda x: (x.job_type, x.competitor)):
        icon  = STATUS_ICONS.get(j.result, "?")
        color = (GREEN if j.result == "SUCCESS"
                 else RED if j.result == "STALLED"
                 else CYAN if j.result == "RUNNING"
                 else "")
        cause_color = CAUSE_COLORS.get(j.stall_cause, "")

        print(
            f"  {_col(j.label, w_label)}"
            f"{color}{icon} {_col(j.result, w_status-2)}{RESET}  "
            f"{_col(j.node or '—', w_node)}"
            f"{cause_color}{_col(j.stall_cause or '—', w_cause)}{RESET}"
        )

    print()

    # Detailed stall reports
    stalled = [j for j in jobs if j.result == "STALLED"]
    if stalled:
        print(f"{BOLD}{RED}  ─── STALL REPORTS ──────────────────────────────────────{RESET}")
        for j in stalled:
            print(f"\n  {BOLD}[STALL] {j.label}  (Job {j.job_id}){RESET}")
            print(f"    Log  : {j.log_path}")
            print(f"    Node : {j.node or 'unknown'}  |  Started: {j.start_time or 'unknown'}")
            print(f"    Ports: ONA={j.ona_port or '?'}  CLAW={j.claw_port or '?'}  PROXY={j.proxy_port or '?'}")
            print(f"\n    {BOLD}Cause:{RESET} {j.stall_cause}")
            print(f"    {j.stall_explanation}")
            print(f"\n    {BOLD}Recommendation:{RESET}")
            for line in j.stall_recommendation.splitlines():
                print(f"      {line}")

            # Live checks
            if j.live_checks:
                print(f"\n    {BOLD}Live checks:{RESET}")
                for k, v in j.live_checks.items():
                    if v is None:
                        status_str = "unavailable"
                    elif isinstance(v, bool):
                        status_str = (f"{GREEN}yes{RESET}" if v else f"{RED}no{RESET}")
                    else:
                        status_str = str(v)
                    print(f"      {k:<22} {status_str}")

            # Resubmit hint
            if j.job_type in ("competitive", "repurposing") and j.competitor:
                slug = j.slug or COMPETITORS.get(j.competitor, "")
                script = ("competitive_briefing_slurm.sh"
                          if j.job_type == "competitive" else "repurposing_slurm.sh")
                print(f"\n    {BOLD}Resubmit command:{RESET}")
                print(f"      cd {PROJECT}")
                print(f"      COMPETITOR=\"{j.competitor}\" COMPANY_SLUG=\"{slug}\" sbatch --job-name=\"{slug}\" {script}")
            elif j.job_type == "ceo":
                print(f"\n    {BOLD}Resubmit command:{RESET}")
                print(f"      sbatch {PROJECT}/ceo_query_slurm.sh")

    # PDF summary
    succeeded = [j for j in jobs if j.result == "SUCCESS"]
    if succeeded:
        print(f"\n{BOLD}  ─── OUTPUTS ─────────────────────────────────────────────{RESET}")
        for j in succeeded:
            pdf = j.pdf_path or "—"
            print(f"  {GREEN}✓{RESET} {j.label:<35} {pdf}")

    print(f"\n{'='*72}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def collect_jobs(job_type_filter: Optional[str], days: int = 2) -> list[JobInfo]:
    """Return jobs from the last `days` days only, keeping newest per (type, competitor)."""
    running = get_running_jobs()
    running_ids = set(running.keys())

    patterns = {
        "competitive": "competitive_*.out",
        "repurposing":  "repurposing_*.out",
        "ceo":          "ceo_briefing_*.out",
        "full_suite":   "full_suite_*.out",
    }

    if job_type_filter and job_type_filter in patterns:
        globs = [patterns[job_type_filter]]
    else:
        globs = list(patterns.values())

    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = []
    for pattern in globs:
        for p in sorted(LOGS_DIR.glob(pattern)):
            mtime = datetime.utcfromtimestamp(p.stat().st_mtime)
            if mtime >= cutoff:
                logs.append(p)

    all_jobs = [parse_log(p, running_ids) for p in logs]

    # Keep only the most recent log per (job_type, competitor).
    # Sort oldest→newest so the last write wins unconditionally.
    seen: dict[tuple, JobInfo] = {}
    for j in sorted(all_jobs, key=lambda x: x.log_path.stat().st_mtime):
        key = (j.job_type, j.competitor)
        seen[key] = j  # always overwrite: newest log wins

    return list(seen.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Whitespace SLURM job monitor")
    parser.add_argument("--watch",     action="store_true", help="Poll every 60s until all done")
    parser.add_argument("--resubmit",  action="store_true", help="Auto-resubmit stalled jobs")
    parser.add_argument("--type",      choices=["competitive", "repurposing", "ceo", "full_suite"],
                        help="Filter to specific job type")
    parser.add_argument("--interval",  type=int, default=60, help="Watch poll interval (seconds)")
    args = parser.parse_args()

    seen_stalled: set[str] = set()   # log paths of already-reported stalls
    resubmitted:  set[str] = set()   # log paths of already-resubmitted jobs

    def run_once() -> list[JobInfo]:
        jobs = collect_jobs(args.type)
        running = get_running_jobs()
        running_ids = set(running.keys())

        # Run live checks on stalled jobs
        for j in jobs:
            if j.result == "STALLED":
                j.live_checks = run_live_checks(j)

        print_report(jobs, running_ids)

        # Handle stalls
        for j in jobs:
            if j.result != "STALLED":
                continue
            log_key = str(j.log_path)

            if log_key not in seen_stalled:
                seen_stalled.add(log_key)
                print(f"{BOLD}{RED}  ! NEW STALL DETECTED:{RESET} {j.label} (Job {j.job_id})")
                print(f"    Cause: {j.stall_cause} — {j.stall_explanation[:80]}")

            if args.resubmit and log_key not in resubmitted:
                try:
                    new_id = resubmit_job(j)
                    resubmitted.add(log_key)
                    print(f"  {GREEN}[RESUBMIT]{RESET} {j.label} → new Job {new_id}")
                except Exception as e:
                    print(f"  {RED}[RESUBMIT FAILED]{RESET} {j.label}: {e}")

        return jobs

    if not args.watch:
        run_once()
        return

    print(f"Watching jobs (interval={args.interval}s). Ctrl+C to stop.")
    while True:
        jobs = run_once()
        still_running = [j for j in jobs if j.result == "RUNNING"]
        if not still_running:
            print(f"{GREEN}All jobs have finished.{RESET}")
            break
        print(f"  {len(still_running)} job(s) still running. Next check in {args.interval}s...")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
