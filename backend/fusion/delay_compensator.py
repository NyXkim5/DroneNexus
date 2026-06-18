"""
Sensor delay compensation for the multi-sensor fusion pipeline.

When sensors have different latencies (e.g. radar at 50 ms, EO/IR at 200 ms),
measurements arrive out of order. Processing them at arrival time instead of
measurement time creates position errors proportional to target_speed * delay.

This module implements backward-forward delay compensation: after each predict
step the filter's state is snapshotted. When a late measurement arrives, we
find the snapshot closest to the detection timestamp, restore the filter to
that state, apply the measurement update there, then re-predict forward to the
present. The result is a corrected present-time estimate that accounts for the
sensor's physical latency.

Reference approach: FusionTracking (TUM) backward-forward delay correction.
"""
from __future__ import annotations

import bisect
import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple, Union

import numpy as np

from fusion.kalman import ConstantVelocityKalman, CTRVKalmanFilter

logger = logging.getLogger("overwatch.fusion.delay_compensator")

KalmanFilter = Union[ConstantVelocityKalman, CTRVKalmanFilter]


@dataclass
class StateSnapshot:
    """Filter state captured after a predict step.

    Stores a full copy of the state vector and covariance so the filter can
    be rewound to any recorded moment without modifying the live estimate.
    """

    timestamp: float
    state: np.ndarray      # copy of filter state at this time
    covariance: np.ndarray  # copy of P at this time


