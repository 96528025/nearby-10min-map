"""Thread-safe geocode caching, request coalescing, and rate limiting."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


GeocodeResponse = dict[str, list[dict[str, Any]]]


class GeocodeFetcher(Protocol):
    def __call__(
        self,
        query: str,
        *,
        bias_lat: float | None = None,
        bias_lon: float | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class _Request:
    query: str
    bias_lat: float | None
    bias_lon: float | None
    key: str


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    result: GeocodeResponse | None = None
    error: BaseException | None = None


def _normalise_bias(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("geocode bias must be finite")
    return 0.0 if number == 0 else number


def _normalise_request(
    query: str,
    bias_lat: float | None,
    bias_lon: float | None,
) -> _Request:
    normalised_query = " ".join(unicodedata.normalize("NFKC", query).split())
    if not normalised_query:
        raise ValueError("geocode query must not be empty")

    normalised_lat = _normalise_bias(bias_lat)
    normalised_lon = _normalise_bias(bias_lon)
    canonical = json.dumps(
        {
            "bias_lat": normalised_lat,
            "bias_lon": normalised_lon,
            "q": normalised_query.casefold(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return _Request(normalised_query, normalised_lat, normalised_lon, key)


def geocode_cache_key(
    query: str,
    bias_lat: float | None = None,
    bias_lon: float | None = None,
) -> str:
    """Return a stable, opaque key for a normalised geocode request."""
    return _normalise_request(query, bias_lat, bias_lon).key


class GeocodeCoordinator:
    """Coordinate cached geocoding across all threads in one process."""

    def __init__(
        self,
        cache_dir: Path,
        fetcher: GeocodeFetcher,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        minimum_interval: float = 1.0,
    ) -> None:
        if minimum_interval < 0:
            raise ValueError("minimum_interval must not be negative")
        self.cache_dir = Path(cache_dir)
        self._fetcher = fetcher
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._minimum_interval = minimum_interval
        self._last_upstream_start: float | None = None
        self._rate_lock = threading.Lock()
        self._flights_lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def cache_path(
        self,
        query: str,
        bias_lat: float | None = None,
        bias_lon: float | None = None,
    ) -> Path:
        request = _normalise_request(query, bias_lat, bias_lon)
        return self.cache_dir / f"{request.key}.json"

    def geocode(
        self,
        query: str,
        bias_lat: float | None = None,
        bias_lon: float | None = None,
    ) -> GeocodeResponse:
        request = _normalise_request(query, bias_lat, bias_lon)
        path = self.cache_dir / f"{request.key}.json"

        cached = self._read_cache(path)
        if cached is not None:
            return cached

        with self._flights_lock:
            flight = self._flights.get(request.key)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._flights[request.key] = flight

        assert flight is not None
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.result is not None
            return flight.result

        try:
            # A previous leader may have populated the file between our first
            # read and registration as the next flight leader.
            result = self._read_cache(path)
            if result is None:
                self._wait_for_rate_slot()
                candidates = self._fetcher(
                    request.query,
                    bias_lat=request.bias_lat,
                    bias_lon=request.bias_lon,
                )
                result = {"candidates": candidates}
                self._write_cache(path, result)
            flight.result = result
            return result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            flight.done.set()
            with self._flights_lock:
                self._flights.pop(request.key, None)

    def _wait_for_rate_slot(self) -> None:
        with self._rate_lock:
            if self._last_upstream_start is not None:
                while True:
                    remaining = (
                        self._last_upstream_start
                        + self._minimum_interval
                        - self._clock()
                    )
                    if remaining <= 0:
                        break
                    self._sleep(remaining)
            self._last_upstream_start = self._clock()

    @staticmethod
    def _read_cache(path: Path) -> GeocodeResponse | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not isinstance(
            value.get("candidates"), list
        ):
            return None
        return value

    def _write_cache(self, path: Path, value: GeocodeResponse) -> None:
        try:
            serialised = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, TypeError, ValueError):
            return

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.cache_dir,
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(serialised)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError:
            pass
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
