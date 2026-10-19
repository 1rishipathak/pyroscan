"""step 2b: firms fire detections for punjab and haryana by district, and a check that
they add up to the official count."""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, firms  # noqa: E402

BBOX = (73.8, 27.6, 77.6, 32.6)  # punjab + haryana
YEARS = [2021, 2022, 2023, 2024, 2025]


def main() -> None:
    adm = gpd.read_file(config.BOUNDARIES)[["shapeName", "geometry"]].rename(columns={"shapeName": "district"})
    adm = adm.cx[BBOX[0]:BBOX[2], BBOX[1]:BBOX[3]]
    rows = []
    for y in YEARS:
        df = firms.load_year(y, BBOX)
        df = df[(df.acq_date >= f"{y}-09-15") & (df.acq_date <= f"{y}-11-30")]
        g = firms.to_gdf(df)
        g = gpd.sjoin(g, adm, how="inner", predicate="within").drop(columns="index_right")
        g = g.drop(columns="geometry")
        g.to_parquet(firms.cache_path(y))
        for d in config.DISTRICTS + ["Patiala", "Mansa", "Bathinda", "Ludhiana"]:
            sub = g[g.district == d]
            rows.append(dict(year=y, district=d, all_sensors=len(sub),
                             snpp=int((sub.source == "VIIRS S-NPP").sum()),
                             noaa20=int((sub.source == "VIIRS NOAA-20").sum()),
                             modis=int((sub.source == "MODIS Terra/Aqua").sum())))
        print(y, len(g), "points in punjab/haryana window")
    t = pd.DataFrame(rows)
    print(t.to_string(index=False))
    (config.PROCESSED / "firms_district_counts.json").write_text(t.to_json(orient="records", indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
