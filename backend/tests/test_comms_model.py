"""
Tests for backend/attacker/comms_model.py

Covers link quality physics, adjacency matrix properties, neighbor detection,
jamming degradation, graph fragmentation, and Olfati-Saber weights.
"""
from __future__ import annotations

import sys
import os

import numpy as np
import pytest

# Ensure backend root is importable when running from backend/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from attacker.comms_model import CommsParams, CommsTopology

# ---- Helpers ----

def make_topology(params: CommsParams = None) -> CommsTopology:
    return CommsTopology(params)


# ---- Link quality ----

def test_close_drones_high_quality() -> None:
    """10 m separation should yield near-perfect link quality."""
    topo = make_topology()
    q = topo.compute_link_quality(10.0)
    assert q > 0.95, f"Expected >0.95 at 10 m, got {q:.4f}"


def test_far_drones_low_quality() -> None:
    """5000 m separation should yield near-zero link quality."""
    topo = make_topology()
    q = topo.compute_link_quality(5000.0)
    assert q < 0.05, f"Expected <0.05 at 5000 m, got {q:.4f}"


# ---- Adjacency matrix ----

def test_adjacency_symmetric() -> None:
    """Adjacency matrix must be symmetric: A[i][j] == A[j][i]."""
    topo = make_topology()
    positions = [
        (0.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
        (100.0, 50.0, 10.0),
        (200.0, 200.0, 20.0),
    ]
    adj = topo.compute_adjacency(positions)
    np.testing.assert_allclose(adj, adj.T, atol=1e-12)


def test_adjacency_diagonal_zero() -> None:
    """Diagonal of adjacency matrix must be zero (no self-link)."""
    topo = make_topology()
    positions = [
        (0.0, 0.0, 0.0),
        (30.0, 0.0, 0.0),
        (60.0, 0.0, 0.0),
    ]
    adj = topo.compute_adjacency(positions)
    for i in range(len(positions)):
        assert adj[i, i] == 0.0, f"Expected 0.0 on diagonal [{i},{i}], got {adj[i, i]}"


# ---- Neighbor detection ----

def test_neighbors_within_range() -> None:
    """Drones close together should appear as neighbors."""
    topo = make_topology()
    positions = [
        (0.0, 0.0, 0.0),    # drone 0
        (20.0, 0.0, 0.0),   # drone 1, close
        (4000.0, 0.0, 0.0), # drone 2, far
    ]
    neighbors = topo.get_neighbors(0, positions, threshold=0.3)
    assert 1 in neighbors, "Drone 1 at 20 m should be a neighbor of drone 0"
    assert 2 not in neighbors, "Drone 2 at 4000 m should not be a neighbor of drone 0"


# ---- Jamming ----

def test_jamming_degrades_links() -> None:
    """Adjacency values should decrease under jamming vs no jamming."""
    topo = make_topology()
    positions = [
        (0.0, 0.0, 0.0),
        (100.0, 0.0, 0.0),
        (200.0, 0.0, 0.0),
    ]
    jammer_pos = (100.0, 0.0, 0.0)  # jammer in the middle

    adj_clean = topo.compute_adjacency(positions)
    adj_jammed = topo.apply_jamming(
        positions,
        jammer_position=jammer_pos,
        jammer_power_dbm=50.0,
        jammer_bandwidth_mhz=40.0,
    )

    # At least some links should degrade.
    degraded = False
    n = len(positions)
    for i in range(n):
        for j in range(i + 1, n):
            if adj_jammed[i, j] < adj_clean[i, j] - 1e-9:
                degraded = True
    assert degraded, "Jamming should degrade at least one link"


def test_jammer_nearby_worse() -> None:
    """Closer jammer should cause more link degradation than a distant one."""
    topo = make_topology()
    positions = [
        (0.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
    ]

    adj_near = topo.apply_jamming(
        positions,
        jammer_position=(25.0, 0.0, 0.0),
        jammer_power_dbm=50.0,
    )
    adj_far = topo.apply_jamming(
        positions,
        jammer_position=(5000.0, 0.0, 0.0),
        jammer_power_dbm=50.0,
    )

    # Near jammer should produce worse (lower) link quality.
    assert adj_near[0, 1] < adj_far[0, 1], (
        f"Near jammer quality {adj_near[0, 1]:.4f} should be worse than "
        f"far jammer quality {adj_far[0, 1]:.4f}"
    )


# ---- Fragmentation ----

def test_fragmentation_connected() -> None:
    """All close drones should form 1 connected component."""
    topo = make_topology()
    # Tight cluster: all pairs within easy communication range.
    positions = [
        (0.0, 0.0, 0.0),
        (15.0, 0.0, 0.0),
        (30.0, 0.0, 0.0),
        (45.0, 0.0, 0.0),
    ]
    adj = topo.compute_adjacency(positions)
    count = topo.fragmentation_count(adj, threshold=0.3)
    assert count == 1, f"Tight cluster should have 1 component, got {count}"


def test_fragmentation_split() -> None:
    """Two well-separated groups should yield 2 connected components."""
    topo = make_topology()
    # Group A near origin, group B far away.
    positions = [
        (0.0, 0.0, 0.0),
        (20.0, 0.0, 0.0),
        (10000.0, 0.0, 0.0),
        (10020.0, 0.0, 0.0),
    ]
    adj = topo.compute_adjacency(positions)
    count = topo.fragmentation_count(adj, threshold=0.3)
    assert count == 2, f"Two isolated groups should give 2 components, got {count}"


# ---- Olfati-Saber weight ----

def test_olfati_saber_decays() -> None:
    """Weight must strictly decrease as distance increases."""
    topo = make_topology()
    distances = [0.0, 25.0, 50.0, 100.0, 200.0]
    weights = [topo.olfati_saber_weight(d) for d in distances]
    for i in range(len(weights) - 1):
        assert weights[i] > weights[i + 1], (
            f"Weight at d={distances[i]} ({weights[i]:.4f}) should exceed "
            f"weight at d={distances[i+1]} ({weights[i+1]:.4f})"
        )


def test_olfati_saber_at_zero() -> None:
    """Weight at distance 0 must be 1.0."""
    topo = make_topology()
    w = topo.olfati_saber_weight(0.0)
    assert abs(w - 1.0) < 1e-12, f"Expected 1.0 at distance 0, got {w}"


def test_olfati_saber_at_r0() -> None:
    """Weight at distance r0 must be exp(-alpha)."""
    import math
    topo = make_topology()
    r0, alpha = 75.0, 2.0
    w = topo.olfati_saber_weight(r0, r0=r0, alpha=alpha)
    expected = math.exp(-alpha)
    assert abs(w - expected) < 1e-12, f"Expected {expected:.6f} at r0, got {w:.6f}"
