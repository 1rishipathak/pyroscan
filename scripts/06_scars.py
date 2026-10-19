"""step 6: burn map -> individual burn scars, each checked against the official satellites.

a scar is "seen" if at least one firms detection from the satellites the official count
uses (viirs s-npp, viirs noaa-20, modis terra/aqua) falls close enough to it while it was
burning. close enough = inside that detection's own pixel footprint (firms gives the
footprint size per point as scan x track) plus 150 m for geolocation error. this is
generous on purpose, so the share we call unseen is a floor, not a ceiling.

outputs (data/processed/<season>/): scars.parquet, stats.json, firms_matched.parquet

run: .venv/Scripts/python.exe scripts/06_scars.py --season 2025 [--source rule]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.measure import label

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, firms  # noqa: E402

MIN_PX = 10  # 0.4 ha = one killa (acre), the usual punjab field unit. smaller blobs were mostly doubtful in the qa check
PX_HA = config.RES**2 / 1e4
GEO_ERR_M = 150
T_BEFORE = pd.Timedelta(hours=12)  # a fire can start a little before the 'before' look
# a field already char at the 11:15 sentinel-2 pass can still be burning at the ~13:30 viirs/aqua
# pass the same day. that fire *was* seen by the official satellites, so allow the same afternoon.
# (scripts/diagnose_misses.py: 38% of the 'detected after the window' misses were within 3 h.)
T_AFTER = pd.Timedelta(hours=6)


def load_detections(season: str, detections: str) -> pd.DataFrame:
    """fire points with lon/lat, utc time, source name and a match radius (m)."""
    if detections == "official":
        fp = pd.read_parquet(config.INTERIM / f"official_events_{season}.parquet")
        fp = fp.rename(columns={"lat": "latitude", "lon": "longitude"})
        return fp
    fp = pd.read_parquet(firms.cache_path(int(season)))
    fp["radius_m"] = 0.5 * np.hypot(fp.scan.fillna(0.4), fp.track.fillna(0.4)) * 1000 + GEO_ERR_M
    return fp


def main(season: str, source: str, detections: str = "firms", out_dir: str | None = None,
         shift_days: float = 0.0) -> None:
    out = Path(out_dir) if out_dir else config.PROCESSED / season
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    tf = rasterio.Affine(*aoi["transform"])
    prefix = "burn" if source == "unet" else "rule"
    with rasterio.open(out / f"{prefix}_idx.tif") as src:
        bidx = src.read(1)
    with rasterio.open(out / f"{prefix}_pre_idx.tif") as src:
        pidx = src.read(1)
    with rasterio.open(config.INTERIM / "districts.tif") as src:
        dist = src.read(1)
    with rasterio.open(config.INTERIM / "worldcover.tif") as src:
        crop = src.read(1) == 40
    scenes = json.loads((out / "scenes.json").read_text(encoding="utf-8"))
    times = pd.to_datetime([s["datetime"] for s in scenes], utc=True, format="ISO8601")
    dnames = {i + 1: n for i, n in enumerate(config.DISTRICTS)}

    # 1. scars = connected pixels detected on the same date
    lab = label(bidx, connectivity=2, background=0)
    n = lab.max()
    ids = np.arange(1, n + 1)
    size = ndimage.sum_labels(np.ones_like(lab, np.int32), lab, ids)
    keep = size >= MIN_PX
    lab[(lab > 0) & ~keep[lab - 1]] = 0  # drop tiny blobs, kept scars keep their ids
    ids = ids[keep]
    size = size[keep]
    k_post = ndimage.maximum(bidx, lab, ids).astype(int)
    k_pre = ndimage.minimum(np.where(pidx > 0, pidx, 255), lab, ids).astype(int)
    cy, cx = np.array(ndimage.center_of_mass(np.ones_like(lab, np.int8), lab, ids)).T
    d_id = dist[np.clip(cy.round().astype(int), 0, dist.shape[0] - 1), np.clip(cx.round().astype(int), 0, dist.shape[1] - 1)]
    xs, ys = rasterio.transform.xy(tf, cy, cx)
    lon, lat = Transformer.from_crs(config.CRS, "EPSG:4326", always_xy=True).transform(xs, ys)
    scars = pd.DataFrame(dict(
        scar_id=ids, area_ha=size * PX_HA, k_post=k_post, k_pre=k_pre,
        t_post=times[k_post - 1], t_pre=times[np.clip(k_pre - 1, 0, len(times) - 1)],
        district=[dnames.get(int(v), "outside") for v in d_id], lon=lon, lat=lat, row=cy, col=cx,
    ))
    print(f"{len(scars):,} scars >= {MIN_PX} px, {scars.area_ha.sum():,.0f} ha")

    # 2. fire detections inside the window, with their own footprint radius
    fp = load_detections(season, detections)
    if shift_days:  # null test: break the time link, keep the places. what still matches is chance
        fp["dt_utc"] = fp.dt_utc + pd.Timedelta(days=shift_days)
    tr = Transformer.from_crs("EPSG:4326", config.CRS, always_xy=True)
    fx, fy = tr.transform(fp.longitude.values, fp.latitude.values)
    fr, fc = rasterio.transform.rowcol(tf, fx, fy)
    fr, fc = np.asarray(fr), np.asarray(fc)
    h, w = bidx.shape
    inwin = (fr >= 0) & (fr < h) & (fc >= 0) & (fc < w)
    fp = fp[inwin].copy()
    fp["x"], fp["y"], fp["r"], fp["c"] = fx[inwin], fy[inwin], fr[inwin], fc[inwin]
    fp = fp[(fp.dt_utc > times[0] - T_BEFORE) & (fp.dt_utc <= times[-1] + T_AFTER)]
    fp["district"] = [dnames.get(int(v), "outside") for v in dist[fp.r.values, fp.c.values]]
    fp["on_cropland"] = crop[fp.r.values, fp.c.values]
    print(f"{len(fp):,} firms detections in window and season")

    # 3. match: every scar pixel in a tree, every firms point asks for pixels within its radius
    rr, cc = np.nonzero(lab)
    pix_lab = lab[rr, cc]
    px, py = rasterio.transform.xy(tf, rr, cc)
    tree = cKDTree(np.column_stack([px, py]))
    pos = {sid: i for i, sid in enumerate(scars.scar_id.values)}
    t_pre = scars.t_pre.values
    t_post = scars.t_post.values
    seen_by = [set() for _ in range(len(scars))]
    n_hits = np.zeros(len(scars), int)
    matched = np.zeros(len(fp), bool)
    for j, (x, y, rad, t, srcname) in enumerate(zip(fp.x.values, fp.y.values, fp.radius_m.values,
                                                       fp.dt_utc.values, fp.source.values)):
        hits = tree.query_ball_point([x, y], rad)
        if not hits:
            continue
        for sid in np.unique(pix_lab[hits]):
            i = pos[sid]
            if t_pre[i] - T_BEFORE.to_timedelta64() < t <= t_post[i] + T_AFTER.to_timedelta64():
                seen_by[i].add(srcname)
                n_hits[i] += 1
                matched[j] = True
    scars["n_detections"] = n_hits
    scars["seen"] = n_hits > 0
    scars["seen_by"] = [",".join(sorted(s)) for s in seen_by]
    scars["window_days"] = (scars.t_post - scars.t_pre).dt.total_seconds() / 86400
    fp["matched_scar"] = matched

    # 4. stats
    def summarise(s: pd.DataFrame, f: pd.DataFrame) -> dict:
        burnt = s.area_ha.sum()
        unseen = s.loc[~s.seen, "area_ha"].sum()
        fcrop = f[f.on_cropland]
        return dict(
            burnt_ha=round(burnt), seen_ha=round(burnt - unseen), unseen_ha=round(unseen),
            unseen_share=round(unseen / burnt, 4) if burnt else None,
            scars=int(len(s)), unseen_scars=int((~s.seen).sum()),
            median_scar_ha=round(float(s.area_ha.median()), 2) if len(s) else None,
            firms_detections=int(len(f)), firms_on_cropland=int(len(fcrop)),
            firms_matched_share=round(float(fcrop.matched_scar.mean()), 4) if len(fcrop) else None,
        )

    stats = dict(season=season, source=source, detections=detections, min_scar_ha=MIN_PX * PX_HA, geo_err_m=GEO_ERR_M,
                 window=summarise(scars, fp), districts={})
    for d in config.DISTRICTS:
        stats["districts"][d] = summarise(scars[scars.district == d], fp[fp.district == d])
    sel = scars[scars.district.isin(config.DISTRICTS)]
    stats["study_area"] = summarise(sel, fp[fp.district.isin(config.DISTRICTS)])
    by_date = sel.groupby(sel.t_post.dt.strftime("%Y-%m-%d")).apply(
        lambda g: pd.Series(dict(burnt_ha=g.area_ha.sum(), unseen_ha=g.loc[~g.seen, "area_ha"].sum(),
                                 scars=len(g))), include_groups=False)
    stats["by_scene_date"] = by_date.round(1).reset_index(names="date").to_dict(orient="records")
    print(json.dumps({k: stats[k] for k in ["study_area", "districts"]}, indent=1))

    suffix = ("" if source == "unet" else "_rule") + ("" if detections == "firms" else "_official")
    scars.to_parquet(out / f"scars{suffix}.parquet")
    fp.drop(columns=["r", "c"]).to_parquet(out / f"firms_matched{suffix}.parquet")
    (out / f"stats{suffix}.json").write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")
    np.save(out / f"scar_labels{suffix}.npy", lab.astype(np.int32))
    seen_ids = scars.loc[scars.seen, "scar_id"].values
    seen_mask = np.isin(lab, seen_ids)
    with rasterio.open(out / f"{prefix}_idx.tif") as src:
        prof = src.profile
    with rasterio.open(out / f"seen{suffix}.tif", "w", **prof) as dst:
        dst.write((np.where(lab > 0, 1, 0) + seen_mask).astype(np.uint8), 1)  # 0 none, 1 unseen, 2 seen


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--source", default="unet", choices=["unet", "rule"])
    ap.add_argument("--detections", default="firms", choices=["firms", "official"])
    ap.add_argument("--dir", default=None, help="folder with burn_idx.tif etc. (default data/processed/<season>)")
    ap.add_argument("--shift-days", type=float, default=0.0, help="null test: shift every detection in time")
    a = ap.parse_args()
    main(a.season, a.source, a.detections, a.dir, a.shift_days)
