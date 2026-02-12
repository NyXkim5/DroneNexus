"""
failsafe_manager.py

ROS 2 node that monitors critical systems (battery, GPS, RC link, geofence)
and triggers failsafe actions (RTL, land, hover) when thresholds are exceeded.
"""

import time
from enum import Enum
from dataclasses import dataclass, field

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String
from sensor_msgs.msg import NavSatFix, BatteryState
from mavros_msgs.msg import State as MavrosState
from mavros_msgs.srv import SetMode, CommandBool

from drone_interfaces.msg import DroneState


class FailsafeLevel(Enum):
    NONE = 'NONE'
    WARNING = 'WARNING'
    CRITICAL = 'CRITICAL'
    EMERGENCY = 'EMERGENCY'


class FailsafeAction(Enum):
    NONE = 'NONE'
    WARN = 'WARN'
    RTL = 'RTL'
    LAND = 'LAND'
    HOVER = 'HOVER'
    DISARM = 'DISARM'


@dataclass
class FailsafeStatus:
    battery_level: FailsafeLevel = FailsafeLevel.NONE
    gps_level: FailsafeLevel = FailsafeLevel.NONE
    link_level: FailsafeLevel = FailsafeLevel.NONE
    geofence_level: FailsafeLevel = FailsafeLevel.NONE
    active_action: FailsafeAction = FailsafeAction.NONE
    messages: list = field(default_factory=list)


