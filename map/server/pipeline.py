#!/usr/bin/env python3
"""Area pipeline for arbitrary attractions.

Same method as the Apple Park build, as reusable functions:
geocode (Nominatim) -> 10-min drive isochrone (Valhalla) -> equal-area
circle boundary -> facilities from OSM Overpass (phase 1) -> Overture
places merge (phase 2, slower). Every facility is point-in-polygon
verified against the boundary before it is returned.
"""
import json
import math
import os
import re
import sys
import datetime
import subprocess
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from verify import point_in_polygon                     # noqa: E402
from fetch_facilities import (CATEGORIES, overpass_query_all,  # noqa: E402
                              categorize, element_coords)
from merge_overture import (map_category, same_place,   # noqa: E402
                            MIN_CONFIDENCE)

VALHALLA = "https://valhalla1.openstreetmap.de"
NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = os.getenv(
    "UPSTREAM_USER_AGENT",
    "nearby-10min-map/1.0 "
    "(+https://github.com/96528025/96528025)",
)
OVERTURE_CLI = Path(sys.executable).parent / "overturemaps"
OVERTURE_RELEASE = os.getenv("OVERTURE_RELEASE", "2026-08-19.0")
GEOCODE_TIMEOUT_SECONDS = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "15"))
VALHALLA_LOCATE_TIMEOUT_SECONDS = float(
    os.getenv("VALHALLA_LOCATE_TIMEOUT_SECONDS", "20")
)
VALHALLA_ISOCHRONE_TIMEOUT_SECONDS = float(
    os.getenv("VALHALLA_ISOCHRONE_TIMEOUT_SECONDS", "60")
)
OVERTURE_CONNECT_TIMEOUT_SECONDS = int(
    os.getenv("OVERTURE_CONNECT_TIMEOUT_SECONDS", "15")
)
OVERTURE_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv("OVERTURE_REQUEST_TIMEOUT_SECONDS", "120")
)
OVERTURE_PROCESS_TIMEOUT_SECONDS = float(
    os.getenv("OVERTURE_PROCESS_TIMEOUT_SECONDS", "600")
)
NOMINAL_RADIUS_M = float(os.getenv("NOMINAL_RADIUS_M", "3000"))
M_PER_DEG_LAT = 111000.0

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


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def http_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


PHOTON = "https://photon.komoot.io"


