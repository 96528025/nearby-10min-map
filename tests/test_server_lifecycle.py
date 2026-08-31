"""Offline tests for cache-backed enrichment lifecycle recovery."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import app as server_app


LAT = 37.4275
LON = -122.1697
NAME = "Stanford University"


def boundary():
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [-122.18, 37.42],
            [-122.16, 37.42],
            [-122.16, 37.44],
            [-122.18, 37.44],
            [-122.18, 37.42],
        ]],
    }
    return {
        "type": "FeatureCollection",
        "metadata": {
            "method": "test routed equal-area circle",
            "center": {"lat": LAT, "lon": LON, "name": NAME},
            "radius_m": 1000,
        },
        "features": [{
            "type": "Feature",
            "properties": {"contour": "approx 10 min drive"},
            "geometry": geometry,
        }],
    }


def facilities():
    return {
        "metadata": {
            "source": "OpenStreetMap",
            "filter": server_app.pipeline.ROUTED_FACILITY_FILTER,
        },
        "categories": {},
    }


def cached_area(status="enriching"):
    area = {
        "status": status,
        "name": NAME,
        "lat": LAT,
        "lon": LON,
        "boundary": boundary(),
        "facilities": facilities(),
        "total": 0,
        "boundary_mode": server_app.pipeline.ROUTED_BOUNDARY_MODE,
        "warnings": [],
    }
    if status == "osm_only":
        area["enrich_error"] = True
    return area


@pytest.fixture(autouse=True)
def isolated_cache_and_enrichment_state(monkeypatch, tmp_path):
    """Never read the repository cache or retain single-flight state."""
    monkeypatch.setattr(server_app, "CACHE", tmp_path)
    with server_app._lock:
        server_app._enriching.clear()
    yield
    with server_app._lock:
        server_app._enriching.clear()


@pytest.fixture
def started_threads(monkeypatch):
    """Capture background starts without running enrichment code."""
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append(self)

    # Replacing app's module reference avoids changing threading.Thread used by
    # ThreadPoolExecutor in the concurrency test below.
    monkeypatch.setattr(
        server_app,
        "threading",
        SimpleNamespace(Thread=FakeThread),
    )
    return started


def write_cached_area(status="enriching"):
    slug = server_app.slug_for(LAT, LON)
    path = server_app.cache_path(slug)
    area = cached_area(status)
    path.write_text(json.dumps(area))
    return slug, area


def test_enriching_cache_is_resumed_once_after_process_restart(started_threads):
    slug, expected = write_cached_area("enriching")
    assert slug not in server_app._enriching

    first = server_app.api_area(LAT, LON, NAME)
    second = server_app.api_area(LAT, LON, NAME)

    assert first == expected
    assert second == expected
    assert slug in server_app._enriching
    assert len(started_threads) == 1
    thread = started_threads[0]
    assert thread.target is server_app._enrich_async
    assert thread.args[0] == slug
    assert thread.args[1] == expected
    assert thread.daemon is True


def test_concurrent_duplicate_requests_start_one_enrichment(
        monkeypatch, started_threads):
    """Both cache reads reach the single-flight lock at the same time."""
    expected = cached_area("enriching")
    serialized = json.dumps(expected)
    both_reading = threading.Barrier(2)

    class ConcurrentCachePath:
        def exists(self):
            return True

        def read_text(self, encoding=None):
            both_reading.wait(timeout=5)
            return serialized

    path = ConcurrentCachePath()
    monkeypatch.setattr(server_app, "cache_path", lambda slug: path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(server_app.api_area, LAT, LON, NAME)
            for _ in range(2)
        ]
        results = [future.result(timeout=5) for future in futures]

    assert results == [expected, expected]
    assert len(started_threads) == 1
    assert server_app.slug_for(LAT, LON) in server_app._enriching


@pytest.mark.parametrize("terminal_status", ["complete", "osm_only"])
def test_terminal_cache_entries_never_restart_enrichment(
        terminal_status, started_threads):
    slug, expected = write_cached_area(terminal_status)

    result = server_app.api_area(LAT, LON, NAME)

    assert result == expected
    assert started_threads == []
    assert slug not in server_app._enriching


def test_disabled_overture_returns_honest_osm_only_result(
        monkeypatch, started_threads):
    """Configuration-off is usable degradation, not an upstream failure."""
    monkeypatch.setattr(
        server_app,
        "OVERTURE_ENRICHMENT_ENABLED",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        server_app.pipeline,
        "snap_to_drivable",
        lambda lat, lon: (lat, lon, 0.0),
    )
    monkeypatch.setattr(
        server_app.pipeline,
        "fetch_isochrone",
        lambda lat, lon: {"features": ["offline test fixture"]},
    )
    monkeypatch.setattr(
        server_app.pipeline,
        "boundary_from_isochrone",
        lambda isochrone, lat, lon, name: boundary(),
    )
    monkeypatch.setattr(
        server_app.pipeline,
        "osm_facilities",
        lambda geometry, boundary_mode: facilities(),
    )
    monkeypatch.setattr(
        server_app.pipeline,
        "verify_inside",
        lambda found_facilities, geometry: 0,
    )

    result = server_app.api_area(LAT, LON, NAME)

    assert result["status"] == "osm_only"
    assert result.get("enrich_error") is not True
    assert started_threads == []
    warnings = result.get("warnings")
    assert isinstance(warnings, list) and warnings
    assert any(
        "overture" in warning.lower()
        and "disabled" in warning.lower()
        and "osm" in warning.lower()
        for warning in warnings
    )

    cached = json.loads(
        server_app.cache_path(server_app.slug_for(LAT, LON)).read_text()
    )
    assert cached["status"] == "osm_only"
    assert cached.get("enrich_error") is not True
    assert cached["warnings"] == warnings
