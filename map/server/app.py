#!/usr/bin/env python3
"""FastAPI backend: geocode search + cached two-phase area computation.

Run: uvicorn app:app --port 8642 --app-dir map/server

The first ``/api/area`` response contains the routed boundary (or an explicit
fixed-radius fallback) plus OSM facilities. Optional Overture enrichment runs
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


def _normalise_cached_area(area: dict) -> dict:
    """Add the Stage 3 optional fields to legacy routed cache entries."""
    boundary_mode = area.setdefault(
        "boundary_mode", pipeline.ROUTED_BOUNDARY_MODE
    )
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
        raise HTTPException(502, f"geocoding failed: {error}") from error


def _enrich_async(slug: str, area: dict):
    try:
        fac = pipeline.merge_overture(
            area["facilities"],
            area["boundary"]["features"][0]["geometry"],
        )
        total = pipeline.verify_inside(
            fac, area["boundary"]["features"][0]["geometry"]
        )
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
    """Start at most one in-process enrichment flight per cache key."""
    with _lock:
        if slug in _enriching:
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


@app.get("/api/area")
def api_area(lat: float, lon: float, name: str = ""):
    slug = slug_for(lat, lon)
    area = _read_cached_area(cache_path(slug))
    if area is not None:
        area = _normalise_cached_area(area)
        if area.get("status") == "enriching":
            if OVERTURE_ENRICHMENT_ENABLED:
                # A prior worker can disappear mid-enrichment on Render's
                # ephemeral free service. The next request resumes the work.
                _start_enrichment(slug, area)
            else:
                _mark_overture_disabled(slug, area)
        return area

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

        geometry = boundary["features"][0]["geometry"]
        facilities = pipeline.osm_facilities(
            geometry, boundary_mode=boundary_mode
        )
        total = pipeline.verify_inside(facilities, geometry)
    except AssertionError as error:
        raise HTTPException(
            500, f"boundary verification failed: {error}"
        ) from error
    except Exception as error:
        raise HTTPException(
            502, f"area computation failed: {error}"
        ) from error

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
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
app.mount(
    "/",
    StaticFiles(directory=WEB_DIST, html=True, check_dir=False),
    name="frontend",
)
