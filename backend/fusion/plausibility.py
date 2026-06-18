"""
Kinematic plausibility gate for OVERWATCH/BULWARK fusion pipeline.

After a Kalman update, the caller passes the state before and after the
update to check_update(). If the transition is physically impossible, the
result is marked implausible and the caller should discard the update and
keep the predicted state instead.

check_track() validates a state in isolation — useful for auditing a coasted
track that has drifted beyond physical bounds.

Two state vector formats are supported:

  CV   — 6D: [x, y, z, vx, vy, vz]           (ConstantVelocityKalman)
  CTRV — 7D: [x, y, z, speed, yaw, yaw_rate, climb_rate]  (CTRVKalmanFilter)

The format is detected from the array length. All units are SI: metres,
metres per second, degrees per second where noted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Index constants — CV state
# ---------------------------------------------------------------------------
_CV_POS = slice(0, 3)
_CV_VEL = slice(3, 6)

# ---------------------------------------------------------------------------
# Index constants — CTRV state
# ---------------------------------------------------------------------------
_CTRV_POS = slice(0, 3)
_CTRV_SPEED = 3
_CTRV_YAW_RATE = 5
_CTRV_CLIMB = 6

_CV_DIM = 6
_CTRV_DIM = 7

# Safety margin applied to the speed-based position-jump limit.
# Allows for up to 2x the expected motion before triggering.
_JUMP_SPEED_FACTOR = 2.0


@dataclass
class PlausibilityLimits:
    """Physical bounds used by the plausibility gate.

    All limits are conservative upper envelopes across known drone classes.
    Adjust for a specific mission envelope by passing a custom instance.
    """

    max_speed_ms: float = 100.0
    max_acceleration_ms2: float = 20.0
    max_climb_rate_ms: float = 30.0
    max_position_jump_m: float = 200.0
    max_yaw_rate_dps: float = 180.0


@dataclass
class PlausibilityResult:
    """Outcome of one plausibility check.

    plausible is False when at least one limit is violated.
    reason names the first violated constraint.
    violation_magnitude is the amount by which the value exceeds the limit,
    in the same units as the limit (m, m/s, m/s², deg/s).
    """

    plausible: bool
    reason: str = ""
    violation_magnitude: float = 0.0


class PlausibilityGate:
    """Validates track state after a Kalman update.

    The gate is stateless with respect to tracks: it takes raw numpy state
    vectors and returns a PlausibilityResult. It does NOT modify any filter
    or track object. The caller decides whether to accept or reject.

    Usage:
        gate = PlausibilityGate()
        result = gate.check_update(state_before, state_after, dt)
        if not result.plausible:
            # reject update, keep predicted state
            ...
    """

    def __init__(self, limits: PlausibilityLimits | None = None) -> None:
        self._limits = limits or PlausibilityLimits()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_update(
        self,
        state_before: np.ndarray,
        state_after: np.ndarray,
        dt: float,
    ) -> PlausibilityResult:
        """Check whether a state transition from before to after is plausible.

        Checks performed in order (first failure wins):
          1. Speed of the post-update state.
          2. Acceleration implied by the velocity change over dt.
          3. Position jump — absolute and speed-normalised.
          4. Climb rate of the post-update state.

        dt must be positive. When dt <= 0 the acceleration check is skipped.
        """
        lim = self._limits

        pos_before = _extract_position(state_before)
        pos_after = _extract_position(state_after)
        vel_before = _extract_velocity(state_before)
        vel_after = _extract_velocity(state_after)

        # 1. Speed check on post-update state.
        speed_after = float(np.linalg.norm(vel_after))
        if speed_after > lim.max_speed_ms:
            excess = speed_after - lim.max_speed_ms
            return PlausibilityResult(
                plausible=False,
                reason=f"speed {speed_after:.1f} m/s exceeds limit {lim.max_speed_ms} m/s",
                violation_magnitude=excess,
            )

        # 2. Acceleration check — only meaningful for positive dt.
        if dt > 0.0:
            delta_v = np.linalg.norm(vel_after - vel_before)
            accel = delta_v / dt
            if accel > lim.max_acceleration_ms2:
                excess = accel - lim.max_acceleration_ms2
                return PlausibilityResult(
                    plausible=False,
                    reason=(
                        f"acceleration {accel:.1f} m/s² exceeds limit "
                        f"{lim.max_acceleration_ms2} m/s²"
                    ),
                    violation_magnitude=excess,
                )

        # 3. Position jump check.
        jump = float(np.linalg.norm(pos_after - pos_before))
        speed_limit = float(np.linalg.norm(vel_before)) * max(dt, 0.0) * _JUMP_SPEED_FACTOR
        dynamic_limit = max(lim.max_position_jump_m, speed_limit)
        if jump > dynamic_limit:
            excess = jump - dynamic_limit
            return PlausibilityResult(
                plausible=False,
                reason=(
                    f"position jump {jump:.1f} m exceeds limit "
                    f"{dynamic_limit:.1f} m"
                ),
                violation_magnitude=excess,
            )

        # 4. Climb rate check.
        climb = _extract_climb_rate(state_after)
        if abs(climb) > lim.max_climb_rate_ms:
            excess = abs(climb) - lim.max_climb_rate_ms
            return PlausibilityResult(
                plausible=False,
                reason=(
                    f"climb rate {climb:.1f} m/s exceeds limit "
                    f"±{lim.max_climb_rate_ms} m/s"
                ),
                violation_magnitude=excess,
            )

        return PlausibilityResult(plausible=True)

    def check_track(self, track_state: np.ndarray) -> PlausibilityResult:
        """Check if a track's current state is physically plausible.

        Validates speed, climb rate, and — for CTRV vectors — yaw rate.
        Use this to audit coasted tracks that may have drifted beyond bounds.
        """
        lim = self._limits

        vel = _extract_velocity(track_state)
        speed = float(np.linalg.norm(vel))
        if speed > lim.max_speed_ms:
            excess = speed - lim.max_speed_ms
            return PlausibilityResult(
                plausible=False,
                reason=f"speed {speed:.1f} m/s exceeds limit {lim.max_speed_ms} m/s",
                violation_magnitude=excess,
            )

        climb = _extract_climb_rate(track_state)
        if abs(climb) > lim.max_climb_rate_ms:
            excess = abs(climb) - lim.max_climb_rate_ms
            return PlausibilityResult(
                plausible=False,
                reason=(
                    f"climb rate {climb:.1f} m/s exceeds limit "
                    f"±{lim.max_climb_rate_ms} m/s"
                ),
                violation_magnitude=excess,
            )

        if len(track_state) == _CTRV_DIM:
            yaw_rate_rads = float(track_state[_CTRV_YAW_RATE])
            yaw_rate_dps = math.degrees(abs(yaw_rate_rads))
            if yaw_rate_dps > lim.max_yaw_rate_dps:
                excess = yaw_rate_dps - lim.max_yaw_rate_dps
                return PlausibilityResult(
                    plausible=False,
                    reason=(
                        f"yaw rate {yaw_rate_dps:.1f} deg/s exceeds limit "
                        f"{lim.max_yaw_rate_dps} deg/s"
                    ),
                    violation_magnitude=excess,
                )

        return PlausibilityResult(plausible=True)


# ---------------------------------------------------------------------------
# Private helpers — state vector accessors
# ---------------------------------------------------------------------------

def _extract_position(state: np.ndarray) -> np.ndarray:
    """Return the [x, y, z] position slice for CV or CTRV state."""
    return state[:3].copy()


def _extract_velocity(state: np.ndarray) -> np.ndarray:
    """Return a 3D velocity vector for either state format.

    CV:   directly reads [vx, vy, vz] at indices 3-5.
    CTRV: reconstructs [vx, vy, vz] from speed, yaw, and climb_rate.
    """
    n = len(state)
    if n == _CV_DIM:
        return state[3:6].copy()
    if n == _CTRV_DIM:
        speed = float(state[_CTRV_SPEED])
        yaw = float(state[4])
        climb = float(state[_CTRV_CLIMB])
        return np.array([speed * math.cos(yaw), speed * math.sin(yaw), climb])
    raise ValueError(
        f"unsupported state vector length {n}; expected {_CV_DIM} (CV) or {_CTRV_DIM} (CTRV)"
    )


def _extract_climb_rate(state: np.ndarray) -> float:
    """Return vertical speed in m/s for either state format.

    CV:   vz at index 5.
    CTRV: climb_rate at index 6.
    """
    n = len(state)
    if n == _CV_DIM:
        return float(state[5])
    if n == _CTRV_DIM:
        return float(state[_CTRV_CLIMB])
    raise ValueError(
        f"unsupported state vector length {n}; expected {_CV_DIM} (CV) or {_CTRV_DIM} (CTRV)"
    )
