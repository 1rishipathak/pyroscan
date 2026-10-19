"""step 12: isro's insat-3dr fire pixels over punjab, from mosdac.

  download   python scripts/12_insat_fires.py download --plan 2025:30 2021:60
             (season:minutes between scans read; needs a mosdac login)
  extract    python scripts/12_insat_fires.py extract --season 2025"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, geo, mosdac  # noqa: E402

DATASET = mosdac.FIRE_3DR
IST = pd.Timedelta(hours=5, minutes=30)
# no polar satellite passes over punjab between 16:00 ist and ~01:30
BLIND_FROM = 16


def raw_dir(season: str) -> Path:
    return config.RAW / "insat" / DATASET / season


def season_entries(season: str, every: int) -> list[dict]:
    """catalogue entries for a season's days, thinned to one scan every `every` minutes."""
    start, end = (date.fromisoformat(d) for d in config.SEASONS[season])
    found = mosdac.search(DATASET, (start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat())
    keep = []
    for e in found:
        t = pd.Timestamp(mosdac.granule_time(e["identifier"]))
        if not start <= (t + IST).date() <= end:
            continue
        if every == 60 and t.minute != 15:
            continue
        keep.append(e)
    return keep


def download(plan: list[str], max_files: int, workers: int) -> None:
    todo = []
    for item in plan:
        season, every = item.split(":")
        entries = season_entries(season, int(every))
        out = raw_dir(season)
        out.mkdir(parents=True, exist_ok=True)
        new = [e for e in entries if not (out / e["identifier"]).exists()]
        print(f"{season}: {len(entries)} scans every {every} min, {len(new)} still to fetch", flush=True)
        todo += [(e, out) for e in new]
    if len(todo) > max_files:
        print(f"capping this run at {max_files} files (mosdac allows {mosdac.DAILY_FILE_CAP} a day)")
        todo = todo[:max_files]
    if not todo:
        return
    t0, done, failed = time.time(), 0, []
    with mosdac.Session() as s, ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(s.download, e, out): e for e, out in todo}
        for f in as_completed(futures):
            try:
                f.result()
                done += 1
            except mosdac.QuotaExceeded as exc:
                print(exc, flush=True)
                for g in futures:
                    g.cancel()
                break
            except Exception as exc:  # one bad file should not end the run
                failed.append(f"{futures[f]['identifier']}: {exc}")
            if done % 200 == 0:
                print(f"  {done}/{len(todo)} files, {time.time() - t0:.0f} s", flush=True)
    print(f"downloaded {done} files in {time.time() - t0:.0f} s, {len(failed)} failed", flush=True)
    for line in failed[:10]:
        print("  ", line)


def extract(season: str) -> None:
    files = sorted(raw_dir(season).glob("*.kml"))
    if not files:
        raise SystemExit(f"no insat files for {season}; run the download step first")
    punjab = geo.punjab_shape()
    districts = gpd.read_file(config.PROCESSED / "districts.geojson")[["district", "geometry"]]
    minx, miny, maxx, maxy = punjab.bounds
    rows, scans = [], []
    for f in files:
        scan = pd.Timestamp(mosdac.granule_time(f.name))
        pts = mosdac.parse_fire_kml(f)
        near = [p for p in pts if minx <= p["lon"] <= maxx and miny <= p["lat"] <= maxy]
        inside = [p for p in near if punjab.contains(Point(p["lon"], p["lat"]))]
        scans.append(dict(granule=f.name, t_utc=scan, n_all=len(pts), n_punjab=len(inside)))
        # each pixel carries the time its scan line passed; fall back to the scan start
        rows += [dict(granule=f.name, scan_utc=scan, t_utc=pd.Timestamp(p["t"]) if p["t"] else scan, lon=p["lon"],
                      lat=p["lat"], bt_tir1=p["bt_tir1"], bt_mir=p["bt_mir"]) for p in inside]
    scans = pd.DataFrame(scans)
    pts = pd.DataFrame(rows, columns=["granule", "scan_utc", "t_utc", "lon", "lat", "bt_tir1", "bt_mir"])
    pts["district"] = None
    if len(pts):
        g = gpd.GeoDataFrame(geometry=gpd.points_from_xy(pts.lon, pts.lat), index=pts.index, crs="EPSG:4326")
        hit = gpd.sjoin(g, districts, how="left", predicate="within")
        pts["district"] = hit[~hit.index.duplicated()]["district"].reindex(pts.index).values
    out = config.PROCESSED / season
    out.mkdir(parents=True, exist_ok=True)
    pts.to_csv(out / "insat_fires.csv", index=False)
    scans.to_csv(out / "insat_scans.csv", index=False)
    print(f"{season}: {len(scans)} scans, {int(scans.n_all.sum())} fire pixels in all, "
          f"{len(pts)} in punjab, {int(pts.district.notna().sum())} in the study area")
    write_web(season, scans, pts)


