"""
Fusion engine tests against synthetic detection streams of known ground truth.

We drive the TrackManager with simulated sensors and assert three things the
design spec calls out: track-count accuracy, identity stability under noise, and
coast then expiry behavior on sensor dropout. The Kalman filter has its own unit
checks for prediction, correction, and gating.
"""
from __future__ import annotations

import math
import random
from typing import List, Tuple

import pytest

from csontology import Detection, Vec3
from fusion import ConstantVelocityKalman, FusionConfig, TrackManager


def _det(
    det_id: str, pos: Vec3, vel: Vec3, t: float,
    sensor: str = "radar-1", conf: float = 0.9,
) -> Detection:
    """Build a Detection with the common defaults for tests."""
    return Detection(
        id=det_id, timestamp=t, position=pos, velocity=vel,
        confidence=conf, sensor_id=sensor,
    )


def _advance(pos: Vec3, vel: Vec3, dt: float) -> Vec3:
    """Move a ground-truth position along its velocity by dt."""
    return (pos[0] + vel[0] * dt, pos[1] + vel[1] * dt, pos[2] + vel[2] * dt)


# ---- Kalman unit behavior ----

def test_kalman_predicts_along_velocity() -> None:
    kf = ConstantVelocityKalman(
        position=(0.0, 0.0, 0.0), velocity=(10.0, 0.0, 0.0),
        pos_sigma=5.0, vel_sigma=2.0, process_noise=1.0,
    )
    kf.predict(2.0)
    px, py, pz = kf.position
    assert px == pytest.approx(20.0, abs=1e-9)
    assert py == pytest.approx(0.0, abs=1e-9)
    assert pz == pytest.approx(0.0, abs=1e-9)


def test_kalman_coast_grows_covariance() -> None:
    kf = ConstantVelocityKalman(
        position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
        pos_sigma=5.0, vel_sigma=2.0, process_noise=4.0,
    )
    before = kf.position_sigma[0]
    kf.predict(3.0)
    after = kf.position_sigma[0]
    assert after > before


def test_kalman_update_pulls_toward_measurement() -> None:
    kf = ConstantVelocityKalman(
        position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
        pos_sigma=20.0, vel_sigma=5.0, process_noise=1.0,
    )
    kf.update((10.0, 0.0, 0.0), meas_sigma=1.0)
    assert kf.position[0] > 5.0
    assert kf.position_sigma[0] < 20.0


def test_kalman_rejects_bad_inputs() -> None:
    kf = ConstantVelocityKalman(
        position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
        pos_sigma=5.0, vel_sigma=2.0, process_noise=1.0,
    )
    with pytest.raises(ValueError):
        kf.predict(-1.0)
    with pytest.raises(ValueError):
        kf.update((0.0, 0.0, 0.0), meas_sigma=0.0)
    with pytest.raises(ValueError):
        ConstantVelocityKalman(
            position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
            pos_sigma=-1.0, vel_sigma=2.0, process_noise=1.0,
        )


# ---- Track count accuracy ----

def test_single_target_yields_one_track() -> None:
    mgr = TrackManager()
    pos: Vec3 = (100.0, 0.0, 50.0)
    vel: Vec3 = (-5.0, 2.0, 0.0)
    t = 0.0
    for i in range(10):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"d{i}", pos, vel, t)], t)
    confirmed = mgr.confirmed_tracks()
    assert len(confirmed) == 1
    track = confirmed[0]
    assert track.position[0] == pytest.approx(pos[0], abs=10.0)
    assert track.position[1] == pytest.approx(pos[1], abs=10.0)


