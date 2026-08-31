#!/usr/bin/env python3
"""Fetch visitor-relevant facilities inside the circular boundary.

Queries the OSM Overpass API for the boundary's bounding box (data/
boundary.json, the equal-area circle derived from the 10-minute isochrone
by make_boundary.py), keeps only named facilities whose coordinates fall
inside it (point-in-polygon, same test as verify.py), and writes
data/facilities.json grouped by category. Residential features are never
queried — the tool is for visitors, not residents.
"""
import json
import re
import datetime
import argparse
import os
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

from verify import point_in_polygon

DATA = Path(__file__).resolve().parent.parent / "data"
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_FALLBACK = "https://overpass.private.coffee/api/interpreter"
OVERPASS_ENDPOINTS = tuple(
    endpoint.strip()
    for endpoint in os.getenv(
        "OVERPASS_ENDPOINTS", f"{OVERPASS},{OVERPASS_FALLBACK}"
    ).split(",")
    if endpoint.strip()
)
USER_AGENT = os.getenv(
    "UPSTREAM_USER_AGENT",
    "nearby-10min-map/1.0 "
    "(+https://github.com/96528025/nearby-10min-map)",
)
OVERPASS_QUERY_TIMEOUT_SECONDS = int(
    os.getenv("OVERPASS_QUERY_TIMEOUT_SECONDS", "120")
)
OVERPASS_HTTP_TIMEOUT_SECONDS = float(
    os.getenv("OVERPASS_HTTP_TIMEOUT_SECONDS", "180")
)

ROUTED_BOUNDARY_MODE = "routed_equal_area_circle"
NOMINAL_BOUNDARY_MODE = "nominal_radius_circle"
ROUTED_FACILITY_FILTER = (
    "named facilities inside the displayed equal-area circle derived from "
    "the routed 10-minute drive isochrone, not the isochrone geometry itself"
)
NOMINAL_FACILITY_FILTER = (
    "named facilities inside the displayed fixed nominal-radius circle; "
    "no road-network input was used to derive this boundary"
)

# category -> (bilingual labels, marker color, list of OSM tag selectors)
CATEGORIES = {
    "dining": {
        "zh": "餐饮", "en": "Dining", "color": "#e0322c",
        "selectors": ['amenity~"^(restaurant|cafe|fast_food|bar|pub|food_court|ice_cream)$"',
                      'shop~"^(bakery|beverages|confectionery)$"'],
    },
    "health": {
        "zh": "医疗", "en": "Health", "color": "#0f9d58",
        "selectors": ['amenity~"^(hospital|clinic|pharmacy)$"',
                      'healthcare~"^(hospital|clinic)$"'],
    },
    "education": {
        "zh": "学校", "en": "Education", "color": "#4285f4",
        "selectors": ['amenity~"^(school|college|university)$"'],
    },
    "lodging": {
        "zh": "住宿", "en": "Lodging", "color": "#9c27b0",
        "selectors": ['tourism~"^(hotel|motel|guest_house)$"'],
    },
    "shopping": {
        "zh": "购物", "en": "Shopping", "color": "#f4a300",
        "selectors": ['shop~"^(supermarket|mall|department_store)$"'],
    },
    "fuel_ev": {
        "zh": "加油/充电", "en": "Fuel / EV", "color": "#607d8b",
        "selectors": ['amenity~"^(fuel|charging_station)$"'],
    },
    "culture": {
        "zh": "景点/文化", "en": "Attractions", "color": "#e91e63",
        "selectors": ['tourism~"^(museum|attraction|gallery)$"'],
    },
    "parks": {
        "zh": "公园", "en": "Parks", "color": "#33a02c",
        "selectors": ['leisure~"^(park)$"'],
    },
}


def overpass_query_all(bbox):
    """One combined request for every category, to stay under rate limits."""
    s, w, n, e = bbox
    parts = []
    for cfg in CATEGORIES.values():
        for sel in cfg["selectors"]:
            for kind in ("node", "way", "relation"):
                parts.append(f'{kind}[{sel}][name]({s},{w},{n},{e});')
    q = (
        f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_SECONDS}];"
        f"({''.join(parts)});out center tags;"
    )
    body = urllib.parse.urlencode({"data": q}).encode()
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=OVERPASS_HTTP_TIMEOUT_SECONDS
            ) as r:
                return json.load(r)["elements"]
        except urllib.error.HTTPError as err:
            if err.code not in (429, 500, 502, 503, 504):
                raise
            last_error = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_error = err

    if last_error is not None:
        raise last_error
    raise RuntimeError("no Overpass endpoints configured")


