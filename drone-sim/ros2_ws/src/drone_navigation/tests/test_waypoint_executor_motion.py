"""
test_waypoint_executor_motion.py

Pure Python tests for the motion handler integration in the waypoint executor.
No ROS2 runtime required. Tests cover _command_to_pose conversions,
hover/speed command behavior, and invalid mode rejection.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Optional
from unittest import mock

import pytest


# ── Mock ROS2 message types so we can import without a ROS2 install ──────

@dataclass
class _Point:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class _Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass
class _Pose:
    position: _Point = field(default_factory=_Point)
    orientation: _Quaternion = field(default_factory=_Quaternion)


@dataclass
class _Header:
    frame_id: str = ''
    stamp: float = 0.0


@dataclass
class _PoseStamped:
    header: _Header = field(default_factory=_Header)
    pose: _Pose = field(default_factory=_Pose)


@dataclass
class _NavSatFix:
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0


def _build_geometry_msgs_module() -> ModuleType:
    mod = ModuleType('geometry_msgs.msg')
    mod.PoseStamped = _PoseStamped  # type: ignore[attr-defined]
    return mod


def _build_sensor_msgs_module() -> ModuleType:
    mod = ModuleType('sensor_msgs.msg')
    mod.NavSatFix = _NavSatFix  # type: ignore[attr-defined]
    return mod


# Patch heavy ROS2 modules before importing the waypoint_executor module
_stubs: dict[str, ModuleType] = {}
for _name in (
    'rclpy', 'rclpy.node', 'rclpy.qos',
    'std_msgs', 'std_msgs.msg',
    'geometry_msgs',
    'sensor_msgs',
    'mavros_msgs', 'mavros_msgs.msg', 'mavros_msgs.srv',
    'yaml',
):
    _stubs[_name] = ModuleType(_name)

_stubs['geometry_msgs.msg'] = _build_geometry_msgs_module()
_stubs['sensor_msgs.msg'] = _build_sensor_msgs_module()

# std_msgs.msg needs String, Int32, Float64
_std_mod = _stubs['std_msgs.msg']
_std_mod.String = mock.MagicMock  # type: ignore[attr-defined]
_std_mod.Int32 = mock.MagicMock  # type: ignore[attr-defined]
_std_mod.Float64 = mock.MagicMock  # type: ignore[attr-defined]

# mavros_msgs.msg
_mav_mod = _stubs['mavros_msgs.msg']
_mav_mod.Waypoint = mock.MagicMock  # type: ignore[attr-defined]
_mav_mod.WaypointList = mock.MagicMock  # type: ignore[attr-defined]
_mav_mod.WaypointReached = mock.MagicMock  # type: ignore[attr-defined]

# mavros_msgs.srv
_mav_srv = _stubs['mavros_msgs.srv']
_mav_srv.WaypointPush = mock.MagicMock  # type: ignore[attr-defined]
_mav_srv.WaypointClear = mock.MagicMock  # type: ignore[attr-defined]
_mav_srv.SetMode = mock.MagicMock  # type: ignore[attr-defined]

# rclpy.node.Node
_node_mod = _stubs['rclpy.node']
_node_mod.Node = mock.MagicMock  # type: ignore[attr-defined]

# rclpy.qos
_qos_mod = _stubs['rclpy.qos']
_qos_mod.QoSProfile = mock.MagicMock  # type: ignore[attr-defined]
_qos_mod.ReliabilityPolicy = mock.MagicMock  # type: ignore[attr-defined]
_qos_mod.HistoryPolicy = mock.MagicMock  # type: ignore[attr-defined]

with mock.patch.dict(sys.modules, _stubs):
    from drone_navigation.waypoint_executor import (
        _command_to_pose,
        _current_gps_as_tuple,
        _yaw_to_quaternion,
    )

from drone_navigation.motion_handler import (
    ControlMode,
    HoverHandler,
    MotionCommand,
    MotionHandlerManager,
    PositionHandler,
    SafetyLimits,
    SpeedHandler,
)


# ── Tests ────────────────────────────────────────────────────────────────


class TestCommandToPosePosition:
    """_command_to_pose converts position fields correctly."""

    def test_position_xyz_mapped(self) -> None:
        cmd = MotionCommand(
            mode=ControlMode.POSITION,
            position=(47.3, -122.1, 50.0),
        )
        pose = _command_to_pose(cmd)
        assert pose.pose.position.x == pytest.approx(47.3)
        assert pose.pose.position.y == pytest.approx(-122.1)
        assert pose.pose.position.z == pytest.approx(50.0)

    def test_no_position_leaves_zeros(self) -> None:
        cmd = MotionCommand(mode=ControlMode.HOVER)
        pose = _command_to_pose(cmd)
        assert pose.pose.position.x == 0.0
        assert pose.pose.position.y == 0.0
        assert pose.pose.position.z == 0.0

    def test_frame_id_is_map(self) -> None:
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0.0, 0.0, 5.0))
        pose = _command_to_pose(cmd)
        assert pose.header.frame_id == 'map'


class TestCommandToPoseYaw:
    """_command_to_pose converts yaw to a quaternion."""

    def test_zero_yaw(self) -> None:
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0, 0, 10), yaw=0.0)
        pose = _command_to_pose(cmd)
        assert pose.pose.orientation.x == pytest.approx(0.0)
        assert pose.pose.orientation.y == pytest.approx(0.0)
        assert pose.pose.orientation.z == pytest.approx(0.0)
        assert pose.pose.orientation.w == pytest.approx(1.0)

    def test_ninety_degree_yaw(self) -> None:
        yaw = math.pi / 2.0
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0, 0, 10), yaw=yaw)
        pose = _command_to_pose(cmd)
        expected_z = math.sin(yaw / 2.0)
        expected_w = math.cos(yaw / 2.0)
        assert pose.pose.orientation.z == pytest.approx(expected_z)
        assert pose.pose.orientation.w == pytest.approx(expected_w)

    def test_no_yaw_gives_identity_quaternion(self) -> None:
        cmd = MotionCommand(mode=ControlMode.POSITION, position=(0, 0, 10))
        pose = _command_to_pose(cmd)
        assert pose.pose.orientation.w == pytest.approx(1.0)
        assert pose.pose.orientation.z == pytest.approx(0.0)


class TestYawToQuaternion:
    """Direct tests for _yaw_to_quaternion helper."""

    def test_pi_yaw(self) -> None:
        qx, qy, qz, qw = _yaw_to_quaternion(math.pi)
        assert qx == pytest.approx(0.0)
        assert qy == pytest.approx(0.0)
        assert qz == pytest.approx(1.0, abs=1e-7)
        assert qw == pytest.approx(0.0, abs=1e-7)


class TestHoverCommand:
    """Hover commands produce zero-velocity setpoints."""

    def test_hover_zeros_velocity(self) -> None:
        limits = SafetyLimits()
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.HOVER, HoverHandler(limits))

        cmd = MotionCommand(
            mode=ControlMode.HOVER,
            position=(10.0, 20.0, 30.0),
        )
        assert mgr.send(cmd) is True

        handler = mgr.get_active_handler()
        assert handler is not None
        setpoint = handler.get_setpoint()
        assert setpoint is not None
        assert setpoint.velocity == (0.0, 0.0, 0.0)
        assert setpoint.yaw_rate == 0.0

    def test_hover_preserves_position(self) -> None:
        limits = SafetyLimits()
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.HOVER, HoverHandler(limits))

        cmd = MotionCommand(
            mode=ControlMode.HOVER,
            position=(1.0, 2.0, 3.0),
        )
        mgr.send(cmd)
        setpoint = mgr.get_active_handler().get_setpoint()
        assert setpoint.position == (1.0, 2.0, 3.0)

    def test_hover_pose_has_zero_velocity_fields(self) -> None:
        limits = SafetyLimits()
        handler = HoverHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.HOVER,
            position=(5.0, 10.0, 15.0),
        )
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        pose = _command_to_pose(setpoint)
        assert pose.pose.position.x == pytest.approx(5.0)
        assert pose.pose.position.z == pytest.approx(15.0)


class TestSpeedCommandLimits:
    """Speed commands respect safety limits."""

    def test_speed_within_limits_unchanged(self) -> None:
        limits = SafetyLimits(max_speed_ms=10.0)
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.SPEED, SpeedHandler(limits))

        cmd = MotionCommand(
            mode=ControlMode.SPEED,
            velocity=(3.0, 4.0, 0.0),
        )
        assert mgr.send(cmd) is True
        setpoint = mgr.get_active_handler().get_setpoint()
        assert setpoint.velocity == (3.0, 4.0, 0.0)

    def test_speed_exceeding_limit_clamped(self) -> None:
        limits = SafetyLimits(max_speed_ms=5.0)
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.SPEED, SpeedHandler(limits))

        cmd = MotionCommand(
            mode=ControlMode.SPEED,
            velocity=(10.0, 0.0, 0.0),
        )
        assert mgr.send(cmd) is True
        setpoint = mgr.get_active_handler().get_setpoint()
        vx, vy, vz = setpoint.velocity
        magnitude = (vx ** 2 + vy ** 2 + vz ** 2) ** 0.5
        assert magnitude == pytest.approx(5.0)

    def test_speed_direction_preserved_after_clamp(self) -> None:
        limits = SafetyLimits(max_speed_ms=5.0)
        handler = SpeedHandler(limits)
        cmd = MotionCommand(
            mode=ControlMode.SPEED,
            velocity=(6.0, 8.0, 0.0),
        )
        handler.send_command(cmd)
        setpoint = handler.get_setpoint()
        vx, vy, _ = setpoint.velocity
        ratio = vx / vy
        assert ratio == pytest.approx(6.0 / 8.0)


class TestInvalidModeRejected:
    """Commands with unregistered or mismatched modes are rejected."""

    def test_unregistered_mode_returns_false(self) -> None:
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.HOVER, HoverHandler(SafetyLimits()))

        cmd = MotionCommand(mode=ControlMode.ACRO)
        assert mgr.send(cmd) is False

    def test_trajectory_not_registered(self) -> None:
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.POSITION, PositionHandler(SafetyLimits()))

        cmd = MotionCommand(
            mode=ControlMode.TRAJECTORY,
            position=(0, 0, 10),
        )
        assert mgr.send(cmd) is False

    def test_position_without_position_field_rejected(self) -> None:
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.POSITION, PositionHandler(SafetyLimits()))

        cmd = MotionCommand(mode=ControlMode.POSITION)
        assert mgr.send(cmd) is False

    def test_speed_without_velocity_rejected(self) -> None:
        mgr = MotionHandlerManager()
        mgr.register_handler(ControlMode.SPEED, SpeedHandler(SafetyLimits()))

        cmd = MotionCommand(mode=ControlMode.SPEED)
        assert mgr.send(cmd) is False


class TestCurrentGpsAsTuple:
    """_current_gps_as_tuple extracts lat/lon/alt correctly."""

    def test_extraction(self) -> None:
        fix = _NavSatFix(latitude=47.6, longitude=-122.3, altitude=100.0)
        result = _current_gps_as_tuple(fix)
        assert result == (47.6, -122.3, 100.0)
