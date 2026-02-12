"""
waypoint_executor.py

ROS 2 node that executes waypoint missions. Reads mission files (JSON/YAML),
sends waypoints to the autopilot via MAVLink/MAVROS, monitors progress,
and handles mission completion and interruption.
"""

import json
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import yaml

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String, Int32, Float64
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import NavSatFix
from mavros_msgs.msg import Waypoint, WaypointList, WaypointReached
from mavros_msgs.srv import WaypointPush, WaypointClear, SetMode


class MissionState(Enum):
    IDLE = 'IDLE'
    LOADING = 'LOADING'
    READY = 'READY'
    EXECUTING = 'EXECUTING'
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    ABORTED = 'ABORTED'
    ERROR = 'ERROR'


@dataclass
class MissionWaypoint:
    """Single waypoint in a mission."""
    seq: int
    frame: int = 3  # MAV_FRAME_GLOBAL_RELATIVE_ALT
    command: int = 16  # MAV_CMD_NAV_WAYPOINT
    is_current: bool = False
    autocontinue: bool = True
    param1: float = 0.0  # Hold time (sec)
    param2: float = 2.0  # Acceptance radius (m)
    param3: float = 0.0  # Pass through (0=stop)
    param4: float = 0.0  # Desired yaw (deg, NaN=unchanged)
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 10.0

    def to_mavros_waypoint(self) -> Waypoint:
        wp = Waypoint()
        wp.frame = self.frame
        wp.command = self.command
        wp.is_current = self.is_current
        wp.autocontinue = self.autocontinue
        wp.param1 = self.param1
        wp.param2 = self.param2
        wp.param3 = self.param3
        wp.param4 = self.param4
        wp.x_lat = self.latitude
        wp.y_long = self.longitude
        wp.z_alt = self.altitude
        return wp


