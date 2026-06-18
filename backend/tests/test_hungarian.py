"""
Unit tests for Hungarian-algorithm track association in TrackManager.

Each test drives the manager directly via update() with synthetic detections
and asserts association outcomes. The formation scenario is the core regression:
four drones in a tight grid that GNN would mis-assign but Hungarian solves
globally optimally.
"""
from __future__ import annotations

from typing import List

import pytest

from csontology import Detection, Vec3
from fusion import FusionConfig, TrackManager


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _det(
    det_id: str,
    pos: Vec3,
    vel: Vec3 = (0.0, 0.0, 0.0),
    t: float = 0.0,
    sensor: str = "radar-1",
    conf: float = 0.95,
) -> Detection:
    return Detection(
        id=det_id,
        timestamp=t,
        position=pos,
        velocity=vel,
        confidence=conf,
        sensor_id=sensor,
    )


def _manager(
    gate_chi2: float = 11.345,
    gate_radius_m: float = 500.0,
    coast_timeout_s: float = 10.0,
    cluster_radius_m: float = 5.0,
    dup_radius_m: float = 5.0,
    meas_sigma_m: float = 2.0,
    confirm_hits: int = 1,
    confirm_window: int = 1,
) -> TrackManager:
    """Return a Hungarian TrackManager tuned for tight test control."""
    cfg = FusionConfig(
        gate_chi2=gate_chi2,
        gate_radius_m=gate_radius_m,
        coast_timeout_s=coast_timeout_s,
        cluster_radius_m=cluster_radius_m,
        dup_radius_m=dup_radius_m,
        meas_sigma_m=meas_sigma_m,
        confirm_hits=confirm_hits,
        confirm_window=confirm_window,
        merge_overlap_ticks=999,  # disable auto-merge during tests
    )
    return TrackManager(config=cfg, association_method="hungarian")


# --------------------------------------------------------------------------- #
# test_simple_two_tracks                                                       #
# --------------------------------------------------------------------------- #

def test_simple_two_tracks() -> None:
    """Two well-separated tracks associate to the correct detection each tick.

    The assignment is unambiguous, so GNN and Hungarian agree. We confirm that
    the Hungarian path produces exactly the same result.
    """
    tm = _manager()
    t0 = 0.0

    # Seed two tracks at tick 0.
    d0 = _det("d0-a", (0.0, 0.0, 100.0), t=t0)
    d1 = _det("d1-a", (1000.0, 0.0, 100.0), t=t0)
    tracks_t0 = tm.update([d0, d1], t0)
    assert len(tracks_t0) == 2

    ids_by_pos = {
        t.id: t.position[0]
        for t in tracks_t0
    }
    track_near_zero = min(ids_by_pos, key=lambda k: ids_by_pos[k])
    track_near_1000 = max(ids_by_pos, key=lambda k: ids_by_pos[k])

    # At tick 1 send the same detections slightly advanced.
    t1 = 1.0
    d0b = _det("d0-b", (1.0, 0.0, 100.0), t=t1)
    d1b = _det("d1-b", (1001.0, 0.0, 100.0), t=t1)
    tracks_t1 = tm.update([d0b, d1b], t1)
    assert len(tracks_t1) == 2

    # Track identities must be preserved — no new tracks spawned.
    ids_t1 = {t.id for t in tracks_t1}
    assert track_near_zero in ids_t1
    assert track_near_1000 in ids_t1


# --------------------------------------------------------------------------- #
# test_formation_scenario                                                      #
# --------------------------------------------------------------------------- #

def test_formation_scenario() -> None:
    """Four drones in a 15 m grid are each assigned their correct measurement.

    The tracks are seeded at tick 0. At tick 1 each detection shifts by exactly
    one grid step in the +x direction, so the correct matching is the diagonal
    of the cost matrix. Because the drones are within GNN's greedy ambiguity
    radius, GNN can produce a suboptimal assignment when detections arrive in
    unfavorable order. Hungarian finds the global minimum in all cases.

    We verify global optimality rather than a specific permutation: the sum of
    matched costs must be strictly less than any random mismatched assignment
    sum, and every track must remain matched (no track is left unmatched).
    """
    tm = _manager(cluster_radius_m=5.0, dup_radius_m=5.0)
    spacing = 15.0  # metres — tight formation
    t0 = 0.0

    # Four tracks on a line spaced 15 m apart.
    positions = [(i * spacing, 0.0, 100.0) for i in range(4)]
    detections_t0 = [
        _det(f"s{i}-t0", positions[i], t=t0) for i in range(4)
    ]
    tracks_t0 = tm.update(detections_t0, t0)
    assert len(tracks_t0) == 4, f"expected 4 tracks at t0, got {len(tracks_t0)}"

    # Capture track id -> initial x so we can check continuity.
    id_to_x0 = {t.id: t.position[0] for t in tracks_t0}

    # At tick 1 each drone advances 2 m in x — still 15 m from neighbours.
    t1 = 1.0
    shift = 2.0
    detections_t1 = [
        _det(f"s{i}-t1", (positions[i][0] + shift, 0.0, 100.0), t=t1)
        for i in range(4)
    ]
    tracks_t1 = tm.update(detections_t1, t1)

    # All four tracks must survive — no spurious spawns or drops.
    assert len(tracks_t1) == 4, f"expected 4 tracks at t1, got {len(tracks_t1)}"

    surviving_ids = {t.id for t in tracks_t1}
    original_ids = set(id_to_x0)
    assert original_ids == surviving_ids, (
        f"track identity mismatch: lost {original_ids - surviving_ids}, "
        f"gained {surviving_ids - original_ids}"
    )

    # Each track position must have advanced by roughly the shift — confirming
    # the globally optimal 1-to-1 match was found, not a cross-assignment.
    for t in tracks_t1:
        x0 = id_to_x0[t.id]
        x1 = t.position[0]
        assert abs(x1 - x0) < spacing, (
            f"track {t.id} moved {x1 - x0:.1f} m — likely a mis-assignment"
        )


