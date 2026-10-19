"""step 8: everything the dashboard needs, small enough to commit to git.

  web/data/<season>/overlay_<k>.png   burn map up to scene k, web mercator, transparent
  web/data/<season>/overlay.json      image bounds + scene dates
  web/data/<season>/summary.json      headline numbers, per district, official counts
  web/data/<season>/timeline.json     daily: official count, firms, burnt ha, unseen ha, delhi aqi
  web/data/<season>/scars.geojson     the largest scars (seen and unseen) with before/after chips
  web/data/<season>/firms.geojson     official-satellite detections in the study area
  web/data/<season>/hours.json        when the official satellites see fires (hour of day, ist)
  web/data/districts.geojson

run: .venv/Scripts/python.exe scripts/08_web_assets.py --season 2025 [--chips 160]
"""
import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pyroscan import config, emissions, s2  # noqa: E402

WEB = config.ROOT / "web" / "data"
SEEN_RGBA = (42, 120, 214, 235)  # detected (#2a78d6): a satellite saw this burn
UNSEEN_RGBA = (235, 104, 52, 245)  # undetected (#eb6834): no satellite saw it
NEW_GLOW = 40  # brighter alpha boost for burns new on the selected date


def overlays(season: str, out: Path, suffix: str = "") -> dict:
    p = config.PROCESSED / season
    with rasterio.open(p / "burn_idx.tif") as src:
        bidx = src.read(1)
        prof = src.profile
        bounds = src.bounds
    with rasterio.open(p / f"seen{suffix}.tif") as src:
        seen = src.read(1)  # 0 none / 1 unseen / 2 seen (small blobs dropped)
    with rasterio.open(config.INTERIM / "districts.tif") as src:
        dist = src.read(1)
    bidx = np.where((seen > 0) & (dist > 0), bidx, 0)  # only the study districts, so the map matches the numbers
    dst_crs = "EPSG:3857"
    tf, w, h = calculate_default_transform(prof["crs"], dst_crs, prof["width"], prof["height"], *bounds, resolution=30)
    b3857 = np.zeros((h, w), np.uint8)
    s3857 = np.zeros((h, w), np.uint8)
    reproject(bidx, b3857, src_transform=prof["transform"], src_crs=prof["crs"], dst_transform=tf, dst_crs=dst_crs,
              resampling=Resampling.nearest)
    reproject(seen, s3857, src_transform=prof["transform"], src_crs=prof["crs"], dst_transform=tf, dst_crs=dst_crs,
              resampling=Resampling.nearest)
    west, north = tf * (0, 0)
    east, south = tf * (w, h)
    lon0, lat0, lon1, lat1 = transform_bounds(dst_crs, "EPSG:4326", west, south, east, north)
    scenes = json.loads((p / "scenes.json").read_text(encoding="utf-8"))
    # 4-colour palette png (index 0 transparent): several times smaller than rgba
    palette = [0, 0, 0, *SEEN_RGBA[:3], *UNSEEN_RGBA[:3], *SEEN_RGBA[:3], *UNSEEN_RGBA[:3]]
    alpha = bytes([0, SEEN_RGBA[3], UNSEEN_RGBA[3], 255, 255])
    for k in range(1, len(scenes) + 1):
        m = (b3857 > 0) & (b3857 <= k)
        new = b3857 == k
        idx = np.zeros((h, w), np.uint8)
        idx[m & (s3857 == 2)] = 1
        idx[m & (s3857 == 1)] = 2
        idx[new & (s3857 == 2)] = 3  # burnt on this very pass: fully opaque
        idx[new & (s3857 == 1)] = 4
        im = Image.fromarray(idx, "P")
        im.putpalette(palette + [0] * (768 - len(palette)))
        im.save(out / f"overlay_{k}.png", optimize=True, transparency=alpha)
    return dict(bounds=[[lat0, lon0], [lat1, lon1]], scenes=[s["datetime"][:10] for s in scenes], n=len(scenes))


