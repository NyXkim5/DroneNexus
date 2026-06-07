"""
Constant-velocity Kalman filter for one fused track.

State is a 6-vector in ENU meters and m/s: [x, y, z, vx, vy, vz]. The motion
model is constant velocity. Process noise injects acceleration uncertainty so a
coasted track grows its covariance over time and a maneuvering target stays
trackable.

The three position axes are independent under this model, but we keep a full
6x6 covariance so position and velocity uncertainty stay coupled per axis. That
coupling is what lets a measurement of position also tighten the velocity
estimate. numpy does the linear algebra. One filter instance lives on one Track.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from csontology import Vec3

# Index layout of the 6-element state vector.
_POS = slice(0, 3)
_VEL = slice(3, 6)


class ConstantVelocityKalman:
    """A 6-state constant-velocity Kalman filter over ENU position and velocity.

    process_noise is the spectral density of unmodeled acceleration in
    (m/s^2)^2. Larger values let the filter react faster to maneuvers and make
    coasted tracks lose confidence sooner. The filter never raises on normal
    operation. It validates inputs and raises ValueError on a malformed state.
    """

    def __init__(
        self,
        position: Vec3,
        velocity: Vec3,
        pos_sigma: float,
        vel_sigma: float,
        process_noise: float,
    ) -> None:
        if pos_sigma <= 0.0 or vel_sigma <= 0.0:
            raise ValueError("initial sigmas must be positive")
        if process_noise < 0.0:
            raise ValueError("process_noise must be non-negative")
        self._q = float(process_noise)
        self._state = np.array(
            [*position, *velocity], dtype=np.float64,
        )
        diag = np.array(
            [pos_sigma**2] * 3 + [vel_sigma**2] * 3, dtype=np.float64,
        )
        self._cov = np.diag(diag)

    @property
    def position(self) -> Vec3:
        """Current position estimate as an ENU Vec3."""
        p = self._state[_POS]
        return (float(p[0]), float(p[1]), float(p[2]))

    @property
    def velocity(self) -> Vec3:
        """Current velocity estimate as an ENU Vec3."""
        v = self._state[_VEL]
        return (float(v[0]), float(v[1]), float(v[2]))

    @property
    def position_sigma(self) -> Vec3:
        """Position standard deviation per axis, in meters."""
        d = np.clip(np.diag(self._cov)[_POS], 0.0, None)
        s = np.sqrt(d)
        return (float(s[0]), float(s[1]), float(s[2]))

    def predict(self, dt: float) -> None:
        """Advance the state and covariance forward by dt seconds.

        dt must be non-negative. A zero dt is a no-op. The constant-velocity
        transition moves position by velocity times dt and inflates covariance
        by the discrete white-noise acceleration model.
        """
        if dt < 0.0:
            raise ValueError("dt must be non-negative")
        if dt == 0.0:
            return
        f = self._transition(dt)
        self._state = f @ self._state
        self._cov = f @ self._cov @ f.T + self._process_cov(dt)

    def update(self, position: Vec3, meas_sigma: float) -> None:
        """Fuse a position measurement with the given per-axis stddev.

        meas_sigma is the measurement standard deviation in meters, applied to
        all three axes. This is the standard linear Kalman correction step. We
        only measure position, so the observation matrix selects the first three
        state elements.
        """
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")
        h = self._observation()
        r = np.eye(3) * (meas_sigma**2)
        innovation = np.array(position, dtype=np.float64) - h @ self._state
        s = h @ self._cov @ h.T + r
        gain = self._cov @ h.T @ np.linalg.inv(s)
        self._state = self._state + gain @ innovation
        ident = np.eye(6)
        self._cov = (ident - gain @ h) @ self._cov

    def mahalanobis_sq(self, position: Vec3, meas_sigma: float) -> float:
        """Squared Mahalanobis distance from this filter to a position.

        This is the gating statistic for association. It accounts for both the
        track covariance and the measurement noise, so an uncertain track gates
        a wider region. Returns a non-negative float.
        """
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")
        h = self._observation()
        r = np.eye(3) * (meas_sigma**2)
        innovation = np.array(position, dtype=np.float64) - h @ self._state
        s = h @ self._cov @ h.T + r
        solved = np.linalg.solve(s, innovation)
        return float(innovation @ solved)

    def gate_inverse(self, meas_sigma: float) -> np.ndarray:
        """Inverse innovation covariance for gating at a fixed measurement sigma.

        The innovation covariance S = H P H^T + R depends only on this track and
        the nominal sensor sigma, not on the candidate measurement. A caller
        computes this once per track per tick and reuses it across every candidate
        measurement, turning a per-pair linear solve into a cheap matrix product.
        The squared Mahalanobis distance to a position is then
        innovation @ gate_inverse @ innovation.
        """
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")
        h = self._observation()
        s = h @ self._cov @ h.T + np.eye(3) * (meas_sigma**2)
        return np.linalg.inv(s)

    @staticmethod
    def _transition(dt: float) -> np.ndarray:
        """Constant-velocity state transition matrix for a time step dt."""
        f = np.eye(6)
        f[0, 3] = dt
        f[1, 4] = dt
        f[2, 5] = dt
        return f

    @staticmethod
    def _observation() -> np.ndarray:
        """Observation matrix mapping the 6-state to a position measurement."""
        h = np.zeros((3, 6))
        h[0, 0] = 1.0
        h[1, 1] = 1.0
        h[2, 2] = 1.0
        return h

    def _process_cov(self, dt: float) -> np.ndarray:
        """Discrete white-noise acceleration process covariance for dt.

        Each axis uses the standard piecewise-white-noise model. The position
        variance grows with dt^4, the cross term with dt^3, and the velocity
        variance with dt^2, all scaled by the acceleration spectral density.
        """
        q = self._q
        p_var = (dt**4) / 4.0 * q
        cross = (dt**3) / 2.0 * q
        v_var = (dt**2) * q
        cov = np.zeros((6, 6))
        for axis in range(3):
            cov[axis, axis] = p_var
            cov[axis, axis + 3] = cross
            cov[axis + 3, axis] = cross
            cov[axis + 3, axis + 3] = v_var
        return cov

    def state(self) -> Tuple[Vec3, Vec3, Vec3]:
        """Return (position, velocity, position_sigma) in one call."""
        return self.position, self.velocity, self.position_sigma
