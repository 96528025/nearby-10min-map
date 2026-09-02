#!/usr/bin/env python3
"""Area pipeline for arbitrary attractions.

Same method as the Apple Park build, as reusable functions:
geocode (Nominatim) -> 10-min drive isochrone (Valhalla) -> the isochrone
polygon itself is the boundary -> facilities from OSM Overpass (phase 1) ->
Overture places merge (phase 2, slower). Display, facility filtering and the
Overture merge all use the same geometry and the same point-in-polygon
predicate; `verify_inside` re-applies that predicate as a consistency guard.

The former equal-area circle was retired after the preregistered benchmark
(reports/accuracy, docs/DECISIONS.md D-2) measured it at 9.1 % macro false
inclusion and 24.7 % macro false exclusion against Valhalla's own geometry.
Rendering the isochrone removes that approximation layer; it does not make
the model's free-flow estimate a real-world drive-time measurement.
"""
import json
import math
import os
import re
import sys
import time
import datetime
import subprocess
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from verify import (M_PER_DEG_LAT, geometry_area_m2,   # noqa: E402
                    geometry_bbox, isochrone_geometry, m_per_deg_lon,
                    point_in_polygon, polygon_rings)
from fetch_facilities import (CATEGORIES, overpass_query_all,  # noqa: E402
                              categorize, element_coords)
from merge_overture import (map_category, same_place,   # noqa: E402
                            MIN_CONFIDENCE)

VALHALLA = "https://valhalla1.openstreetmap.de"
NOMINATIM = "https://nominatim.openstreetmap.org"
USER_AGENT = os.getenv(
    "UPSTREAM_USER_AGENT",
    "nearby-10min-map/1.0 "
    "(+https://github.com/96528025/nearby-10min-map)",
)
OVERTURE_CLI = Path(sys.executable).parent / "overturemaps"
OVERTURE_RELEASE = os.getenv("OVERTURE_RELEASE", "2026-08-19.0")
GEOCODE_TIMEOUT_SECONDS = float(os.getenv("GEOCODE_TIMEOUT_SECONDS", "15"))
VALHALLA_LOCATE_TIMEOUT_SECONDS = float(
    os.getenv("VALHALLA_LOCATE_TIMEOUT_SECONDS", "5")
)
SNAP_TOTAL_TIMEOUT_SECONDS = float(
    os.getenv("SNAP_TOTAL_TIMEOUT_SECONDS", "20")
)
VALHALLA_ISOCHRONE_TIMEOUT_SECONDS = float(
    os.getenv("VALHALLA_ISOCHRONE_TIMEOUT_SECONDS", "30")
)
ISOCHRONE_DENOISE = 0.3
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
FREE_FLOW_NOTE = (
    "free-flow / speed-limit based; no live or historical traffic data"
)

ROUTED_BOUNDARY_MODE = "routed_isochrone"
NOMINAL_BOUNDARY_MODE = "nominal_radius_circle"
# Retired after the preregistered benchmark (docs/DECISIONS.md D-2). Cache
# entries carrying it hold circle geometry and are recomputed, never served.
RETIRED_BOUNDARY_MODES = frozenset({"routed_equal_area_circle"})
ROUTED_FACILITY_FILTER = (
    "named facilities inside the displayed routed 10-minute drive isochrone "
    "(Valhalla, free-flow); the displayed geometry itself is the filter"
)
NOMINAL_FACILITY_FILTER = (
    "named facilities inside the displayed fixed nominal-radius circle; "
    "no road-network input was used to derive this boundary"
)


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


class LocateUnavailable(RuntimeError):
    """Valhalla /locate could not be reached within the request budget."""


def _locate_good_edge(lat, lon, timeout=VALHALLA_LOCATE_TIMEOUT_SECONDS):
    """Nearest GOOD_ROAD_CLASSES edge around a point, or None."""
    req = {"locations": [{"lat": lat, "lon": lon, "radius": 400,
                          "search_cutoff": 400}],
           "costing": "auto", "verbose": True}
    url = f"{VALHALLA}/locate?json=" + urllib.parse.quote(json.dumps(req))
    try:
        edges = http_json(
            url, timeout=timeout
        )[0].get("edges") or []
    except Exception as error:
        raise LocateUnavailable("Valhalla locate unavailable") from error
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

    deadline = time.monotonic() + SNAP_TOTAL_TIMEOUT_SECONDS

    def locate(probe_lat, probe_lon):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocateUnavailable("Valhalla locate budget exhausted")
        return _locate_good_edge(
            probe_lat,
            probe_lon,
            timeout=min(VALHALLA_LOCATE_TIMEOUT_SECONDS, remaining),
        )

    try:
        best = locate(lat, lon)
        if best is None:
            for ring_m in (500, 1000, 1500, 2000):
                hits = []
                for i in range(8):
                    b = 2 * math.pi * i / 8
                    plat = lat + ring_m * math.cos(b) / M_PER_DEG_LAT
                    plon = lon + ring_m * math.sin(b) / m_lon
                    hit = locate(plat, plon)
                    if hit:
                        hits.append(hit)
                if hits:
                    best = min(hits, key=lambda h: dist_m(h[1], h[2]))
                    break
    except LocateUnavailable:
        return lat, lon, None
    if best is None:
        return lat, lon, None
    return best[1], best[2], round(dist_m(best[1], best[2]))


