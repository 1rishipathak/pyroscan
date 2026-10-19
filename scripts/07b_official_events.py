"""step 7b: the official fire count as a list of points.

every daily creams bulletin lists each punjab fire event it counted: satellite, gps, time.
read all bulletins of a season (through tinyfish fetch) and stack them into one table.
06_scars.py --detections official then matches burn scars against the official list itself.

run: .venv/Scripts/python.exe scripts/07b_official_events.py --season 2025
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, sources  # noqa: E402

# pixel size of each sensor at nadir, metres (the bulletins don't give scan/track)
PIXEL_M = {"S-NPP": 375, "NOAA-20": 375, "NOAA20": 375, "NOAA-21": 375, "TERRA": 1000, "AQUA": 1000, "MODIS": 1000}


def one(b: dict) -> tuple[dict, list[dict]]:
    try:
        text, via = sources.page_text(b["url"])
        p = sources.parse_creams(text)
    except Exception as e:
        return dict(date=b["date"], error=repr(e)[:200]), []
    ev = p.get("punjab_events", [])
    for e in ev:
        e["date"] = p.get("date") or b["date"]
    return dict(date=p.get("date") or b["date"], url=b["url"], via=via, day_punjab=p.get("day_punjab"),
                listed=len(ev)), ev


def main(season: str) -> None:
    y = int(season)
    bl = [b for b in sources.creams_bulletins() if b["year"] == y]
    print(f"{len(bl)} creams bulletins for {y}")
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(one, bl))
    meta = pd.DataFrame([r[0] for r in res]).sort_values("date")
    ev = pd.DataFrame([e for r in res for e in r[1]])
    if ev.empty:
        raise SystemExit("no events parsed")
    ev["satellite"] = ev.satellite.str.upper().str.replace("NOAA20", "NOAA-20")
    t = ev.time_ist.str.slice(0, 5)
    ev["dt_utc"] = pd.to_datetime(ev.date + " " + t).dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC")
    # night passes after midnight are listed under the bulletin day; keep as given
    ev["radius_m"] = ev.satellite.map(PIXEL_M).fillna(375) * 0.5 * 2**0.5 + 150
    ev["source"] = "official: " + ev.satellite
    out = config.INTERIM / f"official_events_{y}.parquet"
    ev.to_parquet(out)
    meta.to_csv(config.INTERIM / f"official_bulletins_{y}.csv", index=False)
    ok = (meta.listed == meta.day_punjab).mean() if "listed" in meta else float("nan")
    print(f"{len(ev):,} official punjab events; listed count matches the headline count on {ok:.0%} of days")
    print(meta.tail(5).to_string(index=False))
    print("via:", meta.via.value_counts().to_dict() if "via" in meta else {})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    main(ap.parse_args().season)
