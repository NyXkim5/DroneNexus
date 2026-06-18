"""
Trajectory prediction for BULWARK threat assessment and effector cueing.

TrajectoryPredictor clones a live Kalman filter (CV or CTRV) and runs
repeated predict() steps without any measurement updates. Each step
collects position, reconstructed velocity, and position sigma. The result
is a PredictedTrajectory that BULWARK uses for:

  - time_to_point()  -- replaces crude time-to-impact in threat scoring
  - position_at()    -- arbitrary-time interpolation for effector cueing
  - to_dict()        -- WebSocket / HUD serialization

The live filter state is never mutated. A deep copy is taken before
any prediction step runs.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from csontology import Track, Vec3
from fusion.kalman import KalmanFilter


@dataclass
class TrajectoryPoint:
    """One predicted state snapshot at a future instant."""

    timestamp: float
    position: Vec3
    velocity: Vec3
    covariance: Tuple[float, float, float]  # position sigma (sigma_x, sigma_y, sigma_z)


@dataclass
class PredictedTrajectory:
    """Ordered sequence of predicted states for one track."""

    track_id: str
    points: List[TrajectoryPoint] = field(default_factory=list)

    def position_at(self, t: float) -> Optional[Vec3]:
        """Linearly interpolate position at arbitrary time t.

        Returns None when t is outside the trajectory window or the
        trajectory has fewer than two points.
        """
        if len(self.points) < 2:
            return None
        if t < self.points[0].timestamp or t > self.points[-1].timestamp:
            return None

        for i in range(len(self.points) - 1):
            a = self.points[i]
            b = self.points[i + 1]
            if a.timestamp <= t <= b.timestamp:
                span = b.timestamp - a.timestamp
                if span < 1e-12:
                    return a.position
                alpha = (t - a.timestamp) / span
                return (
                    a.position[0] + alpha * (b.position[0] - a.position[0]),
                    a.position[1] + alpha * (b.position[1] - a.position[1]),
                    a.position[2] + alpha * (b.position[2] - a.position[2]),
                )
        return None

    def time_to_point(
        self, target: Vec3, threshold_m: float = 50.0
    ) -> Optional[float]:
        """Estimate time until the trajectory passes within threshold_m of target.

        Walks prediction points in order and returns the timestamp of the
        first point whose distance to target is within threshold_m.
        Returns None if no such point exists (track moving away, or
        trajectory never reaches the target).
        """
        for pt in self.points:
            dx = pt.position[0] - target[0]
            dy = pt.position[1] - target[1]
            dz = pt.position[2] - target[2]
            if math.sqrt(dx * dx + dy * dy + dz * dz) <= threshold_m:
                return pt.timestamp
        return None

    def to_dict(self) -> dict:
        """Serialize for WebSocket / HUD consumption."""
        return {
            "track_id": self.track_id,
            "points": [
                {
                    "timestamp": pt.timestamp,
                    "position": {
                        "x": pt.position[0],
                        "y": pt.position[1],
                        "z": pt.position[2],
                    },
                    "velocity": {
                        "x": pt.velocity[0],
                        "y": pt.velocity[1],
                        "z": pt.velocity[2],
                    },
                    "covariance": {
                        "sigma_x": pt.covariance[0],
                        "sigma_y": pt.covariance[1],
                        "sigma_z": pt.covariance[2],
                    },
                }
                for pt in self.points
            ],
        }


class TrajectoryPredictor:
    """Runs open-loop filter predictions to build future trajectory windows.

    Works with both ConstantVelocityKalman (straight lines) and
    CTRVKalmanFilter (curved arcs). The live filter is deep-copied before
    any prediction so the fusion engine state is never touched.

    Args:
        horizon_s: How far ahead to predict in seconds (default 30 s).
        step_s:    Time between consecutive prediction points in seconds
                   (default 0.5 s = 60 points at 30 s horizon).
    """

    def __init__(self, horizon_s: float = 30.0, step_s: float = 0.5) -> None:
        if horizon_s <= 0.0:
            raise ValueError("horizon_s must be positive")
        if step_s <= 0.0:
            raise ValueError("step_s must be positive")
        self._horizon_s = horizon_s
        self._step_s = step_s

    def predict(self, track: Track, kalman: KalmanFilter) -> PredictedTrajectory:
        """Build a predicted trajectory for one track.

        Clones the filter, then calls predict(step_s) repeatedly without
        any update(). Covariance grows at each step, reflecting increasing
        uncertainty further into the future.

        The t=0 point uses the current filter state before any propagation,
        anchoring the trajectory at the fused position right now.

        Args:
            track:  The Track whose id labels the trajectory.
            kalman: The live KalmanFilter associated with this track. Not mutated.

        Returns:
            PredictedTrajectory with ceil(horizon_s / step_s) + 1 points.
        """
        clone = copy.deepcopy(kalman)
        t_now = track.last_update
        trajectory = PredictedTrajectory(track_id=track.id)

        # Anchor at current state (t=0 offset).
        trajectory.points.append(
            TrajectoryPoint(
                timestamp=t_now,
                position=clone.position,
                velocity=clone.velocity,
                covariance=clone.position_sigma,
            )
        )

        elapsed = 0.0
        while elapsed < self._horizon_s - 1e-9:
            clone.predict(self._step_s)
            elapsed += self._step_s
            trajectory.points.append(
                TrajectoryPoint(
                    timestamp=t_now + elapsed,
                    position=clone.position,
                    velocity=clone.velocity,
                    covariance=clone.position_sigma,
                )
            )

        return trajectory

    def predict_batch(
        self,
        tracks: List[Track],
        filters: Dict[str, KalmanFilter],
    ) -> List[PredictedTrajectory]:
        """Predict trajectories for all tracks that have an associated filter.

        Tracks without a matching filter entry are silently skipped.

        Args:
            tracks:  All live tracks from the fusion engine.
            filters: Mapping of track_id -> KalmanFilter.

        Returns:
            List of PredictedTrajectory, one per matched track.
        """
        results: List[PredictedTrajectory] = []
        for track in tracks:
            kalman = filters.get(track.id)
            if kalman is None:
                continue
            results.append(self.predict(track, kalman))
        return results
