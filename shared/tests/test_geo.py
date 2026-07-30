import math

from hornethunter_shared.geo import (
    LatLon,
    distance_m,
    from_local_enu,
    initial_bearing_deg,
    intersect_bearings,
    normalize_bearing,
    to_local_enu,
    triangulate,
)

# Two stations 1 km apart on the same parallel, near Zurich.
WEST = LatLon(47.3769, 8.5417)
EAST = from_local_enu(WEST, 1000.0, 0.0)


def test_normalize_bearing_wraps() -> None:
    assert normalize_bearing(370.0) == 10.0
    assert normalize_bearing(-10.0) == 350.0


def test_local_enu_round_trip() -> None:
    point = LatLon(47.3800, 8.5500)
    east, north = to_local_enu(WEST, point)
    back = from_local_enu(WEST, east, north)
    assert math.isclose(back.lat, point.lat, abs_tol=1e-9)
    assert math.isclose(back.lon, point.lon, abs_tol=1e-9)


def test_distance_between_the_two_stations() -> None:
    assert math.isclose(distance_m(WEST, EAST), 1000.0, rel_tol=1e-4)


def test_initial_bearing_is_due_east() -> None:
    assert math.isclose(initial_bearing_deg(WEST, EAST), 90.0, abs_tol=0.01)


def test_symmetric_bearings_fix_midway_north() -> None:
    """45 deg from the west station and 315 deg from the east station cross
    500 m north of the midpoint."""
    fix = intersect_bearings(WEST, 45.0, EAST, 315.0)
    assert fix is not None

    east, north = to_local_enu(WEST, fix)
    assert math.isclose(east, 500.0, abs_tol=0.5)
    assert math.isclose(north, 500.0, abs_tol=0.5)


def test_parallel_bearings_have_no_fix() -> None:
    assert intersect_bearings(WEST, 0.0, EAST, 0.0) is None


def test_bearings_pointing_away_have_no_fix() -> None:
    """The lines cross south of the baseline, i.e. behind both stations."""
    assert intersect_bearings(WEST, 315.0, EAST, 45.0) is None


def test_triangulate_averages_pairwise_fixes() -> None:
    target = from_local_enu(WEST, 500.0, 500.0)
    third = from_local_enu(WEST, 500.0, -500.0)
    observations = [
        (WEST, initial_bearing_deg(WEST, target)),
        (EAST, initial_bearing_deg(EAST, target)),
        (third, initial_bearing_deg(third, target)),
    ]

    fix = triangulate(observations)
    assert fix is not None
    assert math.isclose(distance_m(fix, target), 0.0, abs_tol=1.0)


def test_triangulate_returns_none_when_nothing_crosses() -> None:
    assert triangulate([(WEST, 0.0), (EAST, 0.0)]) is None
