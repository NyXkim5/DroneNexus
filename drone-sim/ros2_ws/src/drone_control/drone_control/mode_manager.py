"""
mode_manager.py

ROS 2 node that manages flight mode transitions. Validates mode changes,
enforces transition rules, and publishes the current mode.
"""

from enum import Enum
from typing import Dict, List, Set

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String
from mavros_msgs.msg import State as MavrosState
from mavros_msgs.srv import SetMode

from drone_interfaces.srv import SetFlightMode


class FlightMode(Enum):
    """Supported flight modes with canonical names."""
    MANUAL = 'MANUAL'
    STABILIZE = 'STABILIZE'
    ALT_HOLD = 'ALT_HOLD'
    LOITER = 'LOITER'
    AUTO = 'AUTO'
    GUIDED = 'GUIDED'
    RTL = 'RTL'
    LAND = 'LAND'
    OFFBOARD = 'OFFBOARD'
    ACRO = 'ACRO'


class ModeManagerNode(Node):
    """Manages flight mode transitions with validation and safety checks."""

    # Mode transition rules: from_mode -> set of allowed to_modes
    TRANSITION_RULES: Dict[str, Set[str]] = {
        'MANUAL': {'STABILIZE', 'ALT_HOLD', 'LOITER', 'GUIDED', 'RTL', 'LAND'},
        'STABILIZE': {'MANUAL', 'ALT_HOLD', 'LOITER', 'GUIDED', 'AUTO', 'RTL', 'LAND'},
        'ALT_HOLD': {'MANUAL', 'STABILIZE', 'LOITER', 'GUIDED', 'AUTO', 'RTL', 'LAND'},
        'LOITER': {'MANUAL', 'STABILIZE', 'ALT_HOLD', 'GUIDED', 'AUTO', 'RTL', 'LAND'},
        'AUTO': {'MANUAL', 'STABILIZE', 'LOITER', 'GUIDED', 'RTL', 'LAND'},
        'GUIDED': {'MANUAL', 'STABILIZE', 'ALT_HOLD', 'LOITER', 'AUTO', 'RTL', 'LAND', 'OFFBOARD'},
        'RTL': {'MANUAL', 'STABILIZE', 'LOITER', 'GUIDED', 'LAND'},
        'LAND': {'MANUAL', 'STABILIZE', 'LOITER', 'GUIDED'},
        'OFFBOARD': {'MANUAL', 'STABILIZE', 'LOITER', 'GUIDED', 'RTL', 'LAND'},
        'ACRO': {'MANUAL', 'STABILIZE', 'RTL', 'LAND'},
    }

    # Mapping from canonical mode names to PX4 custom modes
    PX4_MODE_MAP: Dict[str, str] = {
        'MANUAL': 'MANUAL',
        'STABILIZE': 'STABILIZED',
        'ALT_HOLD': 'ALTCTL',
        'LOITER': 'POSCTL',
        'AUTO': 'AUTO.MISSION',
        'GUIDED': 'OFFBOARD',
        'RTL': 'AUTO.RTL',
        'LAND': 'AUTO.LAND',
        'OFFBOARD': 'OFFBOARD',
        'ACRO': 'ACRO',
    }

    # Mapping from canonical mode names to ArduPilot custom modes
    ARDUPILOT_MODE_MAP: Dict[str, str] = {
        'MANUAL': 'MANUAL',
        'STABILIZE': 'STABILIZE',
        'ALT_HOLD': 'ALT_HOLD',
        'LOITER': 'LOITER',
        'AUTO': 'AUTO',
        'GUIDED': 'GUIDED',
        'RTL': 'RTL',
        'LAND': 'LAND',
        'OFFBOARD': 'GUIDED',
        'ACRO': 'ACRO',
    }

    def __init__(self):
        super().__init__('mode_manager')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('autopilot_type', 'px4')
        self.declare_parameter('enforce_transitions', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.autopilot_type = self.get_parameter('autopilot_type').get_parameter_value().string_value
        self.enforce_transitions = (
            self.get_parameter('enforce_transitions').get_parameter_value().bool_value
        )

        self.get_logger().info(
            f'Mode manager starting: drone_id={self.drone_id}, '
            f'autopilot={self.autopilot_type}, '
            f'enforce_transitions={self.enforce_transitions}'
        )

        # ── State ────────────────────────────────────────────────────────
        self._current_mode = 'STABILIZE'
        self._armed = False
        self._connected = False

        qos_mavros = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            MavrosState, 'mavros/state', self._mavros_state_cb, qos_mavros)

        # ── Publishers ───────────────────────────────────────────────────
        self.mode_pub = self.create_publisher(String, 'drone/current_mode', 10)

        # ── MAVROS clients ───────────────────────────────────────────────
        self.set_mode_client = self.create_client(SetMode, 'mavros/set_mode')

        # ── Services ─────────────────────────────────────────────────────
        self.create_service(
            SetFlightMode, 'drone/set_mode', self._set_mode_service_cb)

        # ── Publish timer ────────────────────────────────────────────────
        self.create_timer(0.5, self._publish_mode)

        self.get_logger().info('Mode manager node initialized')

    def _mavros_state_cb(self, msg: MavrosState):
        self._connected = msg.connected
        self._armed = msg.armed
        # Reverse-map the MAVROS mode to our canonical name
        self._current_mode = self._reverse_mode_map(msg.mode)

    def _reverse_mode_map(self, mavros_mode: str) -> str:
        """Map a MAVROS/autopilot mode string back to canonical name."""
        mode_map = (
            self.PX4_MODE_MAP if self.autopilot_type == 'px4'
            else self.ARDUPILOT_MODE_MAP
        )
        for canonical, mapped in mode_map.items():
            if mapped == mavros_mode:
                return canonical
        return mavros_mode  # Return as-is if unknown

    def _publish_mode(self):
        msg = String()
        msg.data = self._current_mode
        self.mode_pub.publish(msg)

    def _set_mode_service_cb(self, request, response):
        """Handle mode change service calls with transition validation."""
        target_mode = request.mode.upper()

        self.get_logger().info(
            f'Mode change request: {self._current_mode} -> {target_mode}'
        )

        # Validate mode exists
        valid_modes = {m.value for m in FlightMode}
        if target_mode not in valid_modes:
            response.success = False
            response.message = f'Unknown mode: {target_mode}. Valid modes: {sorted(valid_modes)}'
            return response

        # Check connection
        if not self._connected:
            response.success = False
            response.message = 'Autopilot not connected'
            return response

        # Validate transition
        if self.enforce_transitions:
            allowed = self.TRANSITION_RULES.get(self._current_mode, set())
            if target_mode not in allowed and target_mode != self._current_mode:
                response.success = False
                response.message = (
                    f'Transition {self._current_mode} -> {target_mode} not allowed. '
                    f'Allowed from {self._current_mode}: {sorted(allowed)}'
                )
                return response

        # Map to autopilot-specific mode string
        mode_map = (
            self.PX4_MODE_MAP if self.autopilot_type == 'px4'
            else self.ARDUPILOT_MODE_MAP
        )
        autopilot_mode = mode_map.get(target_mode, target_mode)

        # Send to MAVROS
        if not self.set_mode_client.wait_for_service(timeout_sec=3.0):
            response.success = False
            response.message = 'MAVROS set_mode service not available'
            return response

        req = SetMode.Request()
        req.custom_mode = autopilot_mode
        future = self.set_mode_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        if future.result() is not None and future.result().mode_sent:
            self._current_mode = target_mode
            response.success = True
            response.message = f'Mode changed to {target_mode} ({autopilot_mode})'
            self.get_logger().info(response.message)
        else:
            response.success = False
            response.message = f'Autopilot rejected mode change to {autopilot_mode}'
            self.get_logger().warn(response.message)

        return response

    def destroy_node(self):
        self.get_logger().info('Mode manager shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ModeManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
