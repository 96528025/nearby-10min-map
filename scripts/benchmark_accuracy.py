#!/usr/bin/env python3
"""Accuracy benchmark for the displayed boundary.

Measures how faithfully each candidate display boundary reproduces the
10-minute free-flow service area that Valhalla itself estimates.

    THIS DOES NOT MEASURE REAL-WORLD DRIVE TIME.

The reference geometry is Valhalla's own isochrone, so every number here is
bounded by "agreement with Valhalla's free-flow model". Valhalla routes on
posted speed limits and carries no live or historical traffic. Establishing
real-world ten-minute accuracy would need GPS traces, historical traffic, or
field sampling. See reports/accuracy/BENCHMARK_PLAN.md §1.

Execution order is load-bearing and mirrors BENCHMARK_PLAN.md §7:

  1. hash the frozen plan + config
  2. write preflight.json          <-- BEFORE any network access
  3. per location: snap -> isochrone -> POI-INDEPENDENT candidates + search cap
  4. union envelope (+margin) -> ONE POI fetch -> ONE dedup -> FREEZE
  5. score every candidate on that identical frozen universe
  6. only then solve the exploratory, POI-dependent target-FI radius

Steps 3 and 4 are in that order on purpose. The shipped pipeline fetches POIs
only inside the circle, which makes any false-exclusion rate computed on its
output structurally zero no matter how wrong the circle is.

Usage:
    python3 scripts/benchmark_accuracy.py [--limit N] [--offline] [--out DIR]
"""
import argparse
import datetime
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

import yaml

# to_proj4() warns about lossiness; for AEQD/LAEA on a WGS84 datum the string
# is complete, and it is far more readable in results.json than WKT.
warnings.filterwarnings("ignore", category=UserWarning, module="pyproj")
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "map" / "scripts"))
sys.path.insert(0, str(ROOT / "map" / "server"))

import pipeline                                              # noqa: E402
from fetch_facilities import (CATEGORIES, categorize,        # noqa: E402
                              element_coords)
from merge_overture import (MIN_CONFIDENCE, map_category,    # noqa: E402
                            same_place)

PLAN_PATH = ROOT / "reports" / "accuracy" / "BENCHMARK_PLAN.md"
CONFIG_PATH = ROOT / "config" / "benchmark_locations.yaml"
RUNS_DIR = ROOT / "reports" / "accuracy" / "runs"
CACHE_DIR = ROOT / "reports" / "accuracy" / ".request_cache"
LATEST = ROOT / "reports" / "accuracy" / "latest.json"

OVERPASS = "https://overpass-api.de/api/interpreter"
VALHALLA = pipeline.VALHALLA
OVERTURE_CLI = Path(sys.executable).parent / "overturemaps"
OVERTURE_RELEASE = "2026-07-22.0"   # pinned; recorded in poi_universe.json
USER_AGENT = "nearby-10min-map accuracy benchmark (educational project)"

# --- preregistered constants (BENCHMARK_PLAN.md) ---------------------------
ENVELOPE_MARGIN = 0.10          # §7 step 2
BEARINGS = [0, 45, 90, 135, 180, 225, 270, 315]   # §4
TARGET_FI = 0.10                # §6.4 exploratory solve target
THRESH = {"macro_fi": 0.10, "macro_fe": 0.20,     # §3 P1..P4
          "loc_fi": 0.20, "loc_fe": 0.35,
          "min_denom": 30, "min_locations": 4}
GUARD_G1 = 0.005                # §4 equal-area identity tolerance
GUARD_G2 = 0.001                # §4 projection cross-check tolerance
MAX_REQUESTS = 400              # §8
MAX_ATTEMPTS = 4
THROTTLE = {"valhalla": 1.0, "overpass": 2.0}
CIRCLE_QUAD_SEGS = 256          # 1024-gon; area error ~6e-6 relative


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def provenance():
    """Everything needed to tell two runs apart and to re-derive one.

    plan+config hashes alone do not identify a run: the script, the geometry
    libraries and the routing graph all affect the numbers. Recorded in both
    preflight.json and results.json.
    """
    def _ver(dist):
        try:
            import importlib.metadata as md
            return md.version(dist)
        except Exception:                             # noqa: BLE001
            return None

    def _git(*args):
        try:
            return subprocess.check_output(
                ["git", "-C", str(ROOT), *args], text=True,
                stderr=subprocess.DEVNULL).strip()
        except Exception:                             # noqa: BLE001
            return None

    return {
        "script_sha256": sha256_file(__file__),
        "script_path": "scripts/benchmark_accuracy.py",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_worktree_dirty": bool(_git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "packages": {d: _ver(d) for d in
                     ("shapely", "pyproj", "PyYAML", "overturemaps")},
        "valhalla_host": VALHALLA,
        "overpass_host": OVERPASS,
        "overture_release": OVERTURE_RELEASE,
        "reproducibility_note": (
            "The Valhalla public instance does not expose its graph build "
            "version through /isochrone, /route or /locate, so isochrone "
            "geometry, IoU and route times cannot be re-derived from this "
            "repository alone at a later date -- only re-measured against "
            "whatever graph is live then. Raw responses are cached under "
            "reports/accuracy/.request_cache/, which is gitignored for size "
            "(~490 MB). POI-level rates ARE fully recomputable from the "
            "committed poi_universe.json."),
    }


class Budget:
    """Hard cap on external requests, plus per-host throttling (§8)."""

    def __init__(self, limit=MAX_REQUESTS):
        self.limit = limit
        self.used = 0
        self.cache_hits = 0
        self.blocked = []
        self._last = {}

    def spend(self, what):
        if self.used >= self.limit:
            raise RuntimeError(
                f"request budget exhausted ({self.limit}) at {what}")
        self.used += 1

    def throttle(self, host):
        delay = THROTTLE.get(host, 0)
        last = self._last.get(host)
        if last is not None:
            wait = delay - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.time()


BUDGET = Budget()


# --------------------------------------------------------------------------
# Cached external access.
#
# Cache key = sha256 over {endpoint, coordinates, costing, contour minutes,
# every other request parameter, benchmark config hash} -- the config hash is
# folded in via CACHE_SALT so a config change can never silently reuse another
# configuration's responses (BENCHMARK_PLAN.md §8).
# --------------------------------------------------------------------------
CACHE_SALT = {"config_sha256": None}


def cache_key(kind, payload):
    blob = json.dumps({"kind": kind, "payload": payload,
                       "config_sha256": CACHE_SALT["config_sha256"]},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_load(key):
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        BUDGET.cache_hits += 1
        return json.loads(p.read_text())
    return None


def cache_store(key, kind, payload, body):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps({
        "kind": kind, "payload": payload, "fetched_utc": utcnow(),
        "config_sha256": CACHE_SALT["config_sha256"], "body": body,
    }, ensure_ascii=False))


def http_json_cached(url, timeout=60, host="valhalla", offline=False):
    """Drop-in replacement for pipeline.http_json, with cache + budget.

    Installed over pipeline.http_json so the benchmark exercises the real
    production snapping and isochrone code path rather than a reimplementation.
    """
    parts = urllib.parse.urlsplit(url)
    payload = {"endpoint": f"{parts.scheme}://{parts.netloc}{parts.path}",
               "query": urllib.parse.parse_qs(parts.query)}
    key = cache_key("http_get", payload)
    hit = cache_load(key)
    if hit is not None:
        return hit["body"]
    if offline:
        raise RuntimeError(f"offline mode: no cached response for {parts.path}")

    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            BUDGET.throttle(host)
            BUDGET.spend(payload["endpoint"])
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.load(r)
            cache_store(key, "http_get", payload, body)
            return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except Exception as e:                      # noqa: BLE001
            last = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise last