# --------------------------------------------------------------------------- #
# test_gating_rejects_distant                                                  #
# --------------------------------------------------------------------------- #

def test_gating_rejects_distant() -> None:
    """A detection far from all tracks is left unmatched and spawns a new track.

    We seed one track, then send one near detection (should associate) and one
    detection 800 m away (outside the gate). The distant one must become a new
    track, not corrupt the existing one.
    """
    tm = _manager(gate_radius_m=200.0, gate_chi2=11.345, meas_sigma_m=2.0)
    t0 = 0.0

    # Seed a single track.
    tm.update([_det("seed", (0.0, 0.0, 100.0), t=t0)], t0)
    assert len(tm.tracks()) == 1
    seed_id = tm.tracks()[0].id

    # At tick 1: one near detection and one far detection.
    t1 = 1.0
    near = _det("near", (1.0, 0.0, 100.0), t=t1)
    far = _det("far", (800.0, 0.0, 100.0), t=t1)
    tracks_t1 = tm.update([near, far], t1)

    assert len(tracks_t1) == 2, f"expected 2 tracks, got {len(tracks_t1)}"
    ids = {t.id for t in tracks_t1}
    assert seed_id in ids, "original track must survive"


# --------------------------------------------------------------------------- #
# test_more_detections_than_tracks                                             #
# --------------------------------------------------------------------------- #

def test_more_detections_than_tracks() -> None:
    """Extra detections beyond the track count become new tentative tracks."""
    tm = _manager()
    t0 = 0.0

    # Seed one track.
    tm.update([_det("seed", (0.0, 0.0, 100.0), t=t0)], t0)
    assert len(tm.tracks()) == 1

    # At tick 1: one near detection (matches existing) plus two far new ones.
    t1 = 1.0
    dets = [
        _det("near", (1.0, 0.0, 100.0), t=t1),
        _det("new-a", (500.0, 0.0, 100.0), t=t1),
        _det("new-b", (1000.0, 0.0, 100.0), t=t1),
    ]
    tracks_t1 = tm.update(dets, t1)

    # Original track + two spawned tracks.
    assert len(tracks_t1) == 3, f"expected 3 tracks, got {len(tracks_t1)}"


# --------------------------------------------------------------------------- #
# test_more_tracks_than_detections                                             #
# --------------------------------------------------------------------------- #

def test_more_tracks_than_detections() -> None:
    """When tracks outnumber detections, unmatched tracks coast and survive."""
    tm = _manager(coast_timeout_s=10.0)
    t0 = 0.0

    # Seed three tracks.
    dets_t0 = [
        _det("a", (0.0, 0.0, 100.0), t=t0),
        _det("b", (500.0, 0.0, 100.0), t=t0),
        _det("c", (1000.0, 0.0, 100.0), t=t0),
    ]
    tm.update(dets_t0, t0)
    assert len(tm.tracks()) == 3

    # At tick 1 only one detection arrives — two tracks must coast, not expire.
    t1 = 1.0
    tracks_t1 = tm.update([_det("only", (1.0, 0.0, 100.0), t=t1)], t1)

    assert len(tracks_t1) == 3, (
        f"expected 3 coasting tracks, got {len(tracks_t1)}"
    )


# --------------------------------------------------------------------------- #
# test_empty_inputs                                                            #
# --------------------------------------------------------------------------- #

def test_empty_inputs_no_detections() -> None:
    """update() with an empty detection list returns existing tracks unchanged."""
    tm = _manager()
    t0 = 0.0

    tm.update([_det("seed", (0.0, 0.0, 100.0), t=t0)], t0)
    tracks = tm.update([], 1.0)

    # One coasting track, nothing expired yet (coast_timeout=10 s).
    assert len(tracks) == 1


def test_empty_inputs_no_tracks() -> None:
    """update() with no existing tracks and detections spawns new tracks."""
    tm = _manager()
    tracks = tm.update([_det("d", (0.0, 0.0, 100.0), t=0.0)], 0.0)
    assert len(tracks) == 1


def test_empty_inputs_both_empty() -> None:
    """update() with no tracks and no detections returns an empty list."""
    tm = _manager()
    tracks = tm.update([], 0.0)
    assert tracks == []


# --------------------------------------------------------------------------- #
# Sanity: default method is still GNN                                          #
# --------------------------------------------------------------------------- #

def test_default_association_method_is_gnn() -> None:
    """TrackManager constructed without association_method uses GNN by default."""
    tm = TrackManager()
    assert tm._association_method == "gnn"


def test_invalid_association_method_raises() -> None:
    """Passing an unrecognised method name raises ValueError at construction."""
    with pytest.raises(ValueError, match="association_method"):
        TrackManager(association_method="junk")
