"""
Kalman filters for one fused track.

Two models are available:

  ConstantVelocityKalman — 6D linear CV filter, state [x, y, z, vx, vy, vz].
    Accurate for straight-line motion; degrades on banked turns.

  CTRVKalmanFilter — 7D nonlinear EKF using the Constant Turn-Rate and
    Velocity (CTRV) model, state [x, y, z, speed, yaw, yaw_rate, climb_rate].
    Handles maneuvering drones correctly by propagating arcs not straight lines.
    Process noise Q is rotated into the drone's heading frame each step so
    lateral and longitudinal uncertainty are physically correct.

Use create_filter() to instantiate either model by name.

numpy does the linear algebra. One filter instance lives on one Track.
"""
from __future__ import annotations

from math import cos, sin
from typing import Literal, Tuple, Union

import numpy as np

from csontology import Vec3

# Index layout of the 6-element CV state vector.
_POS = slice(0, 3)
_VEL = slice(3, 6)

# Index constants for the 7-element CTRV state vector.
_IX = 0   # x position (m, ENU)
_IY = 1   # y position (m, ENU)
_IZ = 2   # z position (m, ENU)
_IS = 3   # speed (m/s, horizontal ground speed)
_IYA = 4  # yaw (rad, ENU heading, 0 = east)
_IYR = 5  # yaw_rate (rad/s)
_ICR = 6  # climb_rate (m/s)


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


# ---------------------------------------------------------------------------
# CTRV Extended Kalman Filter
# ---------------------------------------------------------------------------

