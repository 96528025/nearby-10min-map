#!/usr/bin/env python3
"""Verify every landmark in landmarks.json lies inside the circular boundary.

Exits non-zero and names the offender if any landmark falls outside, so the
landmark list can never silently drift out of sync with a regenerated
boundary. Also exports point_in_polygon, the shared in-boundary test used
by the whole pipeline.
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


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


def point_in_polygon(lon, lat, geometry):
    if geometry["type"] == "Polygon":
        polys = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polys = geometry["coordinates"]
    else:
        raise ValueError(f"unsupported geometry: {geometry['type']}")
    for rings in polys:
        if point_in_ring(lon, lat, rings[0]):
            # inside outer ring; must not be inside any hole
            if not any(point_in_ring(lon, lat, hole) for hole in rings[1:]):
                return True
    return False


def main():
    iso = json.loads((DATA / "boundary.json").read_text())
    geometry = iso["features"][0]["geometry"]
    landmarks = json.loads((DATA / "landmarks.json").read_text())

    failures = []
    for lm in landmarks:
        ok = point_in_polygon(lm["lon"], lm["lat"], geometry)
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {lm['name_en']} ({lm['lat']}, {lm['lon']})")
        if not ok:
            failures.append(lm["name_en"])

    if failures:
        print(f"\n{len(failures)} landmark(s) outside the circular boundary: {failures}")
        sys.exit(1)
    print(f"\nAll {len(landmarks)} landmarks are inside the circular boundary.")


if __name__ == "__main__":
    main()
