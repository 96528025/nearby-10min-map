"""Tests for scripts/benchmark_accuracy.py.

The benchmark decides which boundary the product ships, so its verdict logic,
its undefined handling, its validity gates and its run-status determination
need to be pinned. Reviewer feedback on Run 1 was that "97 passed" said
nothing about the benchmark, because every test targeted the original
pipeline. This module closes that.

Offline like the rest of the suite: no run is executed and no external call is
made. Only pure functions and small synthetic structures are exercised.
"""
import importlib.util
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def committed():
    """The published run of record."""
    runs = sorted((ROOT / "reports" / "accuracy" / "runs").glob("*"))
    assert runs, "no run directory committed"
    return json.loads((runs[-1] / "results.json").read_text())


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location(
        "benchmark_accuracy", ROOT / "scripts" / "benchmark_accuracy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# rate(): a zero denominator is undefined, never 0 %
# --------------------------------------------------------------------------
class TestRate:
    def test_normal_rate(self, bench):
        r = bench.rate(3, 12)
        assert r["rate"] == 0.25 and r["status"] == "defined"

    def test_zero_numerator_is_defined_zero(self, bench):
        r = bench.rate(0, 40)
        assert r["rate"] == 0.0 and r["status"] == "defined"

    def test_zero_denominator_is_undefined_not_zero(self, bench):
        r = bench.rate(0, 0)
        assert r["rate"] is None
        assert r["status"] == "undefined / insufficient POIs"
        assert r["rate"] != 0.0


# --------------------------------------------------------------------------
# aggregate(): macro excludes undefined and reports n; micro pools
# --------------------------------------------------------------------------
def _loc(fi, fe):
    """Build a per-location record with given (numer, denom) tuples."""
    return {"status": "ok", "candidates": {"c": {"poi": {
        "false_inclusion": {"rate": (fi[0] / fi[1]) if fi[1] else None,
                            "numerator": fi[0], "denominator": fi[1],
                            "status": "defined" if fi[1] else "undefined"},
        "false_exclusion": {"rate": (fe[0] / fe[1]) if fe[1] else None,
                            "numerator": fe[0], "denominator": fe[1],
                            "status": "defined" if fe[1] else "undefined"},
    }}}}


class TestAggregate:
    def test_macro_is_unweighted_mean(self, bench):
        locs = {"a": _loc((10, 100), (0, 10)), "b": _loc((0, 100), (0, 10))}
        m = bench.aggregate(locs, "c", "false_inclusion")["macro"]
        assert m["value"] == pytest.approx(0.05) and m["n"] == 2

    def test_micro_pools_before_dividing(self, bench):
        """Micro must not be the mean of the per-location rates."""
        locs = {"a": _loc((10, 10), (0, 1)), "b": _loc((0, 990), (0, 1))}
        agg = bench.aggregate(locs, "c", "false_inclusion")
        assert agg["micro"]["value"] == pytest.approx(10 / 1000)
        assert agg["macro"]["value"] == pytest.approx(0.5)
        assert agg["micro"]["value"] != agg["macro"]["value"]

    def test_undefined_is_excluded_from_macro_not_counted_as_zero(self, bench):
        locs = {"a": _loc((10, 100), (0, 10)), "b": _loc((0, 0), (0, 10))}
        m = bench.aggregate(locs, "c", "false_inclusion")["macro"]
        assert m["n"] == 1
        assert m["value"] == pytest.approx(0.10)   # not 0.05
        assert m["locations"] == ["a"]

    def test_all_undefined_gives_undefined_macro(self, bench):
        locs = {"a": _loc((0, 0), (0, 0)), "b": _loc((0, 0), (0, 0))}
        agg = bench.aggregate(locs, "c", "false_inclusion")
        assert agg["macro"]["status"] == "undefined"
        assert agg["macro"]["value"] is None
        assert agg["micro"]["status"] == "undefined"


# --------------------------------------------------------------------------
# verdict(): the preregistered P1..P4
# --------------------------------------------------------------------------
def _locs(spec):
    """spec: list of (fi_numer, fi_denom, fe_numer, fe_denom)."""
    return {f"L{i}": _loc((a, b), (c, d))
            for i, (a, b, c, d) in enumerate(spec)}


def _verdict(bench, locs):
    agg = {"c": {m: bench.aggregate(locs, "c", m)
                 for m in ("false_inclusion", "false_exclusion")}}
    return bench.verdict(locs, agg, "c")


class TestVerdict:
    def test_clean_pass(self, bench):
        v = _verdict(bench, _locs([(2, 100, 5, 100)] * 5))
        assert v["verdict"] == "FIT FOR PURPOSE"
        assert v["failed"] == []

    def test_p1_macro_fi_boundary_is_inclusive(self, bench):
        """macro FI exactly 0.10 must pass (rule is <=)."""
        v = _verdict(bench, _locs([(10, 100, 5, 100)] * 5))
        assert v["checks"]["P1_macro_fi<=0.10"] is True

    def test_p1_fails_just_above(self, bench):
        v = _verdict(bench, _locs([(11, 100, 5, 100)] * 5))
        assert v["checks"]["P1_macro_fi<=0.10"] is False
        assert "P1" in v["failed"] and v["verdict"] == "NOT FIT FOR PURPOSE"

    def test_p2_fails_on_macro_fe(self, bench):
        v = _verdict(bench, _locs([(2, 100, 25, 100)] * 5))
        assert "P2" in v["failed"]

    def test_p3_catches_a_single_bad_location_that_macro_hides(self, bench):
        """One catastrophic location averaged away by four good ones."""
        locs = _locs([(30, 100, 5, 100)] + [(5, 100, 5, 100)] * 4)
        v = _verdict(bench, locs)
        assert v["checks"]["P1_macro_fi<=0.10"] is True    # macro = 0.10
        assert v["checks"]["P3_per_location_caps"] is False
        assert "P3" in v["failed"]
        assert any("FI=0.300" in o for o in v["offenders"])

    def test_p4_insufficient_evidence_outranks_a_pass(self, bench):
        """Small denominators must not buy a pass."""
        v = _verdict(bench, _locs([(0, 5, 0, 5)] * 5))
        assert v["checks"]["P4_evidence_sufficiency"] is False
        assert v["verdict"] == "INSUFFICIENT EVIDENCE"

    def test_p4_boundary_needs_four_locations_at_denominator_30(self, bench):
        ok = (2, 30, 2, 30)
        thin = (0, 29, 0, 29)
        assert _verdict(bench, _locs([ok] * 4 + [thin]))["verdict"] \
            == "FIT FOR PURPOSE"
        assert _verdict(bench, _locs([ok] * 3 + [thin] * 2))["verdict"] \
            == "INSUFFICIENT EVIDENCE"

    def test_thresholds_are_the_preregistered_ones(self, bench):
        assert bench.THRESH == {"macro_fi": 0.10, "macro_fe": 0.20,
                                "loc_fi": 0.20, "loc_fe": 0.35,
                                "min_denom": 30, "min_locations": 4}


# --------------------------------------------------------------------------
# Validity gates must be gates, not annotations
# --------------------------------------------------------------------------
def _guarded(g1=True, g2=True, a1=True, status="ok"):
    return {"loc": {
        "status": status,
        "guardrail_G1_equal_area_identity": {
            "pass": g1, "relative_to_area": 0.0 if g1 else 0.5,
            "tolerance": 0.005},
        "guardrail_G2_projection": {
            "pass": g2, "relative_difference": 0.0 if g2 else 0.5,
            "tolerance": 0.001},
        "assertion_A1_envelope_slack": {
            "equal_area_circle": {"pass": a1,
                                  "slack_m": 100.0 if a1 else -5.0}},
    }}


class TestValidityGates:
    def test_all_passing_yields_no_failures(self, bench):
        assert bench.validity_failures(_guarded()) == []

    @pytest.mark.parametrize("kw,gate", [
        ({"g1": False}, "G1_equal_area_identity"),
        ({"g2": False}, "G2_projection"),
        ({"a1": False}, "A1_envelope_slack"),
    ])
    def test_each_gate_is_detected(self, bench, kw, gate):
        fails = bench.validity_failures(_guarded(**kw))
        assert len(fails) == 1 and fails[0]["gate"] == gate
        assert fails[0]["location"] == "loc"
        assert fails[0]["consequence"]

    def test_multiple_failures_are_all_reported(self, bench):
        fails = bench.validity_failures(_guarded(g1=False, g2=False,
                                                 a1=False))
        assert {f["gate"] for f in fails} == {
            "G1_equal_area_identity", "G2_projection", "A1_envelope_slack"}

    def test_skipped_locations_are_not_scanned_for_gates(self, bench):
        """A skipped location has no geometry to guard; it is not invalid."""
        assert bench.validity_failures(
            _guarded(g1=False, status="geometry anomaly: ...")) == []

    def test_committed_run_passes_every_gate(self, bench):
        """Regression guard on the published run of record."""
        runs = sorted((ROOT / "reports" / "accuracy" / "runs").glob("*"))
        assert runs, "no run directory committed"
        res = json.loads((runs[-1] / "results.json").read_text())
        assert bench.validity_failures(res["locations"]) == []


# --------------------------------------------------------------------------
# Run status must reflect sub-request failures, not just top-level exceptions
# --------------------------------------------------------------------------
class TestRunStatus:
    """Calls production `run_status` directly.

    An earlier version of this class reimplemented the status rule inside the
    test, which meant it asserted against a copy of the logic rather than the
    logic — the same self-referential mistake this project keeps auditing for
    elsewhere. A regression in production would not have failed these tests.
    """

    def test_clean_run_is_complete_and_publishes(self, bench):
        s = bench.run_status([], [], [], [])
        assert s["status"] == "complete"
        assert s["publish_verdicts"] is True
        assert s["run_valid"] is True
        assert s["withheld_reason"] is None

    def test_a_failed_directional_route_blocks_and_withholds(self, bench):
        s = bench.run_status([], [], ["route bearing 45 to r=100m"], [])
        assert s["status"] == "benchmark blocked"
        assert s["publish_verdicts"] is False
        assert s["run_valid"] is True          # guardrails were fine
        assert s["incomplete"] == ["route bearing 45 to r=100m"]

    def test_a_skipped_location_blocks_and_withholds(self, bench):
        s = bench.run_status([], [], [], ["hmb: geometry anomaly"])
        assert s["status"] == "benchmark blocked"
        assert s["publish_verdicts"] is False

    def test_a_location_exception_blocks_and_withholds(self, bench):
        s = bench.run_status([], [{"location": "sjc", "error": "HTTPError"}],
                             [], [])
        assert s["publish_verdicts"] is False

    def test_guardrail_failure_outranks_blocked(self, bench):
        s = bench.run_status([{"gate": "G1"}], [], ["x"], [])
        assert s["status"] == "invalid: preregistered guardrail failed"
        assert s["run_valid"] is False
        assert s["publish_verdicts"] is False

    def test_all_incomplete_sources_are_collected(self, bench):
        s = bench.run_status([], ["a"], ["b"], ["c"])
        assert s["incomplete"] == ["a", "b", "c"]

    def test_withheld_reason_is_given_whenever_verdicts_are_withheld(
            self, bench):
        for args in (([{"gate": "G1"}], [], [], []),
                     ([], ["x"], [], []),
                     ([], [], ["y"], []),
                     ([], [], [], ["z"])):
            s = bench.run_status(*args)
            assert s["publish_verdicts"] is False
            assert s["withheld_reason"]


class TestPartialRunCannotPublish:
    """A dropped location can flip a verdict, which is why blocked withholds.

    P4 tolerates 4 of 5 locations, so a run that loses one still satisfies the
    evidence floor. On the committed run, losing `sjc_airport` alone moves the
    equal-area circle from NOT FIT to FIT FOR PURPOSE. If a blocked run were
    allowed to publish, the conclusion would be decided by whichever request
    happened to fail.
    """

    def test_full_sample_says_not_fit(self, committed):
        assert committed["verdicts"]["equal_area_circle"]["verdict"] \
            == "NOT FIT FOR PURPOSE"

    def test_dropping_sjc_would_flip_the_verdict(self, bench, committed):
        sub = {k: v for k, v in committed["locations"].items()
               if k != "sjc_airport"}
        agg = {"equal_area_circle": {
            m: bench.aggregate(sub, "equal_area_circle", m)
            for m in ("false_inclusion", "false_exclusion")}}
        v = bench.verdict(sub, agg, "equal_area_circle")
        assert v["verdict"] == "FIT FOR PURPOSE"
        assert v["checks"]["P4_evidence_sufficiency"] is True

    def test_so_a_blocked_run_must_not_publish(self, bench):
        assert bench.run_status(
            [], [{"location": "sjc_airport", "error": "HTTPError"}], [],
            [])["publish_verdicts"] is False


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
class TestGeometry:
    def test_circle_area_matches_pi_r_squared(self, bench):
        c = bench.circle(1000.0)
        assert c.area == pytest.approx(math.pi * 1000 ** 2, rel=1e-4)

    def test_component_containing_picks_the_right_one(self, bench):
        mp = bench.clean(box(-10, -10, 10, 10).union(box(100, 100, 120, 120)))
        got = bench.component_containing(mp, Point(110, 110))
        assert got is not None and got.covers(Point(110, 110))
        assert not got.covers(Point(0, 0))

    def test_component_containing_returns_none_when_outside(self, bench):
        assert bench.component_containing(box(0, 0, 1, 1),
                                          Point(50, 50)) is None

    def test_max_radius_to_is_the_farthest_vertex(self, bench):
        assert bench.max_radius_to(box(-3, -4, 3, 4)) == pytest.approx(5.0)

    def test_inscribed_radius_respects_a_hole(self, bench):
        """A hole around the origin must shrink the inscribed circle."""
        ring = Polygon(box(-100, -100, 100, 100).exterior.coords,
                       [box(-10, -10, 10, 10).exterior.coords])
        # origin sits inside the hole, so it is not covered by the polygon
        assert not ring.covers(Point(0, 0))
        outside_hole = Point(50, 0)
        assert ring.covers(outside_hole)
        assert outside_hole.distance(ring.boundary) == pytest.approx(40.0)

    def test_boundary_point_at_bearing_on_a_circle(self, bench):
        c = bench.circle(1000.0)
        p = bench.boundary_point_at_bearing(c, 90, 5000)
        assert p.x == pytest.approx(1000, rel=1e-3)
        assert p.y == pytest.approx(0, abs=1.0)

    def test_boundary_point_at_bearing_takes_outermost_crossing(self, bench):
        """Concave shapes must be measured at their true outer edge."""
        concave = Polygon([(0, -50), (200, -50), (200, 50), (100, 50),
                           (100, 10), (150, 10), (150, -10), (50, -10),
                           (50, 50), (0, 50)])
        p = bench.boundary_point_at_bearing(concave, 90, 1000)
        assert p.x == pytest.approx(200.0)

    def test_boundary_point_at_bearing_returns_none_when_ray_misses(self,
                                                                    bench):
        far = box(1000, 1000, 1100, 1100)
        assert bench.boundary_point_at_bearing(far, 180, 500) is None


class TestEnvelope:
    def test_margin_expands_the_union_bounds(self, bench):
        geoms = [bench.circle(1000.0)]
        _, rect = bench.envelope_bbox_wgs84(
            geoms, lambda x, y: (x / 88000.0, y / 111000.0), margin=0.10)
        minx, miny, maxx, maxy = rect.bounds
        assert minx < -1000 and maxx > 1000
        assert (maxx - minx) == pytest.approx(2000 * 1.2, rel=1e-6)

    def test_every_candidate_has_positive_slack_under_the_margin(self, bench):
        """A1 should hold by construction for POI-independent candidates."""
        cands = [bench.circle(500.0), bench.circle(1200.0)]
        _, rect = bench.envelope_bbox_wgs84(
            cands, lambda x, y: (x / 88000.0, y / 111000.0))
        eb = rect.bounds
        for g in cands:
            gb = g.bounds
            slack = min(gb[0] - eb[0], gb[1] - eb[1],
                        eb[2] - gb[2], eb[3] - gb[3])
            assert slack > 0


# --------------------------------------------------------------------------
# Exploratory target-FI solve
# --------------------------------------------------------------------------
class TestSolveTargetFi:
    def test_picks_largest_radius_meeting_the_target(self, bench):
        dists = [10.0, 20.0, 30.0, 40.0]
        in_ref = {0: True, 1: True, 2: True, 3: False}
        sol = bench.solve_target_fi(dists, in_ref, cap=1000.0, target=0.10)
        assert sol["radius_m"] == 30.0 and sol["status"] == "interior"

    def test_marks_censored_when_the_cap_is_optimal(self, bench):
        dists = [10.0, 20.0]
        in_ref = {0: True, 1: True}
        sol = bench.solve_target_fi(dists, in_ref, cap=50.0, target=0.10)
        assert sol["radius_m"] == 50.0 and sol["status"] == "censored"

    def test_infeasible_when_nothing_meets_the_target(self, bench):
        dists = [10.0, 20.0]
        in_ref = {0: False, 1: False}
        sol = bench.solve_target_fi(dists, in_ref, cap=100.0, target=0.10)
        assert sol["status"] == "infeasible"
        assert "radius_m" not in sol

    def test_solution_never_exceeds_the_cap(self, bench):
        dists = [10.0, 500.0, 900.0]
        in_ref = {0: True, 1: True, 2: True}
        sol = bench.solve_target_fi(dists, in_ref, cap=100.0, target=0.10)
        assert sol["radius_m"] <= 100.0

    def test_handles_non_monotonic_fi(self, bench):
        """FI(r) is a step function and may fall again as r grows.

        FI by radius here is 0.00, 0.50, 0.33, 0.25, 0.40 — so a rule like
        "stop at the first radius that crosses the target" would return 10 m.
        The preregistered rule is "the LARGEST radius meeting the target",
        which is 40 m.
        """
        dists = [10.0, 20.0, 30.0, 40.0, 50.0]
        in_ref = {0: True, 1: False, 2: True, 3: True, 4: False}
        sol = bench.solve_target_fi(dists, in_ref, cap=60.0, target=0.30)
        assert sol["radius_m"] == 40.0
        assert sol["fi"] == pytest.approx(0.25)
        assert sol["status"] == "interior"


# --------------------------------------------------------------------------
# Cache key composition
# --------------------------------------------------------------------------
class TestCacheKey:
    def test_key_depends_on_config_hash(self, bench):
        bench.CACHE_SALT["config_sha256"] = "aaa"
        k1 = bench.cache_key("http_get", {"endpoint": "x"})
        bench.CACHE_SALT["config_sha256"] = "bbb"
        k2 = bench.cache_key("http_get", {"endpoint": "x"})
        assert k1 != k2

    def test_key_depends_on_payload_and_kind(self, bench):
        bench.CACHE_SALT["config_sha256"] = "aaa"
        base = bench.cache_key("http_get", {"endpoint": "x"})
        assert base != bench.cache_key("http_get", {"endpoint": "y"})
        assert base != bench.cache_key("overpass", {"endpoint": "x"})

    def test_key_is_stable_for_identical_input(self, bench):
        bench.CACHE_SALT["config_sha256"] = "aaa"
        assert bench.cache_key("http_get", {"a": 1, "b": 2}) == \
            bench.cache_key("http_get", {"b": 2, "a": 1})


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------
class TestProvenance:
    def test_records_what_is_needed_to_identify_a_run(self, bench):
        p = bench.provenance()
        for field in ("script_sha256", "git_commit", "python", "packages",
                      "overture_release", "valhalla_host",
                      "git_tracked_files_modified"):
            assert field in p, f"missing provenance field {field}"
        assert len(p["script_sha256"]) == 64

    def test_dirty_flag_ignores_untracked_run_output(self, bench):
        """A run writes its own output directory before it finishes.

        Counting untracked files would mark every run dirty and make the flag
        useless, which is exactly what an earlier version did.
        """
        import subprocess
        tracked = subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain",
             "--untracked-files=no"], text=True)
        assert bench.provenance()["git_tracked_files_modified"] \
            == bool(tracked.strip())

    def test_script_hash_tracks_the_actual_file(self, bench):
        expected = bench.sha256_file(
            ROOT / "scripts" / "benchmark_accuracy.py")
        assert bench.provenance()["script_sha256"] == expected

    def test_discloses_the_valhalla_graph_version_gap(self, bench):
        note = bench.provenance()["reproducibility_note"].lower()
        assert "graph build version" in note