def overpass_fetch(bbox, offline=False):
    """One combined Overpass request over the union envelope."""
    s, w, n, e = bbox
    parts = []
    for cfg in CATEGORIES.values():
        for sel in cfg["selectors"]:
            for kind in ("node", "way", "relation"):
                parts.append(f'{kind}[{sel}][name]({s},{w},{n},{e});')
    q = f"[out:json][timeout:180];({''.join(parts)});out center tags;"

    payload = {"endpoint": OVERPASS, "bbox": [round(v, 6) for v in bbox],
               "query_sha256": hashlib.sha256(q.encode()).hexdigest()}
    key = cache_key("overpass", payload)
    hit = cache_load(key)
    if hit is not None:
        return hit["body"], q, True
    if offline:
        raise RuntimeError("offline mode: no cached Overpass response")

    for attempt in range(MAX_ATTEMPTS):
        try:
            BUDGET.throttle("overpass")
            BUDGET.spend(OVERPASS)
            req = urllib.request.Request(
                OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as r:
                body = json.load(r)["elements"]
            cache_store(key, "overpass", payload, body)
            return body, q, False
        except urllib.error.HTTPError as e:
            if e.code in (429, 504) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(30 * (attempt + 1))
                continue
            raise
    raise RuntimeError("overpass exhausted retries")


def overture_fetch(bbox, offline=False):
    """Overture places over the envelope, at a pinned release."""
    s, w, n, e = bbox
    payload = {"tool": "overturemaps-cli", "type": "place",
               "release": OVERTURE_RELEASE,
               "bbox": [round(v, 6) for v in bbox]}
    key = cache_key("overture", payload)
    hit = cache_load(key)
    if hit is not None:
        return hit["body"], True
    if offline:
        raise RuntimeError("offline mode: no cached Overture response")

    BUDGET.spend("overture-download")
    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tf:
        tmp = tf.name
    try:
        subprocess.run(
            [str(OVERTURE_CLI), "download", f"--bbox={w},{s},{e},{n}",
             "-f", "geojson", "--type=place", "-r", OVERTURE_RELEASE,
             "-o", tmp],
            check=True, capture_output=True, timeout=1800)
        feats = json.loads(Path(tmp).read_text())["features"]
    finally:
        Path(tmp).unlink(missing_ok=True)
        Path(tmp + ".state").unlink(missing_ok=True)
    cache_store(key, "overture", payload, feats)
    return feats, False


# --------------------------------------------------------------------------
# Geometry (projected CRS only -- never lat/lon; BENCHMARK_PLAN.md §5)
# --------------------------------------------------------------------------
def projections(lat, lon):
    aeqd = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 "
        f"+datum=WGS84 +units=m +no_defs")
    laea = CRS.from_proj4(
        f"+proj=laea +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 "
        f"+datum=WGS84 +units=m +no_defs")
    wgs = CRS.from_epsg(4326)
    return {
        "aeqd": aeqd, "laea": laea,
        "fwd": Transformer.from_crs(wgs, aeqd, always_xy=True).transform,
        "inv": Transformer.from_crs(aeqd, wgs, always_xy=True).transform,
        "fwd_laea": Transformer.from_crs(wgs, laea, always_xy=True).transform,
    }


def clean(geom):
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon",
                                                          "MultiPolygon")]
        geom = unary_union(polys) if polys else geom
    return geom


def isochrone_geometry(iso):
    """Full isochrone: every feature, every polygon, holes subtracted."""
    polys = []
    for f in iso.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") in ("Polygon", "MultiPolygon"):
            polys.append(clean(shape(g)))
    if not polys:
        raise RuntimeError("isochrone contained no polygonal geometry")
    return clean(unary_union(polys))


def repo_first_ring_geometry(iso):
    """What the shipped code actually reads: features[0].coordinates[0].

    Used only to quantify the size of that truncation, never as reference.
    """
    g = iso["features"][0]["geometry"]
    ring = (g["coordinates"][0] if g["type"] == "Polygon"
            else g["coordinates"][0][0])
    return clean(Polygon(ring))


def component_containing(geom, pt):
    comps = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for c in comps:
        if c.covers(pt):
            return c
    return None


def circle(radius):
    return Point(0, 0).buffer(radius, quad_segs=CIRCLE_QUAD_SEGS)


def max_radius_to(polygon):
    """Smallest circle centred at the origin containing `polygon`.

    The maximum of a convex function over a polygon is attained at a vertex,
    so scanning exterior vertices is exact.
    """
    return max(math.hypot(x, y) for x, y in polygon.exterior.coords)


def envelope_bbox_wgs84(geoms, inv, margin=ENVELOPE_MARGIN):
    """Union bbox in projected metres, expanded, returned as a lat/lon bbox.

    The projected rectangle is densified before inverse transform so the
    returned lat/lon bbox provably contains it (a projected rectangle is not a
    lat/lon rectangle).
    """
    minx, miny, maxx, maxy = unary_union(geoms).bounds
    dx, dy = (maxx - minx) * margin, (maxy - miny) * margin
    minx, miny, maxx, maxy = minx - dx, miny - dy, maxx + dx, maxy + dy
    rect = box(minx, miny, maxx, maxy)

    xs, ys = [], []
    n = 100
    for i in range(n + 1):
        t = i / n
        for x, y in ((minx + t * (maxx - minx), miny),
                     (minx + t * (maxx - minx), maxy),
                     (minx, miny + t * (maxy - miny)),
                     (maxx, miny + t * (maxy - miny))):
            lon, lat = inv(x, y)
            xs.append(lon)
            ys.append(lat)
    return (min(ys), min(xs), max(ys), max(xs)), rect


def geom_metrics(candidate, reference):
    inter = candidate.intersection(reference).area
    union = candidate.union(reference).area
    return {
        "candidate_area_km2": candidate.area / 1e6,
        "reference_area_km2": reference.area / 1e6,
        "intersection_km2": inter / 1e6,
        "iou": (inter / union) if union else None,
        "false_inclusion_area_km2": candidate.difference(reference).area / 1e6,
        "false_exclusion_area_km2": reference.difference(candidate).area / 1e6,
        "symmetric_difference_km2": candidate.symmetric_difference(
            reference).area / 1e6,
        "symmetric_difference_pct_of_reference": (
            candidate.symmetric_difference(reference).area / reference.area
            if reference.area else None),
    }


