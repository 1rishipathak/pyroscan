"""the dashboard and its api. run `python -m uvicorn api.main:app --port 8000` and open http://localhost:8000."""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pyroscan import config, tinyfish_client as tf  # noqa: E402

WEB = ROOT / "web"
LIVE = config.PROCESSED / "live.json"
_lock = threading.Lock()
_last_refresh = {"t": 0.0}

app = FastAPI(title="PyroScan", description="counting the farm fires that satellites miss")


def _read(p: Path):
    if not p.exists():
        raise HTTPException(404, f"{p.name} not built yet")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {"ok": True, "tinyfish_key": tf.available(), "seasons": sorted(config.SEASONS)}


@app.get("/api/live")
def live():
    data = _read(LIVE) if LIVE.exists() else {}
    data["tinyfish_key"] = tf.available()
    return data


@app.post("/api/live/refresh")
def live_refresh():
    """today's fire bulletin, delhi's aqi and the news, fetched through tinyfish."""
    if not tf.available():
        raise HTTPException(400, "no TINYFISH_API_KEY in .env, see readme")
    with _lock:
        if time.time() - _last_refresh["t"] < 60 and LIVE.exists():
            return JSONResponse(_read(LIVE), headers={"x-pyroscan-cache": "recent"})
        import importlib.util

        spec = importlib.util.spec_from_file_location("sources_step", ROOT / "scripts" / "07_sources.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = mod.live()
        LIVE.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        _last_refresh["t"] = time.time()
    data["tinyfish_key"] = True
    return data


@app.get("/api/tinyfish/log")
def tinyfish_log(n: int = 40):
    if not tf.LOG.exists():
        return []
    with tf.LOG.open(encoding="utf-8") as f:
        rows = deque(f, maxlen=n)
    return [json.loads(r) for r in reversed(rows)]


@app.get("/api/news/{season}")
def season_news(season: str):
    return _read(config.PROCESSED / f"news_{season}.json")


@app.get("/api/official/{season}")
def official(season: str):
    d = _read(config.PROCESSED / f"official_{season}.json")
    d.pop("punjab_events", None)
    return d


_rasters: dict = {}


def _season_rasters(season: str):
    """the season's burn map, loaded once for point lookups."""
    if season not in _rasters:
        import numpy as np
        import rasterio

        d = WEB / "data" / season / "rasters"
        if not (d / "burn_idx.tif").exists():
            raise HTTPException(404, f"no burn map for {season}")
        with rasterio.open(d / "burn_idx.tif") as s:
            bidx, tf_, crs = s.read(1), s.transform, s.crs
        with rasterio.open(d / "burn_pre_idx.tif") as s:
            pidx = s.read(1)
        with rasterio.open(d / "seen.tif") as s:
            seen = s.read(1)
        with rasterio.open(d / "clear_looks.tif") as s:
            looks = s.read(1)
        scenes = json.loads((d / "scenes.json").read_text(encoding="utf-8"))
        _rasters[season] = dict(bidx=bidx, pidx=pidx, seen=seen, looks=looks, tf=tf_, crs=crs, scenes=scenes, np=np)
    return _rasters[season]


@app.get("/api/check")
def check(lat: float, lon: float, season: str = "2025"):
    """did the field at (lat, lon) burn this season, and when?"""
    from pyproj import Transformer

    r = _season_rasters(season)
    x, y = Transformer.from_crs("EPSG:4326", r["crs"], always_xy=True).transform(lon, lat)
    col, row = ~r["tf"] @ (x, y)
    row, col = int(row), int(col)
    h, w = r["bidx"].shape
    if not (0 <= row < h and 0 <= col < w):
        return {"inside": False}
    # check the 3x3 neighbourhood so a click on a field's edge still counts
    win = (slice(max(row - 1, 0), row + 2), slice(max(col - 1, 0), col + 2))
    np = r["np"]
    b = r["bidx"][win]
    looks = int(r["looks"][row, col])
    if not (b > 0).any():
        return {"inside": True, "burnt": False, "clear_looks": looks,
                "note": f"no burn seen in {looks} clear sentinel-2 looks this season"}
    k = int(np.bincount(b[b > 0]).argmax())
    m = b == k
    pre = int(np.max(r["pidx"][win][m]))
    sc = r["scenes"]
    return {"inside": True, "burnt": True, "clear_looks": looks,
            "burnt_after": sc[pre - 1]["datetime"][:10] if pre else None,
            "burnt_by": sc[k - 1]["datetime"][:10],
            "detected_by_official_satellites": bool((r["seen"][win][m] == 2).any())}


@app.get("/api/export/{season}/unseen.csv")
def export_unseen(season: str):
    """every burnt field the official count missed, as csv."""
    p = WEB / "data" / season / "unseen_scars.csv"
    if not p.exists():
        raise HTTPException(404, "not built")
    return FileResponse(p, media_type="text/csv", filename=f"pyroscan_undetected_burns_{season}.csv")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
