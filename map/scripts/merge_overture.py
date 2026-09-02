#!/usr/bin/env python3
"""Merge Overture Maps places into facilities.json for better completeness.

Reads the OSM-based facilities.json written by fetch_facilities.py plus a raw
Overture places GeoJSON (download with:
  overturemaps download --bbox=W,S,E,N -f geojson --type=place -o places.geojson
), maps Overture's fine-grained categories onto our visitor categories, keeps
only places inside the boundary of record (data/boundary.json, the routed
isochrone; same point-in-polygon predicate as the rest of the pipeline) with
confidence >= MIN_CONFIDENCE, and adds whatever OSM didn't already have
(same-name-nearby entries are considered the same place; the OSM record wins).

Usage: merge_overture.py <overture_places.geojson>
"""
import json
import re
import sys
import datetime
from pathlib import Path

from verify import point_in_polygon

DATA = Path(__file__).resolve().parent.parent / "data"
MIN_CONFIDENCE = 0.6
# The two sources pin the same venue differently (building centroid vs
# storefront), so exact-name matches merge within a wider radius than
# mere name-containment matches (which are weaker evidence).
SAME_PLACE_METERS_EXACT = 300
SAME_PLACE_METERS_CONTAINS = 150

DINING_SET = {
    "coffee_shop", "cafe", "bakery", "bar", "pub", "food_court",
    "ice_cream_shop", "dessert_shop", "bubble_tea", "juice_bar", "tea_room",
    "donut_shop", "frozen_yogurt_shop", "sandwich_shop", "deli",
    "breakfast_and_brunch_restaurant", "diner", "food_truck", "cafeteria",
}
HEALTH_SET = {
    "hospital", "medical_center", "urgent_care_clinic", "pharmacy",
    "emergency_room", "walk_in_clinic",
}
EDU_SET = {
    "elementary_school", "middle_school", "high_school", "school",
    "college_university", "university", "private_school", "public_school",
}
LODGING_SET = {"hotel", "motel", "bed_and_breakfast", "hostel", "inn", "resort"}
SHOPPING_SET = {
    "supermarket", "grocery_store", "shopping_center", "shopping_mall",
    "department_store", "farmers_market",
}
FUEL_SET = {"gas_station", "ev_charging_station",
            "electric_vehicle_charging_station"}
CULTURE_SET = {"museum", "art_gallery", "history_museum", "science_museum",
               "art_museum", "tourist_attraction"}
PARKS_SET = {"park", "state_park", "county_park"}


def map_category(primary):
    if not primary:
        return None
    if primary in DINING_SET or primary.endswith("_restaurant") \
            or primary == "restaurant":
        return "dining"
    if primary in HEALTH_SET:
        return "health"
    if primary in EDU_SET:
        return "education"
    if primary in LODGING_SET:
        return "lodging"
    if primary in SHOPPING_SET or "grocery" in primary:
        return "shopping"
    if primary in FUEL_SET:
        return "fuel_ev"
    if primary in CULTURE_SET:
        return "culture"
    if primary in PARKS_SET:
        return "parks"
    return None


def norm_name(name):
    return re.sub(r"[^a-z0-9一-鿿]", "", name.lower())


def addr_key(addr):
    """'19359 Stevens Creek Boulevard' / '19359 Stevens Creek Blvd Cupertino'
    both normalize to '19359stevens' — house number plus first street word."""
    if not addr:
        return None
    m = re.match(r"^(\d+)\s+(?:north |south |east |west |n |s |e |w )?(\w+)",
                 addr.lower())
    return (m.group(1) + m.group(2)) if m else None


def same_place(items, name, lat, lon, addr=None):
    n = norm_name(name)
    a = addr_key(addr)
    for it in items:
        m = norm_name(it["name"])
        if n == m:
            radius = SAME_PLACE_METERS_EXACT
        elif (len(n) > 4 and n in m) or (len(m) > 4 and m in n):
            radius = SAME_PLACE_METERS_CONTAINS
        else:
            continue
        # names alike + same street address = same venue, however far apart
        # the two sources pinned it
        if a and a == addr_key(it.get("addr")):
            return True
        dx = (it["lon"] - lon) * 88000
        dy = (it["lat"] - lat) * 111000
        if dx * dx + dy * dy < radius ** 2:
            return True
    return False


def main():
    overture_path = Path(sys.argv[1])
    iso = json.loads((DATA / "boundary.json").read_text())
    geometry = iso["features"][0]["geometry"]
    fac = json.loads((DATA / "facilities.json").read_text())

    places = json.loads(overture_path.read_text())["features"]
    added = {k: 0 for k in fac["categories"]}
    skipped_dup = 0

    for f in places:
        props = f["properties"]
        if (props.get("confidence") or 0) < MIN_CONFIDENCE:
            continue
        key = map_category((props.get("categories") or {}).get("primary"))
        if key is None or key not in fac["categories"]:
            continue
        name = (props.get("names") or {}).get("primary")
        if not name:
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        if not point_in_polygon(lon, lat, geometry):
            continue
        addr = None
        if props.get("addresses"):
            a = props["addresses"][0]
            addr = " ".join(filter(None, [a.get("freeform"),
                                          a.get("locality")])) or None
        cat = fac["categories"][key]
        if same_place(cat["items"], name, lat, lon, addr):
            skipped_dup += 1
            continue
        cat["items"].append({
            "name": name,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "kind": (props.get("categories") or {}).get("primary"),
            "addr": addr,
            "src": "overture",
        })
        added[key] += 1

    for key, cat in fac["categories"].items():
        cat["items"].sort(key=lambda x: x["name"])
        cat["count"] = len(cat["items"])
        print(f'{key:10s} +{added[key]:4d} overture -> total {cat["count"]:4d}')

    fac["metadata"]["source"] = ("OpenStreetMap (Overpass API) + "
                                 "Overture Maps places")
    fac["metadata"]["overture_min_confidence"] = MIN_CONFIDENCE
    fac["metadata"]["generated_utc"] = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (DATA / "facilities.json").write_text(
        json.dumps(fac, ensure_ascii=False, indent=1))
    total = sum(c["count"] for c in fac["categories"].values())
    print(f"\ncross-source duplicates skipped: {skipped_dup}")
    print(f"Saved facilities.json with {total} facilities.")


if __name__ == "__main__":
    main()
