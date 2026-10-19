"""punjab's outline from the geoboundaries districts. pipeline only: needs geopandas."""
import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

from . import config


def punjab_shape():
    """the state as one (multi)polygon, the union of its 22 geoboundaries districts."""
    adm = gpd.read_file(config.BOUNDARIES)
    pts = adm.geometry.representative_point()
    pj = adm[adm.shapeName.isin(config.PUNJAB_DISTRICTS) & pts.x.between(73, 78) & pts.y.between(29, 33)]
    assert len(pj) == len(config.PUNJAB_DISTRICTS), sorted(set(config.PUNJAB_DISTRICTS) - set(pj.shapeName))
    return pj.geometry.union_all()


def punjab_outline(tolerance: float = 0.01) -> MultiPolygon:
    """a light outline for small maps: outer rings only (no slivers between districts), simplified."""
    shape = punjab_shape()
    parts = [Polygon(p.exterior) for p in getattr(shape, "geoms", [shape]) if p.area > 0.01]
    return MultiPolygon(parts).simplify(tolerance, preserve_topology=True)
