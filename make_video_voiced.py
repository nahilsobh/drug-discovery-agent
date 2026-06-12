#!/usr/bin/env python3
"""
Generate Whitespace_Demo_5min_voiced.mp4
- Chrome headless screenshots each slide
- edge_tts (Microsoft Neural) narrates each slide's speaker notes
- ffmpeg stitches video + audio, each slide held for its narration duration
"""

import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).parent
SRC_HTML = PROJECT / "Whitespace_Demo_5min.html"
OUTPUT   = PROJECT / "Whitespace_Demo_5min_voiced.mp4"

WIDTH, HEIGHT = 1920, 1080
VOICE = "en-US-GuyNeural"   # professional, clear male voice
PAUSE_AFTER = 1.5            # seconds of silence after each narration

# Speaker notes per slide (in order)
NARRATIONS = [
    # Slide 1 — Cover
    "Welcome. I'm going to show you Whitespace — the Roche AI Factory Strategic Discovery Agent. "
    "This is a 34-tool autonomous agent that turns a CEO-level strategic question into a branded "
    "Roche PDF report in 30 to 45 minutes, with zero human steps in between.",

    # Slide 2 — Problem
    "The problem: drug discovery strategy requires synthesizing data from a dozen fragmented sources — "
    "clinical trials, competitor pipelines, patents, adverse events, literature. "
    "Today this takes weeks of analyst time per competitor. "
    "By the time the PowerPoint lands, the portfolio decision is already made. "
    "CEOs have no way to ask a live strategic question and get a same-day answer. "
    "This is a classic agent-shaped problem.",

    # Slide 3 — Solution
    "The solution is Whitespace. Ask a strategic question. Get a branded CEO-ready PDF in 30 minutes. "
    "Zero human steps. The agent runs a Claude Opus reasoning loop, autonomously deciding which of "
    "34 tools to call across up to 45 turns. It queries live sources including ClinicalTrials.gov, "
    "Open Targets, ChEMBL, USPTO, and FDA FAERS. For drug candidates, it runs GPU-accelerated "
    "ADMET prediction, protein folding, and docking on an NVIDIA A100. "
    "The Whitespace web UI gives CEOs and VPs a browser interface — no SSH, no command line.",

    # Slide 4 — Agent Workflow
    "Here's how the agent works — using the AstraZeneca competitive intelligence run as an example. "
    "The agent called 11 tools in 30 reasoning turns, entirely autonomously. "
    "It started by recalling prior intelligence from its cross-session memory. "
    "Then ranked our portfolio, found gaps where AZ is ahead, pulled live trial signals, "
    "scanned literature and patents, identified repurposing candidates, enforced the mandatory "
    "ADMET gate, scored orphan eligibility, saved findings to memory, and generated the PDF. "
    "No human told it to do any of this.",

    # Slide 5 — Results
    "In a single 3-hour autonomous sweep, the agent generated 17 branded strategic intelligence PDFs — "
    "a full competitive sweep against 8 companies and a full drug repurposing sweep. "
    "Here's a sample finding from the AstraZeneca repurposing run. "
    "The agent identified Capivasertib for Proteus Syndrome as the number one highest-ROI opportunity. "
    "Composite score of 0.94 — AKT1 E17K genotype match, TIER-1 ADMET cleared, "
    "95% competitive vacuum, orphan-eligible for 7-year exclusivity. "
    "IND filing feasible within 18 months. "
    "The agent recommended initiating a pre-IND meeting with FDA on the Breakthrough Therapy pathway.",

    # Slide 6 — Agent Impact
    "This is not a chatbot with tool access. It is an autonomous pharmaceutical research analyst. "
    "The agent decides what to call, detects stalls and retries with corrected format, "
    "enforces ADMET as a mandatory quality gate even when not explicitly prompted, "
    "and saves findings to cross-session memory that compounds with every run. "
    "And notably — the entire codebase: 34 tools, proxy server, SLURM scripts, web UI, and 288 tests "
    "— was built using Claude Code as an agentic coding assistant. "
    "Agents accelerated our development by an estimated 10 times.",

    # Slide 7 — Next Steps
    "Weeks of analyst work, now 30 minutes, fully autonomous. "
    "Next steps: AWS deployment to remove the cluster dependency and open access to all colleagues. "
    "Integration with the Roche internal pipeline database for live asset updates. "
    "Expanding to a 34-company sweep on demand. "
    "Connecting to Agenecy to make these tools reusable across all of Roche R&D. "
    "And a regulatory filing assistant that maps each pipeline gap to the fastest approval pathway.",

    # Slide 8 — Closing
    "Whitespace. From question to CEO-ready strategic intelligence in 30 minutes. "
    "34 tools. 17 reports. 8 competitors. Zero human steps. Thank you.",
]

N = len(NARRATIONS)


# ── 1. Extract CSS + deck HTML ────────────────────────────────────────────────

