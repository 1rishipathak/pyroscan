"""step 3: rule-based burn labels for every date pair + training patches for the u-net.

no hand labelling: labels come from dnbr + char rules (pyroscan/indices.py), and only
confident pixels are labelled. everything unsure is 255 and ignored in training.

outputs
  data/interim/labels/<season>/<key>.tif   full-window labels per date (0/1/255)
  data/interim/patches_<season><tag>_x.npy  256x256 patches, x uint16 [n,11,256,256] (memory-mapped in training)
  data/interim/patches_<season><tag>.npz    labels y uint8 [n,256,256] and meta [n,4]

run: .venv/Scripts/python.exe scripts/03_make_labels.py --season 2025
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, indices, timeline  # noqa: E402

P = 256  # patch size in pixels (5.12 km)
VAL_FRAC = 0.2


def block_split(nby: int, nbx: int, seed: int = 7) -> np.ndarray:
    """fixed spatial split: True = validation block. same blocks for every date."""
    rng = np.random.default_rng(seed)
    return rng.random((nby, nbx)) < VAL_FRAC


def pseudo_labels(lab: np.ndarray, valid: np.ndarray, st, prob_file: Path, hi: float = 0.9, lo: float = 0.1) -> np.ndarray:
    """self-training: fill the rule's 'unsure' pixels with a previous model's confident calls.
    a confident burn only counts where physics allows one (char-ish and changed); anything that is
    not char-ish or did not change cannot be a new burn, so it becomes 'not burnt'."""
    if not prob_file.exists():
        return lab
    with rasterio.open(prob_file) as src:
        q = src.read(1)
    p = np.where(q > 0, (q.astype(np.float32) - 1) / 254, np.nan)
    plaus = indices.plausible_burn(st.pre, st.post)
    unsure = (lab == 255) & valid
    out = lab.copy()
    out[unsure & plaus & (p >= hi)] = 1
    out[unsure & (p <= lo)] = 0
    out[unsure & ~plaus] = 0
    return out


def main(season: str, per_pair: int, tag: str = "", seed: int = 0, pseudo: str | None = None) -> None:
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    h, w = aoi["shape"]
    tf = rasterio.Affine(*aoi["transform"])
    with rasterio.open(config.INTERIM / "worldcover.tif") as src:
        crop = src.read(1) == 40
    with rasterio.open(config.INTERIM / "districts.tif") as src:
        dist = src.read(1)
    lab_dir = config.INTERIM / "labels" / season
    lab_dir.mkdir(parents=True, exist_ok=True)

    nby, nbx = h // P, w // P
    is_val = block_split(nby, nbx)
    np.save(config.INTERIM / "val_blocks.npy", is_val)
    rng = np.random.default_rng(seed)

    # write patches straight to a memory-mapped file so ram stays flat however many we take
    n_max = per_pair * len(timeline.scene_files(season))
    x_path = config.INTERIM / f"patches_{season}{tag}_x.npy"
    X = np.lib.format.open_memmap(x_path, mode="w+", dtype=np.uint16, shape=(n_max, 11, P, P))
    n = 0
    ys, meta = [], []
    stats = []
    for st in timeline.walk(season):
        valid = st.valid & crop
        lab = indices.burn_labels(st.pre, st.post, valid)
        if pseudo:
            lab = pseudo_labels(lab, valid, st, Path(pseudo) / f"{st.key}.tif")
        prof = dict(driver="GTiff", height=h, width=w, count=1, dtype="uint8", crs=config.CRS,
                    transform=tf, compress="deflate", nodata=255)
        if not pseudo:  # keep the plain rule labels on disk; pseudo labels live only in the patches
            with rasterio.open(lab_dir / f"{st.key}.tif", "w", **prof) as dst:
                dst.write(lab, 1)
        npos = int((lab == 1).sum())
        nneg = int((lab == 0).sum())
        stats.append(dict(key=st.key, valid_km2=valid.sum() * 4e-4, pos_ha=npos * 0.04, neg_ha=nneg * 0.04,
                          pos_in_districts_ha=float(((lab == 1) & (dist > 0)).sum() * 0.04)))
        print(f"{st.key}: valid {valid.mean():.0%}  burnt-label {npos * 0.04:,.0f} ha  not-burnt {nneg * 0.04:,.0f} ha", flush=True)
        if npos + nneg == 0:
            continue

        # candidate blocks: enough labelled pixels. weight blocks with burns higher.
        cands = []
        for by in range(nby):
            for bx in range(nbx):
                sl = (slice(by * P, (by + 1) * P), slice(bx * P, (bx + 1) * P))
                lb = lab[sl]
                labelled = (lb != 255).mean()
                if labelled < 0.05:
                    continue
                pos = (lb == 1).mean()
                cands.append((by, bx, 1.0 + 20 * pos))
        if not cands:
            continue
        wts = np.array([c[2] for c in cands])
        k = min(per_pair, len(cands))
        pick = rng.choice(len(cands), size=k, replace=False, p=wts / wts.sum())
        for j in pick:
            by, bx, _ = cands[j]
            sl = (slice(by * P, (by + 1) * P), slice(bx * P, (bx + 1) * P))
            pre = st.pre[:, sl[0], sl[1]]
            post = st.post[:, sl[0], sl[1]]
            v = st.valid[sl]
            x = np.concatenate([pre, post], 0)
            x = np.where(np.isfinite(x) & v[None], x, 0.0)
            # store reflectance as uint16 (x 10000, clipped) to keep the file small
            xq = np.clip(x * 10000, 0, 65535).astype(np.uint16)
            X[n] = np.concatenate([xq, v[None].astype(np.uint16)], 0)
            n += 1
            ys.append(lab[sl])
            meta.append((st.i, by, bx, int(is_val[by, bx])))

    X.flush()
    del X
    Y = np.stack(ys)
    M = np.array(meta, dtype=np.int32)
    out = config.INTERIM / f"patches_{season}{tag}.npz"
    np.savez(out, y=Y, meta=M, n=np.array(n), x_path=np.array(x_path.name))
    (config.INTERIM / f"label_stats_{season}.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"saved {n} patches ({M[:, 3].sum()} val) -> {x_path.name} + {out.name}  {n * 11 * P * P * 2 / 1e9:.1f} GB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--per-pair", type=int, default=110)
    ap.add_argument("--tag", default="", help="suffix for the patch files, e.g. _v2")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pseudo", default=None, help="folder of per-date probability maps from a previous model (self-training)")
    a = ap.parse_args()
    main(a.season, a.per_pair, a.tag, a.seed, a.pseudo)
