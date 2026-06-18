"""
Covariance Intersection (CI) for track-to-track fusion in BULWARK/OVERWATCH.

CI fuses two or more state estimates when the cross-correlations between local
estimates are unknown.  The key property: the fused covariance P_fused is
always consistent (never overconfident) regardless of the unknown correlations.

References:
  Julier & Uhlmann, "A non-divergent estimation algorithm in the presence of
  unknown correlations", ACC 1997.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize_scalar


class CovarianceIntersection:
    """Fuses two or more state estimates with unknown cross-correlations."""

    @staticmethod
    def fuse_two(
        x1: np.ndarray,
        P1: np.ndarray,
        x2: np.ndarray,
        P2: np.ndarray,
        omega: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Fuse two estimates using Covariance Intersection.

        Args:
            x1: State vector from estimator 1, shape (n,).
            P1: Covariance matrix from estimator 1, shape (n, n).
            x2: State vector from estimator 2, shape (n,).
            P2: Covariance matrix from estimator 2, shape (n, n).
            omega: Mixing weight in [0, 1].  omega=1 trusts x1 fully,
                   omega=0 trusts x2 fully.  If None the optimal weight
                   (minimising trace of P_fused) is found automatically.

        Returns:
            (x_fused, P_fused) — the fused state and covariance.

        Raises:
            ValueError: If omega is outside [0, 1] or matrices are singular.
        """
        x1 = np.asarray(x1, dtype=np.float64)
        x2 = np.asarray(x2, dtype=np.float64)
        P1 = np.asarray(P1, dtype=np.float64)
        P2 = np.asarray(P2, dtype=np.float64)

        if omega is None:
            omega = CovarianceIntersection.optimal_omega(P1, P2)

        if not (0.0 <= omega <= 1.0):
            raise ValueError(f"omega must be in [0, 1], got {omega}")

        P1_inv = _safe_inv(P1)
        P2_inv = _safe_inv(P2)

        P_fused_inv = omega * P1_inv + (1.0 - omega) * P2_inv
        P_fused = _safe_inv(P_fused_inv)
        x_fused = P_fused @ (omega * P1_inv @ x1 + (1.0 - omega) * P2_inv @ x2)

        return x_fused, P_fused

    @staticmethod
    def fuse_multiple(
        estimates: List[Tuple[np.ndarray, np.ndarray]],
        weights: Optional[List[float]] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Fuse N estimates using generalised Covariance Intersection.

        Args:
            estimates: List of (x_i, P_i) pairs.  All x_i must have the same
                       dimension and all P_i must be square of that dimension.
            weights:   Mixing weights w_i summing to 1.  If None, equal
                       weights 1/N are used.

        Returns:
            (x_fused, P_fused) — the fused state and covariance.

        Raises:
            ValueError: If estimates is empty, weights don't sum to 1, or a
                        matrix is singular.
        """
        if not estimates:
            raise ValueError("estimates must be a non-empty list")

        n = len(estimates)

        if weights is None:
            weights = [1.0 / n] * n

        if len(weights) != n:
            raise ValueError(
                f"weights length {len(weights)} must match estimates length {n}"
            )

        weight_sum = sum(weights)
        if not np.isclose(weight_sum, 1.0, atol=1e-9):
            raise ValueError(f"weights must sum to 1, got {weight_sum}")

        P_fused_inv = np.zeros_like(np.asarray(estimates[0][1], dtype=np.float64))
        weighted_sum = np.zeros_like(np.asarray(estimates[0][0], dtype=np.float64))

        for (x_i, P_i), w_i in zip(estimates, weights):
            x_i = np.asarray(x_i, dtype=np.float64)
            P_i = np.asarray(P_i, dtype=np.float64)
            P_i_inv = _safe_inv(P_i)
            P_fused_inv = P_fused_inv + w_i * P_i_inv
            weighted_sum = weighted_sum + w_i * P_i_inv @ x_i

        P_fused = _safe_inv(P_fused_inv)
        x_fused = P_fused @ weighted_sum

        return x_fused, P_fused

    @staticmethod
    def optimal_omega(P1: np.ndarray, P2: np.ndarray) -> float:
        """Find omega in [0, 1] that minimises trace(P_fused).

        Uses scipy.optimize.minimize_scalar with bounded search on [0, 1].

        Args:
            P1: Covariance matrix of estimator 1, shape (n, n).
            P2: Covariance matrix of estimator 2, shape (n, n).

        Returns:
            Optimal omega as a float in [0, 1].
        """
        P1 = np.asarray(P1, dtype=np.float64)
        P2 = np.asarray(P2, dtype=np.float64)

        P1_inv = _safe_inv(P1)
        P2_inv = _safe_inv(P2)

        def _trace_fused(omega: float) -> float:
            P_fused_inv = omega * P1_inv + (1.0 - omega) * P2_inv
            try:
                P_fused = _safe_inv(P_fused_inv)
            except np.linalg.LinAlgError:
                return np.inf
            return float(np.trace(P_fused))

        result = minimize_scalar(
            _trace_fused,
            bounds=(0.0, 1.0),
            method="bounded",
            options={"xatol": 1e-8},
        )
        return float(np.clip(result.x, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_inv(matrix: np.ndarray) -> np.ndarray:
    """Invert a square matrix, raising LinAlgError with a clear message if singular."""
    cond = np.linalg.cond(matrix)
    if cond > 1.0 / np.finfo(np.float64).eps:
        raise np.linalg.LinAlgError(
            f"Matrix is singular or near-singular (condition number {cond:.3e}). "
            "Cannot perform Covariance Intersection."
        )
    return np.linalg.inv(matrix)