class DelayCompensator:
    """Backward-forward delay compensator for a single Kalman filter track.

    Call record() after every predict step to snapshot the filter state.
    When a delayed measurement arrives, call compensate() to produce a
    corrected (state, covariance) pair at the current time.

    The compensator is filter-model-agnostic. It saves and restores raw
    numpy arrays, so it works identically with ConstantVelocityKalman (6-state)
    and CTRVKalmanFilter (7-state).

    Args:
        max_history: Maximum number of snapshots to retain. Older snapshots
            are evicted automatically as new ones are added.
    """

    def __init__(self, max_history: int = 200) -> None:
        if max_history < 1:
            raise ValueError("max_history must be at least 1")
        self._history: Deque[StateSnapshot] = deque(maxlen=max_history)
        # Parallel sorted list of timestamps for O(log n) binary search.
        self._times: list[float] = []

    def record(self, timestamp: float, state: np.ndarray, covariance: np.ndarray) -> None:
        """Save a snapshot after each predict step.

        Snapshots must be recorded in non-decreasing timestamp order (the
        natural order of a predict loop). Copies are taken so subsequent
        filter mutations do not corrupt history.

        Args:
            timestamp:  Wall-clock time of this snapshot in seconds.
            state:      Current filter state vector (any shape).
            covariance: Current filter covariance matrix (any shape).
        """
        snap = StateSnapshot(
            timestamp=float(timestamp),
            state=state.copy(),
            covariance=covariance.copy(),
        )
        if len(self._history) == self._history.maxlen:
            # The deque is about to evict its oldest entry — evict the
            # corresponding timestamp from the parallel list too.
            self._times.pop(0)
        self._history.append(snap)
        self._times.append(snap.timestamp)

    def compensate(
        self,
        detection_time: float,
        current_time: float,
        measurement: np.ndarray,
        R: np.ndarray,
        filter: KalmanFilter,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Apply a delayed measurement and re-predict to current_time.

        Steps:
            1. Find the closest historical snapshot to detection_time.
            2. Restore the filter to that state.
            3. Apply the measurement update at detection_time.
            4. Re-predict from detection_time to current_time.
            5. Return (updated_state, updated_covariance).

        If detection_time predates the oldest snapshot the measurement is
        processed at current_time instead (with a warning) so no data is
        silently discarded.

        The filter's live state is restored to its original value after this
        call. compensate() is non-destructive with respect to the filter's
        present-time estimate.

        Args:
            detection_time: Timestamp at which the sensor produced the
                measurement, in seconds (may be in the past).
            current_time:   Present wall-clock time in seconds.
            measurement:    Position observation as a numpy array [x, y, z].
            R:              3x3 measurement noise covariance matrix.
            filter:         The live Kalman filter for this track.

        Returns:
            Tuple of (state_vector, covariance_matrix) at current_time after
            incorporating the delayed measurement.
        """
        # Save the live state so we can restore it unconditionally.
        live_state = filter._state.copy()
        live_cov = filter._cov.copy()

        snap = self._find_snapshot(detection_time)

        if snap is None:
            logger.warning(
                "detection_time %.3f predates all history (oldest %.3f); "
                "processing at current_time instead",
                detection_time,
                self._times[0] if self._times else float("nan"),
            )
            # Fall back: update at current time without rewinding.
            filter._state = live_state
            filter._cov = live_cov
            self._apply_update(filter, measurement, R)
            result_state = filter._state.copy()
            result_cov = filter._cov.copy()
            filter._state = live_state
            filter._cov = live_cov
            return result_state, result_cov

        # Rewind filter to the historical snapshot.
        filter._state = snap.state.copy()
        filter._cov = snap.covariance.copy()

        # Update at detection_time using the delayed measurement.
        self._apply_update(filter, measurement, R)

        # Re-predict from the snapshot time to current_time.
        forward_dt = max(0.0, current_time - snap.timestamp)
        if forward_dt > 0.0:
            filter.predict(forward_dt)

        result_state = filter._state.copy()
        result_cov = filter._cov.copy()

        # Restore the filter to its live (present-time) state.
        filter._state = live_state
        filter._cov = live_cov

        return result_state, result_cov

    def max_compensable_delay(self) -> float:
        """Return the time span covered by the snapshot history.

        A measurement that arrived delay seconds late can be compensated as
        long as delay <= max_compensable_delay().

        Returns:
            Difference between the newest and oldest snapshot timestamps, or
            0.0 if fewer than two snapshots exist.
        """
        if len(self._times) < 2:
            return 0.0
        return self._times[-1] - self._times[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_snapshot(self, target_time: float) -> StateSnapshot | None:
        """Binary-search for the snapshot closest in time to target_time.

        Returns None if no snapshots exist or if target_time is strictly
        older than the oldest snapshot (caller should fall back).
        """
        if not self._times:
            return None

        oldest = self._times[0]
        if target_time < oldest:
            return None

        # bisect_right gives the insertion point for target_time.
        # The closest snapshot is either at that index or the one before.
        idx = bisect.bisect_right(self._times, target_time)

        # Clamp to valid range and pick the nearest neighbour.
        if idx >= len(self._times):
            idx = len(self._times) - 1
        elif idx > 0:
            before = self._times[idx - 1]
            after = self._times[idx]
            if (target_time - before) < (after - target_time):
                idx = idx - 1

        snaps = list(self._history)
        offset = len(self._history) - len(self._times)
        return snaps[idx + offset] if (idx + offset) < len(snaps) else snaps[-1]

    @staticmethod
    def _apply_update(
        filter: KalmanFilter,
        measurement: np.ndarray,
        R: np.ndarray,
    ) -> None:
        """Dispatch a measurement update to whichever filter model is active.

        ConstantVelocityKalman.update() accepts (Vec3, meas_sigma: float).
        CTRVKalmanFilter.update() accepts (Vec3, R: np.ndarray).

        We derive meas_sigma for the CV filter from the diagonal of R so
        the caller always passes a consistent 3x3 R matrix regardless of
        filter model.
        """
        pos = (float(measurement[0]), float(measurement[1]), float(measurement[2]))
        if isinstance(filter, ConstantVelocityKalman):
            # Extract an isotropic sigma from the mean diagonal variance.
            mean_var = float(np.mean(np.diag(R)))
            meas_sigma = float(np.sqrt(max(mean_var, 1e-9)))
            filter.update(pos, meas_sigma)
        else:
            filter.update(pos, R)