# --------------------------------------------------------------------------
# POI universe -- fetched once over the envelope, deduped once, then frozen.
#
# Dedup deliberately reuses the production rules so the universe is the
# product's universe; only the *extent* is corrected. No boundary filter is
# applied here -- that is exactly the bug this construction exists to avoid.
# --------------------------------------------------------------------------
def build_universe(elements, overture_feats, lat):
    m_lon = pipeline.m_per_deg_lon(lat)
    buckets = {k: [] for k in CATEGORIES}
    seen_ids = set()
    stats = {"osm_raw": len(elements), "osm_kept": 0, "osm_dropped_dup": 0,
             "overture_raw": len(overture_feats), "overture_kept": 0,
             "overture_dropped_dup": 0, "overture_dropped_confidence": 0}

    def near_duplicate(items, name, plat, plon):
        for it in items:
            if it["name"] != name:
                continue
            dx = (it["lon"] - plon) * m_lon
            dy = (it["lat"] - plat) * pipeline.M_PER_DEG_LAT
            if dx * dx + dy * dy < 150 ** 2:
                return True
        return False

    for el in elements:
        eid = f'{el["type"]}/{el["id"]}'
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        plat, plon = element_coords(el)
        if plat is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name")
        key = categorize(tags)
        if not name or key is None:
            continue
        if key == "health" and re.match(r"^\d+ ", name):
            continue
        if near_duplicate(buckets[key], name, plat, plon):
            stats["osm_dropped_dup"] += 1
            continue
        addr = " ".join(filter(None, [
            tags.get("addr:housenumber"), tags.get("addr:street"),
            tags.get("addr:city")])) or None
        buckets[key].append({"name": name, "lat": round(plat, 6),
                             "lon": round(plon, 6), "addr": addr,
                             "src": "osm", "osm": eid})
        stats["osm_kept"] += 1

    for f in overture_feats:
        props = f.get("properties") or {}
        if (props.get("confidence") or 0) < MIN_CONFIDENCE:
            stats["overture_dropped_confidence"] += 1
            continue
        key = map_category((props.get("categories") or {}).get("primary"))
        if key is None or key not in buckets:
            continue
        name = (props.get("names") or {}).get("primary")
        if not name:
            continue
        plon, plat = f["geometry"]["coordinates"][:2]
        addr = None
        if props.get("addresses"):
            a = props["addresses"][0]
            addr = " ".join(filter(None, [a.get("freeform"),
                                          a.get("locality")])) or None
        if same_place(buckets[key], name, plat, plon, addr):
            stats["overture_dropped_dup"] += 1
            continue
        buckets[key].append({"name": name, "lat": round(plat, 6),
                             "lon": round(plon, 6), "addr": addr,
                             "src": "overture"})
        stats["overture_kept"] += 1

    universe = []
    for key, items in buckets.items():
        for it in items:
            universe.append(dict(it, category=key))
    return universe, stats


def rate(numer, denom):
    """Zero denominator is undefined -- never 0% (BENCHMARK_PLAN.md §2)."""
    if denom == 0:
        return {"rate": None, "numerator": 0, "denominator": 0,
                "status": "undefined / insufficient POIs"}
    return {"rate": numer / denom, "numerator": numer, "denominator": denom,
            "status": "defined"}


def poi_metrics(points, in_ref, in_cand, categories):
    fi_n = sum(1 for i in points if in_cand[i] and not in_ref[i])
    fi_d = sum(1 for i in points if in_cand[i])
    fe_n = sum(1 for i in points if in_ref[i] and not in_cand[i])
    fe_d = sum(1 for i in points if in_ref[i])
    out = {"false_inclusion": rate(fi_n, fi_d),
           "false_exclusion": rate(fe_n, fe_d), "by_category": {}}
    for cat in CATEGORIES:
        idx = [i for i in points if categories[i] == cat]
        out["by_category"][cat] = {
            "false_inclusion": rate(
                sum(1 for i in idx if in_cand[i] and not in_ref[i]),
                sum(1 for i in idx if in_cand[i])),
            "false_exclusion": rate(
                sum(1 for i in idx if in_ref[i] and not in_cand[i]),
                sum(1 for i in idx if in_ref[i])),
        }
    return out


def aggregate(per_location, cand, metric):
    """Macro excludes undefined and reports n; micro pools numer/denom (§2)."""
    vals, num, den, used = [], 0, 0, []
    for loc_id, loc in per_location.items():
        m = loc["candidates"].get(cand)
        if not m or "poi" not in m:
            continue
        r = m["poi"][metric]
        if r["status"] == "defined":
            vals.append(r["rate"])
            used.append(loc_id)
        num += r["numerator"]
        den += r["denominator"]
    return {
        "macro": {"value": (sum(vals) / len(vals)) if vals else None,
                  "n": len(vals), "locations": used,
                  "status": "defined" if vals else "undefined"},
        "micro": {"value": (num / den) if den else None,
                  "numerator": num, "denominator": den,
                  "status": "defined" if den else "undefined"},
    }


def validity_failures(per_location):
    """Collect the preregistered run-invalidating conditions.

    These are gates, not annotations. BENCHMARK_PLAN.md §4 says a G1 failure
    means "results must not be interpreted until it is fixed", §7 says an A1
    failure means "the run fails". Recording `pass: false` and then publishing
    a verdict anyway would defeat the point of preregistering them.
    """
    out = []
    for loc_id, loc in per_location.items():
        if loc.get("status") != "ok":
            continue
        g1 = loc.get("guardrail_G1_equal_area_identity")
        if g1 and not g1.get("pass"):
            out.append({
                "location": loc_id, "gate": "G1_equal_area_identity",
                "detail": (f"|FI-FE| / area = {g1['relative_to_area']:.3e} "
                           f">= tolerance {g1['tolerance']}"),
                "consequence": ("projection, polygon algebra or isochrone "
                                "area computation is buggy")})
        g2 = loc.get("guardrail_G2_projection")
        if g2 and not g2.get("pass"):
            out.append({
                "location": loc_id, "gate": "G2_projection",
                "detail": (f"AEQD vs LAEA relative difference "
                           f"{g2['relative_difference']:.3e} >= tolerance "
                           f"{g2['tolerance']}"),
                "consequence": "projection handling is suspect"})
        for name, a in (loc.get("assertion_A1_envelope_slack") or {}).items():
            if not a.get("pass"):
                out.append({
                    "location": loc_id, "gate": "A1_envelope_slack",
                    "detail": (f"candidate {name} slack "
                               f"{a['slack_m']:.1f} m <= 0"),
                    "consequence": ("query envelope margin was insufficient; "
                                    "POIs must be re-fetched over a larger "
                                    "envelope")})
    return out


def run_status(validity, blocked, budget_blocked, skipped):
    """Single source of truth for run status and whether verdicts may publish.

    Verdicts are published ONLY by a run that finished everything it set out
    to do. A run missing a location is no longer scored on the preregistered
    sample, and dropping one is not a neutral act: on run
    20260729T081336Z, losing `sjc_airport` alone moves the equal-area circle
    from NOT FIT to FIT FOR PURPOSE, because P4 tolerates 4 of 5 locations.
    Publishing that would be a formal conclusion drawn from a sample chosen,
    in effect, by whichever request happened to fail.

    Failed sub-metrics are withheld on too. A directional route cannot change
    a POI rate, but systematic route failures indicate a degraded routing
    service — which would make the isochrones themselves suspect. Deciding
    case by case which failures are benign reintroduces exactly the
    discretion preregistration exists to remove.
    """
    incomplete = list(blocked) + list(budget_blocked) + list(skipped)
    if validity:
        return {
            "status": "invalid: preregistered guardrail failed",
            "run_valid": False, "publish_verdicts": False,
            "incomplete": incomplete,
            "withheld_reason": (
                "A preregistered validity gate failed; BENCHMARK_PLAN.md "
                "forbids interpreting these results until it is fixed."),
        }
    if incomplete:
        return {
            "status": "benchmark blocked",
            "run_valid": True, "publish_verdicts": False,
            "incomplete": incomplete,
            "withheld_reason": (
                "The run did not complete. Verdicts are withheld because a "
                "partial run is no longer scored on the preregistered "
                "sample, and P4 tolerates 4 of 5 locations, so a missing "
                "location can silently flip a verdict."),
        }
    return {"status": "complete", "run_valid": True, "publish_verdicts": True,
            "incomplete": [], "withheld_reason": None}