class WaypointExecutorNode(Node):
    """Executes waypoint missions via MAVROS."""

    def __init__(self):
        super().__init__('waypoint_executor')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('waypoint_reach_radius_m', 2.0)
        self.declare_parameter('default_altitude_m', 10.0)
        self.declare_parameter('default_speed_m_s', 5.0)
        self.declare_parameter('mission_dir', '/config/missions')

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.reach_radius = self.get_parameter('waypoint_reach_radius_m').get_parameter_value().double_value
        self.default_alt = self.get_parameter('default_altitude_m').get_parameter_value().double_value
        self.mission_dir = self.get_parameter('mission_dir').get_parameter_value().string_value

        self.get_logger().info(f'Waypoint executor starting: drone_id={self.drone_id}')

        # ── State ────────────────────────────────────────────────────────
        self._state = MissionState.IDLE
        self._waypoints: List[MissionWaypoint] = []
        self._current_wp_index = 0
        self._mission_name = ''
        self._current_position = NavSatFix()

        qos_mavros = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            String, 'drone/mission_command', self._mission_cmd_cb, 10)
        self.create_subscription(
            NavSatFix, 'mavros/global_position/global', self._position_cb, qos_mavros)
        self.create_subscription(
            WaypointReached, 'mavros/mission/reached', self._wp_reached_cb, qos_mavros)

        # ── Publishers ───────────────────────────────────────────────────
        self.state_pub = self.create_publisher(String, 'drone/mission_state', 10)
        self.progress_pub = self.create_publisher(Int32, 'drone/mission_progress', 10)
        self.distance_pub = self.create_publisher(
            Float64, 'drone/waypoint_distance', 10)

        # ── MAVROS clients ───────────────────────────────────────────────
        self.wp_push_client = self.create_client(WaypointPush, 'mavros/mission/push')
        self.wp_clear_client = self.create_client(WaypointClear, 'mavros/mission/clear')
        self.set_mode_client = self.create_client(SetMode, 'mavros/set_mode')

        # ── Status timer ─────────────────────────────────────────────────
        self.create_timer(1.0, self._publish_status)
        self.create_timer(0.5, self._monitor_progress)

        self.get_logger().info('Waypoint executor node initialized')

    # ── Callbacks ────────────────────────────────────────────────────────
    def _position_cb(self, msg: NavSatFix):
        self._current_position = msg

    def _wp_reached_cb(self, msg: WaypointReached):
        wp_seq = msg.wp_seq
        self.get_logger().info(f'Waypoint {wp_seq} reached')
        self._current_wp_index = wp_seq + 1

        if self._current_wp_index >= len(self._waypoints):
            self._state = MissionState.COMPLETED
            self.get_logger().info(f'Mission "{self._mission_name}" completed')

    def _mission_cmd_cb(self, msg: String):
        """Handle mission commands: load:<path>, start, pause, resume, abort."""
        cmd = msg.data.strip()
        self.get_logger().info(f'Mission command: {cmd}')

        if cmd.startswith('load:'):
            filepath = cmd[5:].strip()
            self._load_mission(filepath)
        elif cmd == 'start':
            self._start_mission()
        elif cmd == 'pause':
            self._pause_mission()
        elif cmd == 'resume':
            self._resume_mission()
        elif cmd == 'abort':
            self._abort_mission()
        else:
            self.get_logger().warn(f'Unknown mission command: {cmd}')

    # ── Mission operations ───────────────────────────────────────────────
    def _load_mission(self, filepath: str):
        """Load a mission file (JSON or YAML)."""
        self._state = MissionState.LOADING

        if not os.path.isabs(filepath):
            filepath = os.path.join(self.mission_dir, filepath)

        if not os.path.exists(filepath):
            self.get_logger().error(f'Mission file not found: {filepath}')
            self._state = MissionState.ERROR
            return

        try:
            with open(filepath, 'r') as f:
                if filepath.endswith('.json'):
                    data = json.load(f)
                else:
                    data = yaml.safe_load(f)

            self._mission_name = data.get('name', os.path.basename(filepath))
            raw_wps = data.get('waypoints', [])

            self._waypoints = []
            for i, wp_data in enumerate(raw_wps):
                wp = MissionWaypoint(
                    seq=i,
                    latitude=wp_data.get('lat', wp_data.get('latitude', 0.0)),
                    longitude=wp_data.get('lon', wp_data.get('longitude', 0.0)),
                    altitude=wp_data.get('alt', wp_data.get('altitude', self.default_alt)),
                    param1=wp_data.get('hold_time', 0.0),
                    param2=wp_data.get('accept_radius', self.reach_radius),
                    is_current=(i == 0),
                )
                self._waypoints.append(wp)

            self._current_wp_index = 0
            self._state = MissionState.READY
            self.get_logger().info(
                f'Mission "{self._mission_name}" loaded: {len(self._waypoints)} waypoints'
            )

        except Exception as e:
            self.get_logger().error(f'Failed to load mission: {e}')
            self._state = MissionState.ERROR

    def _start_mission(self):
        """Upload waypoints to autopilot and start AUTO mission."""
        if self._state != MissionState.READY:
            self.get_logger().warn(f'Cannot start: state is {self._state.value}')
            return

        if not self._waypoints:
            self.get_logger().warn('No waypoints loaded')
            return

        # Clear existing waypoints
        if self.wp_clear_client.wait_for_service(timeout_sec=5.0):
            clear_req = WaypointClear.Request()
            future = self.wp_clear_client.call_async(clear_req)
            rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        # Push new waypoints
        if not self.wp_push_client.wait_for_service(timeout_sec=5.0):
            self.get_logger().error('Waypoint push service not available')
            self._state = MissionState.ERROR
            return

        push_req = WaypointPush.Request()
        push_req.start_index = 0
        push_req.waypoints = [wp.to_mavros_waypoint() for wp in self._waypoints]

        future = self.wp_push_client.call_async(push_req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)

        if future.result() is not None and future.result().success:
            self.get_logger().info(f'{future.result().wp_transfered} waypoints uploaded')
        else:
            self.get_logger().error('Failed to upload waypoints')
            self._state = MissionState.ERROR
            return

        # Switch to AUTO mode
        if self.set_mode_client.wait_for_service(timeout_sec=3.0):
            mode_req = SetMode.Request()
            mode_req.custom_mode = 'AUTO.MISSION'
            self.set_mode_client.call_async(mode_req)

        self._state = MissionState.EXECUTING
        self.get_logger().info(f'Mission "{self._mission_name}" started')

    def _pause_mission(self):
        if self._state == MissionState.EXECUTING:
            # Switch to LOITER/HOLD
            if self.set_mode_client.wait_for_service(timeout_sec=3.0):
                req = SetMode.Request()
                req.custom_mode = 'AUTO.LOITER'
                self.set_mode_client.call_async(req)
            self._state = MissionState.PAUSED
            self.get_logger().info('Mission paused')

    def _resume_mission(self):
        if self._state == MissionState.PAUSED:
            if self.set_mode_client.wait_for_service(timeout_sec=3.0):
                req = SetMode.Request()
                req.custom_mode = 'AUTO.MISSION'
                self.set_mode_client.call_async(req)
            self._state = MissionState.EXECUTING
            self.get_logger().info('Mission resumed')

    def _abort_mission(self):
        if self._state in (MissionState.EXECUTING, MissionState.PAUSED):
            # Switch to RTL
            if self.set_mode_client.wait_for_service(timeout_sec=3.0):
                req = SetMode.Request()
                req.custom_mode = 'AUTO.RTL'
                self.set_mode_client.call_async(req)
            self._state = MissionState.ABORTED
            self.get_logger().warn('Mission ABORTED - returning to launch')

    # ── Monitoring ───────────────────────────────────────────────────────
    def _monitor_progress(self):
        """Monitor distance to current waypoint."""
        if self._state != MissionState.EXECUTING or not self._waypoints:
            return

        if self._current_wp_index >= len(self._waypoints):
            return

        target = self._waypoints[self._current_wp_index]
        dist = self._haversine(
            self._current_position.latitude,
            self._current_position.longitude,
            target.latitude,
            target.longitude,
        )

        dist_msg = Float64()
        dist_msg.data = dist
        self.distance_pub.publish(dist_msg)

    def _publish_status(self):
        """Publish mission state and progress."""
        state_msg = String()
        state_msg.data = (
            f'{self._state.value}|{self._mission_name}|'
            f'{self._current_wp_index}/{len(self._waypoints)}'
        )
        self.state_pub.publish(state_msg)

        progress_msg = Int32()
        if self._waypoints:
            progress_msg.data = int(
                (self._current_wp_index / len(self._waypoints)) * 100
            )
        else:
            progress_msg.data = 0
        self.progress_pub.publish(progress_msg)

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2) -> float:
        R = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def destroy_node(self):
        self.get_logger().info('Waypoint executor shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = WaypointExecutorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
