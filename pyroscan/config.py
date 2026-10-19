"""paths and settings shared by every script."""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
CHECKPOINTS = ROOT / "checkpoints"

# sentinel-2 l2a on aws (element84 earth search), no login needed
STAC_URL = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"

# one tile covers both districts
MGRS_TILE = "43REP"
CRS = "EPSG:32643"  # utm 43n
RES = 20  # metres, the native resolution of the swir bands

# earth search asset -> band. this order is the channel order everywhere
BANDS = {
    "green": "B03",
    "red": "B04",
    "nir08": "B8A",
    "swir16": "B11",
    "swir22": "B12",
}
BAND_NAMES = list(BANDS.values())

SEASONS = {
    "2021": ("2021-10-01", "2021-11-30"),  # control year, before burning moved to the afternoon
    "2025": ("2025-10-01", "2025-11-30"),
    "2026": ("2026-10-01", min(date.today(), date(2026, 11, 30)).isoformat()),  # live season, up to today
}

# geoboundaries still has the pre-2021 sangrur, which includes malerkotla
DISTRICTS = ["Sangrur", "Barnala"]
PUNJAB_DISTRICTS = ["Amritsar", "Barnala", "Bathinda", "Faridkot", "Fatehgarh Sahib", "Fazilka", "Firozpur", "Gurdaspur",
                    "Hoshiarpur", "Jalandhar", "Kapurthala", "Ludhiana", "Mansa", "Moga", "Muktsar", "Pathankot", "Patiala",
                    "Rupnagar", "Sahibzada Ajit Singh Nagar", "Sangrur", "Shahid Bhagat Singh Nagar", "Tarn Taran"]

# esa worldcover 2021, class 40 = cropland
WORLDCOVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)
WORLDCOVER_TILES = ["N30E075", "N27E075"]

BOUNDARIES = RAW / "boundaries" / "geoBoundaries-IND-ADM2.geojson"
FIRMS_DIR = RAW / "firms"


def s2_dir(season: str) -> Path:
    return INTERIM / "s2" / season
