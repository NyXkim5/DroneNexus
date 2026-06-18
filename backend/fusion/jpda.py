"""
Joint Probabilistic Data Association (JPDA) for ambiguous swarm environments.

Hard 1-to-1 assignment (GNN, Hungarian) fails when multiple drones fly in
formation: one detection is plausibly associated with several nearby tracks and
picking the wrong one corrupts both. JPDA instead computes an association
probability for every (track, detection) pair inside the gate, then updates
each track with a weighted combination of all gated measurement innovations.

This module is standalone. It does not modify TrackManager. The caller:
  1. Predicts all tracks forward (TrackManager.predict or its own loop).
  2. Calls JPDAAssociator.associate() to get a JPDAUpdate per track.
  3. Uses combined_innovation / combined_S to drive its own Kalman correction,
     or skips the update when miss_probability == 1.0.

Math reference: Bar-Shalom, Fortmann & Cable, "Tracking and Data Association",
Academic Press 1988, Chapter 6 (JPDA filter).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import numpy as np

from csontology import Detection, Track

logger = logging.getLogger("overwatch.fusion.jpda")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class JPDAUpdate:
    """Soft-association result for one track.

    combined_innovation is the beta-weighted sum of all gated innovations.
    combined_S is the innovation covariance (from the first gated detection —
    all gated detections share the same S because the track covariance and
    measurement sigma are measurement-independent). betas[0] is the miss
    probability; betas[1:] correspond to each gated detection in gate order.

    When n_gated == 0, miss_probability == 1.0 and combined_innovation is a
    zero vector. The caller should skip the Kalman correction in that case.
    """

    track_id: str
    combined_innovation: np.ndarray  # shape (3,) — weighted sum of innovations
    combined_S: np.ndarray           # shape (3, 3) — innovation covariance
    betas: np.ndarray                # shape (n_gated + 1,); betas[0] = miss
    miss_probability: float          # betas[0], duplicated for convenience
    n_gated: int                     # number of measurements inside the gate


# ---------------------------------------------------------------------------
# Associator
# ---------------------------------------------------------------------------

class JPDAAssociator:
    """Joint Probabilistic Data Association for ambiguous environments.

    Each call to associate() gates detections per track, computes likelihoods
    under a Poisson clutter model, and returns a probability-weighted combined
    innovation for every track. No hard 1-to-1 assignment is made.

    Designed as a drop-in replacement for the GNN/Hungarian step in
    TrackManager. The caller supplies a get_innovation callable so this class
    does not depend on the filter implementation.
    """

    def __init__(
        self,
        gate_chi2: float = 9.21,
        p_detection: float = 0.9,
        clutter_density: float = 1e-6,
        meas_sigma: float = 5.0,
    ) -> None:
        """
        gate_chi2:       Chi-square gate for 3 DOF at 99% (9.21).
        p_detection:     Probability of detection for each target.
        clutter_density: Spatial density of false alarms (false alarms per m^3).
        meas_sigma:      Isotropic measurement standard deviation (m) used to
                         build the innovation covariance S when the caller's
                         get_innovation does not supply one.
        """
        if gate_chi2 <= 0.0:
            raise ValueError("gate_chi2 must be positive")
        if not (0.0 < p_detection <= 1.0):
            raise ValueError("p_detection must be in (0, 1]")
        if clutter_density < 0.0:
            raise ValueError("clutter_density must be non-negative")
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")

        self._gate_chi2 = gate_chi2
        self._p_detection = p_detection
        self._clutter_density = clutter_density
        self._meas_sigma = meas_sigma

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def associate(
        self,
        tracks: List[Track],
        detections: List[Detection],
        get_innovation: Callable[[Track, Detection], Tuple[np.ndarray, np.ndarray]],
    ) -> Dict[str, JPDAUpdate]:
        """Compute soft association probabilities and combined innovations.

        For each track:
          1. Gate all detections by Mahalanobis distance < gate_chi2.
          2. Compute a Gaussian likelihood for each gated detection.
          3. Compute association probabilities (beta_0 = miss, beta_j = detection j).
          4. Return a JPDAUpdate with the beta-weighted combined innovation.

        Args:
            tracks:         Tracks with predicted state (post-predict step).
            detections:     Raw or fused detections for this tick.
            get_innovation: Callable (track, detection) -> (residual, S).
                            residual: shape (3,) measurement minus predicted position.
                            S: shape (3, 3) innovation covariance.

        Returns:
            Dict mapping track_id -> JPDAUpdate. Every track in `tracks` has
            an entry, even when no detections were gated (miss_probability=1.0).
        """
        results: Dict[str, JPDAUpdate] = {}

        for track in tracks:
            update = self._process_track(track, detections, get_innovation)
            results[track.id] = update

        return results

    # ------------------------------------------------------------------
    # Per-track processing
    # ------------------------------------------------------------------

    def _process_track(
        self,
        track: Track,
        detections: List[Detection],
        get_innovation: Callable[[Track, Detection], Tuple[np.ndarray, np.ndarray]],
    ) -> JPDAUpdate:
        """Gate, compute likelihoods, and build the weighted update for one track."""
        gated_innovations: List[np.ndarray] = []
        gated_S: List[np.ndarray] = []
        likelihoods: List[float] = []

        for det in detections:
            residual, s_mat = get_innovation(track, det)
            maha_sq = self._mahalanobis_sq(residual, s_mat)
            if maha_sq > self._gate_chi2:
                continue

            likelihood = self._gaussian_likelihood(residual, s_mat)
            gated_innovations.append(residual)
            gated_S.append(s_mat)
            likelihoods.append(likelihood)

        n_gated = len(gated_innovations)

        if n_gated == 0:
            return JPDAUpdate(
                track_id=track.id,
                combined_innovation=np.zeros(3, dtype=np.float64),
                combined_S=np.eye(3, dtype=np.float64) * (self._meas_sigma ** 2),
                betas=np.array([1.0], dtype=np.float64),
                miss_probability=1.0,
                n_gated=0,
            )

        likelihoods_arr = np.array(likelihoods, dtype=np.float64)
        betas = self._compute_betas(likelihoods_arr, self._p_detection, self._clutter_density)

        # Weighted sum of innovations: skip betas[0] (miss term).
        combined_innovation = np.zeros(3, dtype=np.float64)
        for j, innov in enumerate(gated_innovations):
            combined_innovation += betas[j + 1] * innov

        # Use the S from the first gated detection. S depends only on the
        # track covariance and measurement sigma, not on the detection value,
        # so all gated detections share the same S when meas_sigma is uniform.
        combined_s = gated_S[0]

        return JPDAUpdate(
            track_id=track.id,
            combined_innovation=combined_innovation,
            combined_S=combined_s,
            betas=betas,
            miss_probability=float(betas[0]),
            n_gated=n_gated,
        )

    # ------------------------------------------------------------------
    # Probability computation
    # ------------------------------------------------------------------

    def _compute_betas(
        self,
        likelihoods: np.ndarray,
        p_detection: float,
        clutter_density: float,
    ) -> np.ndarray:
        """Compute association probabilities under a Poisson clutter model.

        beta_0 (miss) = (1 - p_d) * clutter_density / normalizer
        beta_j        = p_d * likelihood_j / normalizer
        normalizer    = sum of all numerators

        When clutter_density is 0.0 the miss term is zero, meaning every
        gated detection is certain to come from a target (ideal, low-clutter
        scenario). When clutter_density is high, the miss term dominates and
        the filter trusts measurements less.

        Returns an array of shape (n_gated + 1,): [beta_0, beta_1, ..., beta_n].
        """
        miss_numerator = (1.0 - p_detection) * clutter_density
        det_numerators = p_detection * likelihoods  # shape (n_gated,)

        total = miss_numerator + det_numerators.sum()

        if total <= 0.0:
            # Degenerate: all likelihoods vanished (numeric underflow at extreme
            # range). Fall back to uniform split across detections, no miss.
            n = len(likelihoods)
            betas = np.full(n + 1, 1.0 / n, dtype=np.float64)
            betas[0] = 0.0
            return betas

        betas = np.empty(len(likelihoods) + 1, dtype=np.float64)
        betas[0] = miss_numerator / total
        betas[1:] = det_numerators / total
        return betas

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mahalanobis_sq(residual: np.ndarray, s_mat: np.ndarray) -> float:
        """Squared Mahalanobis distance: residual^T S^{-1} residual."""
        try:
            solved = np.linalg.solve(s_mat, residual)
        except np.linalg.LinAlgError:
            return float("inf")
        return float(residual @ solved)

    @staticmethod
    def _gaussian_likelihood(residual: np.ndarray, s_mat: np.ndarray) -> float:
        """Gaussian measurement likelihood N(residual; 0, S).

        Returns the probability density value. Clipped to a small positive
        floor to prevent numeric underflow from zeroing the betas.
        """
        n = residual.shape[0]
        try:
            sign, log_det = np.linalg.slogdet(s_mat)
        except np.linalg.LinAlgError:
            return 1e-300

        if sign <= 0:
            return 1e-300

        try:
            solved = np.linalg.solve(s_mat, residual)
        except np.linalg.LinAlgError:
            return 1e-300

        exponent = -0.5 * float(residual @ solved)
        log_norm = -0.5 * (n * np.log(2.0 * np.pi) + log_det)
        log_likelihood = log_norm + exponent

        # Clamp to avoid underflow becoming exactly 0.
        return float(max(np.exp(log_likelihood), 1e-300))
