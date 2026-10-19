"""step 2: download the study window of every scene in the season. finished scenes are
skipped, so it's safe to rerun."""
import argparse
import json
import sys
from pathlib import Path

import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, s2  # noqa: E402


def main(season: str, workers: int, limit: int | None) -> None:
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    out_dir = config.s2_dir(season)
    scenes = [s2.Scene(**d) for d in json.loads((out_dir / "scenes.json").read_text(encoding="utf-8"))]
    if limit:
        scenes = scenes[:limit]
    c0, r0, w, h = aoi["window"]
    win = Window(c0, r0, w, h)
    tf = rasterio.Affine(*aoi["tile_transform"])
    print(f"downloading {len(scenes)} scenes, window {w}x{h}")
    s2.download_all(scenes, win, tf, out_dir, workers=workers)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    main(a.season, a.workers, a.limit)
