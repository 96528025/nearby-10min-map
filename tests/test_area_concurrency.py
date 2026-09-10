"""Offline tests for phase-1 request coalescing, the enrichment cap, and
generic upstream error bodies."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app as server_app


LAT = 37.4275
LON = -122.1697
NAME = "Stanford University"
UPSTREAM_DETAIL = "urlopen error https://upstream.invalid/search?contact=private"


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
        "metadata": {"method": "test isochrone"},
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


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
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

    monkeypatch.setattr(
        server_app, "threading", SimpleNamespace(Thread=FakeThread)
    )
    return started


def stub_phase_one(monkeypatch, snap):
    monkeypatch.setattr(server_app.pipeline, "snap_to_drivable", snap)
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
        server_app.pipeline, "verify_inside", lambda found, geometry: 0
    )


def run_two_overlapping_requests(monkeypatch, snaps, release):
    """Start one request, wait until it is inside phase 1, then start a second
    for the same point and let the first finish once the second has missed
    the cache (or started its own phase 1, which coalescing must prevent)."""
    reads = []
    original_read = server_app._read_cached_area

    def counting_read(path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(server_app, "_read_cached_area", counting_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(server_app.api_area, LAT, LON, NAME)
        assert wait_until(lambda: snaps)
        second = executor.submit(server_app.api_area, LAT, LON, NAME)
        wait_until(lambda: len(reads) >= 3 or len(snaps) >= 2, timeout=2.0)
        release.set()
        return [first, second]


def test_identical_cache_misses_share_one_phase_one_computation(
        monkeypatch, started_threads):
    snaps = []
    release = threading.Event()

    def slow_snap(lat, lon):
        snaps.append((lat, lon))
        assert release.wait(timeout=5)
        return lat, lon, 0.0

    stub_phase_one(monkeypatch, slow_snap)

    futures = run_two_overlapping_requests(monkeypatch, snaps, release)
    results = [future.result(timeout=5) for future in futures]

    assert len(snaps) == 1
    assert results[0] == results[1]
    assert results[0]["status"] == "enriching"
    assert len(started_threads) == 1


def test_requests_waiting_on_a_failed_computation_get_its_error(monkeypatch):
    snaps = []
    release = threading.Event()

    def failing_snap(lat, lon):
        snaps.append((lat, lon))
        assert release.wait(timeout=5)
        raise RuntimeError(UPSTREAM_DETAIL)

    stub_phase_one(monkeypatch, failing_snap)

    futures = run_two_overlapping_requests(monkeypatch, snaps, release)
    for future in futures:
        with pytest.raises(HTTPException) as caught:
            future.result(timeout=5)
        assert caught.value.status_code == 502
    assert len(snaps) == 1


def write_enriching_area(lat, lon):
    slug = server_app.slug_for(lat, lon)
    area = {
        "status": "enriching",
        "name": NAME,
        "lat": lat,
        "lon": lon,
        "boundary": boundary(),
        "facilities": facilities(),
        "total": 0,
        "boundary_mode": server_app.pipeline.ROUTED_BOUNDARY_MODE,
        "warnings": [],
    }
    server_app.cache_path(slug).write_text(json.dumps(area))
    return slug


def test_enrichment_is_capped_and_a_deferred_key_starts_on_a_later_poll(
        monkeypatch, started_threads):
    monkeypatch.setattr(server_app, "MAX_CONCURRENT_ENRICHMENTS", 1)
    first = write_enriching_area(LAT, LON)
    second = write_enriching_area(LAT + 0.01, LON + 0.01)

    server_app.api_area(LAT, LON, NAME)
    deferred = server_app.api_area(LAT + 0.01, LON + 0.01, NAME)

    assert deferred["status"] == "enriching"
    assert [thread.args[0] for thread in started_threads] == [first]

    with server_app._lock:
        server_app._enriching.discard(first)   # that enrichment finished
    server_app.api_area(LAT + 0.01, LON + 0.01, NAME)   # the client's next poll

    assert [thread.args[0] for thread in started_threads] == [first, second]


def test_enrichment_cap_defaults_to_two():
    assert server_app.MAX_CONCURRENT_ENRICHMENTS == 2


def test_geocode_failure_detail_stays_out_of_the_response(monkeypatch):
    class FailingGeocoder:
        def geocode(self, *args, **kwargs):
            raise RuntimeError(UPSTREAM_DETAIL)

    monkeypatch.setattr(server_app, "_geocode", FailingGeocoder())

    with pytest.raises(HTTPException) as caught:
        server_app.api_geocode("apple park")

    assert caught.value.status_code == 502
    assert caught.value.detail == "geocoding failed"


def test_area_failure_detail_stays_out_of_the_response(monkeypatch):
    def failing_snap(lat, lon):
        raise RuntimeError(UPSTREAM_DETAIL)

    stub_phase_one(monkeypatch, failing_snap)

    with pytest.raises(HTTPException) as caught:
        server_app.api_area(LAT, LON, NAME)

    assert caught.value.status_code == 502
    assert caught.value.detail == "area computation failed"
