"""
motion_handler.py

Motion reference handler hierarchy for DroneNexus ROS2 stack.
Provides a unified send_command() interface with frame conversions
instead of raw PoseStamped publishing. Inspired by Aerostack2.

Zero ROS2 imports. Pure Python for testability.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ControlMode(Enum):
    """Supported control modes for motion commands."""
    HOVER = "HOVER"
    POSITION = "POSITION"
    SPEED = "SPEED"
    TRAJECTORY = "TRAJECTORY"
    ACRO = "ACRO"


@dataclass
class SafetyLimits:
    """Configurable safety limits for motion commands."""
    max_speed_ms: float = 15.0
    max_altitude_m: float = 120.0
    min_altitude_m: float = 0.5
    max_yaw_rate_rads: float = 1.5


@dataclass
class MotionCommand:
    """A single motion command in ENU frame."""
    mode: ControlMode
    position: Optional[tuple[float, float, float]] = None
    velocity: Optional[tuple[float, float, float]] = None
    yaw: Optional[float] = None
    yaw_rate: Optional[float] = None
    acceleration: Optional[tuple[float, float, float]] = None
    timestamp: float = field(default_factory=time.time)


class MotionHandler(ABC):
    """Base class for mode-specific motion handlers."""

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self._limits = limits or SafetyLimits()
        self._current_setpoint: MotionCommand | None = None

    @property
    @abstractmethod
    def mode(self) -> ControlMode:
        """Return the control mode this handler supports."""

    def send_command(self, cmd: MotionCommand) -> bool:
        """Validate and stage a motion command. Returns True on success."""
        valid, reason = self.validate(cmd)
        if not valid:
            logger.warning("Command rejected [%s]: %s", self.mode.value, reason)
            return False
        self._current_setpoint = cmd
        logger.debug("Command staged [%s]", self.mode.value)
        return True

    @abstractmethod
    def validate(self, cmd: MotionCommand) -> tuple[bool, str]:
        """Validate a command for this handler. Returns (ok, reason)."""

    def get_setpoint(self) -> MotionCommand | None:
        """Return the current staged setpoint."""
        return self._current_setpoint


class HoverHandler(MotionHandler):
    """Accepts HOVER mode. Returns zero-velocity hold setpoint."""

    @property
    def mode(self) -> ControlMode:
        return ControlMode.HOVER

    def validate(self, cmd: MotionCommand) -> tuple[bool, str]:
        if cmd.mode is not ControlMode.HOVER:
            return False, f"Expected HOVER, got {cmd.mode.value}"
        return True, ""

    def send_command(self, cmd: MotionCommand) -> bool:
        """Stage a hover command with zeroed velocity."""
        valid, reason = self.validate(cmd)
        if not valid:
            logger.warning("Command rejected [HOVER]: %s", reason)
            return False
        hover_cmd = MotionCommand(
            mode=ControlMode.HOVER,
            position=cmd.position,
            velocity=(0.0, 0.0, 0.0),
            yaw=cmd.yaw,
            yaw_rate=0.0,
            timestamp=cmd.timestamp,
        )
        self._current_setpoint = hover_cmd
        logger.debug("Hover setpoint staged")
        return True


class PositionHandler(MotionHandler):
    """Accepts POSITION mode. Validates bounds and clamps altitude."""

    @property
    def mode(self) -> ControlMode:
        return ControlMode.POSITION

    def validate(self, cmd: MotionCommand) -> tuple[bool, str]:
        if cmd.mode is not ControlMode.POSITION:
            return False, f"Expected POSITION, got {cmd.mode.value}"
        if cmd.position is None:
            return False, "POSITION command requires position"
        return True, ""

    def send_command(self, cmd: MotionCommand) -> bool:
        """Stage a position command with altitude clamping."""
        valid, reason = self.validate(cmd)
        if not valid:
            logger.warning("Command rejected [POSITION]: %s", reason)
            return False
        clamped = _clamp_position(cmd.position, self._limits)
        staged = MotionCommand(
            mode=ControlMode.POSITION,
            position=clamped,
            velocity=cmd.velocity,
            yaw=cmd.yaw,
            yaw_rate=cmd.yaw_rate,
            timestamp=cmd.timestamp,
        )
        self._current_setpoint = staged
        logger.debug("Position setpoint staged: %s", clamped)
        return True


class SpeedHandler(MotionHandler):
    """Accepts SPEED mode. Validates and clamps velocity magnitude."""

    @property
    def mode(self) -> ControlMode:
        return ControlMode.SPEED

    def validate(self, cmd: MotionCommand) -> tuple[bool, str]:
        if cmd.mode is not ControlMode.SPEED:
            return False, f"Expected SPEED, got {cmd.mode.value}"
        if cmd.velocity is None:
            return False, "SPEED command requires velocity"
        return True, ""

    def send_command(self, cmd: MotionCommand) -> bool:
        """Stage a speed command with velocity clamping."""
        valid, reason = self.validate(cmd)
        if not valid:
            logger.warning("Command rejected [SPEED]: %s", reason)
            return False
        clamped_vel = _clamp_velocity(cmd.velocity, self._limits)
        clamped_yr = _clamp_yaw_rate(cmd.yaw_rate, self._limits)
        staged = MotionCommand(
            mode=ControlMode.SPEED,
            position=cmd.position,
            velocity=clamped_vel,
            yaw=cmd.yaw,
            yaw_rate=clamped_yr,
            timestamp=cmd.timestamp,
        )
        self._current_setpoint = staged
        logger.debug("Speed setpoint staged: %s", clamped_vel)
        return True


class TrajectoryHandler(MotionHandler):
    """Accepts TRAJECTORY mode. Validates time monotonicity."""

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        super().__init__(limits)
        self._last_timestamp: float = 0.0

    @property
    def mode(self) -> ControlMode:
        return ControlMode.TRAJECTORY

    def validate(self, cmd: MotionCommand) -> tuple[bool, str]:
        if cmd.mode is not ControlMode.TRAJECTORY:
            return False, f"Expected TRAJECTORY, got {cmd.mode.value}"
        if cmd.position is None:
            return False, "TRAJECTORY command requires position"
        if cmd.timestamp <= self._last_timestamp:
            return (
                False,
                f"Timestamp {cmd.timestamp} not monotonically increasing "
                f"(last={self._last_timestamp})",
            )
        return True, ""

    def send_command(self, cmd: MotionCommand) -> bool:
        """Stage a trajectory setpoint after monotonicity check."""
        valid, reason = self.validate(cmd)
        if not valid:
            logger.warning("Command rejected [TRAJECTORY]: %s", reason)
            return False
        clamped = _clamp_position(cmd.position, self._limits)
        staged = MotionCommand(
            mode=ControlMode.TRAJECTORY,
            position=clamped,
            velocity=cmd.velocity,
            yaw=cmd.yaw,
            yaw_rate=cmd.yaw_rate,
            acceleration=cmd.acceleration,
            timestamp=cmd.timestamp,
        )
        self._current_setpoint = staged
        self._last_timestamp = cmd.timestamp
        logger.debug("Trajectory setpoint staged at t=%.3f", cmd.timestamp)
        return True

    def reset_timeline(self) -> None:
        """Reset the monotonicity tracker for a new trajectory."""
        self._last_timestamp = 0.0


class MotionHandlerManager:
    """Dispatches motion commands to the correct handler by mode."""

    def __init__(self) -> None:
        self._handlers: dict[ControlMode, MotionHandler] = {}
        self._current_mode: ControlMode | None = None

    def register_handler(
        self, mode: ControlMode, handler: MotionHandler
    ) -> None:
        """Register a handler for a given control mode."""
        if handler.mode is not mode:
            raise ValueError(
                f"Handler mode {handler.mode.value} does not match "
                f"registration mode {mode.value}"
            )
        self._handlers[mode] = handler
        logger.info("Registered handler for %s", mode.value)

    @property
    def current_mode(self) -> ControlMode | None:
        """Return the currently active control mode."""
        return self._current_mode

    def switch_mode(self, mode: ControlMode) -> bool:
        """Switch to a different control mode. Returns True on success."""
        if mode not in self._handlers:
            logger.warning(
                "Cannot switch to %s: no handler registered", mode.value
            )
            return False
        self._current_mode = mode
        logger.info("Switched to %s mode", mode.value)
        return True

    def send(self, cmd: MotionCommand) -> bool:
        """Dispatch a command to the handler matching cmd.mode."""
        handler = self._handlers.get(cmd.mode)
        if handler is None:
            logger.warning(
                "No handler registered for %s", cmd.mode.value
            )
            return False
        success = handler.send_command(cmd)
        if success:
            self._current_mode = cmd.mode
        return success

    def get_active_handler(self) -> MotionHandler | None:
        """Return the handler for the current mode, if any."""
        if self._current_mode is None:
            return None
        return self._handlers.get(self._current_mode)


# -- Helper functions for clamping ------------------------------------------

def _clamp_position(
    pos: tuple[float, float, float], limits: SafetyLimits
) -> tuple[float, float, float]:
    """Clamp altitude (z) to safety bounds. X/Y pass through."""
    x, y, z = pos
    clamped_z = max(limits.min_altitude_m, min(z, limits.max_altitude_m))
    if clamped_z != z:
        logger.warning(
            "Altitude clamped: %.2f -> %.2f", z, clamped_z
        )
    return (x, y, clamped_z)


def _clamp_velocity(
    vel: tuple[float, float, float], limits: SafetyLimits
) -> tuple[float, float, float]:
    """Clamp velocity magnitude to max_speed_ms. Direction preserved."""
    vx, vy, vz = vel
    magnitude = (vx * vx + vy * vy + vz * vz) ** 0.5
    if magnitude <= limits.max_speed_ms or magnitude == 0.0:
        return vel
    scale = limits.max_speed_ms / magnitude
    logger.warning(
        "Speed clamped: %.2f -> %.2f m/s", magnitude, limits.max_speed_ms
    )
    return (vx * scale, vy * scale, vz * scale)


def _clamp_yaw_rate(
    yaw_rate: float | None, limits: SafetyLimits
) -> float | None:
    """Clamp yaw rate to max_yaw_rate_rads."""
    if yaw_rate is None:
        return None
    clamped = max(-limits.max_yaw_rate_rads, min(yaw_rate, limits.max_yaw_rate_rads))
    if clamped != yaw_rate:
        logger.warning(
            "Yaw rate clamped: %.2f -> %.2f rad/s", yaw_rate, clamped
        )
    return clamped