def geocode(query, limit=6, bias_lat=None, bias_lon=None):
    """Photon (fuzzy, biased toward the user's current map view) merged with
    Nominatim (exact). Bias matters: 'google visitor center' only resolves to
    Google Visitor Experience when searched near the Bay Area."""
    q = urllib.parse.quote(query)
    candidates = []
    successful_sources = 0

    # Nominatim's exact matches lead (it finds "sjc airport" itself);
    # Photon's fuzzy, view-biased matches fill in the rest (it finds
    # "google visitor center" -> Google Visitor Experience).
    try:
        nominatim_results = http_json(
            f"{NOMINATIM}/search?format=json&limit={limit}&q={q}",
            timeout=GEOCODE_TIMEOUT_SECONDS,
        )
        successful_sources += 1
        for r in nominatim_results:
            candidates.append({
                "name": r.get("name") or r["display_name"].split(",")[0],
                "display_name": r["display_name"],
                "lat": float(r["lat"]), "lon": float(r["lon"]),
                "osm": f'{r.get("osm_type")}/{r.get("osm_id")}',
            })
    except Exception:
        pass    # Nominatim down -> Photon alone still works

    photon_url = f"{PHOTON}/api?q={q}&limit={limit}"
    if bias_lat is not None and bias_lon is not None:
        photon_url += f"&lat={bias_lat}&lon={bias_lon}"
    try:
        photon_results = http_json(
            photon_url, timeout=GEOCODE_TIMEOUT_SECONDS
        )
        successful_sources += 1
        for f in photon_results.get("features", []):
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][:2]
            name = p.get("name") or query
            place = ", ".join(filter(None, [
                name, p.get("street"), p.get("city") or p.get("district"),
                p.get("state"), p.get("country")]))
            candidates.append({
                "name": name, "display_name": place,
                "lat": lat, "lon": lon,
                "osm": f'{p.get("osm_type")}/{p.get("osm_id")}',
            })
    except Exception:
        pass

    if successful_sources == 0:
        raise RuntimeError("Nominatim and Photon were both unavailable")

    seen, out = set(), []
    for c in candidates:
        key = (c["name"].lower(), round(c["lat"], 3), round(c["lon"], 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out[:limit]


# Real streets a visitor would actually drive out on. Service roads,
# parking aisles and driveways make the isochrone wildly sensitive to
# exactly where the geocoded pin lands (campus interiors, airport aprons).
GOOD_ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary",
                     "tertiary", "unclassified", "residential"}


def _locate_good_edge(lat, lon):
    """Nearest GOOD_ROAD_CLASSES edge around a point, or None."""
    req = {"locations": [{"lat": lat, "lon": lon, "radius": 400,
                          "search_cutoff": 400}],
           "costing": "auto", "verbose": True}
    url = f"{VALHALLA}/locate?json=" + urllib.parse.quote(json.dumps(req))
    try:
        edges = http_json(
            url, timeout=VALHALLA_LOCATE_TIMEOUT_SECONDS
        )[0].get("edges") or []
    except Exception:
        return None
    best = None
    for e in edges:
        cls = (e.get("edge", {}).get("classification", {})
               .get("classification"))
        if cls not in GOOD_ROAD_CLASSES:
            continue
        clat, clon = e.get("correlated_lat"), e.get("correlated_lon")
        if clat is None:
            continue
        if best is None or e.get("distance", 1e9) < best[0]:
            best = (e.get("distance", 1e9), clat, clon)
    return best


def snap_to_drivable(lat, lon):
    """Snap the geocoded point to the nearest proper public road, so the
    drive-time area starts where a car actually leaves the attraction.
    Large POIs (airports, campuses) can sit >1 km from any public road, so
    when nothing is found at the pin itself, probe outward in rings."""
    m_lon = m_per_deg_lon(lat)

    def dist_m(clat, clon):
        return math.hypot((clon - lon) * m_lon, (clat - lat) * M_PER_DEG_LAT)

    best = _locate_good_edge(lat, lon)
    if best is None:
        for ring_m in (500, 1000, 1500, 2000):
            hits = []
            for i in range(8):
                b = 2 * math.pi * i / 8
                plat = lat + ring_m * math.cos(b) / M_PER_DEG_LAT
                plon = lon + ring_m * math.sin(b) / m_lon
                hit = _locate_good_edge(plat, plon)
                if hit:
                    hits.append(hit)
            if hits:
                best = min(hits, key=lambda h: dist_m(h[1], h[2]))
                break
    if best is None:
        return lat, lon, None
    return best[1], best[2], round(dist_m(best[1], best[2]))


def fetch_isochrone(lat, lon, minutes=10):
    req = {"locations": [{"lat": lat, "lon": lon}], "costing": "auto",
           "contours": [{"time": minutes}], "polygons": True, "denoise": 0.3}
    url = f"{VALHALLA}/isochrone?json=" + urllib.parse.quote(json.dumps(req))
    iso = http_json(url, timeout=VALHALLA_ISOCHRONE_TIMEOUT_SECONDS)
    if not iso.get("features"):
        raise RuntimeError("Valhalla returned no isochrone")
    return iso


def boundary_from_isochrone(iso, lat, lon, name):
    m_lon = m_per_deg_lon(lat)
    ring = iso["features"][0]["geometry"]["coordinates"][0]
    pts = [((rlon - lon) * m_lon, (rlat - lat) * M_PER_DEG_LAT)
           for rlon, rlat in ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2
    radius = math.sqrt(area / math.pi)

    return {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": _now(),
            "method": ("circle with the same area as the routed 10-minute "
                       "isochrone (Valhalla, free-flow)"),
            "center": {"lat": lat, "lon": lon, "name": name},
            "radius_m": round(radius),
            "isochrone_area_km2": round(area / 1e6, 2),
        },
        "features": [{
            "type": "Feature",
            "properties": {"contour": "approx 10 min drive"},
            "geometry": _circle_geometry(lat, lon, radius),
        }],
    }


def _circle_geometry(lat, lon, radius_m):
    m_lon = m_per_deg_lon(lat)
    circle = []
    for i in range(129):
        bearing = 2 * math.pi * i / 128
        circle.append([
            round(lon + radius_m * math.sin(bearing) / m_lon, 6),
            round(lat + radius_m * math.cos(bearing) / M_PER_DEG_LAT, 6),
        ])
    return {"type": "Polygon", "coordinates": [circle]}


def boundary_from_nominal_radius(lat, lon, name, radius_m=NOMINAL_RADIUS_M):
    """Return an explicitly non-routed fixed-radius fallback boundary."""
    return {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": _now(),
            "method": (
                "fixed nominal-radius circle; Valhalla routing was "
                "unavailable and no road-network input was used"
            ),
            "center": {"lat": lat, "lon": lon, "name": name},
            "radius_m": round(radius_m),
        },
        "features": [{
            "type": "Feature",
            "properties": {"contour": "fixed-radius approximation"},
            "geometry": _circle_geometry(lat, lon, radius_m),
        }],
    }


def _now():
    return datetime.datetime.now(datetime.timezone.utc)\
        .strftime("%Y-%m-%d %H:%M UTC")


