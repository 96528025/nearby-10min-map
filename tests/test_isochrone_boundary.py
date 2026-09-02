"""Foundation tests for the true-isochrone boundary (docs/DECISIONS.md D-2).

Covers the geometry contract end to end and offline: Valhalla response ->
`pipeline.boundary_from_isochrone` -> API response and cache -> the facility
filter and the Overture merge, for Polygon, Polygon with hole, MultiPolygon and
MultiPolygon with hole, on both the live path (`app.api_area`) and the bundled
path (`make_boundary`, `facilities_from_frozen_universe` and the committed
data/ artifacts). The last class replays the frozen benchmark universe.

    Every assertion here checks that the displayed geometry, the facility
    predicate and the upstream query envelope agree with each other and with
    the Valhalla reference geometry. It does NOT validate ten-minute
    drive-time accuracy.
"""
from collections import Counter
import json
from pathlib import Path

import pytest
from shapely.geometry import Point, shape as shapely_shape

import app as server_app
import facilities_from_frozen_universe as rebuild
import fetch_facilities
import make_boundary
import pipeline
from verify import (M_PER_DEG_LAT, geometry_area_m2, geometry_bbox,
                    isochrone_geometry, m_per_deg_lon, point_in_polygon)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "map" / "data"
REPORTS = ROOT / "reports" / "accuracy"
VALHALLA_FIXTURES = ROOT / "tests" / "fixtures" / "valhalla"
LAT, LON = 37.33484, -122.01139


def ring(clat, clon, half):
    return [[clon - half, clat - half], [clon + half, clat - half],
            [clon + half, clat + half], [clon - half, clat + half],
            [clon - half, clat - half]]


def square_m2(half):
    return (2 * half * m_per_deg_lon(LAT)) * (2 * half * M_PER_DEG_LAT)


OUTER = ring(LAT, LON, 0.02)
HOLE = ring(LAT, LON, 0.005)
SECOND = ring(LAT + 0.1, LON + 0.1, 0.01)
POLYGON = {"type": "Polygon", "coordinates": [OUTER]}
POLYGON_WITH_HOLE = {"type": "Polygon", "coordinates": [OUTER, HOLE]}
MULTIPOLYGON = {"type": "MultiPolygon", "coordinates": [[OUTER], [SECOND]]}
MULTIPOLYGON_WITH_HOLE = {"type": "MultiPolygon",
                          "coordinates": [[OUTER, HOLE], [SECOND]]}
GEOMETRIES = [POLYGON, POLYGON_WITH_HOLE, MULTIPOLYGON, MULTIPOLYGON_WITH_HOLE]
GEOMETRY_IDS = ["polygon", "polygon_with_hole", "multipolygon",
                "multipolygon_with_hole"]

# One probe per region: inside the outer ring but outside the hole, inside the
# hole, inside the second component, and in the gap between components.
IN_MAIN = (LAT + 0.012, LON + 0.012)
IN_HOLE = (LAT, LON)
IN_SECOND = (LAT + 0.1, LON + 0.1)
BETWEEN = (LAT + 0.05, LON + 0.05)
EXPECTED_NAMES = {
    "polygon": ["Hole Cafe", "Main Cafe"],
    "polygon_with_hole": ["Main Cafe"],
    "multipolygon": ["Hole Cafe", "Island Cafe", "Main Cafe"],
    "multipolygon_with_hole": ["Island Cafe", "Main Cafe"],
}


def node(i, point, name):
    lat, lon = point
    return {"type": "node", "id": i, "lat": lat, "lon": lon,
            "tags": {"name": name, "amenity": "cafe"}}


def place(point, name):
    lat, lon = point
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"confidence": 0.9,
                           "categories": {"primary": "restaurant"},
                           "names": {"primary": name}}}


ELEMENTS = [node(1, IN_MAIN, "Main Cafe"), node(2, IN_HOLE, "Hole Cafe"),
            node(3, IN_SECOND, "Island Cafe"), node(4, BETWEEN, "Gap Cafe")]
