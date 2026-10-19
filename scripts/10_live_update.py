"""refresh the live season end to end: new sentinel-2 passes, new official bulletins, new burn map,
and isro's insat-3dr fire pixels (when MOSDAC_CREDS_FILE is set, in the shell or in .env).

uses the u-net trained on 2025 (no retraining). run it before a demo:
  .venv/Scripts/python.exe scripts/10_live_update.py
"""
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
PY = sys.executable
SEASON = "2026"
# isro's insat-3dr fire product needs a mosdac login; skipped (with a note) when no credentials are set
INSAT = [["12_insat_fires.py", "download", "--plan", f"{SEASON}:30"], ["12_insat_fires.py", "extract", "--season", SEASON]]

STEPS = [
    ["01_prepare_aoi.py", "--season", SEASON],  # scene list up to today
    ["02_download_s2.py", "--season", SEASON],  # only new passes are downloaded
    ["05_infer.py", "--season", SEASON, "--model", "2025"],
    ["07b_official_events.py", "--season", SEASON],  # every official event so far, via tinyfish fetch
    ["07_sources.py", "--season", SEASON, "--live", "--agent", "6"],  # official totals, aqi, news (+ agent), today's feed
    ["06_scars.py", "--season", SEASON, "--detections", "official"],
    ["06_scars.py", "--season", SEASON, "--source", "rule", "--detections", "official"],
    ["08_web_assets.py", "--season", SEASON, "--detections", "official", "--chips", "40"],
    ["08b_hotspots.py", "--season", SEASON, "--detections", "official"],
]
if os.environ.get("MOSDAC_CREDS_FILE"):
    STEPS += INSAT
else:
    print("MOSDAC_CREDS_FILE not set: skipping the insat-3dr fire product")

for step in STEPS:
    print(">>", " ".join(step), flush=True)
    r = subprocess.run([PY, "-u", str(ROOT / "scripts" / step[0]), *step[1:]], cwd=ROOT)
    if r.returncode:
        sys.exit(f"step failed: {step[0]}")
print("live season updated. reload the dashboard with ?season=2026")