def _bbox(geometry):
    ring = geometry["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lats), min(lons), max(lats), max(lons))   # s, w, n, e


def facility_filter_for(boundary_mode):
    if boundary_mode == ROUTED_BOUNDARY_MODE:
        return ROUTED_FACILITY_FILTER
    if boundary_mode == NOMINAL_BOUNDARY_MODE:
        return NOMINAL_FACILITY_FILTER
    raise ValueError(f"unknown boundary mode: {boundary_mode}")


def osm_facilities(geometry, boundary_mode=ROUTED_BOUNDARY_MODE):
    """Phase 1: named OSM facilities inside the boundary, deduped."""
    elements = overpass_query_all(_bbox(geometry))
    DEDUP_METERS = 150
    seen_ids = set()
    buckets = {key: [] for key in CATEGORIES}

    def near_duplicate(items, name, lat, lon):
        m_lon = m_per_deg_lon(lat)
        for it in items:
            if it["name"] != name:
                continue
            dx = (it["lon"] - lon) * m_lon
            dy = (it["lat"] - lat) * M_PER_DEG_LAT
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
        if key == "health" and re.match(r"^\d+ ", name):
            continue
        if near_duplicate(buckets[key], name, lat, lon):
            continue
        addr = " ".join(filter(None, [
            tags.get("addr:housenumber"), tags.get("addr:street"),
            tags.get("addr:city"),
        ])) or None
        buckets[key].append({
            "name": name, "lat": round(lat, 6), "lon": round(lon, 6),
            "kind": (tags.get("amenity") or tags.get("tourism")
                     or tags.get("shop") or tags.get("leisure")
                     or tags.get("healthcare")),
            "addr": addr, "osm": eid,
        })

    out = {"metadata": {"generated_utc": _now(),
                        "source": "OpenStreetMap via Overpass API",
                        "filter": facility_filter_for(boundary_mode)},
           "categories": {}}
    for key, cfg in CATEGORIES.items():
        items = sorted(buckets[key], key=lambda x: x["name"])
        out["categories"][key] = {
            "label_zh": cfg["zh"], "label_en": cfg["en"],
            "color": cfg["color"], "count": len(items), "items": items,
        }
    return out


def merge_overture(fac, geometry):
    """Phase 2: download Overture places for the bbox and merge them in."""
    s, w, n, e = _bbox(geometry)
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tf:
        tmp = tf.name
    try:
        subprocess.run(
            [str(OVERTURE_CLI), "download", f"--bbox={w},{s},{e},{n}",
             "-f", "geojson", "--type=place", "-r", OVERTURE_RELEASE,
             "--connect_timeout", str(OVERTURE_CONNECT_TIMEOUT_SECONDS),
             "--request_timeout", str(OVERTURE_REQUEST_TIMEOUT_SECONDS),
             "-o", tmp],
            check=True, capture_output=True,
            timeout=OVERTURE_PROCESS_TIMEOUT_SECONDS)
        places = json.loads(Path(tmp).read_text())["features"]
    finally:
        Path(tmp).unlink(missing_ok=True)
        Path(tmp + ".state").unlink(missing_ok=True)

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
            continue
        cat["items"].append({
            "name": name, "lat": round(lat, 6), "lon": round(lon, 6),
            "kind": (props.get("categories") or {}).get("primary"),
            "addr": addr, "src": "overture",
        })

    for cat in fac["categories"].values():
        cat["items"].sort(key=lambda x: x["name"])
        cat["count"] = len(cat["items"])
    fac["metadata"]["source"] = ("OpenStreetMap (Overpass API) + "
                                 "Overture Maps places")
    fac["metadata"]["overture_min_confidence"] = MIN_CONFIDENCE
    fac["metadata"]["overture_release"] = OVERTURE_RELEASE
    generated_utc = _now()
    fac["metadata"]["generated_utc"] = generated_utc
    fac["metadata"]["overture_attribution"] = (
        "Overture Maps Foundation, overturemaps.org; includes Foursquare "
        "Places data © 2024 Foursquare Labs, Inc. under Apache-2.0"
    )
    fac["metadata"]["overture_modifications"] = (
        f"Modified from Overture Places release {OVERTURE_RELEASE} on "
        f"{generated_utc} by confidence/category filtering, boundary "
        "subsetting, OSM deduplication, and coordinate rounding"
    )
    return fac


def verify_inside(fac, geometry):
    """Hard gate: every facility must be inside the boundary."""
    bad = [i["name"] for c in fac["categories"].values() for i in c["items"]
           if not point_in_polygon(i["lon"], i["lat"], geometry)]
    if bad:
        raise AssertionError(f"{len(bad)} facilities outside boundary: "
                             f"{bad[:5]}")
    return sum(c["count"] for c in fac["categories"].values())
