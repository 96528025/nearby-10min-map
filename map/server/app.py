#!/usr/bin/env python3
"""FastAPI backend: geocode search + cached two-phase area computation.

Run: uvicorn app:app --port 8642 --app-dir map/server

The first ``/api/area`` response contains the routed isochrone boundary (the
Valhalla polygon itself, or an explicit fixed-radius fallback) plus OSM
facilities filtered with that same geometry. Optional Overture enrichment runs
in a background thread. Its terminal states are ``complete`` and ``osm_only``;
clients poll the same URL while the response is ``enriching``.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import traceback
from pathlib import Path
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

import pipeline
from geocode_cache import GeocodeCoordinator

MAP_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = MAP_DIR.parent
DATA_DIR = MAP_DIR / "data"
WEB_DIST = ROOT_DIR / "web" / "dist"
CACHE = MAP_DIR / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

NOMINAL_BOUNDARY_WARNING = (
    "当前显示的是固定半径的近似范围，不是基于真实路网计算的约 10 分钟驾车可达范围。"
)
OVERTURE_DISABLED_WARNING = (
    "Overture enrichment is disabled for this deployment; current results "
    "use OSM facilities only."
)
OVERTURE_FAILED_WARNING = (
    "Overture enrichment failed; current OSM-only results remain usable."
)
OSM_LOOKUP_WARNING = (
    "OSM facility lookup is currently unavailable; the boundary is usable, "
    "but facility coverage is incomplete."
)
STATIC_JSON_CACHE_CONTROL = "no-cache, must-revalidate"


class RevalidatingJsonStaticFiles(StaticFiles):
    """Serve data JSON with validation while leaving other assets untouched."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.lower().endswith(".json"):
            response.headers["Cache-Control"] = STATIC_JSON_CACHE_CONTROL
        return response


def _env_enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


OVERTURE_ENRICHMENT_ENABLED = _env_enabled(
    "ENABLE_OVERTURE", default=True
)

app = FastAPI()
_lock = threading.Lock()
_enriching: set[str] = set()
_geocode = GeocodeCoordinator(CACHE / "geocode", pipeline.geocode)

# Each enrichment runs an overturemaps subprocess. This caps how many run at once
# in this process; a key without a free slot stays "enriching", and the client's
# next poll starts it.
MAX_CONCURRENT_ENRICHMENTS = max(
    1, int(os.getenv("MAX_CONCURRENT_ENRICHMENTS", "2"))
)


class _AreaFlight:
    """One in-progress phase-1 computation that identical requests wait on."""

    def __init__(self) -> None:
        self.done = Event()
        self.result: dict | None = None
        self.error: BaseException | None = None


_area_flights: dict[str, _AreaFlight] = {}
_area_flights_lock = threading.Lock()


def slug_for(lat: float, lon: float) -> str:
    """Match the repository's four-decimal file-cache contract."""
    return f"{lat:.4f}_{lon:.4f}"


def cache_path(slug: str) -> Path:
    return CACHE / f"{slug}.json"


def _write_area_cache(slug: str, area: dict) -> None:
    """Atomically replace an opportunistic cache entry."""
    CACHE.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(area, ensure_ascii=False)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CACHE,
            prefix=f".{slug}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialised)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, cache_path(slug))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _append_warning(area: dict, warning: str) -> None:
    warnings = area.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


CURRENT_BOUNDARY_MODES = frozenset({
    pipeline.ROUTED_BOUNDARY_MODE,
    pipeline.NOMINAL_BOUNDARY_MODE,
})


def _normalise_cached_area(area: dict) -> dict | None:
    """Return a servable cache entry, or None when it predates the isochrone.

    Entries written before the true-isochrone migration carry the retired
    ``routed_equal_area_circle`` mode (or no mode at all) and hold circle
    geometry. Serving one would label a circle as a routed isochrone, so such
    entries are treated as a cache miss and recomputed; the cache is an
    optimisation, not durable state.
    """
    boundary_mode = area.get("boundary_mode")
    if boundary_mode not in CURRENT_BOUNDARY_MODES:
        return None
    area.setdefault("warnings", [])
    metadata = area.get("facilities", {}).get("metadata")
    if isinstance(metadata, dict):
        metadata["filter"] = pipeline.facility_filter_for(boundary_mode)
    return area


@app.get("/api/health")
def api_health():
    """Local-only liveness probe; it never contacts a public upstream."""
    return {"status": "ok"}


@app.get("/api/geocode")
def api_geocode(q: str, bias_lat: float | None = None,
                bias_lon: float | None = None):
    if not q.strip():
        raise HTTPException(400, "empty query")
    try:
        return _geocode.geocode(q, bias_lat=bias_lat, bias_lon=bias_lon)
    except Exception as error:
        # Upstream errors can carry URLs and client details: log them, and
        # keep the response body generic.
        traceback.print_exc()
        raise HTTPException(502, "geocoding failed") from error


def _enrich_async(slug: str, area: dict):
    try:
        geometry = pipeline.boundary_geometry(area["boundary"])
        fac = pipeline.merge_overture(area["facilities"], geometry)
        total = pipeline.verify_inside(fac, geometry)
        area["facilities"] = fac
        area["status"] = "complete"
        area["total"] = total
        area.pop("enrich_error", None)
        _write_area_cache(slug, area)
    except Exception:
        traceback.print_exc()
        # Phase-one data stays served; this is a usable terminal state.
        area["status"] = "osm_only"
        area["enrich_error"] = True
        _append_warning(area, OVERTURE_FAILED_WARNING)
        _write_area_cache(slug, area)
    finally:
        with _lock:
            _enriching.discard(slug)


