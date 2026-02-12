"""
flight_controller.py

ROS 2 node that interfaces with MAVROS to provide a unified drone control API.
Subscribes to command topics, publishes drone state, and exposes services
for arm/disarm/takeoff/land/goto operations.
"""

import math

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Header
from geometry_msgs.msg import PoseStamped, TwistStamped
from sensor_msgs.msg import NavSatFix, BatteryState, Imu
from mavros_msgs.msg import State as MavrosState
from mavros_msgs.srv import CommandBool, SetMode, CommandTOL

from drone_interfaces.msg import DroneState
from drone_interfaces.srv import SetFlightMode


class FlightControllerNode(Node):
    """Unified flight controller node bridging MAVROS to NEXUS drone API."""

    def __init__(self):
        super().__init__('flight_controller')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('autopilot_type', 'px4')
        self.declare_parameter('state_publish_rate', 20.0)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.autopilot_type = self.get_parameter('autopilot_type').get_parameter_value().string_value
        publish_rate = self.get_parameter('state_publish_rate').get_parameter_value().double_value

        self.get_logger().info(
            f'Flight controller starting: drone_id={self.drone_id}, '
            f'autopilot={self.autopilot_type}'
        )

        # ── Internal state ───────────────────────────────────────────────
        self._mavros_state = MavrosState()
        self._global_position = NavSatFix()
        self._local_position = PoseStamped()
        self._velocity = TwistStamped()
        self._battery = BatteryState()
        self._imu = Imu()
        self._altitude_agl = 0.0

        # QoS for MAVROS compatibility
        qos_mavros = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # ── MAVROS subscriptions ─────────────────────────────────────────
        self.create_subscription(
            MavrosState, 'mavros/state',
            self._mavros_state_cb, qos_mavros)
        self.create_subscription(
            NavSatFix, 'mavros/global_position/global',
            self._global_pos_cb, qos_mavros)
        self.create_subscription(
            PoseStamped, 'mavros/local_position/pose',
            self._local_pos_cb, qos_mavros)
        self.create_subscription(
            TwistStamped, 'mavros/local_position/velocity_local',
            self._velocity_cb, qos_mavros)
        self.create_subscription(
            BatteryState, 'mavros/battery',
            self._battery_cb, qos_mavros)
        self.create_subscription(
            Imu, 'mavros/imu/data',
            self._imu_cb, qos_mavros)

        # ── Publishers ───────────────────────────────────────────────────
        self.state_pub = self.create_publisher(DroneState, 'drone/state', 10)
        self.setpoint_pub = self.create_publisher(
            PoseStamped, 'mavros/setpoint_position/local', 10)

        # ── MAVROS service clients ───────────────────────────────────────
        self.arming_client = self.create_client(CommandBool, 'mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, 'mavros/set_mode')
        self.takeoff_client = self.create_client(CommandTOL, 'mavros/cmd/takeoff')
        self.land_client = self.create_client(CommandTOL, 'mavros/cmd/land')

        # ── Services exposed to NEXUS ────────────────────────────────────
        self.create_service(
            SetFlightMode, 'drone/set_flight_mode', self._set_flight_mode_cb)

        # ── Timer for state publishing ───────────────────────────────────
        timer_period = 1.0 / publish_rate
        self.state_timer = self.create_timer(timer_period, self._publish_state)

        self.get_logger().info('Flight controller node initialized')

    # ── MAVROS callbacks ─────────────────────────────────────────────────
    def _mavros_state_cb(self, msg: MavrosState):
        self._mavros_state = msg

    def _global_pos_cb(self, msg: NavSatFix):
        self._global_position = msg

    def _local_pos_cb(self, msg: PoseStamped):
        self._local_position = msg
        self._altitude_agl = msg.pose.position.z

    def _velocity_cb(self, msg: TwistStamped):
        self._velocity = msg

    def _battery_cb(self, msg: BatteryState):
        self._battery = msg

    def _imu_cb(self, msg: Imu):
        self._imu = msg

    # ── State publisher ──────────────────────────────────────────────────
    def _publish_state(self):
        """Aggregate all telemetry into a single DroneState message."""
        msg = DroneState()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        msg.drone_id = self.drone_id

        # Position
        msg.latitude = self._global_position.latitude
        msg.longitude = self._global_position.longitude
        msg.altitude_msl = self._global_position.altitude
        msg.altitude_agl = self._altitude_agl

        # Attitude (quaternion -> euler)
        q = self._imu.orientation
        roll, pitch, yaw = self._quaternion_to_euler(q.x, q.y, q.z, q.w)
        msg.roll = math.degrees(roll)
        msg.pitch = math.degrees(pitch)
        msg.yaw = math.degrees(yaw)
        msg.heading = (math.degrees(yaw) + 360.0) % 360.0

        # Velocity
        vx = self._velocity.twist.linear.x
        vy = self._velocity.twist.linear.y
        vz = self._velocity.twist.linear.z
        msg.ground_speed = math.sqrt(vx * vx + vy * vy)
        msg.vertical_speed = vz

        # Battery
        msg.battery_voltage = self._battery.voltage
        msg.battery_current = self._battery.current
        msg.battery_remaining_pct = self._battery.percentage * 100.0

        # GPS
        msg.gps_satellites = 0  # Populated from extended MAVROS data
        msg.gps_hdop = 0.0
        msg.gps_fix_type = 'FIX_3D'

        # Link
        msg.rssi = 100

        # State
        msg.armed = self._mavros_state.armed
        msg.in_air = self._altitude_agl > 0.3
        msg.flight_mode = self._mavros_state.mode
        msg.status = 'CONNECTED' if self._mavros_state.connected else 'DISCONNECTED'

        self.state_pub.publish(msg)

    # ── Service callbacks ────────────────────────────────────────────────
    def _set_flight_mode_cb(self, request, response):
        """Handle flight mode change requests."""
        self.get_logger().info(
            f'Set flight mode request: drone={request.drone_id}, mode={request.mode}'
        )

        if request.drone_id != self.drone_id:
            response.success = False
            response.message = f'Unknown drone_id: {request.drone_id}'
            return response

        mode = request.mode.upper()

        # Handle meta-commands
        if mode == 'ARM':
            return self._handle_arm(response, True)
        elif mode == 'DISARM':
            return self._handle_arm(response, False)
        elif mode == 'TAKEOFF':
            return self._handle_takeoff(response)
        elif mode == 'LAND':
            return self._handle_land(response)
        else:
            return self._handle_mode_change(response, request.mode)

    def _handle_arm(self, response, arm: bool):
        """Arm or disarm the vehicle."""
        if not self.arming_client.wait_for_service(timeout_sec=5.0):
            response.success = False
            response.message = 'Arming service not available'
            return response

        req = CommandBool.Request()
        req.value = arm
        future = self.arming_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        if future.result() is not None and future.result().success:
            response.success = True
            response.message = f'Vehicle {"armed" if arm else "disarmed"}'
        else:
            response.success = False
            response.message = f'Failed to {"arm" if arm else "disarm"}'

        return response

    def _handle_takeoff(self, response, altitude: float = 5.0):
        """Command vehicle takeoff."""
        if not self.takeoff_client.wait_for_service(timeout_sec=5.0):
            response.success = False
            response.message = 'Takeoff service not available'
            return response

        req = CommandTOL.Request()
        req.altitude = altitude
        req.latitude = self._global_position.latitude
        req.longitude = self._global_position.longitude
        future = self.takeoff_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)

        if future.result() is not None and future.result().success:
            response.success = True
            response.message = f'Takeoff to {altitude}m commanded'
        else:
            response.success = False
            response.message = 'Takeoff command failed'

        return response

    def _handle_land(self, response):
        """Command vehicle landing."""
        if not self.land_client.wait_for_service(timeout_sec=5.0):
            response.success = False
            response.message = 'Land service not available'
            return response

        req = CommandTOL.Request()
        req.altitude = 0.0
        req.latitude = self._global_position.latitude
        req.longitude = self._global_position.longitude
        future = self.land_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)

        if future.result() is not None and future.result().success:
            response.success = True
            response.message = 'Landing commanded'
        else:
            response.success = False
            response.message = 'Land command failed'

        return response

    def _handle_mode_change(self, response, mode: str):
        """Change autopilot flight mode."""
        if not self.set_mode_client.wait_for_service(timeout_sec=5.0):
            response.success = False
            response.message = 'Set mode service not available'
            return response

        req = SetMode.Request()
        req.custom_mode = mode
        future = self.set_mode_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        if future.result() is not None and future.result().mode_sent:
            response.success = True
            response.message = f'Mode changed to {mode}'
        else:
            response.success = False
            response.message = f'Failed to set mode {mode}'

        return response

    # ── Utility ──────────────────────────────────────────────────────────
    @staticmethod
    def _quaternion_to_euler(x, y, z, w):
        """Convert quaternion to Euler angles (roll, pitch, yaw)."""
        # Roll (x-axis rotation)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # Pitch (y-axis rotation)
        sinp = 2.0 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)
        else:
            pitch = math.asin(sinp)

        # Yaw (z-axis rotation)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return roll, pitch, yaw

    def destroy_node(self):
        """Clean shutdown."""
        self.get_logger().info('Flight controller shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FlightControllerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
