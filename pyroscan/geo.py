"""punjab's outline, built from the geoboundaries districts."""
import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

from . import config


def punjab_shape():
    adm = gpd.read_file(config.BOUNDARIES)
    pts = adm.geometry.representative_point()
    pj = adm[adm.shapeName.isin(config.PUNJAB_DISTRICTS) & pts.x.between(73, 78) & pts.y.between(29, 33)]
    assert len(pj) == len(config.PUNJAB_DISTRICTS), sorted(set(config.PUNJAB_DISTRICTS) - set(pj.shapeName))
    return pj.geometry.union_all()


def punjab_outline(tolerance: float = 0.01) -> MultiPolygon:
    """simplified, without the slivers between districts. good enough for small maps."""
    shape = punjab_shape()
    parts = [Polygon(p.exterior) for p in getattr(shape, "geoms", [shape]) if p.area > 0.01]
    return MultiPolygon(parts).simplify(tolerance, preserve_topology=True)