class CTRVKalmanFilter:
    """7-state CTRV Extended Kalman Filter for maneuvering drone tracking.

    State vector (all ENU):
        [x_pos, y_pos, z_pos, speed, yaw, yaw_rate, climb_rate]

    The nonlinear prediction propagates circular arcs when yaw_rate is
    non-zero, and falls back to straight-line motion when it is near zero.
    The Jacobian F is computed analytically each step. Process noise Q is
    rotated into the drone's heading frame so lateral/longitudinal position
    noise is physically correct (tight across-track, loose along-track).

    Measurements observe [x, y, z] only. H is 3x7.

    pos_std, speed_std, yaw_std, yaw_rate_std, climb_rate_std are the
    1-sigma initial state uncertainties. process_std_pos, process_std_speed,
    process_std_yaw_rate, process_std_climb are the continuous-time noise
    spectral densities for the respective state components.
    """

    def __init__(
        self,
        position: Vec3,
        speed: float,
        yaw: float,
        yaw_rate: float,
        climb_rate: float,
        pos_std: float,
        speed_std: float,
        yaw_std: float,
        yaw_rate_std: float,
        climb_rate_std: float,
        process_std_pos: float = 0.5,
        process_std_speed: float = 1.0,
        process_std_yaw_rate: float = 0.5,
        process_std_climb: float = 0.5,
    ) -> None:
        if pos_std <= 0.0 or speed_std <= 0.0 or yaw_std <= 0.0:
            raise ValueError("initial stddevs must be positive")
        if yaw_rate_std <= 0.0 or climb_rate_std <= 0.0:
            raise ValueError("initial stddevs must be positive")

        self._q_pos = float(process_std_pos)
        self._q_spd = float(process_std_speed)
        self._q_yr = float(process_std_yaw_rate)
        self._q_cr = float(process_std_climb)

        self._state = np.array(
            [position[0], position[1], position[2],
             float(speed), float(yaw), float(yaw_rate), float(climb_rate)],
            dtype=np.float64,
        )
        self._cov = np.diag(np.array([
            pos_std**2, pos_std**2, pos_std**2,
            speed_std**2, yaw_std**2, yaw_rate_std**2, climb_rate_std**2,
        ], dtype=np.float64))

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def position(self) -> Vec3:
        """Current position estimate as an ENU Vec3."""
        s = self._state
        return (float(s[_IX]), float(s[_IY]), float(s[_IZ]))

    @property
    def velocity(self) -> Vec3:
        """Velocity reconstructed from speed, yaw, and climb_rate."""
        s = self._state
        spd = float(s[_IS])
        yaw = float(s[_IYA])
        cr = float(s[_ICR])
        return (spd * cos(yaw), spd * sin(yaw), cr)

    @property
    def position_sigma(self) -> Vec3:
        """Position standard deviation per axis, in meters."""
        d = np.clip(np.diag(self._cov)[:3], 0.0, None)
        s = np.sqrt(d)
        return (float(s[0]), float(s[1]), float(s[2]))

    @property
    def raw_state(self) -> np.ndarray:
        """The full 7-element state vector (copy)."""
        return self._state.copy()

    @property
    def covariance(self) -> np.ndarray:
        """The full 7x7 covariance matrix (copy)."""
        return self._cov.copy()

    # ------------------------------------------------------------------
    # EKF predict step
    # ------------------------------------------------------------------

    def predict(self, dt: float) -> None:
        """Advance the state and covariance forward by dt seconds.

        Uses CTRV motion equations. When |yaw_rate| < 1e-6 the model
        degenerates gracefully to constant-velocity straight-line motion.
        The Jacobian F is computed analytically. Process noise Q is
        assembled in the drone's heading frame and then rotated to ENU.
        """
        if dt < 0.0:
            raise ValueError("dt must be non-negative")
        if dt == 0.0:
            return

        s = self._state
        x, y, z = s[_IX], s[_IY], s[_IZ]
        spd = s[_IS]
        yaw = s[_IYA]
        yr = s[_IYR]
        cr = s[_ICR]

        # Nonlinear state propagation.
        if abs(yr) > 1e-6:
            yaw_dt = yaw + yr * dt
            x_new = x + (spd / yr) * (sin(yaw_dt) - sin(yaw))
            y_new = y + (spd / yr) * (cos(yaw) - cos(yaw_dt))
        else:
            x_new = x + spd * cos(yaw) * dt
            y_new = y + spd * sin(yaw) * dt
        z_new = z + cr * dt
        yaw_new = yaw + yr * dt

        self._state = np.array(
            [x_new, y_new, z_new, spd, yaw_new, yr, cr], dtype=np.float64,
        )

        f = self._jacobian(spd, yaw, yr, dt)
        q = self._process_cov(yaw, dt)
        self._cov = f @ self._cov @ f.T + q

    # ------------------------------------------------------------------
    # EKF update step
    # ------------------------------------------------------------------

    def update(self, measurement: Vec3, R: np.ndarray) -> None:
        """Fuse a position-only measurement [x, y, z] with noise covariance R.

        R must be a 3x3 positive-definite matrix. For an isotropic sensor
        with sigma metres, pass np.eye(3) * sigma**2.
        """
        if R.shape != (3, 3):
            raise ValueError("R must be 3x3")
        h = self._observation()
        z_pred = h @ self._state
        innov = np.array(measurement, dtype=np.float64) - z_pred
        s_mat = h @ self._cov @ h.T + R
        gain = self._cov @ h.T @ np.linalg.inv(s_mat)
        self._state = self._state + gain @ innov
        ident = np.eye(7)
        self._cov = (ident - gain @ h) @ self._cov

    def mahalanobis_sq(self, position: Vec3, meas_sigma: float) -> float:
        """Squared Mahalanobis distance from this filter to a position."""
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")
        h = self._observation()
        r = np.eye(3) * (meas_sigma**2)
        innov = np.array(position, dtype=np.float64) - h @ self._state
        s_mat = h @ self._cov @ h.T + r
        solved = np.linalg.solve(s_mat, innov)
        return float(innov @ solved)

    def gate_inverse(self, meas_sigma: float) -> np.ndarray:
        """Inverse innovation covariance for gating at a fixed measurement sigma."""
        if meas_sigma <= 0.0:
            raise ValueError("meas_sigma must be positive")
        h = self._observation()
        s_mat = h @ self._cov @ h.T + np.eye(3) * (meas_sigma**2)
        return np.linalg.inv(s_mat)

    def state(self) -> Tuple[Vec3, Vec3, Vec3]:
        """Return (position, velocity, position_sigma) in one call."""
        return self.position, self.velocity, self.position_sigma

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _observation() -> np.ndarray:
        """3x7 observation matrix — observes [x, y, z] only."""
        h = np.zeros((3, 7))
        h[0, 0] = 1.0
        h[1, 1] = 1.0
        h[2, 2] = 1.0
        return h

    @staticmethod
    def _jacobian(spd: float, yaw: float, yr: float, dt: float) -> np.ndarray:
        """Analytical 7x7 Jacobian of the CTRV motion equations.

        Rows/cols ordered as [x, y, z, speed, yaw, yaw_rate, climb_rate].
        All partial derivatives are with respect to the pre-prediction state.
        """
        f = np.eye(7)
        yaw_dt = yaw + yr * dt

        if abs(yr) > 1e-6:
            # df_x / d_speed
            f[_IX, _IS] = (sin(yaw_dt) - sin(yaw)) / yr
            # df_x / d_yaw
            f[_IX, _IYA] = (spd / yr) * (cos(yaw_dt) - cos(yaw))
            # df_x / d_yaw_rate
            f[_IX, _IYR] = (spd / (yr**2)) * (
                cos(yaw_dt) * yr * dt - sin(yaw_dt) + sin(yaw)
            )

            # df_y / d_speed
            f[_IY, _IS] = (cos(yaw) - cos(yaw_dt)) / yr
            # df_y / d_yaw
            f[_IY, _IYA] = (spd / yr) * (sin(yaw_dt) - sin(yaw))
            # df_y / d_yaw_rate
            f[_IY, _IYR] = (spd / (yr**2)) * (
                sin(yaw_dt) * yr * dt - (-cos(yaw_dt) + cos(yaw))
            )
        else:
            # Degenerate straight-line Jacobian (yr -> 0 limit of the nonlinear form).
            # Limits: (sin(yaw+yr*dt)-sin(yaw))/yr -> cos(yaw)*dt
            #         (cos(yaw)-cos(yaw+yr*dt))/yr -> sin(yaw)*dt
            # d/d_yr of x_new at yr=0:
            #   x_new = x + spd*cos(yaw)*dt  (no yr dependence in limit branch)
            #   but the limit of df_x/d_yr from the nonlinear branch is:
            #   lim_{yr->0} spd/yr^2*(cos(yaw+yr*dt)*yr*dt - sin(yaw+yr*dt)+sin(yaw))
            #   = spd * (-dt^2/2 * sin(yaw))  (Taylor expand to second order)
            f[_IX, _IS] = cos(yaw) * dt
            f[_IX, _IYA] = -spd * sin(yaw) * dt
            f[_IX, _IYR] = -spd * sin(yaw) * (dt**2) / 2.0

            f[_IY, _IS] = sin(yaw) * dt
            f[_IY, _IYA] = spd * cos(yaw) * dt
            # lim_{yr->0} of df_y/d_yr:
            #   y_new = y + spd/yr*(cos(yaw)-cos(yaw+yr*dt))
            #   d/d_yr at yr=0 -> spd * sin(yaw) * dt^2 / 2
            f[_IY, _IYR] = spd * cos(yaw) * (dt**2) / 2.0

        # df_z / d_climb_rate
        f[_IZ, _ICR] = dt
        # df_yaw / d_yaw_rate
        f[_IYA, _IYR] = dt

        return f

    def _process_cov(self, yaw: float, dt: float) -> np.ndarray:
        """Build the 7x7 process noise matrix Q for this step.

        Position noise is expressed in the heading frame (along-track /
        across-track / vertical) and then rotated to ENU. This keeps the
        uncertainty ellipse aligned with the drone's direction of flight.
        The remaining state components (speed, yaw, yaw_rate, climb_rate)
        are independent and scaled by dt.
        """
        q = np.zeros((7, 7))

        # Position noise in heading frame: longitudinal larger than lateral.
        q_lon = (self._q_pos * dt) ** 2  # along-track
        q_lat = (self._q_pos * 0.3 * dt) ** 2  # across-track (tighter)

        c, s_yaw = cos(yaw), sin(yaw)
        rot = np.array([[c, -s_yaw], [s_yaw, c]])
        q_xy_body = np.diag([q_lon, q_lat])
        q_xy_enu = rot @ q_xy_body @ rot.T
        q[:2, :2] = q_xy_enu

        # Vertical position noise tied to climb_rate uncertainty.
        q[_IZ, _IZ] = (self._q_cr * dt) ** 2

        # Speed uncertainty grows with dt.
        q[_IS, _IS] = (self._q_spd * dt) ** 2

        # Yaw uncertainty from integrated yaw_rate noise.
        q[_IYA, _IYA] = (self._q_yr * dt) ** 2

        # Yaw_rate random walk.
        q[_IYR, _IYR] = (self._q_yr * dt) ** 2

        # Climb rate random walk.
        q[_ICR, _ICR] = (self._q_cr * dt) ** 2

        return q


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

