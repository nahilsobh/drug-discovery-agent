#!/usr/bin/env python3
"""
Whitespace Web UI — FastAPI backend
Serves the CEO/VP chat interface for the RedClaw AI Factory pipeline.

Start:  uvicorn web_ui.server:app --host 127.0.0.1 --port 8080
Access: ssh -L 8080:localhost:8080 sobhn@<cluster> then http://localhost:8080
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets as _secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as _Response

PROJECT = Path(__file__).parent.parent
REPORTS_DIR = PROJECT / "reports"
LOGS_DIR    = PROJECT / "logs"
QUERIES_FILE = Path(__file__).parent / "ceo_queries.json"
STATIC_DIR   = Path(__file__).parent / "static"

COMPETITORS = {
    "AstraZeneca":          "astrazeneca",
    "Eli Lilly":            "eli_lilly",
    "Novartis":             "novartis",
    "Pfizer":               "pfizer",
    "Merck":                "merck",
    "Bristol-Myers Squibb": "bms",
    "AbbVie":               "abbvie",
    "Johnson & Johnson":    "jnj",
}

UI_ONA_PORT = int(os.environ.get("UI_ONA_PORT", "8090"))

CHAT_SYSTEM = """You are the RedClaw AI Factory assistant — an expert on the Whitespace strategic discovery platform.

The platform is a 34-tool ReAct agent that answers pharmaceutical strategic intelligence questions for RedClaw. It queries ClinicalTrials.gov, Open Targets, Europe PMC, ArXiv, ChEMBL, USPTO, FDA FAERS, UniProt, KEGG, Orphanet, and a GPU-accelerated GenomeClaw API (Boltz-1 protein folding, ESM-2 variant effects, ADMET prediction, scaffold clustering, docking).

Available pipeline jobs (launched from the sidebar):
- CEO Strategic Briefing: portfolio ranking, gaps, white spaces, trial scores, threats, 90-day action (~45 min)
- Competitive Intelligence: full briefing vs AstraZeneca, Eli Lilly, Novartis, Pfizer, Merck, BMS, AbbVie, J&J (~30 min each)
- Drug Repurposing: approved drugs for new indications, ADMET gate, orphan eligibility, combination strategies (~35 min each)
- Full Pipeline Suite: three-query deep run — gap sweep, KRAS G12C dive, atezolizumab repurposing (~120 min)
- Custom Job: any drug discovery query run through the full 34-tool agent on a GPU node

