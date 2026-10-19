"""the official-document readers, on real bulletin text from three different years."""
import base64
from pathlib import Path

from pyroscan import sources

FX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


def test_creams_2025_final_bulletin():
    p = sources.parse_creams(read("creams_2025-11-30.txt"))
    assert p["date"] == "2025-11-30"
    assert (p["day_punjab"], p["season_punjab"]) == (2, 5114)
    dc = p["district_cumulative"]
    assert dc["Sangrur"][2025] == 695 and dc["Malerkotla"][2025] == 92 and dc["Barnala"][2025] == 105
    assert dc["Punjab total"][2021] == 71304
    assert len(p["daily"]) == 77 and sum(d["punjab"] for d in p["daily"].values()) == 5114
    assert len(p["punjab_events"]) == 2 and all(29 < e["lat"] < 33 for e in p["punjab_events"])


def test_creams_2026_live_bulletin():
    p = sources.parse_creams(read("creams_2026-10-17.txt"))
    assert p["date"] == "2026-10-17"
    assert (p["day_punjab"], p["season_punjab"]) == (29, 220)
    assert p["district_cumulative"]["Sangrur"][2026] == 3
    assert len(p["punjab_events"]) == 29  # every event of the day, with gps


def test_creams_2021_two_year_table():
    """2021 compares only 2020-21, uses an odd dash, and lists haryana's table first."""
    p = sources.parse_creams(read("creams_2021-11-30.txt"))
    dc = p["district_cumulative"]
    assert dc["Sangrur"] == {2020: 11727, 2021: 9389}
    assert dc["Barnala"][2021] == 4326
    assert dc["Punjab total"][2021] == 71304  # not haryana's 6,987


def test_cpcb_delhi_both_layouts():
    assert sources.parse_cpcb_delhi(read("cpcb_20251105.txt")) == dict(
        category="Poor", aqi=202, pollutant="O3, PM2.5", stations="39/39")
    old = sources.parse_cpcb_delhi(read("cpcb_20211105.txt"))
    assert (old["category"], old["aqi"], old["stations"]) == ("Severe", 462, "35")


def test_cpcb_url_encodes_the_pdf_path():
    from datetime import date

    u = sources.cpcb_url(date(2025, 11, 5))
    assert base64.b64decode(u.split("id=")[1]).decode() == "Downloads/AQI_Bulletin_20251105.pdf"
