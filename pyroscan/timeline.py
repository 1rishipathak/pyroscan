"""walk through a season scene by scene, pairing each clear pixel with its last clear look.

sentinel-2 passes every 2-5 days here, and each pass is partly cloudy or only covers
part of the tile. so "before" is not one image: for every pixel it is the most recent
earlier date when that pixel was clear. a burn is then dated to the window
(last clear look, this look].
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import config, s2

# sen2cor scene classes
SCL_NODATA, SCL_SAT, SCL_DARK, SCL_SHADOW = 0, 1, 2, 3
SCL_CLOUD = (8, 9, 10)  # medium, high probability, thin cirrus


def clear_mask(scl: np.ndarray, refl: np.ndarray) -> np.ndarray:
    """usable pixels. burnt fields are very dark, and sen2cor sometimes files them as
    'dark area' or even 'cloud shadow', so shadows only count as bad near real clouds."""
    cloud = np.isin(scl, SCL_CLOUD)
    cloud_big = ndimage.binary_dilation(cloud, iterations=3)
    near_cloud = ndimage.binary_dilation(cloud, structure=np.ones((3, 3)), iterations=60)  # ~1.2 km
    bad = cloud_big | (scl == SCL_NODATA) | (scl == SCL_SAT) | ((scl == SCL_SHADOW) & near_cloud)
    return ~bad & np.isfinite(refl).all(0)


@dataclass
class Step:
    i: int
    key: str
    datetime: str
    post: np.ndarray  # [5,h,w] this look
    pre: np.ndarray  # [5,h,w] last clear look per pixel (nan if none)
    pre_idx: np.ndarray  # [h,w] scene index of that look, -1 if none
    clear: np.ndarray  # [h,w] this look is clear
    valid: np.ndarray  # [h,w] clear now and seen clear before


def scene_files(season: str) -> list[tuple[str, str, Path]]:
    d = config.s2_dir(season)
    scenes = json.loads((d / "scenes.json").read_text(encoding="utf-8"))
    out = []
    for s in scenes:
        sc = s2.Scene(**s)
        p = d / f"{sc.key}.tif"
        if p.exists():
            out.append((sc.key, sc.datetime, p))
    return out


def walk(season: str, max_gap_days: int = 20):
    files = scene_files(season)
    pre = None
    pre_idx = None
    pre_time = None
    times = [np.datetime64(dt[:19]) for _, dt, _ in files]
    for i, (key, dt, path) in enumerate(files):
        refl, scl, _ = s2.load_scene(path)
        clear = clear_mask(scl, refl)
        if pre is None:
            pre = np.full_like(refl, np.nan)
            pre_idx = np.full(clear.shape, -1, np.int16)
            pre_time = np.array(times)
        valid = clear & (pre_idx >= 0)
        if max_gap_days and valid.any():
            gap = np.zeros(clear.shape, np.float32)
            has = pre_idx >= 0
            gap[has] = (times[i] - pre_time[pre_idx[has]]) / np.timedelta64(1, "D")
            valid &= gap <= max_gap_days
        yield Step(i, key, dt, refl, pre.copy(), pre_idx.copy(), clear, valid)
        pre[:, clear] = refl[:, clear]
        pre_idx[clear] = i
