"""Tests for formation geometry."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swarm.formations import (
    compute_formation_offsets, offset_to_latlon, calculate_cohesion,
    V_FORMATION_OFFSETS, DRONE_ORDER,
)
from protocol import OverlayType, OffsetVector


def test_v_formation_leader_at_origin():
    offsets = compute_formation_offsets(OverlayType.V_FORMATION)
    assert offsets["ALPHA-1"].dx == 0
    assert offsets["ALPHA-1"].dy == 0


def test_v_formation_symmetric():
    offsets = compute_formation_offsets(OverlayType.V_FORMATION)
    assert offsets["BRAVO-2"].dx == -offsets["CHARLIE-3"].dx
    assert offsets["BRAVO-2"].dy == offsets["CHARLIE-3"].dy
    assert offsets["DELTA-4"].dx == -offsets["ECHO-5"].dx
    assert offsets["DELTA-4"].dy == offsets["ECHO-5"].dy


def test_all_formations_have_all_drones():
    for ft in OverlayType:
        offsets = compute_formation_offsets(ft)
        for drone_id in DRONE_ORDER:
            assert drone_id in offsets, f"{drone_id} missing from {ft.value}"


def test_offset_to_latlon_zero_heading():
    lat, lon = offset_to_latlon(33.6405, -117.8443, 0.0, OffsetVector(dx=0, dy=0))
    assert abs(lat - 33.6405) < 0.0001
    assert abs(lon - (-117.8443)) < 0.0001


def test_offset_to_latlon_nonzero():
    lat, lon = offset_to_latlon(33.6405, -117.8443, 90.0, OffsetVector(dx=100, dy=0))
    # dx=100m east, heading=90 -> should rotate
    assert lat != 33.6405 or lon != -117.8443


def test_perfect_cohesion():
    c = calculate_cohesion(OffsetVector(dx=10, dy=-15), OffsetVector(dx=10, dy=-15))
    assert c == 1.0


def test_zero_cohesion():
    c = calculate_cohesion(OffsetVector(dx=100, dy=100), OffsetVector(dx=0, dy=0), 15.0)
    assert c == 0.0


def test_partial_cohesion():
    c = calculate_cohesion(OffsetVector(dx=12, dy=-15), OffsetVector(dx=10, dy=-15), 15.0)
    assert 0 < c < 1


if __name__ == "__main__":
    tests = [
        test_v_formation_leader_at_origin,
        test_v_formation_symmetric,
        test_all_formations_have_all_drones,
        test_offset_to_latlon_zero_heading,
        test_offset_to_latlon_nonzero,
        test_perfect_cohesion,
        test_zero_cohesion,
        test_partial_cohesion,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} tests passed")
