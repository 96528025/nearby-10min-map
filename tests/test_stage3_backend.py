"""Offline contract tests for the Stage 3 public-service boundary."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

import app as server_app
import fetch_facilities
import pipeline


LAT = 37.33182
LON = -122.03118


def empty_facilities(boundary_mode):
    return {
        "metadata": {
            "source": "OpenStreetMap via Overpass API",
            "filter": pipeline.facility_filter_for(boundary_mode),
        },
        "categories": {},
    }


@pytest.fixture(autouse=True)
def isolated_area_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(server_app, "CACHE", tmp_path)
    with server_app._lock:
        server_app._enriching.clear()
    yield
    with server_app._lock:
        server_app._enriching.clear()


def test_valhalla_failure_returns_explicit_nominal_boundary(monkeypatch):
    """A failed routed calculation stays usable and never claims routing."""
    monkeypatch.setattr(server_app, "OVERTURE_ENRICHMENT_ENABLED", False)
    monkeypatch.setattr(
        pipeline,
        "snap_to_drivable",
        lambda lat, lon: (lat + 1, lon + 1, 1234),
    )

    def unavailable(_lat, _lon):
        raise TimeoutError("Valhalla timed out")

    monkeypatch.setattr(pipeline, "fetch_isochrone", unavailable)
    captured = {}

    def fake_osm(geometry, boundary_mode):
        captured["geometry"] = geometry
        captured["boundary_mode"] = boundary_mode
        return empty_facilities(boundary_mode)

    monkeypatch.setattr(pipeline, "osm_facilities", fake_osm)
    monkeypatch.setattr(pipeline, "verify_inside", lambda fac, geometry: 0)

    result = server_app.api_area(LAT, LON, "Apple Park")

    assert result["status"] == "osm_only"
    assert result["boundary_mode"] == pipeline.NOMINAL_BOUNDARY_MODE
    assert captured["boundary_mode"] == pipeline.NOMINAL_BOUNDARY_MODE
    assert result["boundary"]["metadata"]["center"] == {
        "lat": LAT,
        "lon": LON,
        "name": "Apple Park",
    }
    assert "snap_distance_m" not in result["boundary"]["metadata"]
    assert "no road-network input" in result["boundary"]["metadata"][
        "method"
    ]
    assert server_app.NOMINAL_BOUNDARY_WARNING in result["warnings"]
    assert result["facilities"]["metadata"]["filter"] == (
        pipeline.NOMINAL_FACILITY_FILTER
    )


def test_nominal_circle_has_fixed_radius_and_closed_geometry():
    boundary = pipeline.boundary_from_nominal_radius(
        LAT, LON, "Apple Park", radius_m=2500
    )

    assert boundary["metadata"]["radius_m"] == 2500
    assert "isochrone_area_km2" not in boundary["metadata"]
    ring = boundary["features"][0]["geometry"]["coordinates"][0]
    assert len(ring) == 129
    assert ring[0] == ring[-1]
    assert boundary["features"][0]["properties"]["contour"] == (
        "fixed-radius approximation"
    )


def test_overpass_failure_returns_boundary_and_explicit_empty_facilities(
        monkeypatch):
    monkeypatch.setattr(server_app, "OVERTURE_ENRICHMENT_ENABLED", False)
    monkeypatch.setattr(
        pipeline,
        "snap_to_drivable",
        lambda lat, lon: (lat, lon, 0),
    )
    ring = [[
        [LON - 0.01, LAT - 0.01],
        [LON + 0.01, LAT - 0.01],
        [LON + 0.01, LAT + 0.01],
        [LON - 0.01, LAT + 0.01],
        [LON - 0.01, LAT - 0.01],
    ]]
    monkeypatch.setattr(
        pipeline,
        "fetch_isochrone",
        lambda lat, lon: {
            "features": [{"geometry": {"coordinates": ring}}]
        },
    )
    monkeypatch.setattr(
        pipeline,
        "osm_facilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("Overpass unavailable")
        ),
    )

    result = server_app.api_area(LAT, LON, "Apple Park")

    assert result["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
    assert result["status"] == "osm_only"
    assert result["total"] == 0
    assert server_app.OSM_LOOKUP_WARNING in result["warnings"]
    assert result["facilities"]["metadata"]["osm_lookup_error"] is True
    assert "unavailable for this response" in result["facilities"][
        "metadata"
    ]["source"]
    assert result["facilities"]["metadata"]["filter"] == (
        pipeline.ROUTED_FACILITY_FILTER
    )
    assert set(result["facilities"]["categories"]) == set(
        pipeline.CATEGORIES
    )
    assert all(
        category["count"] == 0 and category["items"] == []
        for category in result["facilities"]["categories"].values()
    )


def test_runtime_and_generator_provenance_stay_in_lockstep():
    assert pipeline.ROUTED_FACILITY_FILTER == (
        fetch_facilities.ROUTED_FACILITY_FILTER
    )
    assert pipeline.NOMINAL_FACILITY_FILTER == (
        fetch_facilities.NOMINAL_FACILITY_FILTER
    )
    for mode in (
        pipeline.ROUTED_BOUNDARY_MODE,
        pipeline.NOMINAL_BOUNDARY_MODE,
    ):
        assert pipeline.facility_filter_for(mode) == (
            fetch_facilities.facility_filter_for(mode)
        )


def test_http_client_sends_contact_user_agent_and_timeout(monkeypatch):
    captured = {}

    class Response(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return Response('{"ok": true}')

    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)

    assert pipeline.http_json("https://example.invalid", timeout=7) == {
        "ok": True
    }
    assert captured == {
        "user_agent": pipeline.USER_AGENT,
        "timeout": 7,
    }
    assert "github.com/96528025/nearby-10min-map" in pipeline.USER_AGENT


def test_geocode_raises_only_when_both_sources_fail(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "http_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(RuntimeError, match="both unavailable"):
        pipeline.geocode("Apple Park")

    calls = 0

    def one_empty_success(url, **_kwargs):
        nonlocal calls
        calls += 1
        if "nominatim" in url:
            return []
        raise TimeoutError()

    monkeypatch.setattr(pipeline, "http_json", one_empty_success)
    assert pipeline.geocode("No such place") == []
    assert calls == 2


def test_overpass_client_sends_same_contact_and_has_timeout(monkeypatch):
    captured = {}

    class Response(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return Response('{"elements": []}')

    monkeypatch.setattr(
        fetch_facilities.urllib.request, "urlopen", fake_urlopen
    )

    assert fetch_facilities.overpass_query_all((0, 0, 1, 1)) == []
    assert captured["user_agent"] == fetch_facilities.USER_AGENT
    assert captured["user_agent"] == pipeline.USER_AGENT
    assert captured["timeout"] == (
        fetch_facilities.OVERPASS_HTTP_TIMEOUT_SECONDS
    )


def test_overpass_uses_documented_fallback_after_network_failure(monkeypatch):
    attempted = []

    class Response(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def first_unreachable_then_success(request, timeout):
        attempted.append((request.full_url, timeout))
        if len(attempted) == 1:
            raise urllib.error.URLError(OSError(101, "unreachable"))
        return Response('{"elements": []}')

    monkeypatch.setattr(
        fetch_facilities,
        "OVERPASS_ENDPOINTS",
        ("https://primary.invalid", "https://fallback.invalid"),
    )
    monkeypatch.setattr(
        fetch_facilities.urllib.request,
        "urlopen",
        first_unreachable_then_success,
    )

    assert fetch_facilities.overpass_query_all((0, 0, 1, 1)) == []
    assert [url for url, _timeout in attempted] == [
        "https://primary.invalid",
        "https://fallback.invalid",
    ]


def test_overture_command_is_pinned_bounded_and_records_provenance(
        monkeypatch, square):
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps({"features": []}))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    facilities = empty_facilities(pipeline.ROUTED_BOUNDARY_MODE)

    result = pipeline.merge_overture(facilities, square)

    command, options = commands[0]
    assert command[command.index("-r") + 1] == pipeline.OVERTURE_RELEASE
    assert command[command.index("--connect_timeout") + 1] == str(
        pipeline.OVERTURE_CONNECT_TIMEOUT_SECONDS
    )
    assert command[command.index("--request_timeout") + 1] == str(
        pipeline.OVERTURE_REQUEST_TIMEOUT_SECONDS
    )
    assert options["timeout"] == pipeline.OVERTURE_PROCESS_TIMEOUT_SECONDS
    assert result["metadata"]["overture_release"] == "2026-08-19.0"
    assert "Modified from Overture Places release" in result["metadata"][
        "overture_modifications"
    ]
    assert "Foursquare" in result["metadata"]["overture_attribution"]


def test_overture_metadata_does_not_claim_osm_merge_when_osm_failed(
        monkeypatch, square):
    def fake_run(command, **_kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps({"features": []}))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    facilities = pipeline.empty_osm_facilities(
        pipeline.NOMINAL_BOUNDARY_MODE
    )

    result = pipeline.merge_overture(facilities, square)

    assert result["metadata"]["source"] == (
        "Overture Maps places (OSM Overpass unavailable for this response)"
    )
    assert "OSM deduplication" not in result["metadata"][
        "overture_modifications"
    ]
    assert result["metadata"]["filter"] == pipeline.NOMINAL_FACILITY_FILTER


def test_health_check_has_no_upstream_dependency():
    assert server_app.api_health() == {"status": "ok"}


def test_data_and_frontend_mounts_follow_api_routes():
    routes = server_app.app.routes
    names = [route.name for route in routes]

    assert names.index("api_area") < names.index("data")
    assert names.index("data") < names.index("frontend")

    data_mount = routes[names.index("data")]
    frontend_mount = routes[names.index("frontend")]
    assert Path(data_mount.app.directory) == server_app.DATA_DIR
    assert Path(frontend_mount.app.directory) == server_app.WEB_DIST
