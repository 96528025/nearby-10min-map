#!/usr/bin/env python3
"""Write the map's boundary of record from the routed isochrone.

The displayed boundary is the true 10-minute Valhalla isochrone itself —
every component and every hole — not a circle derived from its area. The
preregistered benchmark (reports/accuracy, docs/DECISIONS.md D-2) found the
former equal-area circle not fit for purpose (macro false inclusion 9.1 %,
macro false exclusion 24.7 % against Valhalla's own geometry), so the circle
approximation is retired on the bundled and the live path alike.

Reads data/isochrone.json — a recorded Valhalla response whose metadata names
its source, origin, costing and fetch time — and writes data/boundary.json
through the same `pipeline.boundary_from_isochrone` the live API uses, so
both paths share one boundary representation and one predicate. The facility
pipeline, the landmark check and the page use boundary.json as the single
boundary of record.
"""
import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

import pipeline  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
CENTER_LAT, CENTER_LON = 37.33484, -122.01139
CENTER_NAME = "Apple Park ring building center"


def build_boundary(iso):
    """Boundary FeatureCollection for a recorded Valhalla response."""
    iso_meta = iso.get("metadata") or {}
    origin = iso_meta.get("origin") or {}
    lat = origin.get("lat", CENTER_LAT)
    lon = origin.get("lon", CENTER_LON)
    boundary = pipeline.boundary_from_isochrone(
        iso, lat, lon, CENTER_NAME,
        minutes=iso_meta.get("contour_minutes", 10),
        denoise=iso_meta.get("denoise", pipeline.ISOCHRONE_DENOISE),
    )
    # Carry the recorded response's own provenance so the bundled artifact
    # says where its geometry came from, not merely when it was rewritten.
    meta = boundary["metadata"]
    meta["source"] = iso_meta.get("source", meta["source"])
    meta["traffic"] = iso_meta.get("traffic", meta["traffic"])
    meta["isochrone_generated_utc"] = iso_meta.get("generated_utc")
    meta["isochrone_origin"] = origin.get("name")
    meta["isochrone_file"] = "data/isochrone.json"
    return boundary


def main():
    iso = json.loads((DATA / "isochrone.json").read_text())
    boundary = build_boundary(iso)
    (DATA / "boundary.json").write_text(json.dumps(boundary, indent=1))
    meta = boundary["metadata"]
    print(f"boundary.json written: {meta['geometry_type']}, "
          f"{meta['geometry_components']} component(s), "
          f"{meta['geometry_holes']} hole(s), "
          f"area {meta['isochrone_area_km2']} km²")


if __name__ == "__main__":
    main()