def spread_daily(scars: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """a scar first seen on date b, last clear look a, burned somewhere in (a, b]. spread its area evenly."""
    days = pd.date_range(start, end, freq="D")
    burnt = pd.Series(0.0, index=days)
    unseen = pd.Series(0.0, index=days)
    for a, b, ha, s in zip(scars.t_pre.dt.normalize(), scars.t_post.dt.normalize(), scars.area_ha, scars.seen):
        a = a.tz_localize(None) + pd.Timedelta(days=1)
        b = b.tz_localize(None)
        if a > b:
            a = b
        rng = pd.date_range(a, b, freq="D")
        rng = rng[(rng >= days[0]) & (rng <= days[-1])]
        if len(rng) == 0:
            continue
        burnt[rng] += ha / len(rng)
        if not s:
            unseen[rng] += ha / len(rng)
    return pd.DataFrame(dict(burnt_ha=burnt, unseen_ha=unseen))


def chips(season: str, scars: pd.DataFrame, out: Path) -> list[str]:
    """true-colour 10 m before/after chips straight from the sentinel-2 cogs."""
    scenes = {s2.Scene(**s).key: s2.Scene(**s) for s in json.loads((config.s2_dir(season) / "scenes.json").read_text(encoding="utf-8"))}
    keys = json.loads((config.PROCESSED / season / "scenes.json").read_text(encoding="utf-8"))
    aoi = json.loads((config.INTERIM / "aoi.json").read_text(encoding="utf-8"))
    c0, r0 = aoi["window"][0], aoi["window"][1]
    half = 60  # 10 m px -> 1.2 km chip
    done = []
    with rasterio.Env(**s2.GDAL_ENV):
        handles = {}
        for _, s in scars.iterrows():
            if all((out / f"chip_{s.scar_id}_{t}.jpg").exists() for t in ("before", "after")):
                done.append(int(s.scar_id))
                continue
            for tag, k in (("before", int(s.k_pre)), ("after", int(s.k_post))):
                key = keys[k - 1]["key"]
                if key not in handles:
                    handles[key] = rasterio.open(scenes[key].hrefs["visual"])
                cx = int((c0 + s.col) * 2)
                cy = int((r0 + s.row) * 2)
                img = handles[key].read(window=Window(cx - half, cy - half, 2 * half, 2 * half), boundless=True)
                img = np.clip(img.transpose(1, 2, 0).astype(np.float32) * 1.6, 0, 255).astype(np.uint8)
                Image.fromarray(img).resize((240, 240), Image.NEAREST).save(out / f"chip_{s.scar_id}_{tag}.jpg", quality=85)
            done.append(int(s.scar_id))
        for hd in handles.values():
            hd.close()
    return done


def write_punjab_outline() -> None:
    """the state outline for the dashboard's locator map: simplified, coordinates to 0.001 deg (a few kb)."""
    from shapely import set_precision
    from shapely.geometry import mapping

    from pyroscan import geo  # needs geopandas

    shape = set_precision(geo.punjab_outline(), 0.001)
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Punjab"}, "geometry": mapping(shape)}]}
    (WEB / "punjab.geojson").write_text(json.dumps(fc), encoding="utf-8")


def main(season: str, n_chips: int, detections: str = "firms") -> None:
    sfx = "" if detections == "firms" else "_official"
    out = WEB / season
    out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    p = config.PROCESSED / season
    shutil.copy(config.PROCESSED / "districts.geojson", WEB / "districts.geojson")
    write_punjab_outline()
    if (config.PROCESSED / f"news_{season}.json").exists():  # tinyfish search + agent results
        shutil.copy(config.PROCESSED / f"news_{season}.json", out / "news.json")
    else:  # no news run for this season (the 2021 control year): an empty file keeps the browser console clean
        (out / "news.json").write_text(json.dumps({"season": season, "items": []}), encoding="utf-8")

    meta = overlays(season, out, sfx)
    (out / "overlay.json").write_text(json.dumps(meta), encoding="utf-8")
    print("overlays:", meta["n"])

    stats = json.loads((p / f"stats{sfx}.json").read_text(encoding="utf-8"))
    stats_rule = json.loads((p / f"stats_rule{sfx}.json").read_text(encoding="utf-8")) if (p / f"stats_rule{sfx}.json").exists() else None
    stats_off = json.loads((p / "stats_official.json").read_text(encoding="utf-8")) if sfx == "" and (p / "stats_official.json").exists() else None
    off = json.loads((config.PROCESSED / f"official_{season}.json").read_text(encoding="utf-8"))
    aqi = json.loads((config.PROCESSED / f"aqi_{season}.json").read_text(encoding="utf-8"))
    y = int(season)
    dc = off.get("district_cumulative", {})
    # geoboundaries' sangrur still contains malerkotla (split off in 2021), so add them up
    official = {
        "Sangrur": (dc.get("Sangrur", {}).get(str(y)) or dc.get("Sangrur", {}).get(y) or 0)
        + (dc.get("Malerkotla", {}).get(str(y)) or dc.get("Malerkotla", {}).get(y) or 0),
        "Barnala": dc.get("Barnala", {}).get(str(y)) or dc.get("Barnala", {}).get(y) or 0,
    }
    # the years chart uses whichever bulletin covers the most years (2021's only compares 2020-21)
    # full-season bulletins only: the live season's table covers 15 sep to today, not the whole season
    offs = [json.loads((config.PROCESSED / f"official_{yy}.json").read_text(encoding="utf-8"))
            for yy in config.SEASONS if yy != max(config.SEASONS) and (config.PROCESSED / f"official_{yy}.json").exists()]
    # the live season compares the same 15 sep - today window across years, from its own bulletin
    if season == max(config.SEASONS) or not offs:
        off_hist = off
    else:
        off_hist = max(offs, key=lambda o: (len(o.get("years") or []), o.get("date") or ""))
    dc_hist = off_hist.get("district_cumulative", {})
    years = {}
    for yy in off_hist.get("years", []):
        years[str(yy)] = dict(
            Sangrur=(dc_hist.get("Sangrur", {}).get(str(yy)) or 0) + (dc_hist.get("Malerkotla", {}).get(str(yy)) or 0),
            Barnala=dc_hist.get("Barnala", {}).get(str(yy)) or 0,
            Punjab=dc_hist.get("Punjab total", {}).get(str(yy)) or 0,
        )
    summary = dict(season=season, live=season == max(config.SEASONS), detections=detections,
                   official_date=off.get("date"), districts={}, study_area={}, official_source=off.get("source"),
                   official_bulletin=off.get("bulletin"), official_via=off.get("via"), years=years,
                   punjab_official_season=off.get("season_punjab"), method=dict(
                       min_scar_ha=stats["min_scar_ha"], geo_err_m=stats["geo_err_m"]))
    for d in config.DISTRICTS:
        s = dict(stats["districts"][d])
        s["official_events"] = official[d]
        s["ha_per_official_event"] = round(s["burnt_ha"] / official[d], 1) if official[d] else None
        if stats_rule:
            s["rule_burnt_ha"] = stats_rule["districts"][d]["burnt_ha"]
        summary["districts"][d] = s
    sa = dict(stats["study_area"])
    sa["official_events"] = sum(official.values())
    sa["ha_per_official_event"] = round(sa["burnt_ha"] / sa["official_events"], 1)
    if stats_rule:
        sa["rule_burnt_ha"] = stats_rule["study_area"]["burnt_ha"]
        sa["rule_unseen_share"] = stats_rule["study_area"]["unseen_share"]
    if stats_off:
        sa["unseen_share_vs_official_list"] = stats_off["study_area"]["unseen_share"]
        sa["unseen_ha_vs_official_list"] = stats_off["study_area"]["unseen_ha"]
        for d in config.DISTRICTS:
            summary["districts"][d]["unseen_share_vs_official_list"] = stats_off["districts"][d]["unseen_share"]
    summary["study_area"] = sa
    # the trail behind every official number: which document, read how, when
    def last_call(url_part: str) -> str | None:
        t = None
        if (config.PROCESSED / "tinyfish_log.jsonl").exists():
            for line in (config.PROCESSED / "tinyfish_log.jsonl").open(encoding="utf-8"):
                r = json.loads(line)
                if r.get("ok") and url_part in r.get("what", ""):
                    t = r["t"]
        return t

    prov = []
    b = off.get("bulletin") or {}
    if b:
        prov.append(dict(numbers="official fire counts, district totals since 2020, daily punjab counts",
                         source=f"creams (icar-iari) bulletin, {b.get('date')}", url=b.get("url"), via=off.get("via"),
                         at=last_call(b.get("url", "")[-40:])))
    bl = config.INTERIM / f"official_bulletins_{season}.csv"
    if bl.exists():
        bdf = pd.read_csv(bl)
        prov.append(dict(numbers=f"gps of every official fire event ({int(bdf.listed.sum()):,} events)",
                         source=f"all {len(bdf)} daily creams bulletins of the season", url="https://creams.iari.res.in/?page_id=1123",
                         via=", ".join(f"{k} ({v})" for k, v in bdf.via.value_counts().items()), at=None))
    av = [v.get("via") for v in aqi["days"].values() if v and "aqi" in v]
    prov.append(dict(numbers="delhi aqi, every day of the season", source=f"{len(av)} cpcb daily aqi bulletins (pdf)",
                     url="https://cpcb.gov.in/AQI_Bulletin.php",
                     via=", ".join(f"{k} ({av.count(k)})" for k in sorted(set(av))), at=None))
    nf = config.PROCESSED / f"news_{season}.json"
    if nf.exists():
        items = json.loads(nf.read_text(encoding="utf-8")).get("items", [])
        prov.append(dict(numbers="enforcement and court stories", source=f"{len(items)} news stories",
                         url=None, via="tinyfish search", at=None))
        nfx = sum(1 for x in items if x.get("facts"))
        if nfx:
            prov.append(dict(numbers="firs, red entries, fines, notices to officials", source=f"{nfx} enforcement stories, read one by one",
                             url=None, via="tinyfish web agent (structured output)", at=None))
    prov.append(dict(numbers="burnt farmland, burn dates", source=f"{meta['n']} sentinel-2 l2a passes, tile 43REP",
                     url="https://earth-search.aws.element84.com/v1", via="cloud-optimised geotiff reads", at=None))
    prov.append(dict(numbers="fire detections (viirs s-npp, noaa-20, modis)",
                     source="nasa firms yearly country files" if detections == "firms" else "official gps events (above)",
                     url="https://firms.modaps.eosdis.nasa.gov/country/" if detections == "firms" else None, via="download", at=None))
    summary["provenance"] = prov

    # invisible smoke: pm2.5 from all burns and from the burns the official count never saw
    def smoke(x: dict) -> dict:
        return dict(pm25_t=emissions.pm25_tonnes(x["burnt_ha"]), pm25_unseen_t=emissions.pm25_tonnes(x["unseen_ha"]))

    summary["smoke"] = dict(study_area=smoke(summary["study_area"]),
                            districts={d: smoke(summary["districts"][d]) for d in config.DISTRICTS},
                            kg_per_ha={c: round(emissions.pm25_kg_per_ha(c), 1) for c in ("low", "mid", "high")})
    # PyroScan's measured burnt area for every finished season, so the years chart can put the
    # official count next to what actually burnt (live season excluded: still running)
    burnt_by_year = {}
    for yy in config.SEASONS:
        f = config.PROCESSED / yy / "stats.json"
        if yy != max(config.SEASONS) and f.exists():
            st_ = json.loads(f.read_text(encoding="utf-8"))
            burnt_by_year[yy] = dict(burnt_ha=st_["study_area"]["burnt_ha"], unseen_share=st_["study_area"]["unseen_share"])
    # the live view's year bars cover 15 sep - today only, so no full-season line there
    summary["burnt_by_year"] = {} if season == max(config.SEASONS) else burnt_by_year
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(sa, indent=1))

    # timeline
    scars = pd.read_parquet(p / f"scars{sfx}.parquet")
    sel = scars[scars.district.isin(config.DISTRICTS)]
    start, end = (date.fromisoformat(d) for d in config.SEASONS[season])
    daily = spread_daily(sel, start, end)
    fp = pd.read_parquet(p / f"firms_matched{sfx}.parquet")
    fsel = fp[fp.district.isin(config.DISTRICTS)]
    fdaily = (fsel.dt_utc + pd.Timedelta(hours=5, minutes=30)).dt.strftime("%Y-%m-%d").value_counts()
    rows = []
    for d0 in pd.date_range(start, end, freq="D"):
        k = d0.strftime("%Y-%m-%d")
        a = aqi["days"].get(k) or {}
        o = off.get("daily", {}).get(k) or {}
        rows.append(dict(date=k, burnt_ha=round(float(daily.burnt_ha[d0]), 1),
                         unseen_ha=round(float(daily.unseen_ha[d0]), 1),
                         firms=int(fdaily.get(k, 0)), official_punjab=o.get("punjab"),
                         aqi_delhi=a.get("aqi"), aqi_cat=a.get("category")))
    (out / "timeline.json").write_text(json.dumps(rows), encoding="utf-8")

    # when do the official satellites look? (ist hour of each detection, study area)
    hrs = (fsel.dt_utc + pd.Timedelta(hours=5, minutes=30)).dt.hour.value_counts().reindex(range(24), fill_value=0)
    (out / "hours.json").write_text(json.dumps(dict(hours=list(range(24)), detections=hrs.tolist(),
                                                    by_source=fsel.groupby("source").size().to_dict())), encoding="utf-8")

    # fire detections as compact rows: [lon, lat, ist time, source index, matched a scar]
    srcs = sorted(fsel.source.unique().tolist())
    rows_ = [[round(r.longitude, 4), round(r.latitude, 4),
              (r.dt_utc + pd.Timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M"), srcs.index(r.source),
              int(bool(r.matched_scar))] for r in fsel.itertuples()]
    (out / "firms.json").write_text(json.dumps(dict(sources=srcs, rows=rows_), separators=(",", ":")), encoding="utf-8")

    # rasters for the point check (/api/check) and the evidence list
    rd = out / "rasters"
    rd.mkdir(exist_ok=True)
    for name in ["burn_idx", "burn_pre_idx", "clear_looks"]:
        shutil.copy(p / f"{name}.tif", rd / f"{name}.tif")
    shutil.copy(p / f"seen{sfx}.tif", rd / "seen.tif")
    shutil.copy(p / "scenes.json", rd / "scenes.json")
    ev = sel[~sel.seen].sort_values("area_ha", ascending=False)
    ev = pd.DataFrame(dict(scar_id=ev.scar_id, district=ev.district, area_ha=ev.area_ha.round(2),
                           lat=ev.lat.round(5), lon=ev.lon.round(5),
                           burnt_after=ev.t_pre.dt.strftime("%Y-%m-%d"), burnt_by=ev.t_post.dt.strftime("%Y-%m-%d")))
    ev.to_csv(out / "unseen_scars.csv", index=False)

    # largest scars, both kinds, with chips
    top_unseen = sel[~sel.seen].nlargest(int(n_chips * 0.7), "area_ha")
    top_seen = sel[sel.seen].nlargest(n_chips - len(top_unseen), "area_ha")
    top = pd.concat([top_unseen, top_seen])
    have = chips(season, top, out / "img") if n_chips else []
    feats = []
    for r in top.itertuples():
        feats.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[round(r.lon, 5), round(r.lat, 5)]),
                          properties=dict(id=int(r.scar_id), ha=round(r.area_ha, 1), seen=bool(r.seen),
                                          by=r.seen_by, n=int(r.n_detections), district=r.district,
                                          pre=str(r.t_pre)[:10], post=str(r.t_post)[:10],
                                          chips=int(r.scar_id) in have)))
    (out / "scars.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats)), encoding="utf-8")
    print(f"chips for {len(have)} scars")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025")
    ap.add_argument("--chips", type=int, default=160)
    ap.add_argument("--detections", default="firms", choices=["firms", "official"])
    a = ap.parse_args()
    main(a.season, a.chips, a.detections)
