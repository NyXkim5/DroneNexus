"""
Integration tests for per-sensor R matrices and delay compensation in TrackManager.

Four test cases:
  - test_default_behavior_unchanged: no flags, identical behavior to baseline.
  - test_sensor_models_produces_different_R: RADAR and EOIR detections drive
    different covariances when use_sensor_models=True.
  - test_delay_compensation_active: a delayed detection still updates the track.
  - test_both_features_combined: both flags True, system processes correctly.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from csontology import Detection, Vec3
from fusion import FusionConfig, TrackManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _det(
    det_id: str,
    pos: Vec3,
    vel: Vec3,
    t: float,
    sensor: str = "radar-1",
    conf: float = 0.9,
) -> Detection:
    """Build a Detection with configurable sensor_id."""
    return Detection(
        id=det_id,
        timestamp=t,
        position=pos,
        velocity=vel,
        confidence=conf,
        sensor_id=sensor,
    )


def _advance(pos: Vec3, vel: Vec3, dt: float) -> Vec3:
    """Move a ground-truth position along its velocity by dt."""
    return (pos[0] + vel[0] * dt, pos[1] + vel[1] * dt, pos[2] + vel[2] * dt)


# Sensor registry used by sensor-model tests.
# Maps sensor_id -> (sensor_kind, sensor_position_ENU).
_RADAR_POS: Vec3 = (0.0, 0.0, 5.0)
_EOIR_POS: Vec3 = (50.0, 0.0, 5.0)

REGISTRY: dict[str, tuple[str, Vec3]] = {
    "radar-1": ("RADAR", _RADAR_POS),
    "eoir-1":  ("EOIR",  _EOIR_POS),
}


# ---------------------------------------------------------------------------
# test_default_behavior_unchanged
# ---------------------------------------------------------------------------

def test_default_behavior_unchanged() -> None:
    """TrackManager() with no flags must behave identically to the baseline.

    We run the same scenario twice — once with bare TrackManager() and once with
    the new flags explicitly False — and assert the track count, confirmation
    status, and approximate position match.
    """
    pos: Vec3 = (300.0, 0.0, 60.0)
    vel: Vec3 = (-8.0, 3.0, 0.0)

    def _run(mgr: TrackManager) -> tuple[int, Vec3]:
        p = pos
        t = 0.0
        for i in range(10):
            p = _advance(p, vel, 0.1)
            t += 0.1
            mgr.update([_det(f"d{i}", p, vel, t)], t)
        confirmed = mgr.confirmed_tracks()
        return len(confirmed), confirmed[0].position if confirmed else (0.0, 0.0, 0.0)

    mgr_default = TrackManager()
    mgr_explicit = TrackManager(use_sensor_models=False, use_delay_compensation=False)

    count_default, pos_default = _run(mgr_default)
    count_explicit, pos_explicit = _run(mgr_explicit)

    assert count_default == 1
    assert count_explicit == 1
    assert pos_default[0] == pytest.approx(pos_explicit[0], abs=1e-9)
    assert pos_default[1] == pytest.approx(pos_explicit[1], abs=1e-9)


# ---------------------------------------------------------------------------
# test_sensor_models_produces_different_R
# ---------------------------------------------------------------------------

def test_sensor_models_produces_different_R() -> None:
    """RADAR and EOIR detections must produce different track covariances.

    We run two independent managers — one fed only radar detections, one fed
    only EOIR detections — on the same ground truth. The confirmed tracks'
    covariances must differ because the two sensor models carry different noise
    shapes. A RADAR sensor has a larger bearing sigma and a moderate range sigma;
    EOIR has a tight bearing sigma but larger range sigma growth. At any given
    geometry these produce non-identical R matrices, so the posterior covariances
    differ.
    """
    pos: Vec3 = (500.0, 0.0, 80.0)
    vel: Vec3 = (-10.0, 0.0, 0.0)

    def _run_sensor(sensor_id: str) -> Vec3:
        mgr = TrackManager(
            use_sensor_models=True,
            sensor_registry=REGISTRY,
        )
        p = pos
        t = 0.0
        for i in range(10):
            p = _advance(p, vel, 0.1)
            t += 0.1
            mgr.update([_det(f"d{i}", p, vel, t, sensor=sensor_id)], t)
        confirmed = mgr.confirmed_tracks()
        assert len(confirmed) == 1, f"expected 1 confirmed track for {sensor_id}"
        return confirmed[0].covariance

    cov_radar = _run_sensor("radar-1")
    cov_eoir = _run_sensor("eoir-1")

    # Covariances must differ by more than floating-point noise.
    diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(cov_radar, cov_eoir)))
    assert diff > 0.01, (
        f"radar covariance {cov_radar} and eoir covariance {cov_eoir} "
        f"are too similar (diff={diff:.6f}); sensor models had no effect"
    )


# ---------------------------------------------------------------------------
# test_delay_compensation_active
# ---------------------------------------------------------------------------

def test_delay_compensation_active() -> None:
    """A delayed detection must still update the track when compensation is on.

    We seed a track with several on-time measurements, then inject a single
    detection whose timestamp is 0.3 s in the past (well above the 0.05 s
    threshold). With use_delay_compensation=True the compensator should rewind
    to the closest snapshot, apply the update there, and re-predict to now.
    The track must survive (not expire) and its position must be near the target.
    """
    cfg = FusionConfig(coast_timeout_s=5.0)
    mgr = TrackManager(
        config=cfg,
        use_delay_compensation=True,
        delay_threshold_s=0.05,
    )

    pos: Vec3 = (200.0, 0.0, 50.0)
    vel: Vec3 = (10.0, 0.0, 0.0)
    t = 0.0

    # Seed the track with enough hits to confirm it.
    for i in range(6):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"seed{i}", pos, vel, t)], t)

    assert len(mgr.confirmed_tracks()) == 1

    # Inject one late detection: timestamp is 0.3 s behind current t.
    late_pos = _advance(pos, vel, -0.2)  # where the target was 0.2 s ago
    late_det = _det("late", late_pos, vel, t - 0.3, conf=0.85)

    t += 0.1
    tracks = mgr.update([late_det], t)

    # The track must still be alive.
    confirmed = mgr.confirmed_tracks()
    assert len(confirmed) == 1, "track lost after delayed detection"

    # Position should still be reasonable (within 30 m of true position).
    true_pos = _advance(pos, vel, 0.1)
    tr = confirmed[0]
    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(tr.position, true_pos)))
    assert dist < 30.0, (
        f"track position {tr.position} too far from true {true_pos} "
        f"after delay compensation (dist={dist:.2f} m)"
    )


# ---------------------------------------------------------------------------
# test_both_features_combined
# ---------------------------------------------------------------------------

def test_both_features_combined() -> None:
    """Both flags True: system must process detections correctly end-to-end.

    This is a smoke test for the combined code path. A single radar sensor
    at a known position observes a moving target. The manager runs with both
    use_sensor_models and use_delay_compensation enabled. We assert that:
      - The track confirms (N-of-M fires normally).
      - The track position tracks the target (within generous tolerance).
      - No exception is raised.
    """
    pos: Vec3 = (800.0, 200.0, 100.0)
    vel: Vec3 = (-15.0, 5.0, 0.0)

    mgr = TrackManager(
        use_sensor_models=True,
        use_delay_compensation=True,
        sensor_registry=REGISTRY,
        delay_threshold_s=0.05,
    )

    t = 0.0
    for i in range(12):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"d{i}", pos, vel, t, sensor="radar-1")], t)

    confirmed = mgr.confirmed_tracks()
    assert len(confirmed) == 1, f"expected 1 confirmed track, got {len(confirmed)}"

    tr = confirmed[0]
    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(tr.position, pos)))
    assert dist < 20.0, (
        f"combined mode: track {tr.position} too far from true {pos} "
        f"(dist={dist:.2f} m)"
    )


# ---------------------------------------------------------------------------
# test_unknown_sensor_falls_back_to_flat_R
# ---------------------------------------------------------------------------

def test_unknown_sensor_falls_back_to_flat_R() -> None:
    """An unknown sensor_id must fall back gracefully to flat R, not crash."""
    mgr = TrackManager(
        use_sensor_models=True,
        sensor_registry=REGISTRY,  # does not contain "unknown-99"
    )

    pos: Vec3 = (400.0, 0.0, 70.0)
    vel: Vec3 = (5.0, 0.0, 0.0)
    t = 0.0

    for i in range(8):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"d{i}", pos, vel, t, sensor="unknown-99")], t)

    # Must confirm without raising — fallback path kept it stable.
    assert len(mgr.confirmed_tracks()) == 1
