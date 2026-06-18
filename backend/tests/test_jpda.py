"""
Unit tests for the JPDA soft association module (fusion/jpda.py).

Each test drives JPDAAssociator with hand-crafted tracks and detections so
expected betas and innovations can be verified analytically or by direct
numeric comparison. No TrackManager is involved — JPDA is tested standalone.
"""
from __future__ import annotations

import numpy as np
import pytest

from csontology import Detection, Track, TrackClass
from fusion.jpda import JPDAAssociator, JPDAUpdate


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_MEAS_SIGMA = 5.0


def _track(
    track_id: str,
    pos: tuple[float, float, float] = (0.0, 0.0, 100.0),
    vel: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Track:
    """Build a minimal Track for association tests."""
    return Track(
        id=track_id,
        position=pos,
        velocity=vel,
        covariance=(5.0, 5.0, 5.0),
        last_update=0.0,
        confidence=0.9,
    )


def _det(
    det_id: str,
    pos: tuple[float, float, float],
    vel: tuple[float, float, float] = (0.0, 0.0, 0.0),
    sensor: str = "radar-1",
    conf: float = 0.9,
) -> Detection:
    """Build a minimal Detection."""
    return Detection(
        id=det_id,
        timestamp=0.0,
        position=pos,
        velocity=vel,
        confidence=conf,
        sensor_id=sensor,
    )


def _make_get_innovation(meas_sigma: float = _MEAS_SIGMA):
    """Return a get_innovation callable using an isotropic R = sigma^2 * I."""
    def get_innovation(
        track: Track, det: Detection
    ) -> tuple[np.ndarray, np.ndarray]:
        residual = np.array(det.position, dtype=np.float64) - np.array(
            track.position, dtype=np.float64
        )
        s_mat = np.eye(3, dtype=np.float64) * (meas_sigma ** 2)
        return residual, s_mat

    return get_innovation


def _jpda(
    gate_chi2: float = 9.21,
    p_detection: float = 0.9,
    clutter_density: float = 1e-6,
    meas_sigma: float = _MEAS_SIGMA,
) -> JPDAAssociator:
    return JPDAAssociator(
        gate_chi2=gate_chi2,
        p_detection=p_detection,
        clutter_density=clutter_density,
        meas_sigma=meas_sigma,
    )


# ---------------------------------------------------------------------------
# test_single_detection_single_track
# ---------------------------------------------------------------------------

def test_single_detection_single_track() -> None:
    """One detection close to one track: beta_1 (hit) near 1, beta_0 (miss) near 0."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    # Detection 2 m away — well inside gate at sigma=5.0
    det = _det("d1", pos=(2.0, 0.0, 100.0))

    associator = _jpda(clutter_density=1e-6)
    results = associator.associate([track], [det], _make_get_innovation())

    update = results["t1"]
    assert update.n_gated == 1
    assert update.miss_probability < 0.05, f"miss_probability too high: {update.miss_probability}"
    assert update.betas[1] > 0.95, f"detection beta too low: {update.betas[1]}"
    assert abs(update.betas.sum() - 1.0) < 1e-9
    # Combined innovation should point from track to detection
    assert update.combined_innovation[0] == pytest.approx(
        update.betas[1] * 2.0, abs=1e-9
    )


# ---------------------------------------------------------------------------
# test_no_detections_gives_miss
# ---------------------------------------------------------------------------

def test_no_detections_gives_miss() -> None:
    """No detections: miss_probability must be exactly 1.0 and n_gated == 0."""
    track = _track("t1")
    associator = _jpda()
    results = associator.associate([track], [], _make_get_innovation())

    update = results["t1"]
    assert update.n_gated == 0
    assert update.miss_probability == pytest.approx(1.0, abs=1e-12)
    assert update.betas.shape == (1,)
    assert update.betas[0] == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(update.combined_innovation, np.zeros(3))


# ---------------------------------------------------------------------------
# test_two_detections_ambiguous
# ---------------------------------------------------------------------------

def test_two_detections_ambiguous() -> None:
    """Two equidistant detections: betas should split roughly equally, both > 0."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    # Both detections are the same distance from the track on opposite sides.
    det_a = _det("dA", pos=(1.0, 0.0, 100.0))
    det_b = _det("dB", pos=(-1.0, 0.0, 100.0))

    associator = _jpda(clutter_density=1e-8)
    results = associator.associate([track], [det_a, det_b], _make_get_innovation())

    update = results["t1"]
    assert update.n_gated == 2
    assert update.betas.shape == (3,)  # [miss, beta_dA, beta_dB]
    # Both detection betas must be positive and roughly equal.
    assert update.betas[1] > 0.0
    assert update.betas[2] > 0.0
    assert abs(update.betas[1] - update.betas[2]) < 0.05, (
        f"Expected roughly equal betas, got {update.betas[1]:.4f} and {update.betas[2]:.4f}"
    )
    # Innovations from the two detections cancel because they are symmetric.
    assert update.combined_innovation[0] == pytest.approx(0.0, abs=1e-6)
    assert abs(update.betas.sum() - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# test_distant_detection_gated_out
# ---------------------------------------------------------------------------

def test_distant_detection_gated_out() -> None:
    """Detection far outside the gate contributes nothing (n_gated == 0)."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    # 1000 m away — Mahalanobis dist >> 9.21 at sigma=5.0
    far_det = _det("dFar", pos=(1000.0, 0.0, 100.0))

    associator = _jpda(gate_chi2=9.21, meas_sigma=_MEAS_SIGMA)
    results = associator.associate([track], [far_det], _make_get_innovation())

    update = results["t1"]
    assert update.n_gated == 0
    assert update.miss_probability == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(update.combined_innovation, np.zeros(3))


def test_detection_at_gate_boundary() -> None:
    """Detection right at the chi2 gate boundary is still included."""
    meas_sigma = _MEAS_SIGMA
    gate_chi2 = 9.21
    # Mahalanobis^2 = (d/sigma)^2 * 3 = gate_chi2 when d = sigma * sqrt(gate_chi2/3)
    # Place the detection just inside the gate along a single axis.
    d = meas_sigma * np.sqrt(gate_chi2 / 3.0) * 0.999
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    det = _det("dBound", pos=(d, d, 100.0 + d))

    associator = _jpda(gate_chi2=gate_chi2, meas_sigma=meas_sigma)
    results = associator.associate([track], [det], _make_get_innovation())

    update = results["t1"]
    assert update.n_gated == 1


# ---------------------------------------------------------------------------
# test_combined_innovation_is_weighted_sum
# ---------------------------------------------------------------------------

def test_combined_innovation_is_weighted_sum() -> None:
    """combined_innovation == sum(beta_j * innovation_j) for each gated detection."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    det_a = _det("dA", pos=(3.0, 0.0, 100.0))
    det_b = _det("dB", pos=(0.0, 2.0, 100.0))

    get_innovation = _make_get_innovation()
    associator = _jpda(clutter_density=1e-7)
    results = associator.associate([track], [det_a, det_b], get_innovation)

    update = results["t1"]
    assert update.n_gated == 2

    # Manually compute expected combined innovation.
    innov_a, _ = get_innovation(track, det_a)
    innov_b, _ = get_innovation(track, det_b)
    expected = update.betas[1] * innov_a + update.betas[2] * innov_b

    assert np.allclose(update.combined_innovation, expected, atol=1e-12)


# ---------------------------------------------------------------------------
# test_many_tracks_independent
# ---------------------------------------------------------------------------

def test_many_tracks_independent() -> None:
    """JPDA returns an independent update per track; result count matches track count."""
    tracks = [
        _track("t1", pos=(0.0, 0.0, 100.0)),
        _track("t2", pos=(500.0, 0.0, 100.0)),
        _track("t3", pos=(1000.0, 0.0, 100.0)),
    ]
    # Each detection is near exactly one track.
    detections = [
        _det("d1", pos=(1.0, 0.0, 100.0)),
        _det("d2", pos=(501.0, 0.0, 100.0)),
        _det("d3", pos=(999.0, 0.0, 100.0)),
    ]

    associator = _jpda()
    results = associator.associate(tracks, detections, _make_get_innovation())

    assert set(results.keys()) == {"t1", "t2", "t3"}

    # Each track should gate exactly the detection near it.
    assert results["t1"].n_gated == 1
    assert results["t2"].n_gated == 1
    assert results["t3"].n_gated == 1

    # Each track's dominant beta should be the detection beta (betas[1]).
    for tid in ("t1", "t2", "t3"):
        update = results[tid]
        assert update.betas[1] > 0.9, (
            f"Track {tid}: expected betas[1] > 0.9, got {update.betas[1]}"
        )

    # Track t1 should not gate detections d2 or d3 (500 m away).
    # Verified implicitly: n_gated == 1 above.


def test_many_tracks_no_cross_gate() -> None:
    """Detections far from a track do not enter its gate even when other tracks gate them."""
    track_near = _track("near", pos=(0.0, 0.0, 100.0))
    track_far = _track("far", pos=(800.0, 0.0, 100.0))
    det = _det("d1", pos=(1.0, 0.0, 100.0))  # near track_near only

    associator = _jpda()
    results = associator.associate(
        [track_near, track_far], [det], _make_get_innovation()
    )

    assert results["near"].n_gated == 1
    assert results["far"].n_gated == 0
    assert results["far"].miss_probability == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# test_high_clutter_increases_miss
# ---------------------------------------------------------------------------

def test_high_clutter_increases_miss() -> None:
    """Higher clutter_density raises miss_probability for the same detection."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    det = _det("d1", pos=(1.0, 0.0, 100.0))
    get_innovation = _make_get_innovation()

    low_clutter = _jpda(clutter_density=1e-9)
    high_clutter = _jpda(clutter_density=1e3)

    res_low = low_clutter.associate([track], [det], get_innovation)
    res_high = high_clutter.associate([track], [det], get_innovation)

    miss_low = res_low["t1"].miss_probability
    miss_high = res_high["t1"].miss_probability

    assert miss_high > miss_low, (
        f"High clutter should increase miss probability: {miss_high} <= {miss_low}"
    )
    assert miss_high > 0.5, (
        f"With clutter_density=1e3, miss should dominate, got {miss_high}"
    )


# ---------------------------------------------------------------------------
# test_beta_sum_always_one
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_dets", [0, 1, 2, 5])
def test_beta_sum_always_one(n_dets: int) -> None:
    """Association probabilities must always sum to 1.0 regardless of n_dets."""
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    detections = [
        _det(f"d{i}", pos=(float(i) * 0.5, 0.0, 100.0))
        for i in range(n_dets)
    ]
    associator = _jpda()
    results = associator.associate([track], detections, _make_get_innovation())

    update = results["t1"]
    assert abs(update.betas.sum() - 1.0) < 1e-9, (
        f"Betas sum {update.betas.sum()} != 1.0 for n_dets={n_dets}"
    )


# ---------------------------------------------------------------------------
# test_single_detection_reduces_to_standard
# ---------------------------------------------------------------------------

def test_single_detection_reduces_to_standard_update() -> None:
    """With one detection inside the gate and zero clutter, JPDA ~ standard KF update.

    combined_innovation == innovation, miss ~ 0, detection beta ~ 1.
    """
    track = _track("t1", pos=(0.0, 0.0, 100.0))
    det = _det("d1", pos=(3.0, 0.0, 100.0))
    get_innovation = _make_get_innovation()

    # Zero clutter: miss numerator = 0, so beta_0 = 0 and beta_1 = 1.
    associator = _jpda(clutter_density=0.0)
    results = associator.associate([track], [det], get_innovation)

    update = results["t1"]
    assert update.n_gated == 1
    assert update.miss_probability == pytest.approx(0.0, abs=1e-9)
    assert update.betas[1] == pytest.approx(1.0, abs=1e-9)

    expected_innov, _ = get_innovation(track, det)
    assert np.allclose(update.combined_innovation, expected_innov, atol=1e-12)


# ---------------------------------------------------------------------------
# test_combined_S_shape_and_symmetry
# ---------------------------------------------------------------------------

def test_combined_s_shape_and_symmetry() -> None:
    """combined_S must be 3x3 and symmetric."""
    track = _track("t1")
    det = _det("d1", pos=(1.0, 1.0, 100.0))
    associator = _jpda()
    results = associator.associate([track], [det], _make_get_innovation())

    s = results["t1"].combined_S
    assert s.shape == (3, 3)
    assert np.allclose(s, s.T, atol=1e-12)
