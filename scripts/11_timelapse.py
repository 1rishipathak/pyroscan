"""step 11: the season as a square timelapse for social posts. burns appear pass by pass, the
counters run, and it ends on 2021 vs 2025. writes an mp4 and a gif to docs/ (needs ffmpeg)."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config  # noqa: E402

# the dashboard's light palette (web/style.css)
BG = "#ffffff"
INK, INK2, MUTED = "#202124", "#3c4350", "#5f6b7a"
LAND = np.array([0.933, 0.945, 0.961, 1])  # #eef1f5
DETECTED = np.array([42, 120, 214]) / 255  # #2a78d6: a satellite saw this burn
UNDETECTED = np.array([235, 104, 52]) / 255  # #eb6834: no satellite saw it
DETECTION = "#1baf7a"  # satellite fire detections
FPS = 12
PER_PASS = 8  # frames per sentinel-2 pass
_fonts = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = [f for f in ("Noto Sans", "Segoe UI") if f in _fonts] or ["DejaVu Sans"]  # imd's typeface if installed


def main(season: str) -> None:
    p = config.PROCESSED / season
    bidx = rasterio.open(p / "burn_idx.tif").read(1)
    seen = rasterio.open(p / "seen.tif").read(1)  # kept scars only: 1 never detected, 2 detected
    with rasterio.open(config.INTERIM / "districts.tif") as src:
        dist = src.read(1)
        tf = src.transform
    seen = np.where(dist > 0, seen, 0)
    k_of = np.where(seen > 0, bidx, 0)
    scenes = json.loads((p / "scenes.json").read_text(encoding="utf-8"))
    K = len(scenes)
    dates = pd.to_datetime([s["datetime"] for s in scenes], utc=True, format="ISO8601")

    # crop to the districts and block-average to ~1 px per 100 m so small fields stay visible
    rows, cols = np.nonzero(dist > 0)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    f = 5
    r1, c1 = r0 + (r1 - r0) // f * f, c0 + (c1 - c0) // f * f
    sl = (slice(r0, r1), slice(c0, c1))
    kk, ss, dd = k_of[sl], seen[sl], dist[sl]
    nby, nbx = kk.shape[0] // f, kk.shape[1] // f

    def blocks(a):
        return a.reshape(nby, f, nbx, f).sum(axis=(1, 3))

    # new burnt pixels per block and pass, for never-detected and detected
    new_u = np.stack([blocks(((kk == k) & (ss == 1)).astype(np.int16)) for k in range(1, K + 1)], -1)
    new_s = np.stack([blocks(((kk == k) & (ss == 2)).astype(np.int16)) for k in range(1, K + 1)], -1)
    cum_u, cum_s = new_u.cumsum(-1), new_s.cumsum(-1)
    land = blocks((dd > 0).astype(np.int16)) > 0
    up, left = np.zeros_like(land), np.zeros_like(land)  # neighbours without wrapping round the frame
    up[1:], left[:, 1:] = land[:-1], land[:, :-1]
    edge = (land ^ up) | (land ^ left)

    fp = pd.read_parquet(p / "firms_matched.parquet")
    fp = fp[fp.district.isin(config.DISTRICTS)]
    fr, fc = rasterio.transform.rowcol(tf, fp.x.values, fp.y.values)
    fp = fp.assign(py=(np.asarray(fr) - r0) / f, px=(np.asarray(fc) - c0) / f)
    ha_px = config.RES**2 / 1e4
    summary = json.loads((config.ROOT / "web" / "data" / season / "summary.json").read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="pyroscan_tl_"))
    fig = plt.figure(figsize=(10.8, 10.8), dpi=100, facecolor=BG)
    n = 0

    def save():
        nonlocal n
        fig.savefig(tmp / f"f_{n:04d}.png", facecolor=BG)
        n += 1

    for k in range(K):
        fig.clf()
        ax = fig.add_axes([0.04, 0.17, 0.92, 0.66], facecolor=BG)
        u, s_ = cum_u[..., k], cum_s[..., k]
        tot = u + s_
        frac = np.clip(tot / (f * f) * 2.2, 0, 1)
        col = np.where((u >= s_)[..., None], UNDETECTED, DETECTED)
        img = np.zeros((nby, nbx, 4))
        img[..., :3] = col
        img[..., 3] = frac
        fresh = (new_u[..., k] + new_s[..., k]) > 0
        img[fresh, 3] = np.maximum(img[fresh, 3], 0.95)
        ax.imshow(np.where(land[..., None], LAND, np.array([0, 0, 0, 0])), interpolation="nearest")
        ax.imshow(img, interpolation="nearest")
        ey, ex = np.nonzero(edge)
        ax.scatter(ex, ey, s=0.15, c="#8a94a3", alpha=0.6, linewidths=0)
        upto = fp[fp.dt_utc <= dates[k] + pd.Timedelta(hours=6)]
        ax.scatter(upto.px, upto.py, s=7, c=DETECTION, alpha=0.95, edgecolors="white", linewidths=0.3)
        ax.set_xlim(0, nbx); ax.set_ylim(nby, 0); ax.axis("off")
        burnt = tot.sum() * ha_px
        unseen = u.sum() * ha_px
        fig.add_artist(plt.Rectangle((0, 0.992), 1, 0.008, transform=fig.transFigure, color="#12355b"))
        fig.text(0.05, 0.94, "PyroScan", color=INK, fontsize=34, fontweight="bold")
        fig.text(0.05, 0.905, f"sangrur + barnala, punjab · kharif {season} · every sentinel-2 pass", color=MUTED, fontsize=15)
        fig.text(0.95, 0.94, dates[k].strftime("%d %b %Y").lower(), color=INK, fontsize=26, ha="right", fontweight="bold")
        # numbers in ink, with a colour swatch by each label
        for x, big, lab, sw in ((0.05, f"{burnt:,.0f} ha", "farmland burnt so far", None),
                                (0.38, f"{len(upto):,}", "satellite fire detections", DETECTION),
                                (0.70, f"{unseen / burnt:.0%}" if burnt else "–", "burnt, never detected", UNDETECTED)):
            fig.text(x, 0.085, big, color=INK, fontsize=30, fontweight="bold")
            fig.text(x + (0.022 if sw is not None else 0), 0.05, lab, color=MUTED, fontsize=14)
            if sw is not None:
                fig.add_artist(plt.Rectangle((x, 0.053), 0.014, 0.014, transform=fig.transFigure, color=sw))
        for x, sw, lab in ((0.05, UNDETECTED, "burnt, no satellite detected it"), (0.37, DETECTED, "burnt, a satellite saw it"),
                           (0.63, DETECTION, "satellite fire detection")):
            fig.add_artist(plt.Rectangle((x, 0.017), 0.012, 0.012, transform=fig.transFigure, color=sw))
            fig.text(x + 0.018, 0.015, lab, color=MUTED, fontsize=12)
        save()
        for _ in range(PER_PASS - 1):
            shutil.copy(tmp / f"f_{n - 1:04d}.png", tmp / f"f_{n:04d}.png")
            n += 1

    # end card
    sa = summary["study_area"]
    by = summary.get("burnt_by_year", {})
    null = json.loads((config.ROOT / "reports" / "null_test.json").read_text(encoding="utf-8"))["results"] \
        if (config.ROOT / "reports" / "null_test.json").exists() else {}
    fig.clf()
    lines = [
        (0.80, f"the official count saw {sa['official_events']:,} fires.", INK, 34),
        (0.72, f"sentinel-2 shows {sa['burnt_ha']:,} ha burnt.", INK, 34),
        (0.62, f"{sa['unseen_share']:.0%} of it no satellite ever detected.", INK, 34),
    ]
    fig.add_artist(plt.Rectangle((0.045, 0.605), 0.008, 0.06, transform=fig.transFigure, color=UNDETECTED))  # the key line
    insat = config.ROOT / "web" / "data" / season / "insat.json"
    insat = json.loads(insat.read_text(encoding="utf-8")) if insat.exists() else None
    if "2021" in by and "2021" in null:
        lines += [(0.48, f"2021, before the shift to evening burning: satellites missed {null['2021']['never_detected_chance_corrected']:.0%}.",
                   INK2, 19),
                  (0.43, f"{season}: they missed {null[season]['never_detected_chance_corrected']:.0%}. the count fell 93%, the burning 61%.",
                   INK2, 19)]
    if insat and not insat["study"]["pixels"]:
        lines += [(0.36, "isro's insat-3dr, scanning every 30 minutes, flagged none of it.", INK2, 19)]
    for y, t, c, fs in lines:
        fig.text(0.07, y, t, color=c, fontsize=fs, fontweight="bold" if fs > 30 else "normal")
    fig.add_artist(plt.Rectangle((0, 0.992), 1, 0.008, transform=fig.transFigure, color="#12355b"))
    fig.text(0.07, 0.16, "PyroScan", color=INK, fontsize=40, fontweight="bold")
    fig.text(0.07, 0.11, "the farm fires satellites miss · built with tinyfish", color=MUTED, fontsize=17)
    for _ in range(FPS * 5):
        save()
    plt.close(fig)

    docs = config.ROOT / "docs"
    mp4 = docs / f"pyroscan_{season}_timelapse.mp4"
    gif = docs / f"pyroscan_{season}_timelapse.gif"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f_%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)], check=True)
    pal = tmp / "palette.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
                    "fps=8,scale=540:-1:flags=lanczos,palettegen=max_colors=64", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-i", str(pal), "-lavfi",
                    "fps=8,scale=540:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", str(gif)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{n} frames -> {mp4.name} ({mp4.stat().st_size / 1e6:.1f} MB), {gif.name} ({gif.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    main(ap.parse_args().season)
