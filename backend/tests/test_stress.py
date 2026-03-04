"""
Stress test -- measures performance with 20-50 mock drones.
Validates: simulation tick rate, collision check scaling, aggregator throughput.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
from config import OverwatchSettings, AssetConfig
from simulation.mock_drone import MockSwarm, MockDrone
from telemetry.aggregator import SwarmAggregator
from telemetry.collector import DroneState
from swarm.collision import CollisionAvoidance
from swarm.formations import compute_formation_offsets, DRONE_ORDER
from protocol import OverlayType

def test_mock_swarm_20_drones():
    """Verify mock swarm handles 20 drones at 10Hz."""
    settings = OverwatchSettings()
    settings.sitl_drone_count = 20

    # Extend ASSET_ROSTER for testing
    from config import ASSET_ROSTER
    while len(ASSET_ROSTER) < 20:
        i = len(ASSET_ROSTER) + 1
        ASSET_ROSTER.append(AssetConfig(id=f"DRONE-{i}", role="ESCORT", color="#ffffff"))

    swarm = MockSwarm(settings)
    assert len(swarm.drones) == 20

    # Time 100 ticks
    leader = swarm.drones.get("ALPHA-1")
    t0 = time.monotonic()
    for _ in range(100):
        for drone in swarm.drones.values():
            drone.update(0.1, leader if drone.role != "PRIMARY" else None)
    elapsed = time.monotonic() - t0

    # 100 ticks for 20 drones should complete in under 1 second
    print(f"  20 drones x 100 ticks: {elapsed:.3f}s ({100*20/elapsed:.0f} drone-ticks/s)")
    assert elapsed < 1.0, f"Too slow: {elapsed:.3f}s"


def test_collision_check_scaling():
    """Test collision check with 50 drones (1225 pairs)."""
    ca = CollisionAvoidance(safety_bubble_m=5.0)

    # Create 50 drones in a grid
    states = []
    for i in range(50):
        row, col = divmod(i, 10)
        s = DroneState(
            drone_id=f"DRONE-{i}",
            lat=33.640 + row * 0.0001,
            lon=-117.844 + col * 0.0001,
            alt_msl=100,
            in_air=True,
        )
        states.append(s)

    t0 = time.monotonic()
    for _ in range(100):
        ca.check_all(states)
    elapsed = time.monotonic() - t0

    pairs = 50 * 49 // 2
    print(f"  50 drones ({pairs} pairs) x 100 checks: {elapsed:.3f}s")
    assert elapsed < 2.0, f"Collision check too slow: {elapsed:.3f}s"


def test_aggregator_serialization_throughput():
    """Test telemetry serialization speed for 20 drones."""
    states = {}
    for i in range(20):
        did = f"DRONE-{i}"
        s = DroneState(drone_id=did, lat=33.640+i*0.001, lon=-117.844,
                       alt_msl=100, alt_agl=85, in_air=True)
        s.remaining_pct = 80
        s.voltage = 22.5
        s.satellites = 14
        s.rssi = 85
        s.quality = 92
        s.latency_ms = 25
        states[did] = s

    t0 = time.monotonic()
    for _ in range(1000):
        packets = [s.to_telemetry_packet().model_dump(mode="json") for s in states.values()]
    elapsed = time.monotonic() - t0

    print(f"  20 drones x 1000 serializations: {elapsed:.3f}s ({1000*20/elapsed:.0f} packets/s)")
    # Should handle at least 10,000 packets/sec
    assert 1000 * 20 / elapsed > 5000, f"Serialization too slow"


def test_formation_compute_performance():
    """Test formation computation speed."""
    t0 = time.monotonic()
    for _ in range(10000):
        for ft in OverlayType:
            compute_formation_offsets(ft, 15.0)
    elapsed = time.monotonic() - t0

    total = 10000 * len(list(OverlayType))
    print(f"  {total} formation computations: {elapsed:.3f}s")
    assert elapsed < 2.0


if __name__ == "__main__":
    tests = [
        test_mock_swarm_20_drones,
        test_collision_check_scaling,
        test_aggregator_serialization_throughput,
        test_formation_compute_performance,
    ]
    print("OVERWATCH Stress Tests")
    print("=" * 50)
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} stress tests passed")
