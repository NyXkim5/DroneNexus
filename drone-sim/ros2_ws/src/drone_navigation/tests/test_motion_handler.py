"""
test_motion_handler.py

Pure Python tests for the motion reference handler hierarchy.
No ROS2 dependencies required.
"""

from __future__ import annotations

import pytest

from drone_navigation.motion_handler import (
    ControlMode,
    HoverHandler,
    MotionCommand,
    MotionHandlerManager,
    PositionHandler,
    SafetyLimits,
    SpeedHandler,
    TrajectoryHandler,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def limits() -> SafetyLimits:
    return SafetyLimits(
        max_speed_ms=15.0,
        max_altitude_m=120.0,
        min_altitude_m=0.5,
        max_yaw_rate_rads=1.5,
    )


@pytest.fixture()
def manager(limits: SafetyLimits) -> MotionHandlerManager:
    mgr = MotionHandlerManager()
    mgr.register_handler(ControlMode.HOVER, HoverHandler(limits))
    mgr.register_handler(ControlMode.POSITION, PositionHandler(limits))
    mgr.register_handler(ControlMode.SPEED, SpeedHandler(limits))
    mgr.register_handler(ControlMode.TRAJECTORY, TrajectoryHandler(limits))
    return mgr


# ---------------------------------------------------------------------------
# HoverHandler
# ---------------------------------------------------------------------------

class TestHoverHandler:
    def test_accepts_hover_mode(self, limits: SafetyLimits) -> None:
        handler = HoverHandler(limits)
        cmd = MotionCommand(mode=ControlMode.HOVER, yaw=0.0)
        assert handler.send_command(cmd) is True

    def test_rejects_wrong_mode(self, limits: SafetyLimits) -> None:
        handler = HoverHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(1.0, 2.0, 3.0))
        assert handler.send_command(cmd) is False

    def test_returns_zero_velocity(self, limits: SafetyLimits) -> None:
        handler = HoverHandler(limits)
        cmd = MotionCommand(mode=ControlMode.HOVER, yaw=0.5)
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.velocity == (0.0, 0.0, 0.0)
        assert setpoint.yaw_rate == 0.0


# ---------------------------------------------------------------------------
# PositionHandler
# ---------------------------------------------------------------------------

