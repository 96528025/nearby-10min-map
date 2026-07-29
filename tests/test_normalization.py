"""Foundation test 5 — address and name normalisation.

`norm_name` and `addr_key` are the two normalisers the cross-source merge
depends on. If either becomes more aggressive, distinct venues start merging;
if either becomes stricter, the same venue appears twice from two sources.
"""
import pytest

from merge_overture import addr_key, norm_name


class TestNormName:
    @pytest.mark.parametrize("raw,expected", [
        ("Blue Bottle", "bluebottle"),
        ("BLUE BOTTLE", "bluebottle"),
        ("  Blue   Bottle  ", "bluebottle"),
        ("Peet's Coffee & Tea", "peetscoffeetea"),
        ("Trader Joe's", "traderjoes"),
        ("7-Eleven", "7eleven"),
        ("Café Rêve", "cafrve"),   # accents are DELETED, not transliterated
    ])
    def test_normalises(self, raw, expected):
        assert norm_name(raw) == expected

    def test_accented_and_unaccented_spellings_do_NOT_match(self):
        """Known limitation, pinned deliberately rather than asserted as good.

        `norm_name` strips anything outside [a-z0-9] and CJK, so an accented
        character is removed instead of folded to its base letter. "Café Rêve"
        becomes "cafrve" while "Cafe Reve" becomes "cafereve", so the two
        sources will NOT dedup the same venue when they disagree about
        accents. Fixing it means Unicode NFKD folding before the strip; that
        is a behaviour change, so it is recorded here rather than made
        silently.
        """
        assert norm_name("Café Rêve") != norm_name("Cafe Reve")
        assert norm_name("Café Rêve") == "cafrve"
        assert norm_name("Cafe Reve") == "cafereve"

    def test_case_and_punctuation_collapse_to_the_same_key(self):
        assert norm_name("In-N-Out Burger") == norm_name("in n out burger")

    def test_cjk_is_preserved(self):
        """Category labels and many venue names are bilingual."""
        assert norm_name("海底捞火锅") == "海底捞火锅"
        assert norm_name("海底捞 (Haidilao)") == "海底捞haidilao"

    def test_distinct_names_stay_distinct(self):
        assert norm_name("Starbucks") != norm_name("Starbird")

    def test_is_idempotent(self):
        once = norm_name("Peet's Coffee & Tea")
        assert norm_name(once) == once


class TestAddrKey:
    def test_house_number_plus_first_street_word(self):
        assert addr_key("19359 Stevens Creek Boulevard") == "19359stevens"

    def test_abbreviation_and_city_suffix_agree(self):
        """The documented case: two spellings of one address must match."""
        assert addr_key("19359 Stevens Creek Boulevard") == \
            addr_key("19359 Stevens Creek Blvd Cupertino")

    def test_case_insensitive(self):
        assert addr_key("19359 STEVENS CREEK BLVD") == "19359stevens"

    @pytest.mark.parametrize("prefix", ["north", "south", "east", "west",
                                        "n", "s", "e", "w"])
    def test_directional_prefix_is_skipped(self, prefix):
        assert addr_key(f"100 {prefix} First Street") == "100first"

    def test_different_house_numbers_differ(self):
        assert addr_key("19359 Stevens Creek Blvd") != \
            addr_key("19360 Stevens Creek Blvd")

    def test_different_streets_differ(self):
        assert addr_key("100 First Street") != addr_key("100 Second Street")

    @pytest.mark.parametrize("bad", [None, "", "Stevens Creek Blvd",
                                     "Suite 200", "no number here"])
    def test_unparseable_addresses_return_none(self, bad):
        assert addr_key(bad) is None

    def test_none_keys_must_not_be_treated_as_equal_by_callers(self):
        """Two unparseable addresses both yield None.

        `same_place` guards this with `if a and a == addr_key(...)`, so this
        test pins the precondition that guard relies on.
        """
        assert addr_key("Suite 200") is None
        assert addr_key("Building C") is None