class FailsafeManagerNode(Node):
    """Monitors drone systems and triggers failsafe responses."""

    def __init__(self):
        super().__init__('failsafe_manager')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('monitor_rate', 5.0)

        # Battery thresholds
        self.declare_parameter('battery_warn_pct', 30.0)
        self.declare_parameter('battery_critical_pct', 20.0)
        self.declare_parameter('battery_emergency_pct', 10.0)
        self.declare_parameter('battery_warn_action', 'WARN')
        self.declare_parameter('battery_critical_action', 'RTL')
        self.declare_parameter('battery_emergency_action', 'LAND')

        # GPS thresholds
        self.declare_parameter('gps_loss_timeout_sec', 5.0)
        self.declare_parameter('gps_min_satellites', 6)
        self.declare_parameter('gps_loss_action', 'HOVER')

        # Link thresholds
        self.declare_parameter('link_loss_timeout_sec', 10.0)
        self.declare_parameter('link_loss_action', 'RTL')

        # Geofence
        self.declare_parameter('geofence_enabled', True)
        self.declare_parameter('geofence_radius_m', 500.0)
        self.declare_parameter('geofence_max_altitude_m', 120.0)
        self.declare_parameter('geofence_action', 'RTL')

        # Read parameter values
        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        monitor_rate = self.get_parameter('monitor_rate').get_parameter_value().double_value

        self.battery_warn_pct = self.get_parameter('battery_warn_pct').get_parameter_value().double_value
        self.battery_critical_pct = self.get_parameter('battery_critical_pct').get_parameter_value().double_value
        self.battery_emergency_pct = self.get_parameter('battery_emergency_pct').get_parameter_value().double_value
        self.battery_warn_action = FailsafeAction[
            self.get_parameter('battery_warn_action').get_parameter_value().string_value]
        self.battery_critical_action = FailsafeAction[
            self.get_parameter('battery_critical_action').get_parameter_value().string_value]
        self.battery_emergency_action = FailsafeAction[
            self.get_parameter('battery_emergency_action').get_parameter_value().string_value]

        self.gps_loss_timeout = self.get_parameter('gps_loss_timeout_sec').get_parameter_value().double_value
        self.gps_min_sats = self.get_parameter('gps_min_satellites').get_parameter_value().integer_value
        self.gps_loss_action = FailsafeAction[
            self.get_parameter('gps_loss_action').get_parameter_value().string_value]

        self.link_loss_timeout = self.get_parameter('link_loss_timeout_sec').get_parameter_value().double_value
        self.link_loss_action = FailsafeAction[
            self.get_parameter('link_loss_action').get_parameter_value().string_value]

        self.geofence_enabled = self.get_parameter('geofence_enabled').get_parameter_value().bool_value
        self.geofence_radius = self.get_parameter('geofence_radius_m').get_parameter_value().double_value
        self.geofence_max_alt = self.get_parameter('geofence_max_altitude_m').get_parameter_value().double_value
        self.geofence_action = FailsafeAction[
            self.get_parameter('geofence_action').get_parameter_value().string_value]

        self.get_logger().info(f'Failsafe manager starting: drone_id={self.drone_id}')

        # ── Internal state ───────────────────────────────────────────────
        self._battery_pct = 100.0
        self._gps_fix = NavSatFix()
        self._last_gps_time = time.time()
        self._mavros_connected = False
        self._last_heartbeat_time = time.time()
        self._armed = False
        self._in_air = False
        self._home_lat = 0.0
        self._home_lon = 0.0
        self._home_set = False
        self._failsafe_triggered = False
        self._status = FailsafeStatus()

        qos_mavros = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            BatteryState, 'mavros/battery', self._battery_cb, qos_mavros)
        self.create_subscription(
            NavSatFix, 'mavros/global_position/global', self._gps_cb, qos_mavros)
        self.create_subscription(
            MavrosState, 'mavros/state', self._state_cb, qos_mavros)
        self.create_subscription(
            DroneState, 'drone/state', self._drone_state_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.failsafe_pub = self.create_publisher(String, 'drone/failsafe_status', 10)

        # ── MAVROS clients ───────────────────────────────────────────────
        self.set_mode_client = self.create_client(SetMode, 'mavros/set_mode')
        self.arming_client = self.create_client(CommandBool, 'mavros/cmd/arming')

        # ── Monitor timer ────────────────────────────────────────────────
        self.create_timer(1.0 / monitor_rate, self._monitor_loop)

        self.get_logger().info('Failsafe manager node initialized')

    # ── Callbacks ────────────────────────────────────────────────────────
    def _battery_cb(self, msg: BatteryState):
        self._battery_pct = msg.percentage * 100.0

    def _gps_cb(self, msg: NavSatFix):
        self._gps_fix = msg
        self._last_gps_time = time.time()
        if not self._home_set and msg.latitude != 0.0:
            self._home_lat = msg.latitude
            self._home_lon = msg.longitude
            self._home_set = True
            self.get_logger().info(
                f'Home position set: lat={self._home_lat}, lon={self._home_lon}'
            )

    def _state_cb(self, msg: MavrosState):
        self._mavros_connected = msg.connected
        self._armed = msg.armed
        if msg.connected:
            self._last_heartbeat_time = time.time()

    def _drone_state_cb(self, msg: DroneState):
        self._in_air = msg.in_air

    # ── Monitor loop ─────────────────────────────────────────────────────
    def _monitor_loop(self):
        """Periodic check of all failsafe conditions."""
        if not self._armed or not self._in_air:
            # Only monitor when armed and in air
            self._status = FailsafeStatus()
            self._failsafe_triggered = False
            self._publish_status()
            return

        status = FailsafeStatus()
        highest_action = FailsafeAction.NONE

        # ── Battery check ────────────────────────────────────────────
        if self._battery_pct <= self.battery_emergency_pct:
            status.battery_level = FailsafeLevel.EMERGENCY
            status.messages.append(f'BATTERY EMERGENCY: {self._battery_pct:.1f}%')
            highest_action = self._higher_priority(highest_action, self.battery_emergency_action)
        elif self._battery_pct <= self.battery_critical_pct:
            status.battery_level = FailsafeLevel.CRITICAL
            status.messages.append(f'Battery critical: {self._battery_pct:.1f}%')
            highest_action = self._higher_priority(highest_action, self.battery_critical_action)
        elif self._battery_pct <= self.battery_warn_pct:
            status.battery_level = FailsafeLevel.WARNING
            status.messages.append(f'Battery low: {self._battery_pct:.1f}%')
            highest_action = self._higher_priority(highest_action, self.battery_warn_action)

        # ── GPS check ────────────────────────────────────────────────
        gps_age = time.time() - self._last_gps_time
        if gps_age > self.gps_loss_timeout:
            status.gps_level = FailsafeLevel.CRITICAL
            status.messages.append(f'GPS lost for {gps_age:.1f}s')
            highest_action = self._higher_priority(highest_action, self.gps_loss_action)

        # ── Link check ───────────────────────────────────────────────
        link_age = time.time() - self._last_heartbeat_time
        if link_age > self.link_loss_timeout:
            status.link_level = FailsafeLevel.CRITICAL
            status.messages.append(f'Link lost for {link_age:.1f}s')
            highest_action = self._higher_priority(highest_action, self.link_loss_action)

        # ── Geofence check ───────────────────────────────────────────
        if self.geofence_enabled and self._home_set:
            dist = self._haversine_distance(
                self._home_lat, self._home_lon,
                self._gps_fix.latitude, self._gps_fix.longitude,
            )
            alt = self._gps_fix.altitude

            if dist > self.geofence_radius:
                status.geofence_level = FailsafeLevel.CRITICAL
                status.messages.append(f'Geofence breach: {dist:.0f}m from home')
                highest_action = self._higher_priority(highest_action, self.geofence_action)

            if alt > self.geofence_max_alt:
                status.geofence_level = FailsafeLevel.CRITICAL
                status.messages.append(f'Altitude limit: {alt:.0f}m > {self.geofence_max_alt:.0f}m')
                highest_action = self._higher_priority(highest_action, self.geofence_action)

        # ── Execute highest priority action ──────────────────────────
        status.active_action = highest_action
        self._status = status

        if highest_action != FailsafeAction.NONE and highest_action != FailsafeAction.WARN:
            if not self._failsafe_triggered:
                self._failsafe_triggered = True
                self._execute_failsafe(highest_action)
                for msg_text in status.messages:
                    self.get_logger().error(f'FAILSAFE: {msg_text}')

        self._publish_status()

    def _execute_failsafe(self, action: FailsafeAction):
        """Execute a failsafe action."""
        self.get_logger().warn(f'Executing failsafe action: {action.value}')

        if action == FailsafeAction.RTL:
            self._set_mode('AUTO.RTL')
        elif action == FailsafeAction.LAND:
            self._set_mode('AUTO.LAND')
        elif action == FailsafeAction.HOVER:
            self._set_mode('POSCTL')  # Hold position
        elif action == FailsafeAction.DISARM:
            self._disarm()

    def _set_mode(self, mode: str):
        if not self.set_mode_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error('set_mode service unavailable for failsafe')
            return
        req = SetMode.Request()
        req.custom_mode = mode
        self.set_mode_client.call_async(req)

    def _disarm(self):
        if not self.arming_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error('arming service unavailable for failsafe')
            return
        req = CommandBool.Request()
        req.value = False
        self.arming_client.call_async(req)

    def _publish_status(self):
        msg = String()
        s = self._status
        msg.data = (
            f'battery={s.battery_level.value},'
            f'gps={s.gps_level.value},'
            f'link={s.link_level.value},'
            f'geofence={s.geofence_level.value},'
            f'action={s.active_action.value}'
        )
        if s.messages:
            msg.data += '|' + ';'.join(s.messages)
        self.failsafe_pub.publish(msg)

    # ── Utility ──────────────────────────────────────────────────────────
    _ACTION_PRIORITY = {
        FailsafeAction.NONE: 0,
        FailsafeAction.WARN: 1,
        FailsafeAction.HOVER: 2,
        FailsafeAction.RTL: 3,
        FailsafeAction.LAND: 4,
        FailsafeAction.DISARM: 5,
    }

    @classmethod
    def _higher_priority(cls, a: FailsafeAction, b: FailsafeAction) -> FailsafeAction:
        return a if cls._ACTION_PRIORITY[a] >= cls._ACTION_PRIORITY[b] else b

    @staticmethod
    def _haversine_distance(lat1, lon1, lat2, lon2) -> float:
        """Calculate distance in meters between two GPS coordinates."""
        import math
        R = 6371000.0  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def destroy_node(self):
        self.get_logger().info('Failsafe manager shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FailsafeManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