def fetch_isochrone(lat, lon, minutes=10):
    req = {"locations": [{"lat": lat, "lon": lon}], "costing": "auto",
           "contours": [{"time": minutes}], "polygons": True,
           "denoise": ISOCHRONE_DENOISE}
    url = f"{VALHALLA}/isochrone?json=" + urllib.parse.quote(json.dumps(req))
    iso = http_json(url, timeout=VALHALLA_ISOCHRONE_TIMEOUT_SECONDS)
    if not iso.get("features"):
        raise RuntimeError("Valhalla returned no isochrone")
    return iso


def boundary_from_isochrone(
        iso, lat, lon, name, minutes=10, denoise=ISOCHRONE_DENOISE):
    """Return the routed isochrone itself as the boundary of record.

    The geometry is Valhalla's response collapsed to one Polygon or
    MultiPolygon with every component and hole preserved (nothing reads
    ``coordinates[0]``). The same geometry object is displayed, used as the
    OSM / Overture filter, and re-checked by `verify_inside`.
    """
    geometry = isochrone_geometry(iso)
    rings = polygon_rings(geometry)
    area = geometry_area_m2(geometry, lat, lon)

    return {
        "type": "FeatureCollection",
        "boundary_mode": ROUTED_BOUNDARY_MODE,
        "metadata": {
            "generated_utc": _now(),
            "method": (
                f"routed {minutes}-minute drive isochrone (Valhalla, auto "
                f"costing, free-flow, denoise={denoise:g}), rendered and "
                "filtered as returned; "
                "no circle approximation"
            ),
            "center": {"lat": lat, "lon": lon, "name": name},
            "isochrone_area_km2": round(area / 1e6, 2),
            "geometry_type": geometry["type"],
            "geometry_components": len(rings),
            "geometry_holes": sum(len(r) - 1 for r in rings),
            "contour_minutes": minutes,
            "costing": "auto",
            "denoise": denoise,
            "traffic": FREE_FLOW_NOTE,
            "source": f"Valhalla ({VALHALLA})",
        },
        "features": [{
            "type": "Feature",
            "properties": {
                "contour": f"approx {minutes} min drive",
                "contour_minutes": minutes,
            },
            "geometry": geometry,
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
        "boundary_mode": NOMINAL_BOUNDARY_MODE,
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
    """Upstream query envelope: covers every component and hole (s, w, n, e)."""
    return geometry_bbox(geometry)


def boundary_geometry(boundary):
    """The single geometry a boundary FeatureCollection displays and filters with."""
    features = boundary["features"]
    if len(features) != 1:
        raise ValueError(
            f"boundary must carry exactly one feature, got {len(features)}"
        )
    return features[0]["geometry"]


def facility_filter_for(boundary_mode):
    if boundary_mode == ROUTED_BOUNDARY_MODE:
        return ROUTED_FACILITY_FILTER
    if boundary_mode == NOMINAL_BOUNDARY_MODE:
        return NOMINAL_FACILITY_FILTER
    raise ValueError(f"unknown boundary mode: {boundary_mode}")


def empty_osm_facilities(boundary_mode=ROUTED_BOUNDARY_MODE):
    """Return an honest, schema-complete result when Overpass is unavailable."""
    return {
        "metadata": {
            "generated_utc": _now(),
            "source": (
                "OpenStreetMap via Overpass API "
                "(unavailable for this response)"
            ),
            "filter": facility_filter_for(boundary_mode),
            "osm_lookup_error": True,
        },
        "categories": {
            key: {
                "label_zh": config["zh"],
                "label_en": config["en"],
                "color": config["color"],
                "count": 0,
                "items": [],
            }
            for key, config in CATEGORIES.items()
        },
    }


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
    osm_lookup_failed = fac["metadata"].get("osm_lookup_error") is True
    if osm_lookup_failed:
        fac["metadata"]["source"] = (
            "Overture Maps places "
            "(OSM Overpass unavailable for this response)"
        )
    else:
        fac["metadata"]["source"] = (
            "OpenStreetMap (Overpass API) + Overture Maps places"
        )
    fac["metadata"]["overture_min_confidence"] = MIN_CONFIDENCE
    fac["metadata"]["overture_release"] = OVERTURE_RELEASE
    generated_utc = _now()
    fac["metadata"]["generated_utc"] = generated_utc
    fac["metadata"]["overture_attribution"] = (
        "Overture Maps Foundation, overturemaps.org; includes Foursquare "
        "Places data © 2024 Foursquare Labs, Inc. under Apache-2.0"
    )
    transformations = (
        "confidence/category filtering, boundary subsetting, and coordinate "
        "rounding"
        if osm_lookup_failed
        else (
            "confidence/category filtering, boundary subsetting, OSM "
            "deduplication, and coordinate rounding"
        )
    )
    fac["metadata"]["overture_modifications"] = (
        f"Modified from Overture Places release {OVERTURE_RELEASE} on "
        f"{generated_utc} by {transformations}"
    )
    return fac


def verify_inside(fac, geometry):
    """Consistency guard: every facility must satisfy the display predicate.

    This re-applies the same `point_in_polygon` over the same geometry the
    facilities were filtered with, so it can only catch a code path that
    bypassed the filter. It is not evidence of accuracy of any kind (see
    docs/DECISIONS.md D-4).
    """
    bad = [i["name"] for c in fac["categories"].values() for i in c["items"]
           if not point_in_polygon(i["lon"], i["lat"], geometry)]
    if bad:
        raise AssertionError(f"{len(bad)} facilities outside boundary: "
                             f"{bad[:5]}")
    return sum(c["count"] for c in fac["categories"].values())
