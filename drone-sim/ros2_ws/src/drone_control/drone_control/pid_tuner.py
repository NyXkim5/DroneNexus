"""
pid_tuner.py

ROS 2 node for real-time PID gain tuning.
Provides a service to update PID gains, publishes current values,
and subscribes to attitude feedback for live tuning visualization.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Float64MultiArray
from sensor_msgs.msg import Imu
from mavros_msgs.srv import ParamSet, ParamGet
from mavros_msgs.msg import ParamValue

from drone_interfaces.srv import UpdatePID


class PIDAxis:
    """Stores PID gains for a single axis."""

    def __init__(self, p: float = 0.0, i: float = 0.0, d: float = 0.0):
        self.p = p
        self.i = i
        self.d = d

    def to_list(self):
        return [self.p, self.i, self.d]


class PIDTunerNode(Node):
    """Real-time PID tuning node with MAVROS parameter integration."""

    # PX4 parameter name mappings
    PX4_PID_PARAMS = {
        'roll':  {'p': 'MC_ROLL_P',  'i': 'MC_ROLLRATE_I', 'd': 'MC_ROLLRATE_D'},
        'pitch': {'p': 'MC_PITCH_P', 'i': 'MC_PITCHRATE_I', 'd': 'MC_PITCHRATE_D'},
        'yaw':   {'p': 'MC_YAW_P',   'i': 'MC_YAWRATE_I',  'd': 'MC_YAWRATE_D'},
    }

    # ArduPilot parameter name mappings
    ARDUPILOT_PID_PARAMS = {
        'roll':  {'p': 'ATC_RAT_RLL_P', 'i': 'ATC_RAT_RLL_I', 'd': 'ATC_RAT_RLL_D'},
        'pitch': {'p': 'ATC_RAT_PIT_P', 'i': 'ATC_RAT_PIT_I', 'd': 'ATC_RAT_PIT_D'},
        'yaw':   {'p': 'ATC_RAT_YAW_P', 'i': 'ATC_RAT_YAW_I', 'd': 'ATC_RAT_YAW_D'},
    }

    VALID_AXES = ['roll', 'pitch', 'yaw', 'altitude', 'position']

    def __init__(self):
        super().__init__('pid_tuner')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('autopilot_type', 'px4')
        self.declare_parameter('publish_rate', 10.0)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.autopilot_type = self.get_parameter('autopilot_type').get_parameter_value().string_value
        publish_rate = self.get_parameter('publish_rate').get_parameter_value().double_value

        self.get_logger().info(
            f'PID tuner starting: drone_id={self.drone_id}, autopilot={self.autopilot_type}'
        )

        # ── PID state ────────────────────────────────────────────────────
        self.pid_gains = {
            'roll': PIDAxis(4.5, 0.01, 0.003),
            'pitch': PIDAxis(4.5, 0.01, 0.003),
            'yaw': PIDAxis(2.8, 0.01, 0.0),
            'altitude': PIDAxis(1.0, 0.1, 0.0),
            'position': PIDAxis(1.0, 0.0, 0.5),
        }

        # ── Attitude feedback tracking ───────────────────────────────────
        self._latest_imu = Imu()

        qos_mavros = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        self.create_subscription(Imu, 'mavros/imu/data', self._imu_cb, qos_mavros)

        # ── Publishers ───────────────────────────────────────────────────
        self.pid_pub = self.create_publisher(Float64MultiArray, 'drone/pid_values', 10)
        self.attitude_error_pub = self.create_publisher(
            Float64MultiArray, 'drone/attitude_error', 10)

        # ── MAVROS service clients ───────────────────────────────────────
        self.param_set_client = self.create_client(ParamSet, 'mavros/param/set')
        self.param_get_client = self.create_client(ParamGet, 'mavros/param/get')

        # ── Services ─────────────────────────────────────────────────────
        self.create_service(UpdatePID, 'drone/update_pid', self._update_pid_cb)

        # ── Publish timer ────────────────────────────────────────────────
        self.create_timer(1.0 / publish_rate, self._publish_pid_values)

        self.get_logger().info('PID tuner node initialized')

    def _imu_cb(self, msg: Imu):
        self._latest_imu = msg

    def _publish_pid_values(self):
        """Publish current PID gains for all axes."""
        msg = Float64MultiArray()
        data = []
        for axis in self.VALID_AXES:
            data.extend(self.pid_gains[axis].to_list())
        msg.data = data
        self.pid_pub.publish(msg)

    def _update_pid_cb(self, request, response):
        """Handle PID update service request."""
        axis = request.axis.lower()
        self.get_logger().info(
            f'PID update: drone={request.drone_id}, axis={axis}, '
            f'P={request.p}, I={request.i}, D={request.d}'
        )

        if request.drone_id != self.drone_id:
            response.success = False
            response.message = f'Unknown drone_id: {request.drone_id}'
            return response

        if axis not in self.VALID_AXES:
            response.success = False
            response.message = f'Invalid axis: {axis}. Valid: {self.VALID_AXES}'
            return response

        # Validate gain ranges
        if request.p < 0.0 or request.i < 0.0 or request.d < 0.0:
            response.success = False
            response.message = 'PID gains must be non-negative'
            return response

        # Update local state
        self.pid_gains[axis] = PIDAxis(request.p, request.i, request.d)

        # Push to autopilot via MAVROS
        success = self._push_gains_to_autopilot(axis, request.p, request.i, request.d)

        if success:
            response.success = True
            response.message = f'{axis} PID updated: P={request.p}, I={request.i}, D={request.d}'
        else:
            response.success = True  # Local update succeeded
            response.message = (
                f'{axis} PID updated locally. '
                f'MAVROS param push may have failed (autopilot may not be connected).'
            )

        return response

    def _push_gains_to_autopilot(self, axis: str, p: float, i: float, d: float) -> bool:
        """Push PID gains to the autopilot via MAVROS param service."""
        if not self.param_set_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().warn('MAVROS param/set service not available')
            return False

        param_map = (
            self.PX4_PID_PARAMS if self.autopilot_type == 'px4'
            else self.ARDUPILOT_PID_PARAMS
        )

        if axis not in param_map:
            self.get_logger().debug(f'No autopilot param mapping for axis: {axis}')
            return True

        all_ok = True
        for gain_name, value in [('p', p), ('i', i), ('d', d)]:
            param_id = param_map[axis][gain_name]
            req = ParamSet.Request()
            req.param_id = param_id
            req.value = ParamValue()
            req.value.real = value

            future = self.param_set_client.call_async(req)
            rclpy.spin_until_future_complete(self, future, timeout_sec=3.0)

            if future.result() is None or not future.result().success:
                self.get_logger().warn(f'Failed to set param {param_id} = {value}')
                all_ok = False
            else:
                self.get_logger().info(f'Set {param_id} = {value}')

        return all_ok

    def destroy_node(self):
        self.get_logger().info('PID tuner shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = PIDTunerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
