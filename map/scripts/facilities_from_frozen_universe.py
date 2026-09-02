#!/usr/bin/env python3
"""Rebuild data/facilities.json offline from the benchmark's frozen POI universe.

Why this exists. The committed Apple Park facilities were fetched over the
bounding box of the retired equal-area circle, and the true isochrone extends
up to about 3.9 km beyond that box, so merely re-filtering the old snapshot
would leave part of the boundary structurally empty. The benchmark run of
record fetched POIs once over a union envelope that contains the whole
isochrone, deduplicated them with the production rules and froze them with
full provenance (reports/accuracy/runs/<run_id>/poi_universe.json). This
script filters that frozen universe with the shared boundary predicate, so
the bundled snapshot is reproducible from committed artifacts alone and
needs no network. Rerunning it on its own output is idempotent.

The universe stores name / category / source / lat / lon only. ``kind``,
``addr`` and the OSM element id are carried over from the previous committed
snapshot when the same named place of the same category lies within 150 m;
otherwise they are null. Landmarks are unaffected.

Facility membership produced here is dataset-to-display-boundary consistency
only. It does not validate ten-minute drive-time accuracy.

Usage: facilities_from_frozen_universe.py [--location apple_park]
                                         [--run RUN_ID] [--previous PATH]
"""
import argparse
import datetime
import json
from pathlib import Path

from fetch_facilities import (CATEGORIES, ROUTED_BOUNDARY_MODE,
                              ROUTED_FACILITY_FILTER)
from verify import (M_PER_DEG_LAT, geometry_bbox, m_per_deg_lon,
                    point_in_polygon)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "map" / "data"
REPORTS = ROOT / "reports" / "accuracy"
CARRY_OVER_METERS = 150
OVERTURE_ATTRIBUTION = (
    "Overture Maps Foundation, overturemaps.org; includes Foursquare "
    "Places data © 2024 Foursquare Labs, Inc. under Apache-2.0"
)


def load_run(run_id=None):
    """The frozen universe and results of a published run (default: latest)."""
    if run_id is None:
        run_id = json.loads((REPORTS / "latest.json").read_text())["run_id"]
    run_dir = REPORTS / "runs" / run_id
    universe = json.loads((run_dir / "poi_universe.json").read_text())
    results = json.loads((run_dir / "results.json").read_text())
    return run_id, universe, results


def bbox_within(inner, outer):
    s, w, n, e = inner
    S, W, N, E = outer
    return s >= S and w >= W and n <= N and e <= E


def _attribute_index(previous):
    index = {}
    for key, cat in (previous or {}).get("categories", {}).items():
        for item in cat.get("items", []):
            index.setdefault((key, item["name"]), []).append(item)
    return index


def _carried(index, key, name, lat, lon):
    m_lon = m_per_deg_lon(lat)
    for item in index.get((key, name), []):
        dx = (item["lon"] - lon) * m_lon
        dy = (item["lat"] - lat) * M_PER_DEG_LAT
        if dx * dx + dy * dy <= CARRY_OVER_METERS ** 2:
            return item
    return None


