"""nasa firms active-fire points: the same polar-orbiting satellites the official count uses.

files are the public yearly country csvs from firms.modaps.eosdis.nasa.gov/country (no key needed).
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import config

SOURCES = {
    "viirs-snpp": "VIIRS S-NPP",
    "viirs-jpss1": "VIIRS NOAA-20",
    "modis": "MODIS Terra/Aqua",
}
COLS = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument",
        "confidence", "frp", "daynight", "type", "scan", "track"]


def load_year(year: int, bbox: tuple[float, float, float, float], sources=SOURCES) -> pd.DataFrame:
    """all fire points inside bbox (lon_min, lat_min, lon_max, lat_max) for one year."""
    frames = []
    for key, label in sources.items():
        f = config.FIRMS_DIR / f"{key}_{year}_India.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f, usecols=lambda c: c in COLS, dtype={"acq_time": str, "confidence": str})
        df = df[
            df.longitude.between(bbox[0], bbox[2]) & df.latitude.between(bbox[1], bbox[3])
        ].copy()
        df["source"] = label
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    # type 0 = presumed vegetation fire. drop static industrial hot spots etc.
    if "type" in df:
        df = df[df["type"].fillna(0) == 0]
    t = df.acq_time.str.zfill(4)
    df["dt_utc"] = pd.to_datetime(df.acq_date + " " + t.str[:2] + ":" + t.str[2:], utc=True)
    df["hour_ist"] = (df.dt_utc + pd.Timedelta(hours=5, minutes=30)).dt.hour
    return df.reset_index(drop=True)


def to_gdf(df: pd.DataFrame, crs: str | None = None) -> gpd.GeoDataFrame:
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs="EPSG:4326")
    return g.to_crs(crs) if crs else g


def cache_path(year: int) -> Path:
    return config.INTERIM / f"firms_{year}.parquet"
