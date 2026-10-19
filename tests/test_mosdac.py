"""the mosdac (isro insat) fire-product helpers: scan times, credentials, kml reading."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pyroscan import mosdac

FX = Path(__file__).parent / "fixtures"


def test_fire_kml_from_mosdac():
    # first four placemarks of a real 3RIMG_L2P_FIR file, with mosdac's run-on <address> text
    pts = mosdac.parse_fire_kml(FX / "insat_3rimg_03oct2025_0715_fir.kml")
    assert len(pts) == 4
    assert pts[0]["lon"] == pytest.approx(116.99907) and pts[0]["lat"] == pytest.approx(28.65371)
    assert pts[0]["t"] == datetime(2025, 10, 3, 7, 21, 40, tzinfo=timezone.utc)
    assert [(p["bt_tir1"], p["bt_mir"]) for p in pts] == [
        (296.873, 312.031), (298.434, 311.56), (297.115, 312.217), (296.507, 311.275)]


def test_fire_kml_with_plain_address():
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Folder>
      <Placemark><address>300.5, 320.25</address><TimeStamp><when>02-NOV-2025T10:41:07 UTC</when></TimeStamp>
      <Point><coordinates>75.8,30.2,0</coordinates></Point></Placemark></Folder></kml>"""
    (p,) = mosdac.parse_fire_kml(kml)
    assert (p["lon"], p["lat"], p["bt_tir1"], p["bt_mir"]) == (75.8, 30.2, 300.5, 320.25)
    assert p["t"].hour == 10


def test_scan_time_comes_from_the_file_name():
    t = mosdac.granule_time("3RIMG_19OCT2026_0345_L2P_FIR_V01R00.kml")
    assert t == datetime(2026, 10, 19, 3, 45, tzinfo=timezone.utc)
    assert mosdac.granule_time("3SIMG_02NOV2025_1230_L2P_FIR_V01R00.kml").hour == 12
    with pytest.raises(ValueError):
        mosdac.granule_time("not_a_granule.kml")


def test_creds_file_is_read_without_echoing(tmp_path):
    f = tmp_path / "creds.txt"
    f.write_text("# mosdac\nUsername = 'someone'\npassword: s3cr=t\n", encoding="utf-8")
    assert mosdac.read_creds(f) == ("someone", "s3cr=t")
    f.write_text("username=someone\n", encoding="utf-8")
    with pytest.raises(KeyError) as e:
        mosdac.read_creds(f)
    assert "password" in str(e.value) and "someone" not in str(e.value)
