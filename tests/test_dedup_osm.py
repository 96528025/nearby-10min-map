"""Foundation test 2 — OSM-internal near-distance deduplication.

Exercised through `pipeline.osm_facilities`, the real production function,
with Overpass mocked. The rule under test is the one the README calls out as
a bug the project already hit once: deduplicating by name alone collapses
every branch of a chain into a single point, so only same-name points within
150 m may merge.
"""
import json

import pytest

import pipeline

LAT, LON = 37.33, -122.01
M_PER_DEG_LAT = 111000.0


def offset_lat(metres):
    return LAT + metres / M_PER_DEG_LAT


@pytest.fixture
def boundary():
    """~11 km square centred on (LAT, LON) — everything below sits inside."""
    return {"type": "Polygon", "coordinates": [[
        [LON - 0.05, LAT - 0.05], [LON + 0.05, LAT - 0.05],
        [LON + 0.05, LAT + 0.05], [LON - 0.05, LAT + 0.05],
        [LON - 0.05, LAT - 0.05]]]}


def node(nid, name, lat, lon, **tags):
    t = {"name": name}
    t.update(tags or {"amenity": "cafe"})
    return {"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": t}


@pytest.fixture
def run(monkeypatch, boundary):
    def _run(elements):
        monkeypatch.setattr(pipeline, "overpass_query_all",
                            lambda bbox: elements)
        return pipeline.osm_facilities(boundary)
    return _run


def names(result, category):
    return [i["name"] for i in result["categories"][category]["items"]]


class TestNearDuplicateMerging:
    def test_same_name_within_150m_is_merged(self, run):
        out = run([node(1, "Blue Bottle", LAT, LON),
                   node(2, "Blue Bottle", offset_lat(50), LON)])
        assert names(out, "dining") == ["Blue Bottle"]

    def test_same_name_beyond_150m_is_kept(self, run):
        """Chain branches must survive — the Starbucks 1-vs-15 regression."""
        out = run([node(1, "Starbucks", LAT, LON),
                   node(2, "Starbucks", offset_lat(500), LON),
                   node(3, "Starbucks", offset_lat(1000), LON)])
        assert names(out, "dining") == ["Starbucks"] * 3

    def test_boundary_of_the_dedup_radius(self, run):
        just_in = run([node(1, "Peet's", LAT, LON),
                       node(2, "Peet's", offset_lat(149), LON)])
        just_out = run([node(1, "Peet's", LAT, LON),
                        node(2, "Peet's", offset_lat(151), LON)])
        assert len(names(just_in, "dining")) == 1
        assert len(names(just_out, "dining")) == 2

    def test_different_names_nearby_are_both_kept(self, run):
        out = run([node(1, "Cafe A", LAT, LON),
                   node(2, "Cafe B", offset_lat(10), LON)])
        assert sorted(names(out, "dining")) == ["Cafe A", "Cafe B"]

    def test_same_element_id_twice_is_counted_once(self, run):
        """One element can match several category selectors."""
        out = run([node(7, "Dupe Cafe", LAT, LON),
                   node(7, "Dupe Cafe", LAT, LON)])
        assert names(out, "dining") == ["Dupe Cafe"]

    def test_dedup_does_not_cross_categories(self, run):
        out = run([node(1, "Same Name", LAT, LON, amenity="cafe"),
                   node(2, "Same Name", offset_lat(10), LON, amenity="fuel")])
        assert names(out, "dining") == ["Same Name"]
        assert names(out, "fuel_ev") == ["Same Name"]


class TestFiltering:
    def test_point_outside_boundary_is_excluded(self, run):
        out = run([node(1, "Far Cafe", LAT + 1.0, LON)])
        assert names(out, "dining") == []

    def test_unnamed_element_is_excluded(self, run):
        out = run([{"type": "node", "id": 1, "lat": LAT, "lon": LON,
                    "tags": {"amenity": "cafe"}}])
        assert names(out, "dining") == []

    def test_uncategorised_element_is_excluded(self, run):
        out = run([node(1, "Some Bench", LAT, LON, amenity="bench")])
        assert sum(c["count"] for c in out["categories"].values()) == 0

    def test_hospital_room_number_is_excluded(self, run):
        """OSM maps hospital-internal suites as clinics named by room."""
        out = run([node(1, "120 Nuclear Medicine", LAT, LON,
                        amenity="clinic"),
                   node(2, "Kaiser Permanente", offset_lat(300), LON,
                        amenity="hospital")])
        assert names(out, "health") == ["Kaiser Permanente"]

    def test_way_with_center_is_included(self, run):
        out = run([{"type": "way", "id": 5,
                    "center": {"lat": LAT, "lon": LON},
                    "tags": {"name": "Mall Way", "shop": "mall"}}])
        assert names(out, "shopping") == ["Mall Way"]

    def test_way_without_center_is_skipped(self, run):
        out = run([{"type": "way", "id": 5,
                    "tags": {"name": "No Centre", "shop": "mall"}}])
        assert names(out, "shopping") == []


def test_output_is_json_serialisable_and_counts_match(run):
    out = run([node(1, "A Cafe", LAT, LON),
               node(2, "B Hotel", offset_lat(200), LON, tourism="hotel")])
    json.dumps(out)
    for cat in out["categories"].values():
        assert cat["count"] == len(cat["items"])