PLACES = [place(IN_MAIN, "Main Diner"), place(IN_HOLE, "Hole Diner"),
          place(IN_SECOND, "Island Diner"), place(BETWEEN, "Gap Diner")]


def valhalla(*geometries):
    """A Valhalla ``polygons: true`` response: one feature per geometry."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"contour": 10.0, "metric": "time", "fill": "#bf4040"},
         "geometry": g} for g in geometries]}


def names(fac):
    return sorted(i["name"] for c in fac["categories"].values()
                  for i in c["items"])


@pytest.fixture(scope="module")
def committed():
    return {name: json.loads((DATA / f"{name}.json").read_text())
            for name in ("isochrone", "boundary", "facilities")}


@pytest.fixture(scope="module")
def run():
    run_id = json.loads((REPORTS / "latest.json").read_text())["run_id"]
    run_dir = REPORTS / "runs" / run_id
    return (run_id,
            json.loads((run_dir / "poi_universe.json").read_text()),
            json.loads((run_dir / "results.json").read_text()))


@pytest.fixture(scope="module")
def snapped_isochrones():
    fixtures = {}
    for path in sorted(VALHALLA_FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text())
        assert fixture["fixture_schema"] == 1
        assert fixture["location_id"] == path.stem
        assert fixture["endpoint"].endswith("/isochrone")
        assert fixture["fetched_utc"]
        assert len(fixture["source_cache_key"]) == 64
        assert len(fixture["config_sha256"]) == 64
        fixtures[fixture["location_id"]] = fixture
    return fixtures


@pytest.fixture(autouse=True)
def isolated_area_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(server_app, "CACHE", tmp_path)
    with server_app._lock:
        server_app._enriching.clear()
    yield
    with server_app._lock:
        server_app._enriching.clear()


class TestBoundaryFromIsochrone:
    @pytest.mark.parametrize("geometry,expected_area,components,holes", [
        (POLYGON, square_m2(0.02), 1, 0),
        (POLYGON_WITH_HOLE, square_m2(0.02) - square_m2(0.005), 1, 1),
        (MULTIPOLYGON, square_m2(0.02) + square_m2(0.01), 2, 0),
        (MULTIPOLYGON_WITH_HOLE,
         square_m2(0.02) - square_m2(0.005) + square_m2(0.01), 2, 1),
    ], ids=GEOMETRY_IDS)
    def test_geometry_is_kept_verbatim_and_area_covers_all_components(
            self, geometry, expected_area, components, holes):
        boundary = pipeline.boundary_from_isochrone(
            valhalla(geometry), LAT, LON, "Apple Park")

        assert boundary["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
        assert len(boundary["features"]) == 1
        assert boundary["features"][0]["geometry"] == geometry
        assert pipeline.boundary_geometry(boundary) == geometry
        meta = boundary["metadata"]
        assert "radius_m" not in meta
        assert meta["geometry_type"] == geometry["type"]
        assert meta["geometry_components"] == components
        assert meta["geometry_holes"] == holes
        assert meta["isochrone_area_km2"] == pytest.approx(
            expected_area / 1e6, abs=0.01)
        assert geometry_area_m2(geometry, LAT, LON) == pytest.approx(
            expected_area, rel=1e-9)
        assert meta["contour_minutes"] == 10 and meta["costing"] == "auto"
        assert "no live or historical traffic" in meta["traffic"]
        assert "no circle approximation" in meta["method"]

    def test_the_129_vertex_circle_is_gone(self):
        boundary = pipeline.boundary_from_isochrone(
            valhalla(POLYGON), LAT, LON, "Apple Park")
        assert len(boundary["features"][0]["geometry"]["coordinates"][0]) == 5

    def test_several_features_merge_without_dropping_any_component(self):
        iso = valhalla(POLYGON_WITH_HOLE,
                       {"type": "Polygon", "coordinates": [SECOND]})
        assert isochrone_geometry(iso) == MULTIPOLYGON_WITH_HOLE
        meta = pipeline.boundary_from_isochrone(iso, LAT, LON, "x")["metadata"]
        assert meta["geometry_components"] == 2
        assert meta["geometry_holes"] == 1

    def test_multipolygon_feature_plus_polygon_feature_keeps_three(self):
        third = ring(LAT - 0.1, LON - 0.1, 0.01)
        geometry = isochrone_geometry(
            valhalla(MULTIPOLYGON, {"type": "Polygon", "coordinates": [third]}))
        assert geometry["type"] == "MultiPolygon"
        assert geometry["coordinates"] == [[OUTER], [SECOND], [third]]

    def test_response_without_polygons_is_rejected(self):
        with pytest.raises(ValueError):
            isochrone_geometry({"features": [{"geometry": {
                "type": "LineString", "coordinates": [[0, 0], [1, 1]]}}]})
        with pytest.raises(ValueError):
            isochrone_geometry({"features": []})

    def test_boundary_geometry_refuses_multi_feature_collections(self):
        boundary = pipeline.boundary_from_isochrone(
            valhalla(POLYGON), LAT, LON, "x")
        boundary["features"].append(boundary["features"][0])
        with pytest.raises(ValueError, match="exactly one feature"):
            pipeline.boundary_geometry(boundary)


class TestQueryEnvelope:
    def test_bbox_covers_every_component_and_hole(self, multipolygon_with_hole):
        assert geometry_bbox(multipolygon_with_hole) == (-1, -1, 12, 12)
        assert pipeline._bbox(multipolygon_with_hole) == (-1, -1, 12, 12)

    def test_bbox_of_polygon_with_hole_is_its_outer_ring(self, square_with_hole):
        assert geometry_bbox(square_with_hole) == (-1, -1, 1, 1)

    def test_bbox_rejects_unsupported_geometry(self):
        with pytest.raises(ValueError):
            geometry_bbox({"type": "Point", "coordinates": [0, 0]})


class TestFacilityFilterUsesTheDisplayedGeometry:
    def test_osm_phase_queries_the_full_envelope_and_honours_holes(
            self, monkeypatch):
        captured = {}

        def fake_overpass(bbox):
            captured["bbox"] = bbox
            return ELEMENTS

        monkeypatch.setattr(pipeline, "overpass_query_all", fake_overpass)

        fac = pipeline.osm_facilities(MULTIPOLYGON_WITH_HOLE)

        assert captured["bbox"] == geometry_bbox(MULTIPOLYGON_WITH_HOLE)
        assert names(fac) == ["Island Cafe", "Main Cafe"]
        assert fac["metadata"]["filter"] == pipeline.ROUTED_FACILITY_FILTER
        assert pipeline.verify_inside(fac, MULTIPOLYGON_WITH_HOLE) == 2

    def test_overture_phase_downloads_the_full_envelope_and_same_filter(
            self, monkeypatch):
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index("-o") + 1]).write_text(
                json.dumps({"features": PLACES}))

        monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
        monkeypatch.setattr(pipeline, "overpass_query_all", lambda bbox: [])
        fac = pipeline.osm_facilities(MULTIPOLYGON_WITH_HOLE)

        result = pipeline.merge_overture(fac, MULTIPOLYGON_WITH_HOLE)

        bbox_arg = next(a for a in commands[0] if a.startswith("--bbox="))
        w, s, e, n = (float(v) for v in bbox_arg[len("--bbox="):].split(","))
        assert (s, w, n, e) == geometry_bbox(MULTIPOLYGON_WITH_HOLE)
        assert names(result) == ["Island Diner", "Main Diner"]
        assert pipeline.verify_inside(result, MULTIPOLYGON_WITH_HOLE) == 2

    def test_verify_inside_applies_the_same_predicate(self):
        """A consistency guard only: it flags a facility that bypassed the
        filter, and says nothing about drive time."""
        fac = pipeline.empty_osm_facilities()
        fac["categories"]["dining"]["items"].append(
            {"name": "Hole Cafe", "lat": IN_HOLE[0], "lon": IN_HOLE[1]})
        fac["categories"]["dining"]["count"] = 1

        with pytest.raises(AssertionError, match="outside boundary"):
            pipeline.verify_inside(fac, MULTIPOLYGON_WITH_HOLE)
        assert pipeline.verify_inside(fac, POLYGON) == 1


def routed_stubs(monkeypatch, geometry, elements):
    monkeypatch.setattr(server_app, "OVERTURE_ENRICHMENT_ENABLED", False)
    monkeypatch.setattr(pipeline, "snap_to_drivable",
                        lambda lat, lon: (lat, lon, 0))
    monkeypatch.setattr(pipeline, "fetch_isochrone",
                        lambda lat, lon: valhalla(geometry))
    captured = {}

    def fake_overpass(bbox):
        captured["bbox"] = bbox
        return elements

    monkeypatch.setattr(pipeline, "overpass_query_all", fake_overpass)
    return captured


class TestLivePath:
    @pytest.mark.parametrize("geometry", GEOMETRIES, ids=GEOMETRY_IDS)
    def test_api_area_ships_the_true_geometry_and_filters_with_it(
            self, monkeypatch, geometry, request):
        captured = routed_stubs(monkeypatch, geometry, ELEMENTS)
        expected = EXPECTED_NAMES[request.node.callspec.id]

        result = server_app.api_area(LAT, LON, "Apple Park")

        assert result["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
        assert result["boundary"]["boundary_mode"] == (
            pipeline.ROUTED_BOUNDARY_MODE)
        assert result["boundary"]["features"][0]["geometry"] == geometry
        assert "radius_m" not in result["boundary"]["metadata"]
        assert captured["bbox"] == geometry_bbox(geometry)
        assert names(result["facilities"]) == expected
        assert result["total"] == len(expected)
        assert result["facilities"]["metadata"]["filter"] == (
            pipeline.ROUTED_FACILITY_FILTER)
        assert server_app.NOMINAL_BOUNDARY_WARNING not in result["warnings"]

        cached = json.loads(server_app.cache_path(
            server_app.slug_for(LAT, LON)).read_text())
        assert cached["boundary"]["features"][0]["geometry"] == geometry
        assert cached["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE

    def test_cached_isochrone_entries_are_served_unchanged(self, monkeypatch):
        routed_stubs(monkeypatch, MULTIPOLYGON_WITH_HOLE, ELEMENTS)
        first = server_app.api_area(LAT, LON, "Apple Park")

        def refetch(*_args):
            raise AssertionError("cached entry must not be recomputed")

        monkeypatch.setattr(pipeline, "fetch_isochrone", refetch)
        second = server_app.api_area(LAT, LON, "Apple Park")

        assert second["boundary"] == first["boundary"]
        assert second["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE

    @pytest.mark.parametrize("legacy_mode",
                             sorted(pipeline.RETIRED_BOUNDARY_MODES) + [None],
                             ids=["retired_circle_mode", "no_mode"])
    def test_legacy_circle_cache_entries_are_recomputed_not_served(
            self, monkeypatch, legacy_mode):
        slug = server_app.slug_for(LAT, LON)
        legacy = {
            "status": "complete", "name": "Apple Park", "lat": LAT, "lon": LON,
            "boundary": {
                "type": "FeatureCollection",
                "metadata": {"method": "circle with the same area as the "
                                       "routed 10-minute isochrone",
                             "center": {"lat": LAT, "lon": LON, "name": "x"},
                             "radius_m": 2855},
                "features": [{"type": "Feature",
                              "properties": {"contour": "approx 10 min drive"},
                              "geometry": pipeline._circle_geometry(
                                  LAT, LON, 2855)}],
            },
            "facilities": pipeline.empty_osm_facilities(), "total": 0,
            "warnings": [],
        }
        if legacy_mode:
            legacy["boundary_mode"] = legacy_mode
        server_app.cache_path(slug).write_text(json.dumps(legacy))
        assert server_app._normalise_cached_area(dict(legacy)) is None
        routed_stubs(monkeypatch, POLYGON_WITH_HOLE, ELEMENTS)

        result = server_app.api_area(LAT, LON, "Apple Park")

        assert result["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
        assert result["boundary"]["features"][0]["geometry"] == (
            POLYGON_WITH_HOLE)
        cached = json.loads(server_app.cache_path(slug).read_text())
        assert cached["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
        assert cached["boundary"]["features"][0]["geometry"] == (
            POLYGON_WITH_HOLE)

    def test_nominal_fallback_stays_a_labelled_circle_with_a_radius(
            self, monkeypatch):
        monkeypatch.setattr(server_app, "OVERTURE_ENRICHMENT_ENABLED", False)
        monkeypatch.setattr(pipeline, "snap_to_drivable",
                            lambda lat, lon: (lat, lon, 0))

        def unavailable(*_args):
            raise TimeoutError("Valhalla timed out")

        monkeypatch.setattr(pipeline, "fetch_isochrone", unavailable)
        monkeypatch.setattr(pipeline, "overpass_query_all", lambda bbox: [])

        result = server_app.api_area(LAT, LON, "Apple Park")

        assert result["boundary_mode"] == pipeline.NOMINAL_BOUNDARY_MODE
        assert result["boundary"]["boundary_mode"] == (
            pipeline.NOMINAL_BOUNDARY_MODE)
        meta = result["boundary"]["metadata"]
        assert meta["radius_m"] == round(pipeline.NOMINAL_RADIUS_M)
        assert "isochrone_area_km2" not in meta
        assert len(result["boundary"]["features"][0]["geometry"][
            "coordinates"][0]) == 129
        assert server_app.NOMINAL_BOUNDARY_WARNING in result["warnings"]
        assert result["facilities"]["metadata"]["filter"] == (
            pipeline.NOMINAL_FACILITY_FILTER)
        assert server_app._normalise_cached_area(dict(result)) is not None

    def test_enrichment_merges_with_the_displayed_geometry(self, monkeypatch):
        seen = {}

        def fake_merge(fac, geometry):
            seen["geometry"] = geometry
            return fac

        monkeypatch.setattr(pipeline, "merge_overture", fake_merge)
        boundary = pipeline.boundary_from_isochrone(
            valhalla(MULTIPOLYGON_WITH_HOLE), LAT, LON, "x")
        area = {"status": "enriching", "name": "x", "lat": LAT, "lon": LON,
                "boundary": boundary,
                "facilities": pipeline.empty_osm_facilities(), "total": 0,
                "boundary_mode": pipeline.ROUTED_BOUNDARY_MODE,
                "warnings": []}
        with server_app._lock:
            server_app._enriching.add("slug")

        server_app._enrich_async("slug", area)

        assert seen["geometry"] == MULTIPOLYGON_WITH_HOLE
        assert area["status"] == "complete"


class TestBundledPath:
    def test_bundled_boundary_is_the_committed_isochrone_via_the_live_code(
            self, committed):
        iso, boundary = committed["isochrone"], committed["boundary"]
        rebuilt = make_boundary.build_boundary(iso)
        center = boundary["metadata"]["center"]
        live = pipeline.boundary_from_isochrone(
            iso, center["lat"], center["lon"], center["name"])

        assert boundary["boundary_mode"] == pipeline.ROUTED_BOUNDARY_MODE
        assert boundary["features"][0]["geometry"] == isochrone_geometry(iso)
        assert (boundary["features"][0]["geometry"]
                == rebuilt["features"][0]["geometry"]
                == live["features"][0]["geometry"])
        assert "radius_m" not in boundary["metadata"]
        for key in ("isochrone_area_km2", "geometry_type",
                    "geometry_components", "geometry_holes", "method",
                    "traffic", "contour_minutes", "denoise"):
            assert (boundary["metadata"][key] == rebuilt["metadata"][key]
                    == live["metadata"][key]), key
        assert boundary["metadata"]["isochrone_generated_utc"] == (
            iso["metadata"]["generated_utc"])
        assert boundary["metadata"]["source"] == iso["metadata"]["source"]

    def test_bundled_facilities_reproduce_from_the_frozen_universe(
            self, committed):
        facilities = committed["facilities"]
        prov = facilities["metadata"]["provenance"]
        run_id, universe, results = rebuild.load_run(prov["benchmark_run_id"])
        assert universe["locations"]["apple_park"]["points_sha256"] == (
            prov["universe_points_sha256"])

        rebuilt = rebuild.build_facilities(
            committed["boundary"], universe, results, run_id, "apple_park",
            previous=facilities, now=facilities["metadata"]["generated_utc"])

        assert rebuilt == facilities
        assert facilities["metadata"]["filter"] == (
            pipeline.ROUTED_FACILITY_FILTER)
        assert pipeline.ROUTED_FACILITY_FILTER == (
            fetch_facilities.ROUTED_FACILITY_FILTER)
        assert sum(c["count"] for c in facilities["categories"].values()) > 0

    def test_bundled_membership_matches_an_independent_geometry_oracle(
            self, committed, run):
        """Dataset consistency only, not real-world drive-time accuracy.

        Shapely is independent of the production ray-casting predicate. Both
        evaluate all frozen Apple Park points against the committed geometry,
        then the resulting membership is compared with the committed facility
        snapshot. This checks representation and filtering, not POI quality or
        whether the modelled isochrone is ten minutes in reality.
        """
        _, universe, _ = run
        location = universe["locations"]["apple_park"]
        columns = {name: i for i, name in enumerate(universe["columns"])}
        geometry = committed["boundary"]["features"][0]["geometry"]
        oracle = shapely_shape(geometry)

        def universe_key(row):
            return (row[columns["category"]], row[columns["name"]],
                    row[columns["src"]], row[columns["lat"]],
                    row[columns["lon"]])

        production_members = [
            universe_key(row) for row in location["points"]
            if point_in_polygon(row[columns["lon"]], row[columns["lat"]],
                                geometry)
        ]
        oracle_members = [
            universe_key(row) for row in location["points"]
            if oracle.covers(Point(row[columns["lon"]],
                                   row[columns["lat"]]))
        ]
        committed_members = [
            (category, item["name"], item.get("src"), item["lat"],
             item["lon"])
            for category, group in committed["facilities"][
                "categories"].items()
            for item in group["items"]
        ]

        assert len(location["points"]) == location["n_points"] == 12_600
        assert Counter(production_members) == Counter(oracle_members)
        assert Counter(production_members) == Counter(committed_members)
        assert len(production_members) == len(oracle_members)
        assert len(production_members) == len(committed_members)
        assert len(committed_members) == sum(
            group["count"]
            for group in committed["facilities"]["categories"].values())

    def test_bundled_boundary_lies_inside_the_frozen_universe_envelope(
            self, committed):
        prov = committed["facilities"]["metadata"]["provenance"]
        bbox = geometry_bbox(committed["boundary"]["features"][0]["geometry"])

        assert rebuild.bbox_within(
            bbox, prov["query_envelope_south_west_north_east"])
        assert list(bbox) == prov["boundary_bbox_south_west_north_east"]

    def test_rebuild_refuses_a_boundary_the_universe_does_not_cover(self):
        far = pipeline.boundary_from_isochrone(
            valhalla({"type": "Polygon", "coordinates": [ring(0, 0, 0.01)]}),
            0, 0, "x")
        run_id, universe, results = rebuild.load_run()
        with pytest.raises(ValueError, match="does not contain"):
            rebuild.build_facilities(far, universe, results, run_id,
                                     "apple_park")

    def test_rebuild_refuses_a_non_isochrone_boundary(self):
        nominal = pipeline.boundary_from_nominal_radius(LAT, LON, "x")
        run_id, universe, results = rebuild.load_run()
        with pytest.raises(ValueError, match="routed isochrone mode"):
            rebuild.build_facilities(nominal, universe, results, run_id,
                                     "apple_park")


class TestFrozenBenchmarkReplay:
    """Offline before/after on the run of record.

    'Before' recomputes the retired circle's published rates from committed
    artifacts alone. 'After' checks that the production geometry parsing and
    predicate agree with the benchmark's reference membership on every frozen
    point. Neither is a new benchmark result and neither measures real-world
    drive time.
    """

    def test_before_the_retired_circle_rates_reproduce_from_the_universe(
            self, run):
        _, universe, results = run
        col = {c: i for i, c in enumerate(universe["columns"])}
        fi_rates, fe_rates = [], []
        for loc_id, loc in universe["locations"].items():
            radius = results["locations"][loc_id]["radii_m"]["equal_area"]
            published = results["locations"][loc_id]["candidates"][
                "equal_area_circle"]["poi"]
            fi_n = fi_d = fe_n = fe_d = 0
            for row in loc["points"]:
                in_iso = bool(row[col["in_isochrone"]])
                in_circle = row[col["distance_from_origin_m"]] <= radius
                fi_d += in_circle
                fi_n += in_circle and not in_iso
                fe_d += in_iso
                fe_n += in_iso and not in_circle
            assert (fi_n, fi_d) == (
                published["false_inclusion"]["numerator"],
                published["false_inclusion"]["denominator"]), loc_id
            assert (fe_n, fe_d) == (
                published["false_exclusion"]["numerator"],
                published["false_exclusion"]["denominator"]), loc_id
            fi_rates.append(fi_n / fi_d)
            fe_rates.append(fe_n / fe_d)

        assert len(fi_rates) == 5
        assert sum(fi_rates) / 5 == pytest.approx(0.091, abs=0.0005)
        assert sum(fe_rates) / 5 == pytest.approx(0.247, abs=0.0005)
        verdict = results["verdicts"]["equal_area_circle"]["verdict"]
        assert "NOT FIT" in verdict.upper()

    def test_after_the_production_predicate_agrees_with_the_reference(
            self, run, snapped_isochrones):
        _, universe, results = run
        col = {c: i for i, c in enumerate(universe["columns"])}
        assert set(snapped_isochrones) == set(universe["locations"])

        checked = 0
        for loc_id, loc in universe["locations"].items():
            snapped = results["locations"][loc_id]["snapped"]
            fixture = snapped_isochrones[loc_id]
            request = fixture["request"]
            origin = request["locations"][0]
            assert (origin["lat"], origin["lon"]) == (
                snapped["lat"], snapped["lon"])
            assert request["costing"] == "auto"
            assert request["contours"] == [{"time": 10}]
            assert request["polygons"] is True
            assert request["denoise"] == 0.3
            body = fixture["response"]
            boundary = pipeline.boundary_from_isochrone(
                body, snapped["lat"], snapped["lon"], loc_id)
            geometry = pipeline.boundary_geometry(boundary)
            reference = results["locations"][loc_id]["isochrone"]

            mismatches = [
                row[col["name"]] for row in loc["points"]
                if point_in_polygon(row[col["lon"]], row[col["lat"]], geometry)
                != bool(row[col["in_isochrone"]])]
            assert mismatches == [], (loc_id, mismatches[:5])
            assert boundary["metadata"]["isochrone_area_km2"] == (
                pytest.approx(reference["area_km2"], rel=0.005))
            assert boundary["metadata"]["geometry_components"] == (
                reference["n_components"])
            assert boundary["metadata"]["geometry_holes"] == (
                reference["n_holes"])
            checked += len(loc["points"])
        assert checked == sum(loc["n_points"]
                              for loc in universe["locations"].values())
