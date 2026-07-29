"""Foundation test 3 — OSM vs Overture cross-source deduplication.

`merge_overture.same_place` decides whether an Overture place is already
present as an OSM record. It applies three rules with deliberately different
tolerances, because the two sources pin the same venue differently (building
centroid vs storefront):

  * exact normalised name  -> merge within 300 m
  * name containment       -> merge within 150 m (weaker evidence)
  * equal house number + first street word -> merge at any distance
"""
import pytest

from merge_overture import (SAME_PLACE_METERS_CONTAINS,
                            SAME_PLACE_METERS_EXACT, same_place)

LAT, LON = 37.33, -122.01
M_PER_DEG_LAT = 111000.0
# same_place uses a hardcoded 88000 m/deg for longitude, so offsets here are
# applied in latitude, where the constant is unambiguous.


def at(metres, name="Blue Bottle Coffee", addr=None):
    return {"name": name, "lat": LAT + metres / M_PER_DEG_LAT, "lon": LON,
            "addr": addr}


class TestExactName:
    def test_merges_well_within_300m(self):
        assert same_place([at(0)], "Blue Bottle Coffee", LAT, LON) is True

    def test_merges_just_inside_300m(self):
        assert same_place([at(0)], "Blue Bottle Coffee",
                          LAT + 290 / M_PER_DEG_LAT, LON) is True

    def test_does_not_merge_beyond_300m(self):
        assert same_place([at(0)], "Blue Bottle Coffee",
                          LAT + 400 / M_PER_DEG_LAT, LON) is False

    def test_normalisation_ignores_case_and_punctuation(self):
        assert same_place([at(0, "Peet's Coffee & Tea")],
                          "PEETS COFFEE  TEA", LAT, LON) is True

    def test_distinct_chain_branches_are_not_merged(self):
        """The whole point of the distance guard."""
        assert same_place([at(0, "Starbucks")], "Starbucks",
                          LAT + 900 / M_PER_DEG_LAT, LON) is False


class TestNameContainment:
    def test_merges_within_150m(self):
        assert same_place([at(0, "Whole Foods Market")], "Whole Foods",
                          LAT + 100 / M_PER_DEG_LAT, LON) is True

    def test_does_not_merge_between_150m_and_300m(self):
        """Containment is weaker evidence, so it gets the tighter radius."""
        assert same_place([at(0, "Whole Foods Market")], "Whole Foods",
                          LAT + 200 / M_PER_DEG_LAT, LON) is False

    def test_short_substrings_do_not_trigger(self):
        """Guard against 'Ono' matching 'Onomichi' style false merges."""
        assert same_place([at(0, "Ono")], "Onomichi Ramen", LAT, LON) is False

    def test_unrelated_names_never_merge(self):
        assert same_place([at(0, "Blue Bottle Coffee")], "Taqueria Corona",
                          LAT, LON) is False


class TestAddressRule:
    def test_same_address_merges_at_any_distance(self):
        existing = [at(0, "Target", addr="20745 Stevens Creek Blvd")]
        assert same_place(existing, "Target", LAT + 5000 / M_PER_DEG_LAT, LON,
                          addr="20745 Stevens Creek Boulevard Cupertino") \
            is True

    def test_address_rule_still_requires_a_name_match(self):
        existing = [at(0, "Target", addr="20745 Stevens Creek Blvd")]
        assert same_place(existing, "Completely Different",
                          LAT + 5000 / M_PER_DEG_LAT, LON,
                          addr="20745 Stevens Creek Boulevard") is False

    def test_different_house_numbers_do_not_merge_far_apart(self):
        existing = [at(0, "Target", addr="20745 Stevens Creek Blvd")]
        assert same_place(existing, "Target", LAT + 5000 / M_PER_DEG_LAT, LON,
                          addr="19359 Stevens Creek Blvd") is False


def test_empty_candidate_list_is_never_a_duplicate():
    assert same_place([], "Anything", LAT, LON) is False


def test_radii_ordering_is_the_documented_one():
    assert SAME_PLACE_METERS_EXACT > SAME_PLACE_METERS_CONTAINS