def _start_enrichment(slug: str, area: dict) -> None:
    """Start at most one enrichment per cache key, and at most
    MAX_CONCURRENT_ENRICHMENTS in total. A key that finds no free slot is not
    queued: its cache entry stays "enriching" and a later poll starts it."""
    with _lock:
        if slug in _enriching or len(_enriching) >= MAX_CONCURRENT_ENRICHMENTS:
            return
        _enriching.add(slug)
        threading.Thread(
            target=_enrich_async,
            args=(slug, copy.deepcopy(area)),
            daemon=True,
        ).start()


def _mark_overture_disabled(slug: str, area: dict) -> dict:
    area["status"] = "osm_only"
    area.pop("enrich_error", None)
    _append_warning(area, OVERTURE_DISABLED_WARNING)
    _write_area_cache(slug, area)
    return area


def _read_cached_area(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _serve_cached_area(slug: str) -> dict | None:
    area = _read_cached_area(cache_path(slug))
    if area is not None:
        # A retired-mode entry comes back as None and falls through to a
        # fresh computation that overwrites it.
        area = _normalise_cached_area(area)
    if area is None:
        return None
    if area.get("status") == "enriching":
        if OVERTURE_ENRICHMENT_ENABLED:
            # A prior worker can disappear mid-enrichment on Render's
            # ephemeral free service. The next request resumes the work.
            _start_enrichment(slug, area)
        else:
            _mark_overture_disabled(slug, area)
    return area


@app.get("/api/area")
def api_area(lat: float, lon: float, name: str = ""):
    slug = slug_for(lat, lon)
    area = _serve_cached_area(slug)
    if area is not None:
        return area

    # Identical cache misses share one phase-1 computation: the first request
    # computes and writes the cache; the others wait for its result instead of
    # repeating the snap, isochrone and Overpass calls.
    with _area_flights_lock:
        flight = _area_flights.get(slug)
        leader = flight is None
        if leader:
            flight = _AreaFlight()
            _area_flights[slug] = flight
    if not leader:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        return flight.result

    try:
        # A previous flight may have written the cache between our first read
        # and becoming the leader.
        area = _serve_cached_area(slug)
        if area is None:
            area = _compute_area(slug, lat, lon, name)
        flight.result = area
        return area
    except BaseException as error:
        flight.error = error
        raise
    finally:
        flight.done.set()
        with _area_flights_lock:
            _area_flights.pop(slug, None)


def _compute_area(slug: str, lat: float, lon: float, name: str) -> dict:
    warnings: list[str] = []
    boundary_mode = pipeline.ROUTED_BOUNDARY_MODE
    try:
        slat, slon, snap_m = pipeline.snap_to_drivable(lat, lon)
        try:
            isochrone = pipeline.fetch_isochrone(slat, slon)
            boundary = pipeline.boundary_from_isochrone(
                isochrone, slat, slon, name
            )
            boundary["metadata"]["requested_point"] = {
                "lat": lat,
                "lon": lon,
            }
            boundary["metadata"]["snap_distance_m"] = snap_m
        except Exception:
            # The fallback deliberately discards the snapped point: no road
            # network input may influence a nominal-radius result.
            boundary_mode = pipeline.NOMINAL_BOUNDARY_MODE
            boundary = pipeline.boundary_from_nominal_radius(lat, lon, name)
            boundary["metadata"]["requested_point"] = {
                "lat": lat,
                "lon": lon,
            }
            warnings.append(NOMINAL_BOUNDARY_WARNING)

        # One geometry object serves display, the OSM filter, the Overture
        # merge and the consistency guard.
        geometry = pipeline.boundary_geometry(boundary)
        try:
            facilities = pipeline.osm_facilities(
                geometry, boundary_mode=boundary_mode
            )
        except Exception:
            traceback.print_exc()
            facilities = pipeline.empty_osm_facilities(boundary_mode)
            warnings.append(OSM_LOOKUP_WARNING)
        total = pipeline.verify_inside(facilities, geometry)
    except AssertionError as error:
        traceback.print_exc()
        raise HTTPException(500, "boundary verification failed") from error
    except Exception as error:
        # Upstream errors can carry URLs and client details: log them, and
        # keep the response body generic.
        traceback.print_exc()
        raise HTTPException(502, "area computation failed") from error

    area = {
        "status": "enriching",
        "name": name,
        "lat": lat,
        "lon": lon,
        "boundary": boundary,
        "facilities": facilities,
        "total": total,
        "boundary_mode": boundary_mode,
        "warnings": warnings,
    }
    if not OVERTURE_ENRICHMENT_ENABLED:
        return _mark_overture_disabled(slug, area)

    _write_area_cache(slug, area)
    _start_enrichment(slug, area)
    return area


# API routes and /data must be registered before this HTML catch-all.
app.mount("/data", RevalidatingJsonStaticFiles(directory=DATA_DIR), name="data")
app.mount(
    "/",
    StaticFiles(directory=WEB_DIST, html=True, check_dir=False),
    name="frontend",
)
