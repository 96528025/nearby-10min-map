#!/usr/bin/env python3
"""Shared boundary predicate and geometry helpers, plus the landmark check.

Exports the single in-boundary test used by the whole pipeline
(`point_in_polygon`) together with the geometry helpers every path shares:

- `isochrone_geometry`: Valhalla response -> one Polygon / MultiPolygon with
  every component and every interior ring (hole) preserved;
- `geometry_bbox`: the upstream POI query envelope, covering every component;
- `geometry_area_m2`: planar area with holes subtracted.

The build-time scripts (`make_boundary.py`, `fetch_facilities.py`,
`merge_overture.py`), the runtime pipeline and the tests all import these, so
the displayed boundary, the facility filter and the Overture merge cannot
drift apart.

Run as a script it verifies every landmark in landmarks.json lies inside the
boundary of record, exiting non-zero and naming the offender so the landmark
list can never silently drift out of sync with a regenerated boundary.
"""
import json
import math
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
M_PER_DEG_LAT = 111000.0


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def polygon_rings(geometry):
    """Ring lists of every component: ``[[outer, hole, ...], ...]``."""
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return list(geometry["coordinates"])
    raise ValueError(f"unsupported geometry: {geometry['type']}")


def point_in_polygon(lon, lat, geometry):
    for rings in polygon_rings(geometry):
        if point_in_ring(lon, lat, rings[0]):
            # inside outer ring; must not be inside any hole
            if not any(point_in_ring(lon, lat, hole) for hole in rings[1:]):
                return True
    return False


def isochrone_geometry(iso):
    """Collapse a Valhalla isochrone response into one boundary geometry.

    Every polygonal feature is kept: a response with several features (one per
    disconnected component) or a MultiPolygon feature becomes a single
    MultiPolygon, and interior rings (holes) are carried through untouched.
    Nothing is reduced to ``coordinates[0]``.
    """
    polygons = []
    for feature in iso.get("features") or []:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Polygon":
            polygons.append(geometry["coordinates"])
        elif geometry.get("type") == "MultiPolygon":
            polygons.extend(geometry["coordinates"])
    if not polygons:
        raise ValueError("isochrone contained no polygonal geometry")
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def geometry_bbox(geometry):
    """``(south, west, north, east)`` over every ring of every component.

    This is the upstream POI query envelope. It must contain the whole
    boundary so the exact point-in-polygon filter never operates on a
    candidate set that already lost part of the geometry.
    """
    points = [p for rings in polygon_rings(geometry) for ring in rings
              for p in ring]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lats), min(lons), max(lats), max(lons))


def _ring_area_m2(ring, lat0, lon0):
    m_lon = m_per_deg_lon(lat0)
    pts = [((p[0] - lon0) * m_lon, (p[1] - lat0) * M_PER_DEG_LAT)
           for p in ring]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def geometry_area_m2(geometry, lat0, lon0):
    """Planar area on a local equirectangular plane centred on (lat0, lon0).

    Every component's outer ring counts; every hole is subtracted.
    """
    total = 0.0
    for rings in polygon_rings(geometry):
        total += _ring_area_m2(rings[0], lat0, lon0)
        total -= sum(_ring_area_m2(hole, lat0, lon0) for hole in rings[1:])
    return total


def main():
    boundary = json.loads((DATA / "boundary.json").read_text())
    geometry = boundary["features"][0]["geometry"]
    landmarks = json.loads((DATA / "landmarks.json").read_text())

    failures = []
    for lm in landmarks:
        ok = point_in_polygon(lm["lon"], lm["lat"], geometry)
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {lm['name_en']} ({lm['lat']}, {lm['lon']})")
        if not ok:
            failures.append(lm["name_en"])

    if failures:
        print(f"\n{len(failures)} landmark(s) outside the boundary: {failures}")
        sys.exit(1)
    print(f"\nAll {len(landmarks)} landmarks are inside the boundary.")


if __name__ == "__main__":
    main()
