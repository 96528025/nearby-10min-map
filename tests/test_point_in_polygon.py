"""Foundation test 1 — `point_in_polygon`.

This predicate is the single in-boundary test used by the whole pipeline
(facility filtering, the Overture merge, and the shipped `verify.py`), so its
behaviour on holes and MultiPolygons is load-bearing.
"""
import pytest

from verify import point_in_polygon


class TestPolygon:
    def test_interior_point_is_inside(self, square):
        assert point_in_polygon(0.0, 0.0, square) is True

    def test_exterior_point_is_outside(self, square):
        assert point_in_polygon(5.0, 5.0, square) is False

    @pytest.mark.parametrize("lon,lat", [
        (0.999, 0.0), (-0.999, 0.0), (0.0, 0.999), (0.0, -0.999)])
    def test_just_inside_each_edge(self, lon, lat, square):
        assert point_in_polygon(lon, lat, square) is True

    @pytest.mark.parametrize("lon,lat", [
        (1.001, 0.0), (-1.001, 0.0), (0.0, 1.001), (0.0, -1.001)])
    def test_just_outside_each_edge(self, lon, lat, square):
        assert point_in_polygon(lon, lat, square) is False


class TestHoles:
    def test_point_in_hole_is_outside(self, square_with_hole):
        assert point_in_polygon(0.0, 0.0, square_with_hole) is False

    def test_point_between_hole_and_outer_ring_is_inside(self,
                                                         square_with_hole):
        assert point_in_polygon(0.75, 0.75, square_with_hole) is True

    def test_point_outside_outer_ring_is_outside(self, square_with_hole):
        assert point_in_polygon(5.0, 5.0, square_with_hole) is False

    def test_hole_is_not_ignored(self, square, square_with_hole):
        """The same point flips when a hole is introduced.

        Guards against a regression that only ever reads `coordinates[0]` and
        silently drops interior rings.
        """
        assert point_in_polygon(0.0, 0.0, square) is True
        assert point_in_polygon(0.0, 0.0, square_with_hole) is False


class TestMultiPolygon:
    def test_inside_first_component(self, multipolygon_with_hole):
        assert point_in_polygon(0.0, 0.0, multipolygon_with_hole) is True

    def test_inside_second_component(self, multipolygon_with_hole):
        assert point_in_polygon(10.2, 10.2, multipolygon_with_hole) is True

    def test_inside_hole_of_second_component(self, multipolygon_with_hole):
        assert point_in_polygon(11.0, 11.0, multipolygon_with_hole) is False

    def test_between_components(self, multipolygon_with_hole):
        assert point_in_polygon(5.0, 5.0, multipolygon_with_hole) is False

    def test_second_component_is_not_dropped(self, multipolygon_with_hole):
        """A point in a non-first component must still count as inside.

        `pipeline.boundary_from_isochrone` reads only
        `features[0].geometry.coordinates[0]`; this asserts that the
        *predicate* at least does not share that truncation.
        """
        assert point_in_polygon(11.9, 10.1, multipolygon_with_hole) is True


class TestContract:
    def test_unsupported_geometry_raises(self):
        with pytest.raises(ValueError):
            point_in_polygon(0.0, 0.0, {"type": "LineString",
                                        "coordinates": [[0, 0], [1, 1]]})

    def test_is_deterministic_on_boundary_points(self, square):
        """Exactly-on-edge points are not asserted to a convention.

        Ray casting is ill-defined on the boundary, and the pipeline never
        depends on which way an exact-boundary point falls. What must hold is
        that the answer is a stable bool, not that it is True or False.
        """
        for lon, lat in [(1.0, 0.0), (0.0, 1.0), (-1.0, -1.0), (1.0, 1.0)]:
            first = point_in_polygon(lon, lat, square)
            assert isinstance(first, bool)
            assert point_in_polygon(lon, lat, square) is first