def categorize(tags):
    a = tags.get("amenity"); t = tags.get("tourism")
    sh = tags.get("shop"); le = tags.get("leisure"); hc = tags.get("healthcare")
    if a in ("restaurant", "cafe", "fast_food", "bar", "pub", "food_court",
             "ice_cream") or sh in ("bakery", "beverages", "confectionery"):
        return "dining"
    if a in ("hospital", "clinic", "pharmacy") or hc in ("hospital", "clinic"):
        return "health"
    if a in ("school", "college", "university"):
        return "education"
    if t in ("hotel", "motel", "guest_house"):
        return "lodging"
    if sh in ("supermarket", "mall", "department_store"):
        return "shopping"
    if a in ("fuel", "charging_station"):
        return "fuel_ev"
    if t in ("museum", "attraction", "gallery"):
        return "culture"
    if le == "park":
        return "parks"
    return None


def element_coords(el):
    if el["type"] == "node":
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else (None, None)


def facility_filter_for(boundary_mode):
    if boundary_mode == ROUTED_BOUNDARY_MODE:
        return ROUTED_FACILITY_FILTER
    if boundary_mode == NOMINAL_BOUNDARY_MODE:
        return NOMINAL_FACILITY_FILTER
    raise ValueError(f"unknown boundary mode: {boundary_mode}")


def main(boundary_mode=ROUTED_BOUNDARY_MODE):
    iso = json.loads((DATA / "boundary.json").read_text())
    geometry = iso["features"][0]["geometry"]
    ring = geometry["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    bbox = (min(lats), min(lons), max(lats), max(lons))

    out = {
        "metadata": {
            "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%d %H:%M UTC"),
            "source": "OpenStreetMap via Overpass API",
            "filter": facility_filter_for(boundary_mode),
        },
        "categories": {},
    }

    elements = overpass_query_all(bbox)
    print(f"Overpass returned {len(elements)} raw elements")

    # Same OSM element can match several selectors; same place can be mapped
    # twice (node + building way). Chains have many branches with identical
    # names, so dedup by name alone is wrong: only merge same-name points
    # within DEDUP_METERS of each other.
    DEDUP_METERS = 150
    seen_ids = set()
    buckets = {key: [] for key in CATEGORIES}

    def near_duplicate(items, name, lat, lon):
        for it in items:
            if it["name"] != name:
                continue
            dx = (it["lon"] - lon) * 88000   # meters per degree at 37.3°N
            dy = (it["lat"] - lat) * 111000
            if dx * dx + dy * dy < DEDUP_METERS ** 2:
                return True
        return False

    for el in elements:
        eid = f'{el["type"]}/{el["id"]}'
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        lat, lon = element_coords(el)
        if lat is None or not point_in_polygon(lon, lat, geometry):
            continue
        tags = el.get("tags", {})
        name = tags.get("name")
        key = categorize(tags)
        if not name or key is None:
            continue
        # Hospital-internal suites are mapped as clinics named after their
        # room number ("120 Nuclear Medicine"); useless to visitors.
        if key == "health" and re.match(r"^\d+ ", name):
            continue
        if near_duplicate(buckets[key], name, lat, lon):
            continue
        addr = " ".join(filter(None, [
            tags.get("addr:housenumber"), tags.get("addr:street"),
            tags.get("addr:city"),
        ])) or None
        buckets[key].append({
            "name": name,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "kind": (tags.get("amenity") or tags.get("tourism")
                     or tags.get("shop") or tags.get("leisure")
                     or tags.get("healthcare")),
            "addr": addr,
            "osm": eid,
        })

    for key, cfg in CATEGORIES.items():
        items = sorted(buckets[key], key=lambda x: x["name"])
        out["categories"][key] = {
            "label_zh": cfg["zh"], "label_en": cfg["en"],
            "color": cfg["color"], "count": len(items), "items": items,
        }
        print(f'{key:10s} {cfg["zh"]:6s} kept {len(items):4d}')

    (DATA / "facilities.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    total = sum(c["count"] for c in out["categories"].values())
    print(f"\nSaved data/facilities.json with {total} facilities.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--boundary-mode",
        choices=(ROUTED_BOUNDARY_MODE, NOMINAL_BOUNDARY_MODE),
        default=ROUTED_BOUNDARY_MODE,
        help="Provenance of the displayed circular boundary being filtered",
    )
    args = parser.parse_args()
    main(args.boundary_mode)