def verdict(per_location, agg, cand):
    """Preregistered P1..P4. Thresholds are read-only after freeze."""
    checks, fails = {}, []
    fi, fe = agg[cand]["false_inclusion"], agg[cand]["false_exclusion"]

    p1 = fi["macro"]["status"] == "defined" and \
        fi["macro"]["value"] <= THRESH["macro_fi"]
    p2 = fe["macro"]["status"] == "defined" and \
        fe["macro"]["value"] <= THRESH["macro_fe"]
    checks["P1_macro_fi<=0.10"] = p1
    checks["P2_macro_fe<=0.20"] = p2
    if not p1:
        fails.append("P1")
    if not p2:
        fails.append("P2")

    p3, offenders = True, []
    for loc_id, loc in per_location.items():
        m = loc["candidates"].get(cand)
        if not m or "poi" not in m:
            continue
        a, b = m["poi"]["false_inclusion"], m["poi"]["false_exclusion"]
        if a["status"] == "defined" and a["rate"] > THRESH["loc_fi"]:
            p3 = False
            offenders.append(f"{loc_id} FI={a['rate']:.3f}")
        if b["status"] == "defined" and b["rate"] > THRESH["loc_fe"]:
            p3 = False
            offenders.append(f"{loc_id} FE={b['rate']:.3f}")
    checks["P3_per_location_caps"] = p3
    if not p3:
        fails.append("P3")

    ok_locs = 0
    for loc in per_location.values():
        m = loc["candidates"].get(cand)
        if not m or "poi" not in m:
            continue
        a, b = m["poi"]["false_inclusion"], m["poi"]["false_exclusion"]
        if (a["status"] == "defined" and b["status"] == "defined"
                and a["denominator"] >= THRESH["min_denom"]
                and b["denominator"] >= THRESH["min_denom"]):
            ok_locs += 1
    p4 = ok_locs >= THRESH["min_locations"]
    checks["P4_evidence_sufficiency"] = p4

    if not p4:
        return {"verdict": "INSUFFICIENT EVIDENCE", "checks": checks,
                "failed": fails, "locations_with_sufficient_poi": ok_locs,
                "offenders": offenders}
    return {"verdict": "FIT FOR PURPOSE" if not fails else "NOT FIT FOR PURPOSE",
            "checks": checks, "failed": fails,
            "locations_with_sufficient_poi": ok_locs, "offenders": offenders}


# --------------------------------------------------------------------------
def boundary_point_at_bearing(geom, bearing_deg, reach_m):
    """Where a ray from the origin last crosses `geom`'s boundary.

    Lets the directional check cover non-circular candidates (the true
    isochrone), which BENCHMARK_PLAN.md §4 requires for every formal
    candidate. The outermost crossing is taken, so a concave isochrone is
    measured at its actual edge along that bearing.
    """
    rad = math.radians(bearing_deg)
    ray = LineString([(0.0, 0.0),
                      (reach_m * math.sin(rad), reach_m * math.cos(rad))])
    hit = ray.intersection(geom.boundary)
    if hit.is_empty:
        return None
    pts = [hit] if hit.geom_type == "Point" else [
        g for g in getattr(hit, "geoms", []) if g.geom_type == "Point"]
    if not pts:
        return None
    return max(pts, key=lambda p: math.hypot(p.x, p.y))


def directional_check(origin_ll, proj, offline, radius_m=None, geom=None,
                      reach_m=None):
    """Valhalla route-estimated FREE-FLOW travel time to the boundary.

    NOT an observed travel time. /route and /isochrone share one road graph
    and one costing model, so this is a model self-consistency measurement.

    `radius_m` is used for circular candidates (exact, and identical to the
    behaviour of earlier runs); `geom` + `reach_m` handle arbitrary shapes.
    """
    out = []
    for b in BEARINGS:
        if radius_m is not None:
            rad = math.radians(b)
            x, y = radius_m * math.sin(rad), radius_m * math.cos(rad)
        else:
            p = boundary_point_at_bearing(geom, b, reach_m)
            if p is None:
                out.append({"bearing_deg": b,
                            "status": "no boundary crossing on this bearing"})
                continue
            x, y = p.x, p.y
        lon, lat = proj["inv"](x, y)
        req = {"locations": [{"lat": origin_ll[0], "lon": origin_ll[1]},
                             {"lat": lat, "lon": lon}],
               "costing": "auto",
               "directions_options": {"units": "kilometers"}}
        url = f"{VALHALLA}/route?json=" + urllib.parse.quote(json.dumps(req))
        rec = {"bearing_deg": b, "target_lat": round(lat, 6),
               "target_lon": round(lon, 6)}
        try:
            body = http_json_cached(url, timeout=60, host="valhalla",
                                    offline=offline)
            summ = body["trip"]["summary"]
            rec["valhalla_route_estimated_free_flow_min"] = round(
                summ["time"] / 60.0, 2)
            rec["route_length_km"] = round(summ["length"], 3)
            rec["status"] = "ok"
        except Exception as e:                       # noqa: BLE001
            rec["status"] = f"failed: {type(e).__name__}"
            where = (f"r={radius_m:.0f}m" if radius_m is not None
                     else "polygon boundary")
            BUDGET.blocked.append(f"route bearing {b} to {where}")
        out.append(rec)
    return out


def solve_target_fi(dists, in_ref, cap, target=TARGET_FI):
    """EXPLORATORY ONLY -- POI-dependent, excluded from the Phase E decision.

    Solved strictly after the POI universe is frozen, over [0, cap]. FI(r) is
    a step function and may be non-monotonic, so the rule is fixed in advance:
    take the LARGEST radius with FI <= target (max coverage at equal
    correctness). Hitting the cap is reported as `censored` and the envelope
    is NOT enlarged to re-tune (BENCHMARK_PLAN.md §6.4).
    """
    cands = sorted({0.0, cap} | {d for d in dists if d <= cap})
    best = None
    for r in cands:
        idx = [i for i, d in enumerate(dists) if d <= r]
        if not idx:
            continue
        fi = sum(1 for i in idx if not in_ref[i]) / len(idx)
        if fi <= target and (best is None or r > best["radius_m"]):
            best = {"radius_m": r, "fi": fi, "n_inside": len(idx)}
    if best is None:
        return {"status": "infeasible", "target_fi": target, "cap_m": cap}
    best.update({"status": "censored" if abs(best["radius_m"] - cap) < 1e-6
                 else "interior", "target_fi": target, "cap_m": cap})
    return best


