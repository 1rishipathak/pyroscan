"""sentinel-2 l2a from earth search: find the scenes, then read only our study window straight from the
cloud-optimised geotiffs, which keeps a season to a few gb instead of hundreds."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from pystac_client import Client

from . import config

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MULTIPLEX="YES",
    GDAL_HTTP_MAX_RETRY="6",
    GDAL_HTTP_RETRY_DELAY="3",
    GDAL_HTTP_TIMEOUT="120",
    VSI_CACHE="TRUE",
    AWS_NO_SIGN_REQUEST="YES",
)


@dataclass
class Scene:
    id: str
    datetime: str  # iso, utc
    platform: str
    cloud: float
    nodata: float
    hrefs: dict  # asset name -> url
    scale: dict  # asset name -> (scale, offset)
    # when true, earth search has already removed the 2022+ boa offset. applying it again
    # turns canal water negative, so it must be skipped.
    boa_applied: bool = True

    @property
    def date(self) -> str:
        return self.datetime[:10]

    @property
    def key(self) -> str:
        # two satellites can pass on the same day
        return f"{self.date}_{self.platform[-2:].upper()}"


def search(start: str, end: str, tile: str = config.MGRS_TILE) -> list[Scene]:
    """every l2a scene of one tile, keeping only the newest processing run."""
    cat = Client.open(config.STAC_URL)
    res = cat.search(
        collections=[config.S2_COLLECTION],
        datetime=f"{start}T00:00:00Z/{end}T23:59:59Z",
        query={"grid:code": {"eq": f"MGRS-{tile}"}},
        max_items=500,
    )
    best: dict[str, object] = {}
    for it in res.items():
        if it.properties.get("grid:code") != f"MGRS-{tile}":
            continue
        # ids look like S2B_43REP_20251010_1_L2A; the digit is the processing run
        stem = "_".join(it.id.split("_")[:3])
        if stem not in best or it.id > best[stem].id:
            best[stem] = it
    scenes = []
    for it in best.values():
        hrefs, scale = {}, {}
        for name in list(config.BANDS) + ["scl", "visual"]:
            a = it.assets[name]
            hrefs[name] = a.href
            rb = (a.extra_fields.get("raster:bands") or [{}])[0]
            scale[name] = (rb.get("scale", 1.0), rb.get("offset", 0.0))
        scenes.append(
            Scene(
                id=it.id,
                datetime=it.properties["datetime"],
                platform=it.properties.get("platform", "unknown"),
                cloud=float(it.properties.get("eo:cloud_cover", -1)),
                nodata=float(it.properties.get("s2:nodata_pixel_percentage", 0)),
                hrefs=hrefs,
                scale=scale,
                boa_applied=bool(it.properties.get("earthsearch:boa_offset_applied", False)),
            )
        )
    return sorted(scenes, key=lambda s: s.datetime)


def tile_grid(scene: Scene) -> tuple[rasterio.Affine, int, int]:
    """the 20 m grid of the whole tile."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(scene.hrefs["nir08"]) as src:
        return src.transform, src.height, src.width


def _read(href: str, win20: Window, native_res: int, resampling) -> np.ndarray:
    """one band for the 20 m window. 10 m bands are averaged down."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        if native_res == 10:
            win = Window(win20.col_off * 2, win20.row_off * 2, win20.width * 2, win20.height * 2)
            return src.read(1, window=win, out_shape=(int(win20.height), int(win20.width)), resampling=resampling)
        return src.read(1, window=win20)


NATIVE_RES = {"green": 10, "red": 10, "nir08": 20, "swir16": 20, "swir22": 20, "scl": 20}


def download_scene(scene: Scene, win20: Window, transform20, out_dir: Path, pool: ThreadPoolExecutor) -> Path:
    """saves one scene's study window: the 5 bands plus scl."""
    out = out_dir / f"{scene.key}.tif"
    if out.exists():
        return out
    names = list(config.BANDS) + ["scl"]
    futs = {
        pool.submit(
            _read,
            scene.hrefs[n],
            win20,
            NATIVE_RES[n],
            Resampling.nearest if n == "scl" else Resampling.average,
        ): n
        for n in names
    }
    arrs = {}
    for fu in as_completed(futs):
        arrs[futs[fu]] = fu.result()
    stack = np.stack([arrs[n] for n in names]).astype(np.uint16)
    tf = rasterio.windows.transform(win20, transform20)
    tmp = out.with_suffix(".tmp.tif")
    profile = dict(
        driver="GTiff", dtype="uint16", count=stack.shape[0], height=stack.shape[1], width=stack.shape[2],
        crs=config.CRS, transform=tf, compress="deflate", predictor=2, tiled=True, blockxsize=512, blockysize=512,
        nodata=0,
    )
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(stack)
        dst.descriptions = tuple(config.BAND_NAMES + ["SCL"])
        dst.update_tags(
            scene_id=scene.id, datetime=scene.datetime, platform=scene.platform,
            cloud=str(scene.cloud), scale=json.dumps(scene.scale), boa_applied=str(scene.boa_applied),
        )
    tmp.replace(out)
    return out


def download_all(scenes: list[Scene], win20: Window, transform20, out_dir: Path, workers: int = 12) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as band_pool, ThreadPoolExecutor(max_workers=3) as scene_pool:
        futs = {scene_pool.submit(download_scene, s, win20, transform20, out_dir, band_pool): s for s in scenes}
        t0 = time.time()
        for i, fu in enumerate(as_completed(futs), 1):
            s = futs[fu]
            try:
                p = fu.result()
                mb = p.stat().st_size / 1e6
                print(f"[{i}/{len(scenes)}] {s.key} {mb:.0f} MB  ({time.time() - t0:.0f}s)", flush=True)
            except Exception as e:  # keep going; a rerun picks up whatever failed
                print(f"[{i}/{len(scenes)}] {s.key} FAILED: {e}", flush=True)


def load_scene(path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """(reflectance [5, h, w] with nan for nodata, scl [h, w], tags)."""
    with rasterio.open(path) as src:
        stack = src.read()
        tags = src.tags()
    scale = json.loads(tags["scale"])
    boa_applied = tags.get("boa_applied", "True") == "True"  # older files have no flag, and all had it applied
    dn = stack[:5].astype(np.float32)
    refl = np.empty_like(dn)
    for i, name in enumerate(config.BANDS):
        s, o = scale[name]
        refl[i] = dn[i] * s + (0.0 if boa_applied else o)
    refl[:, stack[0] == 0] = np.nan
    return refl, stack[5].astype(np.uint8), tags
