"""rebuilds the burn map at several u-net thresholds from saved probabilities and scores each, to
pick the threshold the model runs at."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, indices, timeline  # noqa: E402


def main(season: str, stem: str, thrs: list[float]) -> None:
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    h, w = aoi["shape"]
    tf = rasterio.Affine(*aoi["transform"])
    crop = rasterio.open(config.INTERIM / "worldcover.tif").read(1) == 40
    bidx = {t: np.zeros((h, w), np.uint8) for t in thrs}
    pidx = {t: np.zeros((h, w), np.uint8) for t in thrs}
    prob_dir = config.INTERIM / "prob" / season / stem
    for st in timeline.walk(season):
        f = prob_dir / f"{st.key}.tif"
        if not f.exists():
            continue
        q = rasterio.open(f).read(1)
        p = np.where(q > 0, (q.astype(np.float32) - 1) / 254, 0)
        ok = st.valid & crop & indices.plausible_burn(st.pre, st.post)
        for t in thrs:
            new = (p > t) & ok & (bidx[t] == 0)
            bidx[t][new] = st.i + 1
            pidx[t][new] = st.pre_idx[new] + 1
    src = config.PROCESSED / season
    prof = dict(driver="GTiff", height=h, width=w, count=1, crs=config.CRS, transform=tf, compress="deflate", dtype="uint8")
    rows = []
    for t in thrs:
        d = config.INTERIM / "sweep" / f"{stem}_t{t:.2f}"
        d.mkdir(parents=True, exist_ok=True)
        for name, arr in (("burn_idx", bidx[t]), ("burn_pre_idx", pidx[t])):
            with rasterio.open(d / f"{name}.tif", "w", **prof) as dst:
                dst.write(arr, 1)
        shutil.copy(src / "scenes.json", d / "scenes.json")
        subprocess.run([sys.executable, str(Path(__file__).with_name("06_scars.py")), "--season", season, "--dir", str(d)],
                       check=True, capture_output=True)
        sa = json.loads((d / "stats.json").read_text(encoding="utf-8"))["study_area"]
        rows.append(dict(thr=t, burnt_ha=sa["burnt_ha"], unseen_share=sa["unseen_share"],
                         seen_fires_on_a_scar=sa["firms_matched_share"], scars=sa["scars"]))
        print(rows[-1], flush=True)
    (config.ROOT / "reports" / f"sweep_{stem}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--stem", default="unet_2025_v2")
    ap.add_argument("--thr", type=float, nargs="+", default=[0.5, 0.6, 0.7, 0.8, 0.9])
    a = ap.parse_args()
    main(a.season, a.stem, a.thr)
