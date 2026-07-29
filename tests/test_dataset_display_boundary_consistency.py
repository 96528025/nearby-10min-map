"""Foundation test 6 — dataset-to-display-boundary consistency.

    This verifies dataset-to-display-boundary consistency. It does NOT
    validate ten-minute drive-time accuracy.

Read that twice before citing anything in this file. Every assertion here is
`point_in_polygon(poi, displayed_boundary)`. It answers exactly one question —
"is the shipped facility list consistent with the shipped boundary?" — and it
is structurally incapable of answering any question about travel time, because
the boundary it checks against is the same artifact the data was filtered with.

Accuracy evidence comes only from Phase B
(`scripts/benchmark_accuracy.py`, `reports/accuracy/`), and even Phase B
reaches only "agreement with Valhalla's free-flow model". Neither this test
nor Phase B validates real-world ten-minute drive time.

See `docs/CURRENT_STATE_AUDIT.md` §6 and `docs/DECISIONS.md`.
"""
import json
from pathlib import Path

import pytest

import pipeline
from verify import point_in_polygon

DATA = Path(__file__).resolve().parent.parent / "map" / "data"

DISCLAIMER = ("This verifies dataset-to-display-boundary consistency. "
              "It does not validate ten-minute drive-time accuracy.")


@pytest.fixture(scope="module")
def boundary_geometry():
    return json.loads((DATA / "boundary.json").read_text())["features"][0][
        "geometry"]


@pytest.fixture(scope="module")
def facilities():
    return json.loads((DATA / "facilities.json").read_text())


def test_every_shipped_facility_is_inside_the_displayed_boundary_which_is_not_drive_time_accuracy(  # noqa: E501
        facilities, boundary_geometry):
    """Dataset-to-display-boundary consistency for the shipped Apple Park data.

    This verifies dataset-to-display-boundary consistency. It does NOT
    validate ten-minute drive-time accuracy.
    """
    outside = [
        (cat_key, item["name"])
        for cat_key, cat in facilities["categories"].items()
        for item in cat["items"]
        if not point_in_polygon(item["lon"], item["lat"], boundary_geometry)
    ]
    assert outside == [], (
        f"{len(outside)} shipped facilities fall outside the shipped "
        f"boundary: {outside[:5]}")


def test_every_shipped_landmark_is_inside_the_displayed_boundary_which_is_not_drive_time_accuracy(  # noqa: E501
        boundary_geometry):
    """Same check for the six featured landmarks.

    This verifies dataset-to-display-boundary consistency. It does NOT
    validate ten-minute drive-time accuracy. In particular, the `drive_min`
    values stored in landmarks.json are Valhalla route estimates recorded by
    hand; nothing in this repository re-derives or verifies them.
    """
    landmarks = json.loads((DATA / "landmarks.json").read_text())
    outside = [lm["name_en"] for lm in landmarks
               if not point_in_polygon(lm["lon"], lm["lat"],
                                       boundary_geometry)]
    assert outside == [], f"landmarks outside the boundary: {outside}"


def test_category_counts_match_item_counts(facilities):
    """Cheap integrity check on the shipped artifact itself."""
    for key, cat in facilities["categories"].items():
        assert cat["count"] == len(cat["items"]), f"count mismatch in {key}"


def test_verify_inside_gate_cannot_fail_by_construction(monkeypatch):
    """Characterisation test: the production 'hard boundary gate' is a no-op.

    `pipeline.verify_inside` is documented as a hard gate that aborts the run
    if any facility lies outside the boundary. Every facility reaching it was
    already filtered by the *same* `point_in_polygon` call against the *same*
    geometry, so the gate is a tautology and cannot fire.

    This test pins that fact so nobody later cites the gate as accuracy
    evidence. It asserts a structural property of the current design, not a
    desired one; see docs/CURRENT_STATE_AUDIT.md §6.

    This verifies dataset-to-display-boundary consistency. It does NOT
    validate ten-minute drive-time accuracy.
    """
    lat, lon = 37.33, -122.01
    geometry = {"type": "Polygon", "coordinates": [[
        [lon - 0.01, lat - 0.01], [lon + 0.01, lat - 0.01],
        [lon + 0.01, lat + 0.01], [lon - 0.01, lat + 0.01],
        [lon - 0.01, lat - 0.01]]]}

    elements = [
        {"type": "node", "id": 1, "lat": lat, "lon": lon,
         "tags": {"name": "Inside Cafe", "amenity": "cafe"}},
        # far outside the boundary; must never reach the gate
        {"type": "node", "id": 2, "lat": lat + 5.0, "lon": lon,
         "tags": {"name": "Outside Cafe", "amenity": "cafe"}},
    ]
    monkeypatch.setattr(pipeline, "overpass_query_all", lambda bbox: elements)

    fac = pipeline.osm_facilities(geometry)
    names = [i["name"] for i in fac["categories"]["dining"]["items"]]
    assert names == ["Inside Cafe"], "the filter, not the gate, did the work"

    # The gate therefore passes trivially — it has nothing left to reject.
    assert pipeline.verify_inside(fac, geometry) == 1


def test_disclaimer_wording_is_present_in_this_module():
    """Guard the required wording against a careless edit.

    The distinction this module draws is easy to lose in a refactor, and the
    consequence of losing it is that a point-in-polygon check gets cited as
    drive-time accuracy evidence.
    """
    source = Path(__file__).read_text().lower()
    assert "does not validate ten-minute drive-time accuracy" in source \
        or "does not\n    validate ten-minute drive-time accuracy" in source
    assert "dataset-to-display-boundary consistency" in source
