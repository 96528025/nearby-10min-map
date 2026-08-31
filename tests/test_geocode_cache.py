import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import geocode_cache
from geocode_cache import GeocodeCoordinator, geocode_cache_key


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def candidate(name):
    return {
        "name": name,
        "display_name": f"{name}, California",
        "lat": 37.0,
        "lon": -122.0,
        "osm": "node/1",
    }


def test_cache_key_is_stable_normalised_and_opaque():
    first = geocode_cache_key("  Apple   PARK  ", 37.0, -122.0)
    second = geocode_cache_key("apple park", 37, -122)

    assert first == second
    assert len(first) == 64
    assert "apple" not in first
    assert first != geocode_cache_key("apple park", 37.1, -122.0)


def test_cache_hit_happens_before_rate_limit_or_upstream(tmp_path):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("cache hit reached rate limiter or upstream")

    coordinator = GeocodeCoordinator(
        tmp_path,
        must_not_run,
        clock=must_not_run,
        sleep=must_not_run,
    )
    path = coordinator.cache_path("Apple Park", 37.0, -122.0)
    expected = {"candidates": [candidate("Apple Park")]}
    path.write_text(json.dumps(expected), encoding="utf-8")

    assert coordinator.geocode(" apple   park ", 37, -122) == expected


def test_cache_misses_share_one_global_rate_limit(tmp_path):
    fake_time = FakeTime()
    upstream_starts = []

    def fetcher(query, **_bias):
        upstream_starts.append((query, fake_time.clock()))
        return [candidate(query)]

    coordinator = GeocodeCoordinator(
        tmp_path,
        fetcher,
        clock=fake_time.clock,
        sleep=fake_time.sleep,
    )

    coordinator.geocode("first")
    coordinator.geocode("second")
    coordinator.geocode("first")

    assert upstream_starts == [("first", 0.0), ("second", 1.0)]
    assert fake_time.sleeps == [1.0]


def test_same_request_is_single_flight_and_waiters_share_result(tmp_path):
    workers = 6
    start = threading.Barrier(workers + 1)
    upstream_started = threading.Event()
    release_upstream = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def fetcher(_query, **_bias):
        nonlocal calls
        with calls_lock:
            calls += 1
        upstream_started.set()
        assert release_upstream.wait(timeout=2)
        return [candidate("shared")]

    coordinator = GeocodeCoordinator(tmp_path, fetcher, minimum_interval=0)

    def lookup():
        start.wait()
        return coordinator.geocode("  Same Request  ", 1.0, 2.0)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(lookup) for _ in range(workers)]
        start.wait()
        assert upstream_started.wait(timeout=2)
        time.sleep(0.05)
        release_upstream.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert all(result is results[0] for result in results)


def test_single_flight_waiters_share_exception_and_failure_is_not_cached(
    tmp_path,
):
    workers = 4
    start = threading.Barrier(workers + 1)
    upstream_started = threading.Event()
    release_upstream = threading.Event()
    failure = RuntimeError("upstream unavailable")
    calls = 0
    calls_lock = threading.Lock()

    def failing_fetcher(_query, **_bias):
        nonlocal calls
        with calls_lock:
            calls += 1
        upstream_started.set()
        assert release_upstream.wait(timeout=2)
        raise failure

    coordinator = GeocodeCoordinator(
        tmp_path,
        failing_fetcher,
        minimum_interval=0,
    )

    def lookup():
        start.wait()
        try:
            coordinator.geocode("failure")
        except RuntimeError as error:
            return error
        raise AssertionError("lookup unexpectedly succeeded")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(lookup) for _ in range(workers)]
        start.wait()
        assert upstream_started.wait(timeout=2)
        time.sleep(0.05)
        release_upstream.set()
        errors = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert all(error is failure for error in errors)
    assert not coordinator.cache_path("failure").exists()

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        coordinator.geocode("failure")
    assert calls == 2


def test_cache_file_is_replaced_atomically(tmp_path, monkeypatch):
    coordinator = GeocodeCoordinator(
        tmp_path,
        lambda query, **_bias: [candidate(query)],
    )
    replacements = []
    original_replace = geocode_cache.os.replace

    def record_replace(source, target):
        replacements.append((source, target))
        original_replace(source, target)

    monkeypatch.setattr("geocode_cache.os.replace", record_replace)

    expected = coordinator.geocode("atomic")
    path = coordinator.cache_path("atomic")

    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert len(replacements) == 1
    source, target = replacements[0]
    assert target == path
    assert source != target
    assert not list(tmp_path.glob("*.tmp"))
