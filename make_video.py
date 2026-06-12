#!/usr/bin/env python3
"""
Generate Whitespace_Demo_5min.mp4
Uses Chrome headless to screenshot each slide, then ffmpeg to stitch into a 5-min video.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).parent
SRC_HTML = PROJECT / "Whitespace_Demo_5min.html"
OUTPUT   = PROJECT / "Whitespace_Demo_5min.mp4"

SLIDE_DURATION = 37.5   # seconds per slide × 8 = 5 min
WIDTH, HEIGHT  = 1920, 1080

# ── 1. Extract CSS and slide HTML from the source ─────────────────────────────

src = SRC_HTML.read_text()

css_m = re.search(r"<style>(.*?)</style>", src, re.DOTALL)
if not css_m:
    sys.exit("Could not find <style> block in source HTML")
css = css_m.group(1)

deck_m = re.search(r'(<div id="deck">.*?</div>)<!-- /deck -->', src, re.DOTALL)
if not deck_m:
    sys.exit("Could not find #deck block in source HTML")
deck_html = deck_m.group(1) + "</div>"

# ── 2. Count slides ────────────────────────────────────────────────────────────

n_slides = len(re.findall(r'class="slide ', deck_html))
print(f"[make_video] Found {n_slides} slides")

# ── 3. Screenshot each slide with Chrome headless ─────────────────────────────

tmpdir = Path(tempfile.mkdtemp(prefix="whitespace_slides_"))
print(f"[make_video] Working dir: {tmpdir}")

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

for i in range(n_slides):
    slide_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
{css}
/* Show only slide {i} */
.slide {{ display: none !important; opacity: 0 !important; }}
.slide:nth-child({i + 1}) {{
    display: flex !important;
    opacity: 1 !important;
    position: static !important;
    height: 100vh;
    width: 100vw;
}}
#controls, #notes {{ display: none !important; }}
body {{
    height: 100vh;
    width: 100vw;
    overflow: hidden;
    background: #000;
}}
#deck {{
    height: 100vh;
    width: 100vw;
    position: relative;
}}
</style>
</head>
<body>
{deck_html}
</body>
</html>"""

    slide_file = tmpdir / f"slide_{i}.html"
    slide_file.write_text(slide_html)
    png_out = tmpdir / f"slide_{i}.png"

    cmd = chrome_args + [
        f"--screenshot={png_out}",
        slide_file.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not png_out.exists():
        print(f"  [!] Chrome failed on slide {i}: {result.stderr.decode()[:200]}")
        sys.exit(1)
    print(f"  [✓] Slide {i + 1}/{n_slides} → {png_out.name}")

# ── 4. Build ffmpeg concat file ───────────────────────────────────────────────

concat_file = tmpdir / "slides.txt"
lines = []
for i in range(n_slides):
    lines.append(f"file '{tmpdir / f'slide_{i}.png'}'")
    lines.append(f"duration {SLIDE_DURATION}")
# ffmpeg concat needs last file repeated without duration
lines.append(f"file '{tmpdir / f'slide_{n_slides - 1}.png'}'")
concat_file.write_text("\n".join(lines) + "\n")

# ── 5. Encode MP4 ─────────────────────────────────────────────────────────────

print(f"[make_video] Encoding {OUTPUT.name} ...")

ffmpeg_cmd = [
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0",
    "-i", str(concat_file),
    "-vf", f"scale={WIDTH}:{HEIGHT}:flags=lanczos,fps=24",
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-preset", "slow",
    "-crf", "18",
    str(OUTPUT),
]

result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
if result.returncode != 0:
    print(result.stderr[-1000:])
    sys.exit("ffmpeg failed")

size_mb = OUTPUT.stat().st_size / 1024 / 1024
print(f"[make_video] Done → {OUTPUT}  ({size_mb:.1f} MB)")

# ── 6. Cleanup ────────────────────────────────────────────────────────────────

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print("[make_video] Temporary files removed.")
