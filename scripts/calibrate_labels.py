"""checks the label rules against real fires: false-colour quicklooks with the labels on top, and
the spectra of pixels next to firms fires vs random cropland. writes to docs/calibration/."""
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
from pyproj import Transformer  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, firms, indices, s2, timeline  # noqa: E402

OUT = config.ROOT / "docs" / "calibration"


def false_color(x: np.ndarray) -> np.ndarray:
    """swir2 / nir / red. burnt fields show up dark brown-black, crops green, straw pink."""
    rgb = np.stack([x[indices.SW2], x[indices.NIR], x[indices.R]], -1)
    rgb = np.clip(rgb / np.array([0.45, 0.45, 0.25]), 0, 1)
    return np.nan_to_num(rgb ** 0.8)


def main(pre_key: str, post_key: str, crops: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = config.s2_dir("2025")
    pre, scl_pre, tags_pre = s2.load_scene(d / f"{pre_key}.tif")
    post, scl_post, tags_post = s2.load_scene(d / f"{post_key}.tif")
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    tf = rasterio.Affine(*aoi["transform"])
    with rasterio.open(config.INTERIM / "worldcover.tif") as src:
        crop = src.read(1) == 40
    valid = timeline.clear_mask(scl_pre, pre) & timeline.clear_mask(scl_post, post) & crop
    lab = indices.burn_labels(pre, post, valid)
    print(f"valid {valid.mean():.0%}, burnt {np.mean(lab == 1):.2%}, not-burnt {np.mean(lab == 0):.1%}, "
          f"ignored {np.mean(lab == 255):.1%}")
    # what sen2cor calls the pixels we label burnt
    u, c = np.unique(scl_post[lab == 1], return_counts=True)
    print("scl classes of burnt-labelled pixels:", dict(zip(u.tolist(), c.tolist())))

    # firms points between the two overpasses
    t0 = pd.Timestamp(tags_pre["datetime"]).tz_convert("UTC")
    t1 = pd.Timestamp(tags_post["datetime"]).tz_convert("UTC")
    fp = pd.read_parquet(firms.cache_path(2025))
    fp = fp[(fp.dt_utc > t0) & (fp.dt_utc <= t1)]
    tr = Transformer.from_crs("EPSG:4326", config.CRS, always_xy=True)
    x, y = tr.transform(fp.longitude.values, fp.latitude.values)
    rows, cols = rasterio.transform.rowcol(tf, x, y)
    rows, cols = np.asarray(rows), np.asarray(cols)
    h, w = lab.shape
    ok = (rows >= 5) & (rows < h - 5) & (cols >= 5) & (cols < w - 5)
    rows, cols, fp = rows[ok], cols[ok], fp[ok]
    print(f"{len(fp)} firms points between {t0} and {t1} inside the window")

    # for each fire point, the darkest swir1 pixel within 9x9 (180 m) = the most burn-like pixel nearby
    def sample(r, c):
        win = post[:, r - 4:r + 5, c - 4:c + 5]
        sw = np.where(np.isfinite(win[indices.SW1]), win[indices.SW1], 9)
        k = np.unravel_index(np.argmin(sw), sw.shape)
        return r - 4 + k[0], c - 4 + k[1]

    fire_px = [sample(r, c) for r, c in zip(rows, cols)]
    fr = np.array([p[0] for p in fire_px]); fc = np.array([p[1] for p in fire_px])
    labs_at_fire = lab[fr, fc] if len(fr) else np.array([])
    # how many fire points have a burnt label within 9x9
    hit = []
    for r, c in zip(rows, cols):
        hit.append(bool((lab[r - 4:r + 5, c - 4:c + 5] == 1).any()))
    hit = np.array(hit)
    print(f"fire points with a burnt label within 90 m: {hit.mean():.0%}  "
          f"(label at darkest pixel: burnt {np.mean(labs_at_fire == 1):.0%}, not {np.mean(labs_at_fire == 0):.0%}, "
          f"unsure {np.mean(labs_at_fire == 255):.0%})")

    rng = np.random.default_rng(0)
    cr, cc = np.nonzero(valid)
    pick = rng.choice(len(cr), size=min(20000, len(cr)), replace=False)
    rr, rc = cr[pick], cc[pick]

    def feats(xx, r, c, xpre):
        return dict(
            B04=xx[indices.R, r, c], B8A=xx[indices.NIR, r, c], B11=xx[indices.SW1, r, c], B12=xx[indices.SW2, r, c],
            NBR=indices.nbr(xx)[r, c], NBR2=indices.nbr2(xx)[r, c],
            dNBR=(indices.nbr(xpre) - indices.nbr(xx))[r, c],
            dB11=(xpre[indices.SW1] - xx[indices.SW1])[r, c],
        )

    F = feats(post, fr, fc, pre)
    Rn = feats(post, rr, rc, pre)
    fig, axs = plt.subplots(2, 4, figsize=(16, 7))
    rngs = dict(B04=(0, .25), B8A=(0, .5), B11=(0, .5), B12=(0, .45), NBR=(-.4, .8), NBR2=(-.1, .4),
                dNBR=(-.4, .8), dB11=(-.2, .3))
    for ax, k in zip(axs.flat, F):
        bins = np.linspace(*rngs[k], 60)
        ax.hist(Rn[k][np.isfinite(Rn[k])], bins, density=True, alpha=.5, label="random cropland")
        ax.hist(F[k][np.isfinite(F[k])], bins, density=True, alpha=.6, label="next to a firms fire")
        thr = {"B11": indices.T["sw1_max"], "NBR": indices.T["nbr_max"], "NBR2": indices.T["nbr2_max"],
               "dNBR": indices.T["dnbr_min"], "dB11": indices.T["dsw1_min"], "B8A": indices.T["nir_max"]}.get(k)
        if thr is not None:
            ax.axvline(thr, color="k", ls="--", lw=1)
        ax.set_title(k)
    axs.flat[0].legend(fontsize=8)
    fig.suptitle(f"after = {post_key}, before = {pre_key}: spectra at firms fires vs random cropland")
    fig.tight_layout()
    fig.savefig(OUT / f"spectra_{post_key}.png", dpi=90)
    plt.close(fig)

    # quicklooks around the densest fire clusters
    if len(fr):
        H, xe, ye = np.histogram2d(fr, fc, bins=[h // 300, w // 300])
        order = np.argsort(H.ravel())[::-1][:crops]
        for j, k in enumerate(order):
            bi, bj = np.unravel_index(k, H.shape)
            r0 = int(np.clip(xe[bi] + (xe[1] - xe[0]) / 2 - 150, 0, h - 300))
            c0 = int(np.clip(ye[bj] + (ye[1] - ye[0]) / 2 - 150, 0, w - 300))
            sl = (slice(r0, r0 + 300), slice(c0, c0 + 300))
            fig, axs = plt.subplots(1, 3, figsize=(15, 5.3))
            axs[0].imshow(false_color(pre[:, sl[0], sl[1]])); axs[0].set_title(f"before {pre_key}")
            axs[1].imshow(false_color(post[:, sl[0], sl[1]])); axs[1].set_title(f"after {post_key}")
            ov = false_color(post[:, sl[0], sl[1]]).copy()
            L = lab[sl]
            ov[L == 1] = [1, 0.1, 0.6]
            ov[L == 0] = ov[L == 0] * 0.6
            axs[2].imshow(ov); axs[2].set_title("rule label: magenta = burnt, dimmed = not burnt")
            m = (fr >= r0) & (fr < r0 + 300) & (fc >= c0) & (fc < c0 + 300)
            for a in axs:
                a.scatter(cols[m] - c0, rows[m] - r0, s=18, facecolors="none", edgecolors="yellow", lw=1)
                a.set_xticks([]); a.set_yticks([])
            fig.suptitle("yellow circles = firms fire points between the two dates. 300 px = 6 km")
            fig.tight_layout()
            fig.savefig(OUT / f"quicklook_{post_key}_{j}.png", dpi=80)
            plt.close(fig)
    print("wrote", OUT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", default="2025-11-02_2B")
    ap.add_argument("--post", default="2025-11-07_2C")
    ap.add_argument("--crops", type=int, default=3)
    a = ap.parse_args()
    main(a.pre, a.post, a.crops)