# --------------------------------------------------------------------------
def run_location(loc, offline):
    lat0, lon0 = loc["lat"], loc["lon"]
    rec = {"id": loc["id"], "name": loc["name"],
           "network_type": loc["network_type"],
           "config_lat": lat0, "config_lon": lon0}

    slat, slon, snap_m = pipeline.snap_to_drivable(lat0, lon0)
    rec["snapped"] = {"lat": slat, "lon": slon, "snap_distance_m": snap_m}

    iso = pipeline.fetch_isochrone(slat, slon, minutes=10)
    rec["isochrone_fetched_utc"] = utcnow()

    proj = projections(slat, slon)
    rec["crs"] = {"projection": "Azimuthal Equidistant (AEQD), WGS84 datum",
                  "proj4": proj["aeqd"].to_proj4(),
                  "cross_check_proj4": proj["laea"].to_proj4()}

    iso_ll = isochrone_geometry(iso)
    iso_p = clean(shapely_transform(proj["fwd"], iso_ll))
    iso_laea = clean(shapely_transform(proj["fwd_laea"], iso_ll))

    # Guardrail G2 -- projection sanity.
    g2 = abs(iso_p.area - iso_laea.area) / iso_laea.area
    rec["guardrail_G2_projection"] = {
        "aeqd_area_km2": iso_p.area / 1e6, "laea_area_km2": iso_laea.area / 1e6,
        "relative_difference": g2, "tolerance": GUARD_G2, "pass": g2 < GUARD_G2}

    rec["isochrone"] = {
        "geometry_type": iso_ll.geom_type,
        "n_components": (len(iso_ll.geoms)
                         if iso_ll.geom_type == "MultiPolygon" else 1),
        "n_holes": sum(len(g.interiors) for g in
                       (iso_ll.geoms if iso_ll.geom_type == "MultiPolygon"
                        else [iso_ll])),
        "area_km2": iso_p.area / 1e6,
    }

    # --- DIAGNOSTIC (not part of the preregistered decision rule) ----------
    # How much does the reference geometry itself move when the origin is
    # snapped? The product snaps in pipeline.snap_to_drivable, but the shipped
    # Apple Park static artifact was generated without snapping. If this
    # number is large, the "10-minute area" is not a stable quantity and the
    # reference is conditional on an implementation detail.
    rec["snap_sensitivity_diagnostic"] = {
        "note": ("Diagnostic added during implementation. It measures the "
                 "stability of the reference geometry and takes no part in "
                 "the preregistered pass/fail rule."),
        "snapped_area_km2": iso_p.area / 1e6,
        "snapped_equal_area_radius_m": math.sqrt(iso_p.area / math.pi),
    }
    if snap_m:
        try:
            iso_cfg = pipeline.fetch_isochrone(lat0, lon0, minutes=10)
            cfg_p = clean(shapely_transform(proj["fwd"],
                                            isochrone_geometry(iso_cfg)))
            d = rec["snap_sensitivity_diagnostic"]
            d["unsnapped_area_km2"] = cfg_p.area / 1e6
            d["unsnapped_equal_area_radius_m"] = math.sqrt(cfg_p.area / math.pi)
            d["area_ratio_snapped_over_unsnapped"] = (
                iso_p.area / cfg_p.area if cfg_p.area else None)
        except Exception as e:                        # noqa: BLE001
            rec["snap_sensitivity_diagnostic"]["error"] = \
                f"{type(e).__name__}: {e}"

    # Size of the shipped coordinates[0] truncation.
    repo_p = clean(shapely_transform(proj["fwd"], repo_first_ring_geometry(iso)))
    rec["shipped_first_ring_bug"] = {
        "full_area_km2": iso_p.area / 1e6,
        "first_ring_area_km2": repo_p.area / 1e6,
        "area_understated_pct": (1 - repo_p.area / iso_p.area) * 100,
        "full_equal_area_radius_m": math.sqrt(iso_p.area / math.pi),
        "shipped_equal_area_radius_m": math.sqrt(repo_p.area / math.pi),
    }

    origin = Point(0, 0)
    comp = component_containing(iso_p, origin)
    if comp is None:
        rec["status"] = "geometry anomaly: snapped origin lies in no isochrone " \
                        "component; location skipped"
        return rec

    # --- POI-INDEPENDENT objects, all built before any POI request ---------
    r_equal = math.sqrt(iso_p.area / math.pi)
    r_insc = origin.distance(comp.boundary)
    r_cap = max_radius_to(comp)
    if not comp.covers(circle(r_insc * (1 - 1e-9))):
        r_insc *= (1 - 1e-6)          # numerical tolerance

    cands = {"equal_area_circle": circle(r_equal),
             "inscribed_circle_fixed_center": circle(r_insc),
             "true_isochrone": iso_p}
    search_cap = circle(r_cap)
    rec["radii_m"] = {"equal_area": r_equal, "inscribed": r_insc,
                      "search_cap": r_cap}

    bbox, env_rect = envelope_bbox_wgs84(
        [iso_p, cands["equal_area_circle"],
         cands["inscribed_circle_fixed_center"], search_cap], proj["inv"])
    rec["query_envelope"] = {
        "margin": ENVELOPE_MARGIN,
        "bbox_south_west_north_east": [round(v, 6) for v in bbox],
        "projected_bounds_m": list(env_rect.bounds),
        "composed_of": ["isochrone", "equal_area_circle",
                        "inscribed_circle_fixed_center", "search_cap"],
        "note": ("POI-independent by construction; the exploratory target-FI "
                 "candidate took no part in defining it"),
    }

    # --- ONE fetch, ONE dedup, then FREEZE --------------------------------
    elements, oq, osm_hit = overpass_fetch(bbox, offline=offline)
    overture_feats, ov_hit = overture_fetch(bbox, offline=offline)
    universe, stats = build_universe(elements, overture_feats, slat)
    rec["poi_universe"] = {
        "count": len(universe), "stats": stats,
        "overpass_cache_hit": osm_hit, "overture_cache_hit": ov_hit,
        "overture_release": OVERTURE_RELEASE,
        "overture_min_confidence": MIN_CONFIDENCE,
        "overpass_query_sha256": hashlib.sha256(oq.encode()).hexdigest(),
        "dedup_rules": {
            "osm_internal": "same name within 150 m, per category",
            "cross_source": ("merge_overture.same_place: exact name <300 m, "
                             "name containment <150 m, or equal house-number "
                             "+ first street word"),
        },
        "frozen_utc": utcnow(),
    }

    pts_p = [Point(proj["fwd"](p["lon"], p["lat"])) for p in universe]
    cats = [p["category"] for p in universe]
    idxs = list(range(len(universe)))
    in_ref = {i: iso_p.covers(pts_p[i]) for i in idxs}

    # Assertion A1 -- no candidate may touch the envelope edge.
    a1 = {}
    for name, g in list(cands.items()) + [("search_cap", search_cap)]:
        gb, eb = g.bounds, env_rect.bounds
        slack = min(gb[0] - eb[0], gb[1] - eb[1], eb[2] - gb[2], eb[3] - gb[3])
        a1[name] = {"slack_m": slack, "pass": slack > 0}
    rec["assertion_A1_envelope_slack"] = a1

    rec["candidates"] = {}
    for name, geom in cands.items():
        entry = {}
        if name == "true_isochrone":
            entry["geometry"] = {
                "iou": 1.0, "note": (
                    "Definitional, not evidence: the true isochrone is the "
                    "reference geometry being compared with itself. Its merit "
                    "is only that it adds no approximation on top of the "
                    "model's; it is NOT real-world accuracy."),
                "candidate_area_km2": iso_p.area / 1e6,
                "reference_area_km2": iso_p.area / 1e6,
                "false_inclusion_area_km2": 0.0,
                "false_exclusion_area_km2": 0.0,
                "symmetric_difference_km2": 0.0}
        else:
            entry["geometry"] = geom_metrics(geom, iso_p)
        # BENCHMARK_PLAN.md §4 requires this for EVERY formal candidate, so
        # the non-circular reference gets it too, via ray intersection.
        if name == "true_isochrone":
            entry["directional_check"] = directional_check(
                (slat, slon), proj, offline, geom=geom, reach_m=r_cap * 1.5)
        else:
            entry["directional_check"] = directional_check(
                (slat, slon), proj, offline, radius_m=rec["radii_m"][
                    "equal_area" if name == "equal_area_circle"
                    else "inscribed"])
        in_cand = {i: geom.covers(pts_p[i]) for i in idxs}
        entry["poi"] = poi_metrics(idxs, in_ref, in_cand, cats)
        rec["candidates"][name] = entry

    # Guardrail G1 -- equal-area identity, a geometry-code check only.
    ea = rec["candidates"]["equal_area_circle"]["geometry"]
    diff = abs(ea["false_inclusion_area_km2"] - ea["false_exclusion_area_km2"])
    rec["guardrail_G1_equal_area_identity"] = {
        "false_inclusion_km2": ea["false_inclusion_area_km2"],
        "false_exclusion_km2": ea["false_exclusion_area_km2"],
        "abs_difference_km2": diff,
        "relative_to_area": diff / (iso_p.area / 1e6),
        "tolerance": GUARD_G1,
        "pass": diff / (iso_p.area / 1e6) < GUARD_G1,
        "note": ("These two areas are identically equal by construction for an "
                 "equal-area candidate -- ONE number, not two independent "
                 "findings. Used only to detect projection/polygon-algebra "
                 "bugs."),
    }

    # --- EXPLORATORY, solved only now that the universe is frozen ---------
    dists = [math.hypot(p.x, p.y) for p in pts_p]
    sol = solve_target_fi(dists, in_ref, r_cap)
    if sol["status"] in ("interior", "censored"):
        g = circle(sol["radius_m"])
        in_c = {i: g.covers(pts_p[i]) for i in idxs}
        sol["poi"] = poi_metrics(idxs, in_ref, in_c, cats)
        sol["geometry"] = geom_metrics(g, iso_p)
        gb, eb = g.bounds, env_rect.bounds
        sol["envelope_slack_m"] = min(gb[0] - eb[0], gb[1] - eb[1],
                                      eb[2] - gb[2], eb[3] - gb[3])
    rec["exploratory_target_fi_radius"] = dict(
        sol, excluded_from_decision=True,
        exclusion_reasons=[
            "POI-dependent: violates the POI-independence admission rule",
            "tuned on the same POIs it is then scored against"])

    rec["universe_points"] = [
        dict(p, in_isochrone=in_ref[i],
             distance_from_origin_m=round(dists[i], 1))
        for i, p in enumerate(universe)]
    rec["status"] = "ok"
    return rec


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="benchmark only the first N locations")
    ap.add_argument("--offline", action="store_true",
                    help="use cached responses only; never touch the network")
    ap.add_argument("--out", default=None, help="override run directory")
    args = ap.parse_args()

    plan_hash = sha256_file(PLAN_PATH)
    config_hash = sha256_file(CONFIG_PATH)
    CACHE_SALT["config_sha256"] = config_hash
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    locations = cfg["locations"][:args.limit] if args.limit else cfg["locations"]

    started = utcnow()
    run_id = (f"{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}"
              f"_cfg{config_hash[:8]}_plan{plan_hash[:8]}")
    out_dir = Path(args.out) if args.out else RUNS_DIR / run_id
    if out_dir.exists():
        raise SystemExit(f"run directory already exists, refusing to "
                         f"overwrite: {out_dir}")
    out_dir.mkdir(parents=True)

    # ---- preflight manifest: written BEFORE the first external request ----
    preflight = {
        "run_id": run_id, "started_utc": started,
        "plan_sha256": plan_hash, "config_sha256": config_hash,
        "plan_path": str(PLAN_PATH.relative_to(ROOT)),
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "locations": [loc["id"] for loc in locations],
        "thresholds": THRESH, "bearings": BEARINGS,
        "envelope_margin": ENVELOPE_MARGIN,
        "max_requests": MAX_REQUESTS,
        "overture_release": OVERTURE_RELEASE,
        "provenance": provenance(),
        "note": ("Written before any network access, so preregistration "
                 "ordering does not depend on being able to create commits."),
    }
    (out_dir / "preflight.json").write_text(json.dumps(preflight, indent=1))
    (out_dir / "plan.md").write_text(PLAN_PATH.read_text())
    print(f"preflight written: {out_dir/'preflight.json'}")

    # install cached transport over the production code path
    pipeline.http_json = lambda url, timeout=60: http_json_cached(
        url, timeout=timeout, host="valhalla", offline=args.offline)

    per_location, blocked = {}, []
    for loc in locations:
        print(f"[{loc['id']}] …", flush=True)
        try:
            per_location[loc["id"]] = run_location(loc, args.offline)
            print(f"[{loc['id']}] {per_location[loc['id']]['status']}")
        except Exception as e:                        # noqa: BLE001
            blocked.append({"location": loc["id"], "error": f"{type(e).__name__}: {e}"})
            print(f"[{loc['id']}] BLOCKED: {e}")

    formal = ["equal_area_circle", "inscribed_circle_fixed_center",
              "true_isochrone"]
    ok_locs = {k: v for k, v in per_location.items() if v.get("status") == "ok"}
    agg = {c: {m: aggregate(ok_locs, c, m)
               for m in ("false_inclusion", "false_exclusion")}
           for c in formal}

    # ---- preregistered validity gates, ENFORCED ---------------------------
    # BENCHMARK_PLAN.md makes these run-invalidating, not merely recorded:
    #   A1 -- "the run fails" if any candidate touches the envelope edge
    #   G1 -- "results must not be interpreted until it is fixed"
    #   G2 -- projection handling "is suspect"
    # A run that trips any of them must not publish verdicts.
    validity = validity_failures(per_location)
    skipped = [f"{k}: {v.get('status')}" for k, v in per_location.items()
               if v.get("status") != "ok"]
    state = run_status(validity, blocked, BUDGET.blocked, skipped)
    status, incomplete = state["status"], state["incomplete"]

    if state["publish_verdicts"]:
        verdicts = {c: verdict(ok_locs, agg, c) for c in formal}
    else:
        verdicts = {c: {"verdict": "WITHHELD",
                        "reason": state["withheld_reason"],
                        "validity_failures": validity,
                        "incomplete": incomplete} for c in formal}

    results = {
        "run_id": run_id, "started_utc": started, "finished_utc": utcnow(),
        "plan_sha256": plan_hash, "config_sha256": config_hash,
        "reference_geometry": "Valhalla 10-minute auto isochrone (free-flow)",
        "scope_limit": ("Measures agreement with Valhalla's free-flow model. "
                        "Does NOT measure real-world drive time."),
        "provenance": provenance(),
        "requests_made": BUDGET.used, "cache_hits": BUDGET.cache_hits,
        "request_budget": MAX_REQUESTS,
        "blocked": incomplete,
        "validity_failures": validity,
        "run_valid": state["run_valid"],
        "verdicts_published": state["publish_verdicts"],
        "status": status,
        "thresholds": THRESH,
        "formal_candidates": formal,
        "exploratory_candidates_excluded_from_decision":
            ["target_fi_radius"],
        "aggregates": agg, "verdicts": verdicts,
        "locations": {k: {kk: vv for kk, vv in v.items()
                          if kk != "universe_points"}
                      for k, v in per_location.items()},
    }
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False))

    # Columnar + compact: the same frozen set, small enough to live in a
    # public repository, with a content hash so it can be integrity-checked.
    cols = ["name", "category", "src", "lat", "lon", "in_isochrone",
            "distance_from_origin_m"]
    uni = {}
    for k, v in per_location.items():
        rows = [[p["name"], p["category"], p["src"], p["lat"], p["lon"],
                 int(p["in_isochrone"]), p["distance_from_origin_m"]]
                for p in v.get("universe_points", [])]
        canon = json.dumps(rows, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
        uni[k] = {"acquisition": v.get("poi_universe"),
                  "n_points": len(rows),
                  "points_sha256": hashlib.sha256(canon.encode()).hexdigest(),
                  "points": rows}
    (out_dir / "poi_universe.json").write_text(json.dumps({
        "run_id": run_id, "config_sha256": config_hash,
        "overture_release": OVERTURE_RELEASE,
        "overture_min_confidence": MIN_CONFIDENCE,
        "columns": cols,
        "note": ("One frozen universe per location, fetched once over the "
                 "union envelope and deduped once. Every candidate boundary "
                 "is scored on exactly this set. `in_isochrone` is membership "
                 "in the Valhalla reference geometry, so all POI-level rates "
                 "in results.json are recomputable from this file alone."),
        "locations": uni,
    }, ensure_ascii=False, separators=(",", ":")))

    (out_dir / "report.md").write_text(render_report(results, per_location))
    try:
        out_rel = str(out_dir.relative_to(ROOT))
    except ValueError:
        out_rel = str(out_dir)
    LATEST.write_text(json.dumps({
        "run_id": run_id, "path": out_rel,
        "finished_utc": results["finished_utc"], "status": results["status"],
        "note": "Pointer to the newest run only; historical runs are immutable.",
    }, indent=1))

    print(f"\nrun {run_id}: {results['status']}  "
          f"requests={BUDGET.used} cache_hits={BUDGET.cache_hits}")
    for c in formal:
        print(f"  {c:34s} {verdicts[c]['verdict']}")


def _pct(r):
    if r is None or r.get("status") != "defined":
        return "undefined"
    return f"{r['rate']*100:.1f}% ({r['numerator']}/{r['denominator']})"


def render_report(results, per_location):
    L = []
    a = L.append
    a("# Accuracy Benchmark — Run Report\n")
    a(f"**Run ID:** `{results['run_id']}`  ")
    a(f"**Started / finished (UTC):** {results['started_utc']} / "
      f"{results['finished_utc']}  ")
    a(f"**Plan SHA-256:** `{results['plan_sha256']}`  ")
    a(f"**Config SHA-256:** `{results['config_sha256']}`  ")
    a(f"**Status:** {results['status']}  ")
    a(f"**External requests:** {results['requests_made']} "
      f"(budget {results['request_budget']}), "
      f"cache hits {results['cache_hits']}\n")
    a("## Scope limit\n")
    a("> The reference geometry is **Valhalla's own 10-minute free-flow "
      "isochrone**. Every figure below measures agreement with Valhalla's "
      "model. None of it measures real-world drive time, which would require "
      "GPS traces, historical traffic data, or field sampling. Valhalla routes "
      "on posted speed limits with no live or historical traffic.\n")

    if results.get("validity_failures"):
        a("## RUN INVALID — a preregistered guardrail failed\n")
        a("Verdicts are withheld. `BENCHMARK_PLAN.md` treats these as gates, "
          "not annotations: results must not be interpreted until the cause "
          "is fixed.\n")
        a("| Location | Gate | Detail | Consequence |")
        a("|---|---|---|---|")
        for v in results["validity_failures"]:
            a(f"| {v['location']} | `{v['gate']}` | {v['detail']} | "
              f"{v['consequence']} |")
        a("")
    if results.get("blocked"):
        a("## Incomplete work — verdicts withheld\n")
        a(f"Status `{results['status']}`. The following did not complete and "
          f"are listed rather than silently dropped:\n")
        for b in results["blocked"]:
            a(f"- {b}")
        a("")
        a("Verdicts are **withheld**: a partial run is no longer scored on "
          "the preregistered sample, and P4 tolerates 4 of 5 locations, so a "
          "single missing location can flip a verdict.\n")

    a("## Verdicts against the preregistered rule\n")
    t = results["thresholds"]
    a(f"Thresholds (frozen before the run): macro FI ≤ {t['macro_fi']}, "
      f"macro FE ≤ {t['macro_fe']}, per-location FI ≤ {t['loc_fi']} and "
      f"FE ≤ {t['loc_fe']}, ≥ {t['min_locations']} locations with both rates "
      f"defined at denominator ≥ {t['min_denom']}.\n")
    a("| Candidate | Macro FI | Macro FE | Micro FI | Micro FE | Verdict |")
    a("|---|---|---|---|---|---|")
    for c in results["formal_candidates"]:
        ag, v = results["aggregates"][c], results["verdicts"][c]
        fi, fe = ag["false_inclusion"], ag["false_exclusion"]
        f = lambda d: ("undefined" if d["status"] != "defined"      # noqa: E731
                       else f"{d['value']*100:.1f}%")
        a(f"| `{c}` | {f(fi['macro'])} (n={fi['macro']['n']}) | "
          f"{f(fe['macro'])} (n={fe['macro']['n']}) | "
          f"{f(fi['micro'])} | {f(fe['micro'])} | **{v['verdict']}** |")
    a("")
    a("### Two of these numbers are definitional and carry no information\n")
    a("- `true_isochrone` scores perfectly **by definition** — it is the "
      "reference compared with itself. That is not evidence of accuracy; it "
      "only means it adds no approximation on top of the model's.")
    a("- `inscribed_circle_fixed_center` has **FI = 0 by construction**: a "
      "circle inscribed in the isochrone is a subset of it, so no point "
      "inside the circle can be outside the reference. Its FI column is "
      "guaranteed, not measured. Only its **FE** column carries information, "
      "and it may be described solely as *geometric false inclusion relative "
      "to the Valhalla reference is zero* — never as real-world accuracy.\n")
    a("Only `equal_area_circle` has both columns free to vary, so it is the "
      "only candidate whose full result is empirical.\n")

    a("### Interpreting any macro/micro divergence\n")
    a("If macro and micro disagree, the **only** admissible reading is that "
      "locations are heterogeneous and that this correlates with POI count. "
      "It may **not** be read as *error concentrates in road-network type X* "
      "or *different network types need different boundary rules*: this run "
      "has exactly **one location per network type (n = 1)**, so network "
      "type, POI density and site idiosyncrasy are perfectly confounded. Such "
      "a divergence is a reason to enlarge the sample in Run 2, nothing "
      "more.\n")

    for loc_id, loc in per_location.items():
        a(f"## {loc['name']} — `{loc_id}` ({loc.get('network_type')})\n")
        if loc.get("status") != "ok":
            a(f"**{loc.get('status')}**\n")
            continue
        s = loc["snapped"]
        a(f"Snapped origin `{s['lat']:.6f}, {s['lon']:.6f}` "
          f"({s['snap_distance_m']} m from the configured pin). "
          f"CRS: AEQD centred on the snapped origin.\n")
        iso = loc["isochrone"]
        a(f"Isochrone: {iso['geometry_type']}, {iso['n_components']} "
          f"component(s), {iso['n_holes']} hole(s), "
          f"**{iso['area_km2']:.2f} km²**.\n")
        b = loc["shipped_first_ring_bug"]
        if b["area_understated_pct"] > 0.01:
            a(f"> **Shipped `coordinates[0]` truncation:** the production code "
              f"sees only {b['first_ring_area_km2']:.2f} km² of "
              f"{b['full_area_km2']:.2f} km² "
              f"(**{b['area_understated_pct']:.1f}% understated**), giving "
              f"radius {b['shipped_equal_area_radius_m']:.0f} m instead of "
              f"{b['full_equal_area_radius_m']:.0f} m.\n")
        sd = loc.get("snap_sensitivity_diagnostic", {})
        if sd.get("area_ratio_snapped_over_unsnapped"):
            a(f"> **Snap-sensitivity diagnostic** (not part of the decision "
              f"rule): the same attraction yields "
              f"{sd['unsnapped_area_km2']:.2f} km² unsnapped vs "
              f"{sd['snapped_area_km2']:.2f} km² after a "
              f"{s['snap_distance_m']} m snap — a "
              f"**{sd['area_ratio_snapped_over_unsnapped']:.2f}×** change, "
              f"radius {sd['unsnapped_equal_area_radius_m']:.0f} m → "
              f"{sd['snapped_equal_area_radius_m']:.0f} m.\n")
        r = loc["radii_m"]
        a(f"Radii — equal-area {r['equal_area']:.0f} m · inscribed "
          f"{r['inscribed']:.0f} m · search cap {r['search_cap']:.0f} m\n")
        u = loc["poi_universe"]
        a(f"Frozen POI universe: **{u['count']}** points "
          f"(OSM {u['stats']['osm_kept']} kept / "
          f"{u['stats']['osm_dropped_dup']} deduped; Overture "
          f"{u['stats']['overture_kept']} kept / "
          f"{u['stats']['overture_dropped_dup']} deduped / "
          f"{u['stats']['overture_dropped_confidence']} below confidence "
          f"{u['overture_min_confidence']}). Overture release "
          f"`{u['overture_release']}`.\n")

        a("| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |")
        a("|---|---|---|---|---|---|")
        for cname, c in loc["candidates"].items():
            g = c["geometry"]
            iou = g.get("iou")
            sd = g.get("symmetric_difference_km2", 0.0)
            sdp = g.get("symmetric_difference_pct_of_reference")
            a(f"| `{cname}` | {iou:.3f} | {sd:.2f} | "
              f"{(sdp*100 if sdp is not None else 0):.1f}% | "
              f"{_pct(c['poi']['false_inclusion'])} | "
              f"{_pct(c['poi']['false_exclusion'])} |")
        a("")

        g1 = loc["guardrail_G1_equal_area_identity"]
        a(f"**Guardrail G1** (equal-area identity): FI area "
          f"{g1['false_inclusion_km2']:.4f} km² vs FE area "
          f"{g1['false_exclusion_km2']:.4f} km², |Δ| "
          f"{g1['abs_difference_km2']:.6f} km² "
          f"({g1['relative_to_area']:.2e} of area) — "
          f"{'PASS' if g1['pass'] else 'FAIL'}. These are one number, not two "
          f"findings.  ")
        g2 = loc["guardrail_G2_projection"]
        a(f"**Guardrail G2** (projection): AEQD {g2['aeqd_area_km2']:.4f} km² "
          f"vs LAEA {g2['laea_area_km2']:.4f} km², relative difference "
          f"{g2['relative_difference']:.2e} — "
          f"{'PASS' if g2['pass'] else 'FAIL'}.  ")
        a1 = loc["assertion_A1_envelope_slack"]
        a(f"**Assertion A1** (no candidate touches the envelope edge): "
          f"{'PASS' if all(v['pass'] for v in a1.values()) else 'FAIL'} "
          f"(min slack {min(v['slack_m'] for v in a1.values()):.0f} m)\n")

        a("### Category split\n")
        a("| Category | Equal-area FI | Equal-area FE | Inscribed FI | "
          "Inscribed FE |")
        a("|---|---|---|---|---|")
        ea = loc["candidates"]["equal_area_circle"]["poi"]["by_category"]
        ic = loc["candidates"]["inscribed_circle_fixed_center"]["poi"][
            "by_category"]
        for cat in CATEGORIES:
            a(f"| {cat} | {_pct(ea[cat]['false_inclusion'])} | "
              f"{_pct(ea[cat]['false_exclusion'])} | "
              f"{_pct(ic[cat]['false_inclusion'])} | "
              f"{_pct(ic[cat]['false_exclusion'])} |")
        a("")

        a("### Directional consistency — Valhalla route-estimated free-flow "
          "travel time\n")
        a("> Directional routing checks measure consistency between the "
          "simplified boundary and Valhalla's routing model. They are **not** "
          "observed travel times and do **not** independently validate "
          "real-world traffic accuracy. Valhalla uses posted speed limits and "
          "carries no live traffic data.\n")
        a("| Bearing | Equal-area circle (min) | Inscribed circle (min) |")
        a("|---|---|---|")
        dc_e = {d["bearing_deg"]: d for d in
                loc["candidates"]["equal_area_circle"]["directional_check"]}
        dc_i = {d["bearing_deg"]: d for d in
                loc["candidates"]["inscribed_circle_fixed_center"][
                    "directional_check"]}
        for bg in BEARINGS:
            f = lambda d: d.get(                                  # noqa: E731
                "valhalla_route_estimated_free_flow_min", d.get("status"))
            a(f"| {bg}° | {f(dc_e[bg])} | {f(dc_i[bg])} |")
        a("")

        ex = loc["exploratory_target_fi_radius"]
        a(f"### Exploratory: target-FI radius (EXCLUDED from the decision)\n")
        a(f"Status `{ex['status']}`"
          + (f", radius {ex['radius_m']:.0f} m, cap {ex['cap_m']:.0f} m, "
             f"FI {ex['fi']*100:.1f}%" if "radius_m" in ex else "") + ".  ")
        a("Excluded because it is POI-dependent (violating the "
          "POI-independence admission rule) and is tuned on the same POIs it "
          "is then scored against.\n")

    a("## Cross-cutting observations\n")

    a("### Shipped `coordinates[0]` truncation: real in code, not exercised "
      "here\n")
    a("`pipeline.boundary_from_isochrone` and `make_boundary.py` read only "
      "`features[0].geometry.coordinates[0]`, discarding extra MultiPolygon "
      "components and all holes. Across these five locations Valhalla "
      "returned a **single-component, hole-free Polygon every time**, so the "
      "measured area understatement was **0.000 % at all five**. The defect "
      "is therefore **latent, not active** at this sample. It is not "
      "evidence that the code is correct — only that this sample did not "
      "reach it.\n")
    a("| Location | Components | Holes | Area understated |")
    a("|---|---|---|---|")
    for loc_id, loc in per_location.items():
        if loc.get("status") != "ok":
            continue
        i, b = loc["isochrone"], loc["shipped_first_ring_bug"]
        a(f"| {loc_id} | {i['n_components']} | {i['n_holes']} | "
          f"{b['area_understated_pct']:.3f}% |")
    a("")

    a("### Reference stability: the 10-minute area is not a stable quantity\n")
    a("Diagnostic only — no part of the decision rule. Road snapping moves "
      "the origin by an implementation-chosen distance, and the reference "
      "geometry moves with it:\n")
    a("| Location | Snap (m) | Unsnapped km² | Snapped km² | Ratio |")
    a("|---|---|---|---|---|")
    for loc_id, loc in per_location.items():
        d = loc.get("snap_sensitivity_diagnostic", {})
        if "area_ratio_snapped_over_unsnapped" not in d:
            continue
        a(f"| {loc_id} | {loc['snapped']['snap_distance_m']} | "
          f"{d['unsnapped_area_km2']:.2f} | {d['snapped_area_km2']:.2f} | "
          f"**{d['area_ratio_snapped_over_unsnapped']:.2f}×** |")
    a("")
    a("Where this ratio is large, the modelled 10-minute area depends more on "
      "a snapping heuristic than on the destination itself. That bounds how "
      "precisely *any* boundary rule can be said to represent \"10 minutes\", "
      "independently of which candidate is chosen.\n")
    return "\n".join(L)


if __name__ == "__main__":
    main()
