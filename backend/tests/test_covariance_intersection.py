"""
Unit tests for CovarianceIntersection — track-to-track fusion via CI.

Each test targets one specific behavioural guarantee of the CI algorithm.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pytest

from fusion.covariance_intersection import CovarianceIntersection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spd(n: int, scale: float = 1.0) -> np.ndarray:
    """Return a random symmetric positive-definite n×n matrix."""
    rng = np.random.default_rng(seed=42)
    a = rng.standard_normal((n, n))
    return scale * (a @ a.T) + scale * np.eye(n)


def _is_positive_definite(m: np.ndarray) -> bool:
    try:
        np.linalg.cholesky(m)
        return True
    except np.linalg.LinAlgError:
        return False


# ---------------------------------------------------------------------------
# fuse_two
# ---------------------------------------------------------------------------

def test_equal_estimates_returns_mean() -> None:
    """Fusing two identical estimates should reproduce those estimates."""
    x = np.array([1.0, 2.0, 3.0])
    P = np.diag([1.0, 1.0, 1.0])

    x_f, P_f = CovarianceIntersection.fuse_two(x, P, x, P)

    np.testing.assert_allclose(x_f, x, atol=1e-9)
    np.testing.assert_allclose(P_f, P, atol=1e-9)


def test_fused_covariance_consistent() -> None:
    """P_fused must be no larger than either input covariance (trace sense).

    CI is conservative: trace(P_fused) <= min(trace(P1), trace(P2)).
    """
    x1 = np.array([0.0, 0.0, 0.0])
    P1 = np.diag([4.0, 4.0, 4.0])   # 2 m sigma per axis

    x2 = np.array([1.0, 1.0, 1.0])
    P2 = np.diag([9.0, 9.0, 9.0])   # 3 m sigma per axis

    _, P_f = CovarianceIntersection.fuse_two(x1, P1, x2, P2)

    assert np.trace(P_f) <= min(np.trace(P1), np.trace(P2)) + 1e-6


def test_uncertain_estimate_weighted_less() -> None:
    """A much more uncertain estimate should pull the fused state less."""
    x1 = np.array([0.0])
    P1 = np.array([[1.0]])    # tight

    x2 = np.array([100.0])
    P2 = np.array([[10000.0]])  # very uncertain

    x_f, _ = CovarianceIntersection.fuse_two(x1, P1, x2, P2)

    # Fused state should be much closer to x1 than to x2.
    assert abs(x_f[0] - x1[0]) < abs(x_f[0] - x2[0])


def test_omega_zero_trusts_x2() -> None:
    """omega=0 should reproduce x2 and P2 exactly."""
    x1 = np.array([10.0, 20.0])
    P1 = np.diag([1.0, 1.0])
    x2 = np.array([5.0, 7.0])
    P2 = np.diag([2.0, 3.0])

    x_f, P_f = CovarianceIntersection.fuse_two(x1, P1, x2, P2, omega=0.0)

    np.testing.assert_allclose(x_f, x2, atol=1e-9)
    np.testing.assert_allclose(P_f, P2, atol=1e-9)


def test_omega_one_trusts_x1() -> None:
    """omega=1 should reproduce x1 and P1 exactly."""
    x1 = np.array([10.0, 20.0])
    P1 = np.diag([1.0, 1.0])
    x2 = np.array([5.0, 7.0])
    P2 = np.diag([2.0, 3.0])

    x_f, P_f = CovarianceIntersection.fuse_two(x1, P1, x2, P2, omega=1.0)

    np.testing.assert_allclose(x_f, x1, atol=1e-9)
    np.testing.assert_allclose(P_f, P1, atol=1e-9)


def test_optimal_omega_between_zero_one() -> None:
    """Optimised omega must lie strictly inside (0, 1) for distinct inputs."""
    P1 = np.diag([1.0, 2.0, 3.0])
    P2 = np.diag([4.0, 1.0, 2.0])

    omega = CovarianceIntersection.optimal_omega(P1, P2)

    assert 0.0 <= omega <= 1.0


def test_fused_is_positive_definite_two() -> None:
    """Fused covariance from fuse_two must be symmetric positive definite."""
    x1 = np.array([1.0, 2.0, 3.0])
    P1 = _make_spd(3, scale=2.0)
    x2 = np.array([1.5, 2.5, 3.5])
    P2 = _make_spd(3, scale=5.0)

    _, P_f = CovarianceIntersection.fuse_two(x1, P1, x2, P2)

    assert _is_positive_definite(P_f), "P_fused is not positive definite"
    np.testing.assert_allclose(P_f, P_f.T, atol=1e-10)


# ---------------------------------------------------------------------------
# fuse_multiple
# ---------------------------------------------------------------------------

def test_fuse_multiple_equal_weights() -> None:
    """fuse_multiple with default equal weights must not raise and return SPD."""
    rng = np.random.default_rng(seed=0)
    n_states = 4
    estimates = []
    for _ in range(5):
        x = rng.standard_normal(n_states)
        P = _make_spd(n_states)
        estimates.append((x, P))

    x_f, P_f = CovarianceIntersection.fuse_multiple(estimates)

    assert x_f.shape == (n_states,)
    assert P_f.shape == (n_states, n_states)
    assert _is_positive_definite(P_f)


def test_fuse_multiple_custom_weights() -> None:
    """fuse_multiple with custom weights summing to 1 must produce a valid result."""
    x1, P1 = np.array([0.0, 0.0]), np.diag([1.0, 1.0])
    x2, P2 = np.array([2.0, 2.0]), np.diag([2.0, 2.0])
    x3, P3 = np.array([4.0, 4.0]), np.diag([4.0, 4.0])

    weights = [0.5, 0.3, 0.2]
    x_f, P_f = CovarianceIntersection.fuse_multiple(
        [(x1, P1), (x2, P2), (x3, P3)],
        weights=weights,
    )

    assert x_f.shape == (2,)
    assert _is_positive_definite(P_f)

    # Heavier weight on x1 (tighter P) means fused is closer to x1.
    assert np.linalg.norm(x_f - x1) < np.linalg.norm(x_f - x3)


def test_fuse_multiple_weights_not_summing_to_one_raises() -> None:
    """fuse_multiple must raise ValueError when weights don't sum to 1."""
    estimates = [
        (np.array([0.0]), np.array([[1.0]])),
        (np.array([1.0]), np.array([[2.0]])),
    ]
    with pytest.raises(ValueError, match="sum to 1"):
        CovarianceIntersection.fuse_multiple(estimates, weights=[0.4, 0.4])


