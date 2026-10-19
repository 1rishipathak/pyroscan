"""step 8b: where to send field teams. ranks villages and 2 km blocks by burnt farmland that no
satellite detected; every burnt pixel goes to its nearest village (openstreetmap)."""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config  # noqa: E402

BLOCK = 100  # 20 m px -> 2 km
PX_HA = config.RES**2 / 1e4


def main(season: str, detections: str, top: int) -> None:
    sfx = "" if detections == "firms" else "_official"
    p = config.PROCESSED / season
    out = config.ROOT / "web" / "data" / season
    with rasterio.open(p / f"seen{sfx}.tif") as src:
        seen = src.read(1)  # 0 none, 1 burnt never detected, 2 burnt and detected
        tf, crs = src.transform, src.crs
    dist = rasterio.open(config.INTERIM / "districts.tif").read(1)
    seen = np.where(dist > 0, seen, 0)
    h, w = seen.shape
    nby, nbx = h // BLOCK, w // BLOCK
    crop = (slice(0, nby * BLOCK), slice(0, nbx * BLOCK))

    def blocks(a):
        return a[crop].reshape(nby, BLOCK, nbx, BLOCK).sum(axis=(1, 3))

    unseen_ha = blocks((seen == 1).astype(np.int32)) * PX_HA
    burnt_ha = blocks((seen > 0).astype(np.int32)) * PX_HA
    # majority district per block
    dcount = np.stack([blocks((dist == i + 1).astype(np.int32)) for i in range(len(config.DISTRICTS))])
    dmaj = dcount.argmax(0)
    inside = dcount.max(0) > BLOCK * BLOCK * 0.25

    # detections per block
    fp = pd.read_parquet(p / f"firms_matched{sfx}.parquet")
    rr, cc = rasterio.transform.rowcol(tf, fp.x.values, fp.y.values)
    rr, cc = np.asarray(rr) // BLOCK, np.asarray(cc) // BLOCK
    ok = (rr >= 0) & (rr < nby) & (cc >= 0) & (cc < nbx)
    det = np.zeros((nby, nbx), np.int32)
    np.add.at(det, (rr[ok], cc[ok]), 1)

    places = json.loads((config.RAW / "osm" / "places.json").read_text(encoding="utf-8"))
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    px_, py_ = to_utm.transform([q["lon"] for q in places], [q["lat"] for q in places])
    tree = cKDTree(np.column_stack([px_, py_]))

    rows = []
    for by, bx in zip(*np.nonzero((unseen_ha > 0) & inside)):
        x0, y0 = tf * (bx * BLOCK, by * BLOCK)
        x1, y1 = tf * ((bx + 1) * BLOCK, (by + 1) * BLOCK)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        dkm, k = tree.query([cx, cy])
        lon, lat = to_ll.transform(cx, cy)
        corners = [to_ll.transform(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
        rows.append(dict(
            village=places[k]["name"], village_km=round(dkm / 1000, 1), district=config.DISTRICTS[dmaj[by, bx]],
            unseen_ha=round(float(unseen_ha[by, bx]), 1), burnt_ha=round(float(burnt_ha[by, bx]), 1),
            unseen_share=round(float(unseen_ha[by, bx] / burnt_ha[by, bx]), 3), detections=int(det[by, bx]),
            lat=round(lat, 5), lon=round(lon, 5), ring=[[round(a, 5), round(b, 5)] for a, b in corners],
        ))
    rows.sort(key=lambda r: -r["unseen_ha"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    # villages: every burnt pixel goes to its nearest village (a voronoi split), then rank villages
    rr_, cc_ = np.nonzero(seen > 0)
    xs, ys = rasterio.transform.xy(tf, rr_, cc_)
    _, near = tree.query(np.column_stack([xs, ys]))
    unseen_px = seen[rr_, cc_] == 1
    n = len(places)
    v_burnt = np.bincount(near, minlength=n) * PX_HA
    v_unseen = np.bincount(near[unseen_px], minlength=n) * PX_HA
    v_dist = np.zeros((n, len(config.DISTRICTS)))
    np.add.at(v_dist, (near, dist[rr_, cc_] - 1), 1)
    fin = fp[ok]
    _, fnear = tree.query(np.column_stack([fin.x.values, fin.y.values]))
    v_det = np.bincount(fnear, minlength=n)
    villages = [dict(village=places[k]["name"], kind=places[k]["place"], district=config.DISTRICTS[int(v_dist[k].argmax())],
                     unseen_ha=round(float(v_unseen[k]), 1), burnt_ha=round(float(v_burnt[k]), 1),
                     unseen_share=round(float(v_unseen[k] / v_burnt[k]), 3), detections=int(v_det[k]),
                     lat=round(places[k]["lat"], 5), lon=round(places[k]["lon"], 5))
                for k in np.nonzero(v_burnt > 0)[0]]
    villages.sort(key=lambda v: -v["unseen_ha"])
    for i, v in enumerate(villages, 1):
        v["rank"] = i
    tot = sum(v["unseen_ha"] for v in villages)
    top_share = sum(v["unseen_ha"] for v in villages[:top]) / tot if tot else 0
    zero_det = sum(1 for v in villages if v["detections"] == 0 and v["unseen_ha"] >= 50)
    print(f"{len(villages)} villages with burns, {tot:,.0f} ha never detected; top {top} hold {top_share:.0%}; "
          f"{zero_det} villages with 50+ ha never detected and not one detection all season")
    for v in villages[:10]:
        print(f"  #{v['rank']:2d} {v['village']:<18s} {v['district']:<8s} {v['unseen_ha']:6.0f} ha never detected of {v['burnt_ha']:6.0f}  "
              f"{v['detections']} detections")

    (out / "hotspots.json").write_text(json.dumps(dict(
        villages_with_burns=len(villages), unseen_ha=round(tot), top=top, top_share=round(top_share, 3),
        zero_detection_villages_50ha=zero_det, names="openstreetmap contributors (odbl)", block_km=BLOCK * config.RES / 1000,
        villages=villages[:top]), indent=1), encoding="utf-8")
    with (out / "hotspots.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["rank", "village", "kind", "district", "unseen_ha", "burnt_ha", "unseen_share", "detections", "lat", "lon"]
        wr = csv.writer(f)
        wr.writerow(cols)
        for v in villages:
            wr.writerow([v[k] for k in cols])
    feats = [dict(type="Feature", geometry=dict(type="Polygon", coordinates=[r["ring"]]),
                  properties=dict(rank=r["rank"], v=r["village"], u=r["unseen_ha"], b=r["burnt_ha"], d=r["detections"]))
             for r in rows if r["unseen_ha"] >= 2]
    (out / "grid.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats), separators=(",", ":")),
                                      encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--detections", default="firms", choices=["firms", "official"])
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    main(a.season, a.detections, a.top)
