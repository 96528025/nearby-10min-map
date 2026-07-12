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
import urllib.request
import urllib.parse
from pathlib import Path

from verify import point_in_polygon

DATA = Path(__file__).resolve().parent.parent / "data"
OVERPASS = "https://overpass-api.de/api/interpreter"

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
    import time
    s, w, n, e = bbox
    parts = []
    for cfg in CATEGORIES.values():
        for sel in cfg["selectors"]:
            for kind in ("node", "way", "relation"):
                parts.append(f'{kind}[{sel}][name]({s},{w},{n},{e});')
    q = f"[out:json][timeout:120];({''.join(parts)});out center tags;"
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "apple-park-visitor-map (educational project)"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)["elements"]
        except urllib.error.HTTPError as err:
            if err.code in (429, 504) and attempt < 3:
                wait = 30 * (attempt + 1)
                print(f"Overpass busy ({err.code}), retrying in {wait}s…")
                time.sleep(wait)
            else:
                raise


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


def main():
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
            "filter": "named facilities inside the 10-min drive isochrone",
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
    main()