def test_separated_targets_yield_distinct_tracks() -> None:
    mgr = TrackManager()
    truths: List[Tuple[Vec3, Vec3]] = [
        ((0.0, 0.0, 50.0), (5.0, 0.0, 0.0)),
        ((500.0, 500.0, 60.0), (-5.0, 0.0, 0.0)),
        ((-400.0, 300.0, 40.0), (0.0, -5.0, 0.0)),
    ]
    t = 0.0
    for step in range(8):
        t += 0.1
        dets = []
        new_truths = []
        for ti, (pos, vel) in enumerate(truths):
            pos = _advance(pos, vel, 0.1)
            new_truths.append((pos, vel))
            dets.append(_det(f"d{step}-{ti}", pos, vel, t))
        truths = new_truths
        mgr.update(dets, t)
    assert len(mgr.confirmed_tracks()) == 3


def test_track_count_holds_under_noise_dense_field() -> None:
    rng = random.Random(7)
    mgr = TrackManager()
    n = 40
    truths: List[Tuple[Vec3, Vec3]] = []
    for _ in range(n):
        px = rng.uniform(-2000, 2000)
        py = rng.uniform(-2000, 2000)
        truths.append(((px, py, 80.0), (rng.uniform(-8, 8), rng.uniform(-8, 8), 0.0)))
    t = 0.0
    for step in range(12):
        t += 0.1
        dets = []
        nxt = []
        for ti, (pos, vel) in enumerate(truths):
            pos = _advance(pos, vel, 0.1)
            nxt.append((pos, vel))
            noisy = (
                pos[0] + rng.gauss(0, 2.0),
                pos[1] + rng.gauss(0, 2.0),
                pos[2] + rng.gauss(0, 1.0),
            )
            dets.append(_det(f"d{step}-{ti}", noisy, vel, t))
        truths = nxt
        mgr.update(dets, t)
    assert len(mgr.confirmed_tracks()) == n


# ---- Identity stability under noise ----

def test_identity_stable_under_noise() -> None:
    rng = random.Random(13)
    mgr = TrackManager()
    pos: Vec3 = (200.0, -100.0, 70.0)
    vel: Vec3 = (-6.0, 4.0, 0.0)
    seen_ids = set()
    t = 0.0
    for i in range(30):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        noisy = (
            pos[0] + rng.gauss(0, 3.0),
            pos[1] + rng.gauss(0, 3.0),
            pos[2] + rng.gauss(0, 1.5),
        )
        mgr.update([_det(f"d{i}", noisy, vel, t)], t)
        for track in mgr.confirmed_tracks():
            seen_ids.add(track.id)
    assert len(seen_ids) == 1


def test_two_crossing_targets_keep_distinct_identity() -> None:
    mgr = TrackManager()
    a_pos: Vec3 = (-300.0, 0.0, 60.0)
    a_vel: Vec3 = (20.0, 0.0, 0.0)
    b_pos: Vec3 = (300.0, 0.0, 60.0)
    b_vel: Vec3 = (-20.0, 0.0, 0.0)
    t = 0.0
    a_id = None
    b_id = None
    for i in range(20):
        a_pos = _advance(a_pos, a_vel, 0.1)
        b_pos = _advance(b_pos, b_vel, 0.1)
        t += 0.1
        mgr.update(
            [_det(f"a{i}", a_pos, a_vel, t), _det(f"b{i}", b_pos, b_vel, t)], t,
        )
        if i == 5:
            ids = sorted(tr.id for tr in mgr.confirmed_tracks())
            assert len(ids) == 2
            a_id, b_id = ids[0], ids[1]
    final_ids = sorted(tr.id for tr in mgr.confirmed_tracks())
    assert final_ids == sorted([a_id, b_id])


# ---- Coast and expiry ----

def test_track_coasts_then_expires_on_dropout() -> None:
    cfg = FusionConfig(coast_timeout_s=1.0)
    mgr = TrackManager(cfg)
    pos: Vec3 = (100.0, 0.0, 50.0)
    vel: Vec3 = (10.0, 0.0, 0.0)
    t = 0.0
    for i in range(5):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"d{i}", pos, vel, t)], t)
    assert len(mgr.confirmed_tracks()) == 1
    track = mgr.confirmed_tracks()[0]
    sigma_before = track.covariance[0]

    t += 0.5
    coasted = mgr.update([], t)
    assert len(coasted) == 1
    assert coasted[0].covariance[0] > sigma_before
    assert coasted[0].position[0] == pytest.approx(pos[0] + vel[0] * 0.5, abs=5.0)

    t += 1.0
    after = mgr.update([], t)
    assert len(after) == 0