def build_facilities(boundary, universe, results, run_id, location,
                     previous=None, now=None):
    """facilities.json content for `boundary` from one frozen universe."""
    if boundary.get("boundary_mode") != ROUTED_BOUNDARY_MODE:
        raise ValueError(
            "boundary.json must declare the routed isochrone mode; got "
            f"{boundary.get('boundary_mode')!r}"
        )
    geometry = boundary["features"][0]["geometry"]
    boundary_bbox = geometry_bbox(geometry)
    loc = universe["locations"][location]
    envelope = results["locations"][location]["query_envelope"][
        "bbox_south_west_north_east"]
    if not bbox_within(boundary_bbox, envelope):
        raise ValueError(
            "the frozen universe's query envelope does not contain the "
            "boundary; the snapshot would be structurally incomplete"
        )

    col = {name: i for i, name in enumerate(universe["columns"])}
    index = _attribute_index(previous)
    buckets = {key: [] for key in CATEGORIES}
    for row in loc["points"]:
        key = row[col["category"]]
        lat, lon = row[col["lat"]], row[col["lon"]]
        if key not in buckets or not point_in_polygon(lon, lat, geometry):
            continue
        name = row[col["name"]]
        prev = _carried(index, key, name, lat, lon)
        item = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "kind": prev.get("kind") if prev else None,
            "addr": prev.get("addr") if prev else None,
            "src": row[col["src"]],
        }
        if prev and prev.get("osm"):
            item["osm"] = prev["osm"]
        buckets[key].append(item)

    generated_utc = now or datetime.datetime.now(datetime.timezone.utc)\
        .strftime("%Y-%m-%d %H:%M UTC")
    acquisition = loc["acquisition"]
    release = acquisition["overture_release"]
    out = {
        "metadata": {
            "generated_utc": generated_utc,
            "source": "OpenStreetMap (Overpass API) + Overture Maps places",
            "filter": ROUTED_FACILITY_FILTER,
            "overture_min_confidence": acquisition["overture_min_confidence"],
            "overture_release": release,
            "overture_attribution": OVERTURE_ATTRIBUTION,
            "overture_modifications": (
                f"Modified from Overture Places release {release} on "
                f"{generated_utc} by confidence/category filtering, boundary "
                "subsetting, OSM deduplication, and coordinate rounding"
            ),
            "provenance": {
                "method": (
                    "offline rebuild: the benchmark's frozen POI universe "
                    "filtered with the shared boundary predicate "
                    "(map/scripts/facilities_from_frozen_universe.py); "
                    "no network request was made"
                ),
                "benchmark_run_id": run_id,
                "poi_universe_file": (
                    f"reports/accuracy/runs/{run_id}/poi_universe.json"
                ),
                "location": location,
                "universe_points": loc["n_points"],
                "universe_points_sha256": loc["points_sha256"],
                "universe_frozen_utc": acquisition["frozen_utc"],
                "overpass_query_sha256": acquisition["overpass_query_sha256"],
                "dedup_rules": acquisition["dedup_rules"],
                "query_envelope_south_west_north_east": envelope,
                "boundary_bbox_south_west_north_east": list(boundary_bbox),
                "boundary_mode": ROUTED_BOUNDARY_MODE,
                "attribute_carry_over": (
                    "kind, addr and osm id copied from the previous committed "
                    f"snapshot when the same named place of the same category "
                    f"lies within {CARRY_OVER_METERS} m; null otherwise"
                ),
                "note": (
                    "Facility membership is dataset-to-display-boundary "
                    "consistency only; it does not validate ten-minute "
                    "drive-time accuracy."
                ),
            },
        },
        "categories": {},
    }
    for key, cfg in CATEGORIES.items():
        items = sorted(buckets[key], key=lambda x: x["name"])
        out["categories"][key] = {
            "label_zh": cfg["zh"], "label_en": cfg["en"],
            "color": cfg["color"], "count": len(items), "items": items,
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--location", default="apple_park")
    parser.add_argument("--run", default=None, help="benchmark run id")
    parser.add_argument("--previous", default=str(DATA / "facilities.json"),
                        help="previous snapshot for kind/addr/osm carry-over")
    args = parser.parse_args()

    boundary = json.loads((DATA / "boundary.json").read_text())
    run_id, universe, results = load_run(args.run)
    previous_path = Path(args.previous)
    previous = (json.loads(previous_path.read_text())
                if previous_path.exists() else None)

    out = build_facilities(boundary, universe, results, run_id,
                           args.location, previous=previous)
    (DATA / "facilities.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))

    total = 0
    carried = 0
    for key, cat in out["categories"].items():
        with_attrs = sum(1 for i in cat["items"] if i["kind"] or i["addr"])
        carried += with_attrs
        total += cat["count"]
        print(f'{key:10s} {cat["label_zh"]:6s} kept {cat["count"]:4d} '
              f'(kind/addr carried for {with_attrs})')
    print(f"\nSaved data/facilities.json with {total} facilities from "
          f"{universe['locations'][args.location]['n_points']} frozen "
          f"points of run {run_id}; attributes carried for {carried}.")


if __name__ == "__main__":
    main()
