"""Tests for geofence engine."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swarm.geofence import Geofence, GeofenceViolation
from telemetry.collector import DroneState

# Default test polygon — matches config.py geofence_vertices
VERTICES = [
    (33.6450, -117.8500),
    (33.6450, -117.8380),
    (33.6360, -117.8380),
    (33.6360, -117.8500),
]
MAX_ALT = 120.0


def _make_fence() -> Geofence:
    return Geofence(vertices=VERTICES, max_altitude_m=MAX_ALT)


def test_point_inside_fence():
    """A drone well inside the polygon should produce no violation."""
    gf = _make_fence()
    state = DroneState(
        drone_id="ALPHA-1",
        lat=33.6405, lon=-117.8440,
        alt_msl=50.0, in_air=True,
    )
    violation = gf.check(state)
    assert violation is None, f"Expected no violation, got {violation}"


def test_point_outside_fence():
    """A drone clearly outside the polygon should trigger a BOUNDARY violation."""
    gf = _make_fence()
    state = DroneState(
        drone_id="BRAVO-2",
        lat=33.6500, lon=-117.8440,   # north of the top edge (33.6450)
        alt_msl=50.0, in_air=True,
    )
    violation = gf.check(state)
    assert violation is not None, "Expected a BOUNDARY violation"
    assert violation.violation_type == "BOUNDARY"
    assert violation.drone_id == "BRAVO-2"
    assert violation.suggested_action == "RTL"
    assert violation.distance_to_fence > 0


def test_altitude_violation():
    """A drone inside the boundary but above max_altitude triggers ALTITUDE."""
    gf = _make_fence()
    state = DroneState(
        drone_id="CHARLIE-3",
        lat=33.6405, lon=-117.8440,
        alt_msl=150.0, in_air=True,   # 30 m above the 120 m ceiling
    )
    violation = gf.check(state)
    assert violation is not None, "Expected an ALTITUDE violation"
    assert violation.violation_type == "ALTITUDE"
    assert violation.drone_id == "CHARLIE-3"
    # distance_to_fence should equal the excess altitude (30 m)
    assert abs(violation.distance_to_fence - 30.0) < 0.01


def test_grounded_drone_ignored():
    """A grounded drone (in_air=False) should never produce a violation,
    even if it is outside the polygon."""
    gf = _make_fence()
    state = DroneState(
        drone_id="DELTA-4",
        lat=34.0000, lon=-118.0000,   # far outside the fence
        alt_msl=0.0, in_air=False,
    )
    violation = gf.check(state)
    assert violation is None, f"Grounded drone should be ignored, got {violation}"


def test_distance_to_boundary():
    """distance_to_boundary returns positive inside, negative outside."""
    gf = _make_fence()

    # Centre of the polygon — definitely inside
    centre_lat = sum(v[0] for v in VERTICES) / len(VERTICES)
    centre_lon = sum(v[1] for v in VERTICES) / len(VERTICES)
    d_inside = gf.distance_to_boundary(centre_lat, centre_lon)
    assert d_inside > 0, f"Centre should be inside (positive), got {d_inside}"

    # A point clearly outside
    d_outside = gf.distance_to_boundary(33.6500, -117.8440)
    assert d_outside < 0, f"External point should be outside (negative), got {d_outside}"

    # Magnitude should be reasonable (hundreds of metres, not kilometres)
    assert abs(d_inside) < 2000, f"Inside distance unreasonable: {d_inside}"
    assert abs(d_outside) < 2000, f"Outside distance unreasonable: {d_outside}"


if __name__ == "__main__":
    tests = [
        test_point_inside_fence,
        test_point_outside_fence,
        test_altitude_violation,
        test_grounded_drone_ignored,
        test_distance_to_boundary,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} tests passed")