For drug discovery analysis that needs live data, recommend running a pipeline job via the query cards.
Answer general, meta, and strategic questions directly and concisely."""

SLURM_SCRIPTS = {
    "competitive": PROJECT / "competitive_briefing_slurm.sh",
    "repurposing":  PROJECT / "repurposing_slurm.sh",
    "ceo":          PROJECT / "ceo_query_slurm.sh",
    "full_suite":   PROJECT / "run_full_suite.sh",
}

UI_PASSWORD = os.environ.get("UI_PASSWORD", "whitespace2026")
_SESSION_TOKEN = hashlib.sha256(UI_PASSWORD.encode()).hexdigest()[:32]


class _BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # WebSocket upgrades — browser sends cookie automatically
        if request.headers.get("upgrade", "").lower() == "websocket":
            cookie = request.cookies.get("ws_session", "")
            if _secrets.compare_digest(cookie, _SESSION_TOKEN):
                return await call_next(request)
            return _Response("Unauthorized", status_code=401)

        # Cookie already set from a previous login
        cookie = request.cookies.get("ws_session", "")
        if _secrets.compare_digest(cookie, _SESSION_TOKEN):
            return await call_next(request)

        # HTTP Basic Auth
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                password = decoded.split(":", 1)[1] if ":" in decoded else ""
                if _secrets.compare_digest(password.encode(), UI_PASSWORD.encode()):
                    response = await call_next(request)
                    response.set_cookie("ws_session", _SESSION_TOKEN,
                                        httponly=True, samesite="strict")
                    return response
            except Exception:
                pass

        return _Response(
            "Authentication required", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Whitespace — RedClaw AI Factory"'},
        )


app = FastAPI(title="Whitespace — RedClaw AI Factory")
app.add_middleware(_BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Models ────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    job_type: str           # competitive | repurposing | ceo | full_suite | custom
    competitor: Optional[str] = None
    slug: Optional[str] = None
    query: Optional[str] = None   # for custom jobs


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/queries")
async def get_queries():
    return json.loads(QUERIES_FILE.read_text())


@app.get("/api/reports")
async def list_reports():
    if not REPORTS_DIR.exists():
        return []
    reports = []
    for p in sorted(REPORTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.suffix.lower() == ".pdf":
            stat = p.stat()
            reports.append({
                "name":     p.name,
                "size_kb":  round(stat.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })
    return reports


@app.get("/api/reports/{filename}")
async def get_report(filename: str):
    # Path-traversal guard
    if "/" in filename or ".." in filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(str(path), media_type="application/pdf")


@app.get("/api/jobs")
async def get_jobs():
    try:
        from monitor_jobs import collect_jobs
        jobs = collect_jobs(job_type_filter=None, days=3)
        return [
            {
                "label":    j.label,
                "job_type": j.job_type,
                "competitor": j.competitor,
                "job_id":   j.job_id,
                "result":   j.result,
                "stall_cause": j.stall_cause,
                "node":     j.node,
                "pdf_path": j.pdf_path,
                "log_path": str(j.log_path),
            }
            for j in jobs
        ]
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/run")
async def run_job(req: RunRequest):
    job_type = req.job_type

    if job_type not in SLURM_SCRIPTS and job_type != "custom":
        raise HTTPException(status_code=400, detail=f"Unknown job_type: {job_type}")

    # Custom query: write a one-off SLURM script
    if job_type == "custom":
        if not req.query or not req.query.strip():
            raise HTTPException(status_code=400, detail="query is required for custom job_type")
        return await _submit_custom(req.query.strip())

    script = SLURM_SCRIPTS[job_type]
    env    = os.environ.copy()
    cmd    = ["sbatch"]

    if job_type in ("competitive", "repurposing"):
        competitor = req.competitor or ""
        slug = req.slug or COMPETITORS.get(competitor, "")
        if not slug:
            raise HTTPException(status_code=400, detail=f"Unknown competitor: {competitor}")
        env["COMPETITOR"]   = competitor
        env["COMPANY_SLUG"] = slug
        cmd += ["--job-name", slug]

    cmd.append(str(script))

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(PROJECT))
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"sbatch failed: {result.stderr.strip()}")

    m = re.search(r"(\d+)", result.stdout)
    job_id = m.group(1) if m else "?"

    # Predict log path
    slug_or_type = req.slug or job_type
    log_glob = f"logs/{job_type}_{slug_or_type}_{job_id}.out"

    return {"job_id": job_id, "log_path": log_glob}


async def _submit_custom(query: str) -> dict:
    script_path = PROJECT / f"_custom_{int(time.time())}.sh"
    script_path.write_text(f"""#!/usr/bin/env bash
#SBATCH --job-name=custom-query
#SBATCH --output={LOGS_DIR}/custom_%j.out
#SBATCH --error={LOGS_DIR}/custom_%j.out
#SBATCH --time=03:00:00
#SBATCH --mem=16G
#SBATCH --cpus-per-task=4
#SBATCH --partition=batch_gpu
#SBATCH --qos=3h
#SBATCH --gres=gpu:1

set -euo pipefail
cd {PROJECT}

source /apps/rocs/2024.04/common/x86-64-v4/software/Micromamba/2.0.7-0/etc/profile.d/mamba.sh
micromamba activate /gpfs/scratchfs01/site/u/sobhn/conda/envs/drug-discovery

JOB_ID="${{SLURM_JOB_ID:-0}}"
ONA_PORT=$(( 8095 + JOB_ID % 900 ))
$HOME/.local/bin/ona-claude -p "${{ONA_PORT}}" &
ONA_PID=$!
for i in $(seq 1 30); do
    ss -tlnp 2>/dev/null | grep -q ":${{ONA_PORT}} " && break
    sleep 2
