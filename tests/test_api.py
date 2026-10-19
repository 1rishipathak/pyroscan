"""the dashboard api, against the precomputed data shipped in web/data (no network, no tinyfish key)."""
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pyproj import Transformer

from api.main import WEB, app

client = TestClient(app)


def test_health():
    r = client.get("/api/health").json()
    assert r["ok"] is True and "2025" in r["seasons"]


def test_dashboard_and_season_data_are_served():
    assert "PyroScan" in client.get("/").text
    s = client.get("/data/2025/summary.json").json()
    sa = s["study_area"]
    assert sa["official_events"] == 892 and sa["burnt_ha"] > 10_000 and 0 < sa["unseen_share"] < 1


def test_insat_fire_product_summary_is_served():
    # isro's insat-3dr fire product, every 30-minute scan of the 2025 season, over punjab
    i = client.get("/data/2025/insat.json").json()
    assert i["dataset"] == "3RIMG_L2P_FIR" and i["every_min"] == 30 and i["scans"] > 2500
    assert sum(i["punjab"]["by_hour"]) == i["punjab"]["pixels"] and i["study"]["pixels"] == len(i["rows"])
    assert i["punjab_hourly"]["pixels"] <= i["punjab"]["pixels"]


def _a_burnt_pixel(season: str) -> tuple[float, float]:
    with rasterio.open(WEB / "data" / season / "rasters" / "burn_idx.tif") as src:
        b = src.read(1)
        rows, cols = np.nonzero(b)
        i = len(rows) // 2
        x, y = src.xy(rows[i], cols[i])
        lon, lat = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return lat, lon


@pytest.mark.parametrize("season", ["2021", "2025"])
def test_field_check_finds_a_burn(season):
    lat, lon = _a_burnt_pixel(season)
    r = client.get(f"/api/check?lat={lat}&lon={lon}&season={season}").json()
    assert r["inside"] and r["burnt"] and r["burnt_after"] <= r["burnt_by"]


def test_field_check_outside_the_study_area():
    assert client.get("/api/check?lat=28.61&lon=77.21").json() == {"inside": False}  # delhi


def test_evidence_export():
    r = client.get("/api/export/2025/unseen.csv")
    assert r.status_code == 200
    assert r.text.splitlines()[0] == "scar_id,district,area_ha,lat,lon,burnt_after,burnt_by"
