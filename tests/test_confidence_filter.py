"""Foundation test 4 — Overture confidence-threshold filtering.

Exercised through `pipeline.merge_overture`, the real production function,
with the `overturemaps` CLI subprocess replaced by a stub that writes a
fixture GeoJSON. Nothing here touches the network or the real CLI.

The product decision under test: keep `confidence >= 0.6` and drop the rest,
preferring to under-report rather than display a venue that may have closed.
"""
import json
from pathlib import Path

import pytest

import pipeline
from fetch_facilities import CATEGORIES
from merge_overture import MIN_CONFIDENCE

LAT, LON = 37.33, -122.01


@pytest.fixture
def boundary():
    return {"type": "Polygon", "coordinates": [[
        [LON - 0.05, LAT - 0.05], [LON + 0.05, LAT - 0.05],
        [LON + 0.05, LAT + 0.05], [LON - 0.05, LAT + 0.05],
        [LON - 0.05, LAT - 0.05]]]}


@pytest.fixture
def empty_facilities():
    return {"metadata": {}, "categories": {
        k: {"label_zh": c["zh"], "label_en": c["en"], "color": c["color"],
            "count": 0, "items": []} for k, c in CATEGORIES.items()}}


def place(name, confidence, primary="restaurant", lat=LAT, lon=LON,
          addresses=None):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"names": {"primary": name},
                           "confidence": confidence,
                           "categories": {"primary": primary},
                           "addresses": addresses}}


@pytest.fixture
def merge(monkeypatch, boundary, empty_facilities):
    """Run pipeline.merge_overture against a fixture instead of the CLI."""
    def _merge(features, fac=None):
        def fake_run(cmd, **kwargs):
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_text(json.dumps({"type": "FeatureCollection",
                                       "features": features}))
            return type("R", (), {"returncode": 0, "stdout": b"",
                                  "stderr": b""})()

        monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
        return pipeline.merge_overture(fac or empty_facilities, boundary)
    return _merge


def dining(result):
    return [i["name"] for i in result["categories"]["dining"]["items"]]


class TestThreshold:
    def test_above_threshold_is_kept(self, merge):
        assert dining(merge([place("High", 0.95)])) == ["High"]

    def test_exactly_at_threshold_is_kept(self, merge):
        """`>= MIN_CONFIDENCE`, so the boundary value survives."""
        assert dining(merge([place("Exact", MIN_CONFIDENCE)])) == ["Exact"]

    def test_just_below_threshold_is_dropped(self, merge):
        assert dining(merge([place("Low", MIN_CONFIDENCE - 0.01)])) == []

    def test_far_below_threshold_is_dropped(self, merge):
        assert dining(merge([place("VeryLow", 0.1)])) == []

    def test_missing_confidence_is_treated_as_zero_and_dropped(self, merge):
        p = place("NoConf", 0.9)
        del p["properties"]["confidence"]
        assert dining(merge([p])) == []

    def test_null_confidence_is_dropped(self, merge):
        assert dining(merge([place("NullConf", None)])) == []

    def test_mixed_batch_keeps_only_qualifying(self, merge):
        out = merge([place("Keep A", 0.9), place("Drop B", 0.3),
                     place("Keep C", 0.75, lat=LAT + 0.01),
                     place("Drop D", 0.59)])
        assert sorted(dining(out)) == ["Keep A", "Keep C"]


class TestOtherGates:
    def test_place_outside_boundary_is_dropped(self, merge):
        assert dining(merge([place("Far", 0.99, lat=LAT + 1.0)])) == []

    def test_unnamed_place_is_dropped(self, merge):
        p = place("x", 0.99)
        p["properties"]["names"] = {}
        assert dining(merge([p])) == []

    def test_unmappable_category_is_dropped(self, merge):
        assert dining(merge([place("Barber", 0.99,
                                   primary="barber_shop")])) == []

    def test_duplicate_of_existing_osm_record_is_dropped(
            self, merge, empty_facilities):
        empty_facilities["categories"]["dining"]["items"].append(
            {"name": "Blue Bottle Coffee", "lat": LAT, "lon": LON,
             "addr": None, "osm": "node/1"})
        out = merge([place("Blue Bottle Coffee", 0.99)], empty_facilities)
        assert dining(out) == ["Blue Bottle Coffee"]
        assert len(out["categories"]["dining"]["items"]) == 1


class TestMetadata:
    def test_records_threshold_and_source(self, merge):
        out = merge([place("Any", 0.9)])
        assert out["metadata"]["overture_min_confidence"] == MIN_CONFIDENCE
        assert "Overture" in out["metadata"]["source"]

    def test_counts_are_recomputed(self, merge):
        out = merge([place("A", 0.9), place("B", 0.9, lat=LAT + 0.01)])
        assert out["categories"]["dining"]["count"] == 2
