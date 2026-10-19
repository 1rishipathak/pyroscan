"""compare two burn maps where it matters: the fields they disagree on.

pixels both models call burnt (or not) say nothing about which model is better. so this
takes the disagreements, "burnt only in a" and "burnt only in b", cuts them into scars of
at least one killa, samples them area-weighted and makes before/after true-colour sheets to
judge by eye. it also prints the areas.

run: .venv/Scripts/python.exe scripts/compare_models.py --a data/interim/v1 --b data/processed/2025 --n 24
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio.windows import Window  # noqa: E402
from scipy import ndimage  # noqa: E402
from skimage.measure import label  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, s2  # noqa: E402

MIN_PX = 10


def scars_of(mask: np.ndarray, bidx: np.ndarray, pidx: np.ndarray) -> pd.DataFrame:
    lab = label(mask, connectivity=2)
    ids = np.arange(1, lab.max() + 1)
    size = ndimage.sum_labels(np.ones_like(lab, np.int32), lab, ids)
    keep = size >= MIN_PX
    ids, size = ids[keep], size[keep]
    cy, cx = np.array(ndimage.center_of_mass(np.ones_like(lab, np.int8), lab, ids)).reshape(-1, 2).T
    kpost = ndimage.maximum(bidx, lab, ids).astype(int)
    kpre = ndimage.maximum(pidx, lab, ids).astype(int)
    return pd.DataFrame(dict(id=ids, px=size, area_ha=size * 0.04, row=cy, col=cx, k_post=kpost, k_pre=kpre)), lab


def sheet(df: pd.DataFrame, lab: np.ndarray, title: str, out: Path, season: str) -> None:
    scenes = {s2.Scene(**s).key: s2.Scene(**s) for s in
              json.loads((config.s2_dir(season) / "scenes.json").read_text(encoding="utf-8"))}
    keys = json.loads((config.PROCESSED / season / "scenes.json").read_text(encoding="utf-8"))
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    c0, r0 = aoi["window"][0], aoi["window"][1]
    half = 40
    cols = 6
    rows = int(np.ceil(len(df) / (cols // 2)))
    fig, axs = plt.subplots(rows, cols, figsize=(cols * 2.6, rows * 2.75))
    handles = {}
    with rasterio.Env(**s2.GDAL_ENV):
        for i, s in enumerate(df.itertuples()):
            for j, (tag, k) in enumerate((("before", max(s.k_pre, 1)), ("after", s.k_post))):
                key = keys[int(k) - 1]["key"]
                if key not in handles:
                    handles[key] = rasterio.open(scenes[key].hrefs["visual"])
                cx, cy = int((c0 + s.col) * 2), int((r0 + s.row) * 2)
                img = handles[key].read(window=Window(cx - half, cy - half, 2 * half, 2 * half), boundless=True)
                ax = axs.flat[i * 2 + j]
                ax.imshow(np.clip(img.transpose(1, 2, 0).astype(np.float32) * 1.6 / 255, 0, 1))
                if tag == "after":
                    rr, cc = int(s.row), int(s.col)
                    m = np.asarray(lab[rr - half // 2: rr + half // 2, cc - half // 2: cc + half // 2]) == s.id
                    ax.contour(np.kron(m, np.ones((2, 2))), levels=[0.5], colors="cyan", linewidths=0.9)
                ax.set_title(f"#{i + 1} {tag} {key[5:10]}" + (f" · {s.area_ha:.1f} ha" if tag == "after" else ""), fontsize=8)
                ax.set_xticks([]); ax.set_yticks([])
        for ax in axs.flat[len(df) * 2:]:
            ax.axis("off")
    for h in handles.values():
        h.close()
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=70)
    plt.close(fig)


def main(a_dir: str, b_dir: str, n: int, seed: int, season: str, name_a: str, name_b: str) -> None:
    a = rasterio.open(Path(a_dir) / "burn_idx.tif").read(1)
    b = rasterio.open(Path(b_dir) / "burn_idx.tif").read(1)
    pa = rasterio.open(Path(a_dir) / "burn_pre_idx.tif").read(1)
    pb = rasterio.open(Path(b_dir) / "burn_pre_idx.tif").read(1)
    dist = rasterio.open(config.INTERIM / "districts.tif").read(1) > 0
    A, B = (a > 0) & dist, (b > 0) & dist
    both = A & B
    print(f"study area: {name_a} {A.sum() * 0.04:,.0f} ha, {name_b} {B.sum() * 0.04:,.0f} ha, both {both.sum() * 0.04:,.0f} ha")
    print(f"  only {name_a}: {(A & ~B).sum() * 0.04:,.0f} ha   only {name_b}: {(B & ~A).sum() * 0.04:,.0f} ha")
    out = config.ROOT / "docs" / "compare"
    out.mkdir(parents=True, exist_ok=True)
    rng_seed = seed
    for nm, mask, bidx, pidx in ((f"only_{name_b}", B & ~A, b, pb), (f"only_{name_a}", A & ~B, a, pa)):
        df, lab = scars_of(mask, bidx, pidx)
        if df.empty:
            print(nm, "no scars >= 1 killa")
            continue
        w = df.area_ha.clip(upper=df.area_ha.quantile(0.9))  # area-weighted, without letting one giant scar dominate
        try:
            pick = df.sample(n=min(n, len(df)), weights=w, random_state=rng_seed)
        except ValueError:
            pick = df.sample(n=min(n, len(df)), random_state=rng_seed)
        print(f"  {nm}: {len(df):,} scars >= 1 killa, {df.area_ha.sum():,.0f} ha; sheet of {len(pick)}")
        sheet(pick, lab, f"{nm}: {len(pick)} random disagreement scars (area-weighted), 10 m true colour", out / f"{nm}.png", season)
        pick.to_csv(out / f"{nm}.csv", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="data/interim/v1")
    ap.add_argument("--b", default="data/processed/2025")
    ap.add_argument("--name-a", default="v1")
    ap.add_argument("--name-b", default="v2")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--season", default="2025")
    x = ap.parse_args()
    main(x.a, x.b, x.n, x.seed, x.season, x.name_a, x.name_b)
