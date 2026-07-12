#!/usr/bin/env python3
"""FastAPI backend: geocode search + area computation with cache.

Run: uvicorn app:app --port 8642 --app-dir map/server
Serves the static frontend from the map/ directory and exposes:
  GET /api/geocode?q=...        -> candidate locations (user confirms one)
  GET /api/area?lat=&lon=&name= -> boundary + facilities for that point.
     First call computes phase 1 (isochrone, circle, OSM facilities,
     ~5-15 s) synchronously and starts phase 2 (Overture merge, ~1 min)
     in the background; response carries status "enriching" until phase 2
     lands in the cache, then "complete". Clients poll the same URL.
"""
import json
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

import pipeline

MAP_DIR = Path(__file__).resolve().parent.parent
CACHE = MAP_DIR / "cache"
CACHE.mkdir(exist_ok=True)

app = FastAPI()
_lock = threading.Lock()
_enriching: set[str] = set()


def slug_for(lat: float, lon: float) -> str:
    return f"{lat:.4f}_{lon:.4f}"


def cache_path(slug: str) -> Path:
    return CACHE / f"{slug}.json"


@app.get("/api/geocode")
def api_geocode(q: str, bias_lat: float | None = None,
                bias_lon: float | None = None):
    if not q.strip():
        raise HTTPException(400, "empty query")
    try:
        return {"candidates": pipeline.geocode(q.strip(), bias_lat=bias_lat,
                                               bias_lon=bias_lon)}
    except Exception as e:
        raise HTTPException(502, f"geocoding failed: {e}")


def _enrich_async(slug: str, area: dict):
    try:
        fac = pipeline.merge_overture(area["facilities"],
                                      area["boundary"]["features"][0]
                                      ["geometry"])
        total = pipeline.verify_inside(
            fac, area["boundary"]["features"][0]["geometry"])
        area["facilities"] = fac
        area["status"] = "complete"
        area["total"] = total
        cache_path(slug).write_text(json.dumps(area, ensure_ascii=False))
    except Exception:
        traceback.print_exc()
        # Phase-1 data stays served; mark enrichment as failed but usable.
        area["status"] = "osm_only"
        area["enrich_error"] = True
        cache_path(slug).write_text(json.dumps(area, ensure_ascii=False))
    finally:
        with _lock:
            _enriching.discard(slug)


@app.get("/api/area")
def api_area(lat: float, lon: float, name: str = ""):
    slug = slug_for(lat, lon)
    p = cache_path(slug)
    if p.exists():
        area = json.loads(p.read_text())
        with _lock:
            if area["status"] == "enriching" and slug not in _enriching:
                # server restarted mid-enrichment; resume it
                _enriching.add(slug)
                threading.Thread(target=_enrich_async, args=(slug, area),
                                 daemon=True).start()
        return area

    try:
        slat, slon, snap_m = pipeline.snap_to_drivable(lat, lon)
        iso = pipeline.fetch_isochrone(slat, slon)
        boundary = pipeline.boundary_from_isochrone(iso, slat, slon, name)
        boundary["metadata"]["requested_point"] = {"lat": lat, "lon": lon}
        boundary["metadata"]["snap_distance_m"] = snap_m
        geometry = boundary["features"][0]["geometry"]
        fac = pipeline.osm_facilities(geometry)
        total = pipeline.verify_inside(fac, geometry)
    except AssertionError as e:
        raise HTTPException(500, f"boundary verification failed: {e}")
    except Exception as e:
        raise HTTPException(502, f"area computation failed: {e}")

    area = {"status": "enriching", "name": name, "lat": lat, "lon": lon,
            "boundary": boundary, "facilities": fac, "total": total}
    p.write_text(json.dumps(area, ensure_ascii=False))
    with _lock:
        if slug not in _enriching:
            _enriching.add(slug)
            threading.Thread(target=_enrich_async, args=(slug, area),
                             daemon=True).start()
    return area


# static frontend (mounted last so /api/* wins)
app.mount("/", StaticFiles(directory=MAP_DIR, html=True), name="static")