class TestPositionHandler:
    def test_accepts_position_mode(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(10.0, 20.0, 30.0))
        assert handler.send_command(cmd) is True

    def test_rejects_wrong_mode(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(1.0, 0.0, 0.0))
        assert handler.send_command(cmd) is False

    def test_rejects_missing_position(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION)
        assert handler.send_command(cmd) is False

    def test_clamps_altitude_above_max(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0.0, 0.0, 200.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.position[2] == limits.max_altitude_m

    def test_clamps_altitude_below_min(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(5.0, 5.0, 0.1))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.position[2] == limits.min_altitude_m

    def test_preserves_valid_altitude(self, limits: SafetyLimits) -> None:
        handler = PositionHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(1.0, 2.0, 50.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.position == (1.0, 2.0, 50.0)


# ---------------------------------------------------------------------------
# SpeedHandler
# ---------------------------------------------------------------------------

class TestSpeedHandler:
    def test_accepts_speed_mode(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(3.0, 4.0, 0.0))
        assert handler.send_command(cmd) is True

    def test_rejects_wrong_mode(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.HOVER)
        assert handler.send_command(cmd) is False

    def test_rejects_missing_velocity(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED)
        assert handler.send_command(cmd) is False

    def test_clamps_excessive_speed(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(20.0, 0.0, 0.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        vx, vy, vz = setpoint.velocity
        magnitude = (vx ** 2 + vy ** 2 + vz ** 2) ** 0.5
        assert magnitude == pytest.approx(limits.max_speed_ms, abs=0.01)

    def test_preserves_speed_within_limit(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(3.0, 4.0, 0.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.velocity == (3.0, 4.0, 0.0)

    def test_clamps_yaw_rate(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.SPEED, velocity=(1.0, 0.0, 0.0), yaw_rate=5.0
        )
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.yaw_rate == limits.max_yaw_rate_rads

    def test_clamps_negative_yaw_rate(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.SPEED, velocity=(1.0, 0.0, 0.0), yaw_rate=-5.0
        )
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.yaw_rate == -limits.max_yaw_rate_rads

    def test_preserves_direction_when_clamping(self, limits: SafetyLimits) -> None:
        handler = SpeedHandler(limits)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(30.0, 0.0, 0.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        vx, vy, vz = setpoint.velocity
        assert vx == pytest.approx(15.0, abs=0.01)
        assert vy == pytest.approx(0.0, abs=0.01)
        assert vz == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# TrajectoryHandler
# ---------------------------------------------------------------------------

class TestTrajectoryHandler:
    def test_accepts_trajectory_mode(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(1.0, 2.0, 10.0), timestamp=1.0
        )
        assert handler.send_command(cmd) is True

    def test_rejects_wrong_mode(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(1.0, 2.0, 3.0))
        assert handler.send_command(cmd) is False

    def test_rejects_missing_position(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd = MotionCommand(mode=ControlMode.TRAJECTORY, timestamp=1.0)
        assert handler.send_command(cmd) is False

    def test_enforces_time_monotonicity(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd1 = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(0.0, 0.0, 10.0), timestamp=5.0
        )
        cmd2 = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(1.0, 1.0, 10.0), timestamp=3.0
        )
        assert handler.send_command(cmd1) is True
        assert handler.send_command(cmd2) is False

    def test_rejects_duplicate_timestamp(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd1 = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(0.0, 0.0, 10.0), timestamp=5.0
        )
        cmd2 = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(1.0, 1.0, 10.0), timestamp=5.0
        )
        assert handler.send_command(cmd1) is True
        assert handler.send_command(cmd2) is False

    def test_accepts_increasing_timestamps(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        for t in [1.0, 2.0, 3.0, 4.0]:
            cmd = MotionCommand(
                mode=ControlMode.TRAJECTORY, position=(t, t, 10.0), timestamp=t
            )
            assert handler.send_command(cmd) is True

    def test_reset_timeline(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(0.0, 0.0, 10.0), timestamp=100.0
        )
        handler.send_command(cmd)
        handler.reset_timeline()
        cmd2 = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(1.0, 1.0, 10.0), timestamp=1.0
        )
        assert handler.send_command(cmd2) is True

    def test_clamps_trajectory_altitude(self, limits: SafetyLimits) -> None:
        handler = TrajectoryHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.TRAJECTORY, position=(0.0, 0.0, 999.0), timestamp=1.0
        )
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.position[2] == limits.max_altitude_m


# ---------------------------------------------------------------------------
# MotionHandlerManager
# ---------------------------------------------------------------------------

class TestMotionHandlerManager:
    def test_dispatches_to_correct_handler(
        self, manager: MotionHandlerManager
    ) -> None:
        cmd = MotionCommand(mode=ControlMode.HOVER)
        assert manager.send(cmd) is True
        assert manager.current_mode is ControlMode.HOVER

    def test_dispatches_position(self, manager: MotionHandlerManager) -> None:
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(1.0, 2.0, 10.0))
        assert manager.send(cmd) is True
        assert manager.current_mode is ControlMode.POSITION

    def test_dispatches_speed(self, manager: MotionHandlerManager) -> None:
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(1.0, 0.0, 0.0))
        assert manager.send(cmd) is True
        assert manager.current_mode is ControlMode.SPEED

    def test_rejects_unregistered_mode(self, manager: MotionHandlerManager) -> None:
        cmd = MotionCommand(mode=ControlMode.ACRO)
        assert manager.send(cmd) is False

    def test_mode_switching(self, manager: MotionHandlerManager) -> None:
        assert manager.switch_mode(ControlMode.HOVER) is True
        assert manager.current_mode is ControlMode.HOVER
        assert manager.switch_mode(ControlMode.POSITION) is True
        assert manager.current_mode is ControlMode.POSITION

    def test_switch_to_unregistered_mode_fails(
        self, manager: MotionHandlerManager
    ) -> None:
        assert manager.switch_mode(ControlMode.ACRO) is False

    def test_failed_command_does_not_change_mode(
        self, manager: MotionHandlerManager
    ) -> None:
        manager.switch_mode(ControlMode.HOVER)
        bad_cmd = MotionCommand(mode=ControlMode.POSITION)  # missing position
        assert manager.send(bad_cmd) is False
        assert manager.current_mode is ControlMode.HOVER

    def test_register_mismatched_mode_raises(self, limits: SafetyLimits) -> None:
        mgr = MotionHandlerManager()
        with pytest.raises(ValueError):
            mgr.register_handler(ControlMode.SPEED, HoverHandler(limits))

    def test_get_active_handler(self, manager: MotionHandlerManager) -> None:
        assert manager.get_active_handler() is None
        manager.switch_mode(ControlMode.HOVER)
        handler = manager.get_active_handler()
        assert handler is not None
        assert handler.mode is ControlMode.HOVER


# ---------------------------------------------------------------------------
# Safety limits integration
# ---------------------------------------------------------------------------

class TestSafetyLimits:
    def test_custom_limits_applied(self) -> None:
        tight = SafetyLimits(max_speed_ms=5.0, max_altitude_m=50.0)
        handler = SpeedHandler(tight)
        cmd = MotionCommand(mode=ControlMode.SPEED, velocity=(10.0, 0.0, 0.0))
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        vx, _, _ = setpoint.velocity
        assert vx == pytest.approx(5.0, abs=0.01)

    def test_custom_altitude_limits(self) -> None:
        tight = SafetyLimits(min_altitude_m=5.0, max_altitude_m=20.0)
        handler = PositionHandler(tight)
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0.0, 0.0, 1.0))
        handler.send_command(cmd)
        assert handler.get_setpoint().position[2] == 5.0

    def test_default_limits(self) -> None:
        defaults = SafetyLimits()
        assert defaults.max_speed_ms == 15.0
        assert defaults.max_altitude_m == 120.0
        assert defaults.min_altitude_m == 0.5
        assert defaults.max_yaw_rate_rads == 1.5
