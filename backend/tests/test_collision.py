"""Tests for collision avoidance."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swarm.collision import CollisionAvoidance, haversine_meters
from telemetry.collector import DroneState


def test_haversine_zero_distance():
    d = haversine_meters(33.6405, -117.8443, 33.6405, -117.8443)
    assert d < 0.01


def test_haversine_known_distance():
    # ~111m per degree latitude
    d = haversine_meters(33.0, -117.0, 33.001, -117.0)
    assert 100 < d < 120


def test_no_collision_far_apart():
    ca = CollisionAvoidance(safety_bubble_m=5.0)
    s1 = DroneState(drone_id="A", lat=33.640, lon=-117.844, alt_msl=100, in_air=True)
    s2 = DroneState(drone_id="B", lat=33.641, lon=-117.844, alt_msl=100, in_air=True)
    cmds = ca.check_all([s1, s2])
    assert len(cmds) == 0


def test_collision_close():
    ca = CollisionAvoidance(safety_bubble_m=5.0)
    s1 = DroneState(drone_id="A", lat=33.640000, lon=-117.844, alt_msl=100, in_air=True)
    s2 = DroneState(drone_id="B", lat=33.640001, lon=-117.844, alt_msl=100, in_air=True)
    cmds = ca.check_all([s1, s2])
    assert len(cmds) > 0


def test_grounded_drones_ignored():
    ca = CollisionAvoidance(safety_bubble_m=5.0)
    s1 = DroneState(drone_id="A", lat=33.640, lon=-117.844, alt_msl=0, in_air=False)
    s2 = DroneState(drone_id="B", lat=33.640, lon=-117.844, alt_msl=0, in_air=False)
    cmds = ca.check_all([s1, s2])
    assert len(cmds) == 0


if __name__ == "__main__":
    tests = [
        test_haversine_zero_distance,
        test_haversine_known_distance,
        test_no_collision_far_apart,
        test_collision_close,
        test_grounded_drones_ignored,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} tests passed")
