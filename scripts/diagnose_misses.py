"""why do some satellite fire detections not land on a burn scar? each miss goes into the first
bucket that fits: timing (scar dated to a different window), too_small (under 0.4 ha), gated
(the u-net said burnt, the physics check said no), weak (u-net unsure), no_look (no clear
image within 10 days) or nothing (no char at the next clear look)."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, s2, timeline  # noqa: E402


def main(season: str, stem: str) -> None:
    p = config.PROCESSED / season
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    tf = rasterio.Affine(*aoi["transform"])
    bidx = rasterio.open(p / "burn_idx.tif").read(1)
    pidx = rasterio.open(p / "burn_pre_idx.tif").read(1)
    lab = np.load(p / "scar_labels.npy", mmap_mode="r")
    scenes = json.loads((p / "scenes.json").read_text(encoding="utf-8"))
    times = pd.to_datetime([s["datetime"] for s in scenes], utc=True, format="ISO8601")
    fp = pd.read_parquet(p / "firms_matched.parquet")
    fp = fp[fp.district.isin(config.DISTRICTS) & fp.on_cropland].copy()
    rows, cols = rasterio.transform.rowcol(tf, fp.x.values, fp.y.values)
    fp["r"], fp["c"] = np.asarray(rows), np.asarray(cols)
    miss = fp[~fp.matched_scar].copy()
    print(f"{len(fp)} detections on cropland in the study area, {len(miss)} missed ({len(miss) / len(fp):.0%})")

    # clear masks and probabilities per scene, only at the missed points' neighbourhoods
    files = timeline.scene_files(season)
    R = 25  # px (500 m) neighbourhood half-size
    clear_at = np.zeros((len(files), len(miss)), bool)
    pmax = np.zeros((len(files), len(miss)), np.float32)
    for k, (key, dt, path) in enumerate(files):
        refl, scl, _ = s2.load_scene(path)
        clr = timeline.clear_mask(scl, refl)
        clear_at[k] = clr[miss.r.values, miss.c.values]
        pf = config.INTERIM / "prob" / season / stem / f"{key}.tif"
        if not pf.exists():  # first look of the season: nothing to compare against
            continue
        pr = rasterio.open(pf).read(1)
        pm = ndimage.maximum_filter(pr, size=2 * 10 + 1)  # within 200 m
        pmax[k] = (pm[miss.r.values, miss.c.values].astype(np.float32) - 1) / 254

    buckets = []
    for j, (_, d) in enumerate(miss.iterrows()):
        r, c, t = d.r, d.c, d.dt_utc
        rad = int(np.ceil(d.radius_m / config.RES))
        win = (slice(max(r - rad, 0), r + rad + 1), slice(max(c - rad, 0), c + rad + 1))
        b = bidx[win]
        L = np.asarray(lab[win])
        k_det = int(np.searchsorted(times, t - pd.Timedelta(hours=12)))  # first look that could show it
        later = [k for k in range(k_det, len(files)) if clear_at[k, j] and (times[k] - t).days <= 10]
        if (L > 0).any():
            buckets.append("timing")
        elif ((b > 0) & (L == 0)).any():
            buckets.append("too_small")
        elif not later:
            buckets.append("no_look")
        else:
            k1 = later[0]
            pv = pmax[k1, j]
            buckets.append("gated" if pv > 0.5 else "weak" if pv > 0.2 else "nothing")
    miss["bucket"] = buckets
    out = miss.bucket.value_counts().rename_axis("bucket").reset_index(name="n")
    out["share_of_missed"] = (out.n / len(miss)).round(3)
    out["share_of_all"] = (out.n / len(fp)).round(3)
    print(out.to_string(index=False))
    # timing detail: how far off are the windows?
    tm = miss[miss.bucket == "timing"]
    if len(tm):
        hrs = []
        for _, d in tm.iterrows():
            rad = int(np.ceil(d.radius_m / config.RES))
            win = (slice(max(d.r - rad, 0), d.r + rad + 1), slice(max(d.c - rad, 0), d.c + rad + 1))
            ks = np.unique(bidx[win][np.asarray(lab[win]) > 0])
            # signed hours between detection and the nearest scar window edge
            best = min(((d.dt_utc - times[k - 1]).total_seconds() / 3600 for k in ks), key=abs)
            hrs.append(best)
        hrs = np.array(hrs)
        print(f"timing misses: detection was {np.median(hrs):+.1f} h from the scar's 'after' look (median); "
              f"{np.mean((hrs > 0) & (hrs < 12)):.0%} were 0-12 h after it")
    rep = config.ROOT / "reports"
    (rep / f"misses_{season}_{stem}.json").write_text(json.dumps(dict(
        detections=int(len(fp)), missed=int(len(miss)), buckets=out.to_dict(orient="records")), indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--ckpt-stem", default="unet_2025_v1")
    a = ap.parse_args()
    main(a.season, a.ckpt_stem)