src = SRC_HTML.read_text()
css_m = re.search(r"<style>(.*?)</style>", src, re.DOTALL)
css = css_m.group(1)
deck_m = re.search(r'(<div id="deck">.*?</div>)<!-- /deck -->', src, re.DOTALL)
deck_html = deck_m.group(1) + "</div>"


# ── 2. Generate narration audio ────────────────────────────────────────────────

async def gen_audio(text: str, out: Path):
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate="+5%")
    await comm.save(str(out))

tmpdir = Path(tempfile.mkdtemp(prefix="whitespace_voiced_"))
print(f"[make_video] Working dir: {tmpdir}")

print("[make_video] Generating narration audio ...")
for i, narr in enumerate(NARRATIONS):
    mp3 = tmpdir / f"narr_{i}.mp3"
    asyncio.run(gen_audio(narr, mp3))
    print(f"  [✓] Narration {i + 1}/{N}")


# ── 3. Measure audio durations via ffprobe ────────────────────────────────────

def audio_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())

durations = []
for i in range(N):
    d = audio_duration(tmpdir / f"narr_{i}.mp3") + PAUSE_AFTER
    durations.append(d)
    print(f"  Slide {i + 1}: {d:.1f}s")

total_secs = sum(durations)
print(f"[make_video] Total video length: {total_secs:.0f}s ({total_secs/60:.1f} min)")


# ── 4. Pad each audio to its slide duration (silence after narration) ─────────

for i, dur in enumerate(durations):
    src_mp3 = tmpdir / f"narr_{i}.mp3"
    padded   = tmpdir / f"audio_{i}.wav"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_mp3),
        "-af", f"apad=pad_dur={PAUSE_AFTER}",
        "-t", str(dur),
        str(padded),
    ], capture_output=True, check=True)

# Concatenate all audio into one track
audio_list = tmpdir / "audio_list.txt"
audio_list.write_text(
    "\n".join(f"file '{tmpdir / f'audio_{i}.wav'}'" for i in range(N)) + "\n"
)
full_audio = tmpdir / "full_audio.wav"
subprocess.run([
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0",
    "-i", str(audio_list),
    str(full_audio),
], capture_output=True, check=True)
print("[make_video] Audio track assembled.")


# ── 5. Screenshot each slide with Chrome headless ─────────────────────────────

chrome_args = [
    "google-chrome",
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-software-rasterizer",
    f"--window-size={WIDTH},{HEIGHT}",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
]

print("[make_video] Screenshotting slides ...")
for i in range(N):
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
{css}
.slide {{ display: none !important; opacity: 0 !important; }}
.slide:nth-child({i + 1}) {{
    display: flex !important; opacity: 1 !important;
    position: static !important; height: 100vh; width: 100vw;
}}
#controls, #notes {{ display: none !important; }}
body {{ height: 100vh; width: 100vw; overflow: hidden; background: #000; }}
#deck {{ height: 100vh; width: 100vw; position: relative; }}
</style></head>
<body>{deck_html}</body>
</html>"""
    html_file = tmpdir / f"slide_{i}.html"
    html_file.write_text(html)
    png = tmpdir / f"slide_{i}.png"
    r = subprocess.run(chrome_args + [f"--screenshot={png}", html_file.as_uri()],
                       capture_output=True, timeout=30)
    if r.returncode != 0 or not png.exists():
        sys.exit(f"Chrome failed on slide {i}: {r.stderr.decode()[:200]}")
    print(f"  [✓] Slide {i + 1}/{N}")


# ── 6. Build video from slides with per-slide durations ───────────────────────

concat_txt = tmpdir / "slides.txt"
lines = []
for i, dur in enumerate(durations):
    lines.append(f"file '{tmpdir / f'slide_{i}.png'}'")
    lines.append(f"duration {dur:.3f}")
lines.append(f"file '{tmpdir / f'slide_{N-1}.png'}'")
concat_txt.write_text("\n".join(lines) + "\n")

silent_video = tmpdir / "silent.mp4"
print("[make_video] Encoding silent video ...")
subprocess.run([
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0", "-i", str(concat_txt),
    "-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos,fps=24",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "18",
    str(silent_video),
], capture_output=True, check=True)


# ── 7. Mux video + audio ──────────────────────────────────────────────────────

print(f"[make_video] Muxing audio → {OUTPUT.name} ...")
r = subprocess.run([
    "ffmpeg", "-y",
    "-i", str(silent_video),
    "-i", str(full_audio),
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "192k",
    "-shortest",
    str(OUTPUT),
], capture_output=True, text=True)
if r.returncode != 0:
    print(r.stderr[-1000:])
    sys.exit("ffmpeg mux failed")

size_mb = OUTPUT.stat().st_size / 1024 / 1024
print(f"[make_video] Done → {OUTPUT}  ({size_mb:.1f} MB, {total_secs:.0f}s)")

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print("[make_video] Temporary files removed.")