def test_predict_advances_without_consuming() -> None:
    mgr = TrackManager()
    pos: Vec3 = (0.0, 0.0, 50.0)
    vel: Vec3 = (10.0, 0.0, 0.0)
    t = 0.0
    for i in range(4):
        pos = _advance(pos, vel, 0.1)
        t += 0.1
        mgr.update([_det(f"d{i}", pos, vel, t)], t)
    t += 0.3
    predicted = mgr.predict(t)
    assert len(predicted) == 1
    assert predicted[0].position[0] == pytest.approx(pos[0] + vel[0] * 0.3, abs=3.0)


# ---- Scale ----

def test_handles_thousand_contacts() -> None:
    rng = random.Random(99)
    mgr = TrackManager()
    n = 1000
    truths: List[Tuple[Vec3, Vec3]] = []
    for _ in range(n):
        px = rng.uniform(-5000, 5000)
        py = rng.uniform(-5000, 5000)
        truths.append(((px, py, 80.0), (rng.uniform(-10, 10), rng.uniform(-10, 10), 0.0)))
    t = 0.0
    for step in range(6):
        t += 0.1
        dets = []
        nxt = []
        for ti, (pos, vel) in enumerate(truths):
            pos = _advance(pos, vel, 0.1)
            nxt.append((pos, vel))
            noisy = (pos[0] + rng.gauss(0, 1.5), pos[1] + rng.gauss(0, 1.5), pos[2])
            dets.append(_det(f"d{step}-{ti}", noisy, vel, t))
        truths = nxt
        mgr.update(dets, t)
    confirmed = len(mgr.confirmed_tracks())
    assert confirmed >= int(0.98 * n)
    assert confirmed <= n


def test_multi_sensor_does_not_multiply_tracks() -> None:
    """Three sensors viewing the same targets must fuse, not triplicate.

    This is the core multi-sensor fusion contract. Each of K well-separated
    targets is seen by three sensors every tick, so the manager receives 3*K
    detections per tick. A correct fusion engine holds about K tracks, not 3*K.
    """
    rng = random.Random(7)
    mgr = TrackManager()
    k = 200
    sensors = ("radar-1", "radar-2", "eo-1")
    truths: List[Tuple[Vec3, Vec3]] = []
    for _ in range(k):
        px = rng.uniform(-5000, 5000)
        py = rng.uniform(-5000, 5000)
        truths.append(((px, py, 80.0), (rng.uniform(-8, 8), rng.uniform(-8, 8), 0.0)))
    t = 0.0
    for step in range(6):
        t += 0.1
        dets = []
        nxt = []
        for ti, (pos, vel) in enumerate(truths):
            pos = _advance(pos, vel, 0.1)
            nxt.append((pos, vel))
            for sensor in sensors:
                noisy = (pos[0] + rng.gauss(0, 2.0), pos[1] + rng.gauss(0, 2.0), pos[2])
                dets.append(_det(f"d{step}-{ti}-{sensor}", noisy, vel, t, sensor=sensor))
        truths = nxt
        mgr.update(dets, t)
    held = len(mgr.tracks())
    confirmed = len(mgr.confirmed_tracks())
    assert held <= int(1.15 * k), f"track inflation: {held} tracks for {k} targets"
    assert confirmed >= int(0.9 * k), f"too few confirmed: {confirmed} of {k}"


def test_empty_update_is_safe() -> None:
    mgr = TrackManager()
    assert mgr.update([], 0.0) == []
    assert mgr.predict(1.0) == []
