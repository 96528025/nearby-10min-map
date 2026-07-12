#!/usr/bin/env python3
"""Derive the map's circular boundary from the routed isochrone.

The product wants a clean circle rather than the jagged true isochrone, with
"about 10 minutes" as a soft requirement. The radius is chosen objectively:
the circle with the same area as the 10-minute isochrone. Writes
data/boundary.json (GeoJSON polygon, 128 vertices) which the facility
pipeline and the page use as the single boundary of record.
"""
import json
import math
import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
CENTER_LAT, CENTER_LON = 37.33484, -122.01139
M_PER_DEG_LAT = 111000
M_PER_DEG_LON = 88000   # at 37.3°N


def main():
    iso = json.loads((DATA / "isochrone.json").read_text())
    ring = iso["features"][0]["geometry"]["coordinates"][0]
    pts = [((lon - CENTER_LON) * M_PER_DEG_LON,
            (lat - CENTER_LAT) * M_PER_DEG_LAT) for lon, lat in ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2
    radius = math.sqrt(area / math.pi)

    circle = []
    for i in range(129):
        b = 2 * math.pi * i / 128
        circle.append([
            round(CENTER_LON + radius * math.sin(b) / M_PER_DEG_LON, 6),
            round(CENTER_LAT + radius * math.cos(b) / M_PER_DEG_LAT, 6),
        ])

    boundary = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%d %H:%M UTC"),
            "method": ("circle with the same area as the routed 10-minute "
                       "isochrone (Valhalla, free-flow)"),
            "center": {"lat": CENTER_LAT, "lon": CENTER_LON,
                       "name": "Apple Park ring building center"},
            "radius_m": round(radius),
            "isochrone_area_km2": round(area / 1e6, 2),
            "calibration": ("driving times to the circle edge measured "
                            "9.5-12.0 min across 8 bearings "
                            "(Valhalla route API, 2026-07-12)"),
        },
        "features": [{
            "type": "Feature",
            "properties": {"contour": "approx 10 min drive"},
            "geometry": {"type": "Polygon", "coordinates": [circle]},
        }],
    }
    (DATA / "boundary.json").write_text(json.dumps(boundary, indent=1))
    print(f"boundary.json written: radius {radius:.0f} m, "
          f"area {area/1e6:.2f} km²")


if __name__ == "__main__":
    main()