done
export ANTHROPIC_BASE_URL="http://127.0.0.1:${{ONA_PORT}}"
export ANTHROPIC_AUTH_TOKEN=ona-proxy
export AGENT_MAX_TURNS=30
export AUDIT_SUMMARY=1

USE_GPU=1 CLAWAPI_URL="http://127.0.0.1:$(( 8083 + JOB_ID % 900 ))" \\
bash {PROJECT}/run_singularity.sh python3 run_agent.py {json.dumps(query)}

kill $ONA_PID 2>/dev/null || true
""")
    result = subprocess.run(
        ["sbatch", "--job-name", "custom", str(script_path)],
        capture_output=True, text=True, cwd=str(PROJECT)
    )
    script_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"sbatch failed: {result.stderr.strip()}")
    m = re.search(r"(\d+)", result.stdout)
    job_id = m.group(1) if m else "?"
    return {"job_id": job_id, "log_path": f"logs/custom_{job_id}.out"}


# ── WebSocket log tail ────────────────────────────────────────────────────────

@app.websocket("/ws/log/{job_id}")
async def stream_log(websocket: WebSocket, job_id: str):
    if not re.fullmatch(r"\d+", job_id):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # Wait up to 10 min for the log file to appear, with periodic status messages
    log_path: Optional[Path] = None
    for i in range(1200):
        candidates = list(LOGS_DIR.glob(f"*_{job_id}.out"))
        if candidates:
            log_path = candidates[0]
            break
        if i > 0 and i % 60 == 0:
            waited = i // 2
            await websocket.send_text(f"[ui] Waiting for job to start… ({waited}s elapsed, job may be queued)")
        await asyncio.sleep(0.5)

    if log_path is None:
        await websocket.send_text("[ui] Job did not start within 10 minutes — check squeue")
        await websocket.close()
        return

    try:
        with open(log_path, "r", errors="replace") as f:
            # Send existing content first
            existing = f.read()
            if existing:
                for line in existing.splitlines():
                    await websocket.send_text(line)

            # Tail new lines
            while True:
                line = f.readline()
                if line:
                    await websocket.send_text(line.rstrip())
                    if "[result]" in line:
                        break
                else:
                    await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ── Direct chat (no SLURM) ────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat(websocket: WebSocket):
    await websocket.accept()
    try:
        question = await websocket.receive_text()
        question = question.strip()
        if not question:
            await websocket.close()
            return

        real_home = Path(os.environ.get("HOME", "/root"))
        claude_bin = real_home / ".local/bin/claude"
        if not claude_bin.exists():
            await websocket.send_text("__ERROR__ claude binary not found at ~/.local/bin/claude")
            await websocket.close()
            return

        # Build a temp HOME with a cleaned settings.json so claude -p uses our
        # UI_ONA_PORT instead of whatever the last SLURM job wrote to settings.json.
        import shutil, tempfile
        tmp_home = Path(tempfile.mkdtemp(prefix="claude_chat_"))
        try:
            (tmp_home / ".claude").mkdir()
            try:
                settings = json.loads((real_home / ".claude/settings.json").read_text())
            except Exception:
                settings = {}
            settings.setdefault("env", {}).pop("ANTHROPIC_BASE_URL", None)
            (tmp_home / ".claude/settings.json").write_text(json.dumps(settings))
            for d in (".ssh", ".local"):
                src = real_home / d
                if src.exists():
                    (tmp_home / d).symlink_to(src)

            prompt = f"{CHAT_SYSTEM}\n\nUser: {question}\n\nAssistant:"
            env = os.environ.copy()
            env["HOME"] = str(tmp_home)
            env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{UI_ONA_PORT}"
            env["ANTHROPIC_AUTH_TOKEN"] = "ona-proxy"

            proc = await asyncio.create_subprocess_exec(
                str(claude_bin), "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )

            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break
                await websocket.send_text(chunk.decode("utf-8", errors="replace"))

            await proc.wait()
        finally:
            shutil.rmtree(tmp_home, ignore_errors=True)

    except WebSocketDisconnect:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