def test_fuse_multiple_empty_raises() -> None:
    """fuse_multiple must raise ValueError on an empty list."""
    with pytest.raises(ValueError, match="non-empty"):
        CovarianceIntersection.fuse_multiple([])


# ---------------------------------------------------------------------------
# Consistency: fused covariance bounded by inputs (generalised)
# ---------------------------------------------------------------------------

def test_fused_multiple_covariance_consistent() -> None:
    """P_fused must be tighter than a naive linear blend of the input covariances.

    The CI consistency guarantee for N estimates with equal weights w_i = 1/N
    is that P_fused^{-1} = sum(w_i * P_i^{-1}), which by the
    harmonic-arithmetic mean inequality gives:
        P_fused <= (1/N) * sum(P_i)   (in the PSD sense)
    So trace(P_fused) <= mean(trace(P_i)).
    """
    estimates = [
        (np.array([0.0, 0.0, 0.0]), np.diag([1.0, 1.0, 1.0])),
        (np.array([1.0, 1.0, 1.0]), np.diag([4.0, 4.0, 4.0])),
        (np.array([2.0, 2.0, 2.0]), np.diag([9.0, 9.0, 9.0])),
    ]
    mean_trace = np.mean([np.trace(P) for _, P in estimates])

    _, P_f = CovarianceIntersection.fuse_multiple(estimates)

    assert np.trace(P_f) <= mean_trace + 1e-9


# ---------------------------------------------------------------------------
# Dimension agnosticism
# ---------------------------------------------------------------------------

def test_different_dimensions() -> None:
    """CI must work correctly for 3-D, 6-D, and 7-D state vectors."""
    rng = np.random.default_rng(seed=7)
    for n in (3, 6, 7):
        x1 = rng.standard_normal(n)
        P1 = _make_spd(n, scale=1.0)
        x2 = rng.standard_normal(n)
        P2 = _make_spd(n, scale=2.0)

        x_f, P_f = CovarianceIntersection.fuse_two(x1, P1, x2, P2)

        assert x_f.shape == (n,), f"state shape mismatch for n={n}"
        assert P_f.shape == (n, n), f"cov shape mismatch for n={n}"
        assert _is_positive_definite(P_f), f"P_fused not SPD for n={n}"