KalmanFilter = Union[ConstantVelocityKalman, CTRVKalmanFilter]


def create_filter(
    model: Literal["cv", "ctrv"],
    position: Vec3,
    velocity: Vec3,
    pos_sigma: float,
    vel_sigma: float,
    process_noise: float,
    *,
    yaw: float = 0.0,
    yaw_rate: float = 0.0,
    yaw_rate_std: float = 0.3,
) -> KalmanFilter:
    """Instantiate a Kalman filter by model name.

    For "cv": returns a ConstantVelocityKalman using the given position and
    velocity directly.

    For "ctrv": derives speed and yaw from the velocity vector, sets
    climb_rate from the Z component of velocity, and returns a
    CTRVKalmanFilter. The yaw and yaw_rate parameters allow the caller to
    supply better initial heading estimates when available (e.g. from IMU).

    Args:
        model:        "cv" or "ctrv".
        position:     Initial ENU position (m).
        velocity:     Initial ENU velocity (m/s). Used as-is for CV.
                      For CTRV, horizontal speed and heading are derived.
        pos_sigma:    Initial position uncertainty 1-sigma (m).
        vel_sigma:    Initial velocity uncertainty 1-sigma (m/s).
        process_noise: For CV, spectral density of unmodeled acceleration
                      (m/s^2)^2. For CTRV, used as process_std_pos and
                      process_std_speed (sqrt of spectral density).
        yaw:          CTRV only — initial heading (rad, ENU).  When 0.0
                      the heading is inferred from the velocity vector.
        yaw_rate:     CTRV only — initial turn rate (rad/s).
        yaw_rate_std: CTRV only — initial yaw_rate uncertainty (rad/s).
    """
    if model == "cv":
        return ConstantVelocityKalman(
            position=position,
            velocity=velocity,
            pos_sigma=pos_sigma,
            vel_sigma=vel_sigma,
            process_noise=process_noise,
        )

    if model == "ctrv":
        import math as _math
        vx, vy, vz = velocity
        speed = _math.hypot(vx, vy)
        heading = yaw if (yaw != 0.0 or speed < 1e-6) else _math.atan2(vy, vx)
        return CTRVKalmanFilter(
            position=position,
            speed=speed,
            yaw=heading,
            yaw_rate=yaw_rate,
            climb_rate=float(vz),
            pos_std=pos_sigma,
            speed_std=vel_sigma,
            yaw_std=0.5,
            yaw_rate_std=yaw_rate_std,
            climb_rate_std=vel_sigma,
            process_std_pos=_math.sqrt(max(process_noise, 1e-9)),
            process_std_speed=_math.sqrt(max(process_noise, 1e-9)),
            process_std_yaw_rate=0.5,
            process_std_climb=0.5,
        )

    raise ValueError(f"unknown filter model: {model!r}. Choose 'cv' or 'ctrv'.")