def ist(t: pd.Series) -> pd.Series:
    return pd.to_datetime(t, utc=True) + IST


def by_hour(df: pd.DataFrame) -> dict:
    """fire-pixel detections per ist hour, and how many came after the polar satellites' last pass.
    a fire that burns for an hour shows up in two scans, so these are detections, not fires."""
    h = ist(df.t_utc).dt.hour.to_numpy()
    n = np.bincount(h, minlength=24).astype(int)
    blind = int((h >= BLIND_FROM).sum())
    return dict(pixels=len(df), by_hour=n.tolist(), blind=blind,
                blind_share=round(blind / len(df), 4) if len(df) else None,
                peak_hour=int(n.argmax()) if len(df) else None)


def write_web(season: str, scans: pd.DataFrame, pts: pd.DataFrame) -> None:
    study = pts[pts.district.notna()]
    on_15 = lambda df: df[pd.to_datetime(df.scan_utc, utc=True).dt.minute == 15]  # noqa: E731 (one scan an hour)
    days = ist(pts.t_utc).dt.date.astype(str)
    every = int(round(pd.to_datetime(scans.t_utc, utc=True).sort_values().diff().dt.total_seconds().median() / 60))
    off_file = config.PROCESSED / f"official_{season}.json"  # creams, for scale: punjab's official events
    off = json.loads(off_file.read_text(encoding="utf-8")) if off_file.exists() else {}
    web = dict(
        punjab_official=off.get("season_punjab"), official_date=off.get("date"),
        dataset=DATASET, satellite="INSAT-3DR", source="MOSDAC, SAC/ISRO", every_min=every, blind_from=BLIND_FROM,
        scans=len(scans), first=str(ist(scans.t_utc).min())[:16], last=str(ist(scans.t_utc).max())[:16],
        scans_by_hour=np.bincount(ist(scans.t_utc).dt.hour, minlength=24).astype(int).tolist(),
        punjab=by_hour(pts), study=by_hour(study),
        # the same counts from one scan an hour, to compare with seasons fetched at that rate
        punjab_hourly=by_hour(on_15(pts)), study_hourly=by_hour(on_15(study)),
        by_district={d: int(n) for d, n in study.district.value_counts().items()},
        daily={d: dict(punjab=int((days == d).sum()), study=int((days[pts.district.notna()] == d).sum()))
               for d in sorted(days.unique())},
        # study-area pixels for the map: lon, lat, ist time, mir brightness temperature (k)
        rows=[[round(r.lon, 4), round(r.lat, 4), str(pd.Timestamp(r.t_utc) + IST)[:16],
               None if pd.isna(r.bt_mir) else round(r.bt_mir, 1)] for r in study.itertuples()],
    )
    dst = Path(config.ROOT) / "web" / "data" / season
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "insat.json").write_text(json.dumps(web), encoding="utf-8")
    for k in ("punjab", "study"):
        s = web[k]
        print(f"  {k}: {s['pixels']} fire-pixel detections, {s['blind']} ({(s['blind_share'] or 0):.0%}) from "
              f"{BLIND_FROM}:00 ist, peak hour {s['peak_hour']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--plan", nargs="+", default=["2025:30", "2026:30", "2021:60"])
    d.add_argument("--max-files", type=int, default=4600)
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--dry-run", action="store_true", help="search the catalogue only")
    x = sub.add_parser("extract")
    x.add_argument("--season", default="2025")
    a = ap.parse_args()
    if a.cmd == "download":
        if a.dry_run:
            for item in a.plan:
                s, every = item.split(":")
                e = season_entries(s, int(every))
                n_new = sum(not (raw_dir(s) / x["identifier"]).exists() for x in e)
                print(f"{s}: {len(e)} scans every {every} min ({n_new} not downloaded yet)")
        else:
            download(a.plan, a.max_files, a.workers)
    else:
        extract(a.season)
