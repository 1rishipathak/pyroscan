"""step 1: study window, district raster, cropland mask, scene list.

run: .venv/Scripts/python.exe scripts/01_prepare_aoi.py --season 2025
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds, Window
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, s2  # noqa: E402


def main(season: str) -> None:
    config.INTERIM.mkdir(parents=True, exist_ok=True)
    config.PROCESSED.mkdir(parents=True, exist_ok=True)

    start, end = config.SEASONS[season]
    scenes = s2.search(start, end)
    print(f"{len(scenes)} scenes for {config.MGRS_TILE} {start}..{end}")
    out_dir = config.s2_dir(season)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scenes.json").write_text(json.dumps([s.__dict__ for s in scenes], indent=1), encoding="utf-8")

    aoi_path = config.INTERIM / "aoi.json"
    if aoi_path.exists():
        print("aoi.json exists, keeping the same window")
        return

    # districts -> utm, buffered bounds, snapped to the tile's 20 m grid
    adm = gpd.read_file(config.BOUNDARIES)
    d = adm[adm["shapeName"].isin(config.DISTRICTS)].copy()
    assert len(d) == len(config.DISTRICTS), d["shapeName"].tolist()
    d = d[["shapeName", "geometry"]].rename(columns={"shapeName": "district"}).reset_index(drop=True)
    d["district_id"] = np.arange(1, len(d) + 1)
    d_utm = d.to_crs(config.CRS)
    minx, miny, maxx, maxy = d_utm.total_bounds
    pad = 1000
    tf, th, tw = s2.tile_grid(scenes[0])
    win = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, transform=tf)
    win = win.round_offsets().round_lengths()
    # clip to the tile
    c0, r0 = max(0, int(win.col_off)), max(0, int(win.row_off))
    c1, r1 = min(tw, int(win.col_off + win.width)), min(th, int(win.row_off + win.height))
    win = Window(c0, r0, c1 - c0, r1 - r0)
    wtf = rasterio.windows.transform(win, tf)
    h, w = int(win.height), int(win.width)
    print(f"window {w} x {h} px at {config.RES} m = {w * h * config.RES**2 / 1e6:.0f} km2")

    aoi = dict(
        tile=config.MGRS_TILE, crs=config.CRS, res=config.RES,
        window=[c0, r0, w, h], tile_transform=list(tf)[:6], transform=list(wtf)[:6], shape=[h, w],
    )
    aoi_path.write_text(json.dumps(aoi, indent=1), encoding="utf-8")

    # how much of each district the tile actually sees
    tile_box = box(*rasterio.transform.array_bounds(th, tw, tf))  # (west, south, east, north)
    d_utm["coverage"] = d_utm.geometry.intersection(tile_box).area / d_utm.geometry.area
    d_utm["area_km2"] = d_utm.geometry.area / 1e6
    print(d_utm[["district", "area_km2", "coverage"]])
    d_out = d_utm.to_crs("EPSG:4326")
    d_out.to_file(config.PROCESSED / "districts.geojson", driver="GeoJSON")

    profile = dict(driver="GTiff", height=h, width=w, count=1, crs=config.CRS, transform=wtf, compress="deflate")
    dist_r = rasterize(
        [(g, int(i)) for g, i in zip(d_utm.geometry, d_utm["district_id"])],
        out_shape=(h, w), transform=wtf, fill=0, dtype="uint8",
    )
    # pixels outside the tile footprint get no district
    with rasterio.open(config.INTERIM / "districts.tif", "w", dtype="uint8", **profile) as dst:
        dst.write(dist_r, 1)

    # esa worldcover, mode-resampled onto our 20 m grid
    wc = np.zeros((h, w), np.uint8)
    with rasterio.Env(**s2.GDAL_ENV):
        for t in config.WORLDCOVER_TILES:
            url = config.WORLDCOVER_URL.format(tile=t)
            with rasterio.open(url) as src, WarpedVRT(
                src, crs=config.CRS, transform=wtf, width=w, height=h, resampling=Resampling.mode, nodata=0
            ) as vrt:
                part = vrt.read(1)
            wc = np.where(wc == 0, part, wc)
            print(f"worldcover {t}: {np.count_nonzero(part)} px")
    with rasterio.open(config.INTERIM / "worldcover.tif", "w", dtype="uint8", **profile) as dst:
        dst.write(wc, 1)
    crop = (wc == 40) & (dist_r > 0)
    print(f"cropland inside districts: {crop.sum() * config.RES**2 / 1e6:.0f} km2")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    main(ap.parse_args().season)
