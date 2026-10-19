"""step 9: contact sheet of random 'never detected' scars for a human check.

the model was trained on automatic labels only. this sheet is how we check, by eye, that
the scars nobody counted really are burnt fields: before / after, 10 m true colour.

run: .venv/Scripts/python.exe scripts/09_qa_sheet.py --season 2025 --n 48 --seed 1
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, s2  # noqa: E402


def main(season: str, n: int, seed: int, which: str) -> None:
    p = config.PROCESSED / season
    scars = pd.read_parquet(p / "scars.parquet")
    sel = scars[scars.district.isin(config.DISTRICTS)]
    sel = sel[~sel.seen] if which == "unseen" else sel[sel.seen]
    # random, but area-weighted: a hectare is what we report, so sample hectares, not blobs
    pick = sel.sample(n=min(n, len(sel)), weights=sel.area_ha, random_state=seed)
    scenes = {s2.Scene(**s).key: s2.Scene(**s) for s in json.loads((config.s2_dir(season) / "scenes.json").read_text(encoding="utf-8"))}
    keys = json.loads((p / "scenes.json").read_text(encoding="utf-8"))
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    c0, r0 = aoi["window"][0], aoi["window"][1]
    lab = np.load(p / "scar_labels.npy", mmap_mode="r")
    half = 40  # 10 m px -> 800 m chips
    cols = 6
    rows = int(np.ceil(len(pick) / (cols // 2)))
    fig, axs = plt.subplots(rows, cols, figsize=(cols * 2.6, rows * 2.75))
    handles = {}
    with rasterio.Env(**s2.GDAL_ENV):
        for i, s in enumerate(pick.itertuples()):
            for j, (tag, k) in enumerate((("before", s.k_pre), ("after", s.k_post))):
                key = keys[int(k) - 1]["key"]
                if key not in handles:
                    handles[key] = rasterio.open(scenes[key].hrefs["visual"])
                cx, cy = int((c0 + s.col) * 2), int((r0 + s.row) * 2)
                img = handles[key].read(window=Window(cx - half, cy - half, 2 * half, 2 * half), boundless=True)
                img = np.clip(img.transpose(1, 2, 0).astype(np.float32) * 1.6 / 255, 0, 1)
                ax = axs.flat[i * 2 + j]
                ax.imshow(img)
                if tag == "after":
                    rr, cc = int(s.row), int(s.col)
                    m = np.asarray(lab[rr - half // 2: rr + half // 2, cc - half // 2: cc + half // 2]) == s.scar_id
                    ax.contour(np.kron(m, np.ones((2, 2))), levels=[0.5], colors="magenta", linewidths=0.9)
                ax.set_title(f"#{i + 1} {tag} {keys[int(k) - 1]['key'][5:10]}" + (f" · {s.area_ha:.1f} ha" if tag == "after" else ""),
                             fontsize=8)
                ax.set_xticks([]); ax.set_yticks([])
        for ax in axs.flat[len(pick) * 2:]:
            ax.axis("off")
    for h in handles.values():
        h.close()
    fig.suptitle(f"{len(pick)} random {which} burn scars (area-weighted), sentinel-2 true colour 10 m, 800 m chips", fontsize=11)
    fig.tight_layout()
    out = config.ROOT / "docs" / f"qa_{which}_{season}.png"
    fig.savefig(out, dpi=70)
    pick[["scar_id", "district", "area_ha", "lat", "lon", "t_pre", "t_post"]].to_csv(out.with_suffix(".csv"), index=False)
    print("wrote", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--which", default="unseen", choices=["unseen", "seen"])
    a = ap.parse_args()
    main(a.season, a.n, a.seed, a.which)
