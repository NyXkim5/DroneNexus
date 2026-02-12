"""
vio_fallback.py

Visual-Inertial Odometry (VIO) fallback node. Activates when GPS signal
is lost and uses camera + IMU data to estimate position. Publishes pose
estimates to maintain navigation capability without GPS.
"""

import math
import time
from typing import Optional

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Bool
from sensor_msgs.msg import Image, Imu, NavSatFix
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, TransformStamped
from nav_msgs.msg import Odometry

from tf2_ros import TransformBroadcaster


class VIOFallbackNode(Node):
    """
    Visual-Inertial Odometry fallback for GPS-denied navigation.
    Fuses camera feature tracking with IMU data for dead reckoning.
    """

    def __init__(self):
        super().__init__('vio_fallback')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('camera_topic', '/camera')
        self.declare_parameter('imu_topic', 'mavros/imu/data')
        self.declare_parameter('gps_topic', 'mavros/global_position/global')
        self.declare_parameter('gps_loss_timeout_sec', 3.0)
        self.declare_parameter('update_rate', 30.0)
        self.declare_parameter('auto_activate', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        gps_loss_timeout = self.get_parameter('gps_loss_timeout_sec').get_parameter_value().double_value
        update_rate = self.get_parameter('update_rate').get_parameter_value().double_value
        auto_activate = self.get_parameter('auto_activate').get_parameter_value().bool_value

        self.get_logger().info(f'VIO fallback starting: drone_id={self.drone_id}')

        # ── State ────────────────────────────────────────────────────────
        self._active = False
        self._auto_activate = auto_activate
        self._gps_loss_timeout = gps_loss_timeout
        self._last_gps_time = time.time()
        self._gps_available = True

        # Pose estimate (ENU frame)
        self._position = np.array([0.0, 0.0, 0.0])
        self._velocity = np.array([0.0, 0.0, 0.0])
        self._orientation_quat = np.array([0.0, 0.0, 0.0, 1.0])  # x, y, z, w

        # IMU integration state
        self._last_imu_time: Optional[float] = None
        self._accel_bias = np.array([0.0, 0.0, 0.0])
        self._gyro_bias = np.array([0.0, 0.0, 0.0])
        self._gravity = np.array([0.0, 0.0, -9.81])

        # Previous image for feature tracking
        self._prev_image: Optional[Image] = None

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        camera_topic = self.get_parameter('camera_topic').get_parameter_value().string_value
        imu_topic = self.get_parameter('imu_topic').get_parameter_value().string_value
        gps_topic = self.get_parameter('gps_topic').get_parameter_value().string_value

        self.create_subscription(Image, camera_topic, self._camera_cb, qos_sensor)
        self.create_subscription(Imu, imu_topic, self._imu_cb, qos_sensor)
        self.create_subscription(NavSatFix, gps_topic, self._gps_cb, qos_sensor)
        self.create_subscription(Bool, 'vio/activate', self._activate_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, 'vio/pose', 10)
        self.odom_pub = self.create_publisher(Odometry, 'vio/odom', 10)
        self.status_pub = self.create_publisher(Bool, 'vio/active', 10)

        # ── TF ───────────────────────────────────────────────────────────
        self.tf_broadcaster = TransformBroadcaster(self)

        # ── Timers ───────────────────────────────────────────────────────
        self.create_timer(1.0 / update_rate, self._update)
        self.create_timer(1.0, self._check_gps_status)

        self.get_logger().info('VIO fallback node initialized')

    # ── Callbacks ────────────────────────────────────────────────────────
    def _camera_cb(self, msg: Image):
        if self._active:
            self._process_visual_odometry(msg)
        self._prev_image = msg

    def _imu_cb(self, msg: Imu):
        if not self._active:
            return
        self._integrate_imu(msg)

    def _gps_cb(self, msg: NavSatFix):
        self._last_gps_time = time.time()
        if not self._gps_available:
            self._gps_available = True
            self.get_logger().info('GPS signal restored')
            if self._auto_activate and self._active:
                self._deactivate()

    def _activate_cb(self, msg: Bool):
        if msg.data and not self._active:
            self._activate()
        elif not msg.data and self._active:
            self._deactivate()

    # ── GPS monitoring ───────────────────────────────────────────────────
    def _check_gps_status(self):
        gps_age = time.time() - self._last_gps_time
        if gps_age > self._gps_loss_timeout:
            if self._gps_available:
                self._gps_available = False
                self.get_logger().warn(f'GPS signal lost ({gps_age:.1f}s)')
                if self._auto_activate:
                    self._activate()

    def _activate(self):
        self._active = True
        self._last_imu_time = None  # Reset integration
        self.get_logger().warn('VIO ACTIVATED - using visual-inertial odometry')

    def _deactivate(self):
        self._active = False
        self.get_logger().info('VIO deactivated - GPS available')

    # ── IMU integration ──────────────────────────────────────────────────
    def _integrate_imu(self, msg: Imu):
        """Dead reckoning using IMU accelerometer data."""
        now = self.get_clock().now().nanoseconds * 1e-9

        if self._last_imu_time is None:
            self._last_imu_time = now
            return

        dt = now - self._last_imu_time
        self._last_imu_time = now

        if dt <= 0.0 or dt > 1.0:
            return

        # Extract linear acceleration (body frame)
        accel_body = np.array([
            msg.linear_acceleration.x - self._accel_bias[0],
            msg.linear_acceleration.y - self._accel_bias[1],
            msg.linear_acceleration.z - self._accel_bias[2],
        ])

        # Update orientation from IMU quaternion
        self._orientation_quat = np.array([
            msg.orientation.x,
            msg.orientation.y,
            msg.orientation.z,
            msg.orientation.w,
        ])

        # Rotate acceleration to world frame and remove gravity
        accel_world = self._rotate_vector_by_quaternion(accel_body, self._orientation_quat)
        accel_world += self._gravity  # Remove gravity (gravity is [0,0,-9.81])

        # Integrate (simple Euler, production would use RK4 or preintegration)
        self._velocity += accel_world * dt
        self._position += self._velocity * dt

    def _process_visual_odometry(self, msg: Image):
        """
        Visual odometry using feature tracking between frames.
        Placeholder: in production, this would use ORB/FAST features
        with essential matrix decomposition for relative pose estimation.
        """
        if self._prev_image is None:
            return

        # Feature matching and motion estimation would go here.
        # The visual estimate would be fused with IMU via an EKF.
        pass

    # ── Update / publish ─────────────────────────────────────────────────
    def _update(self):
        """Publish VIO state."""
        status_msg = Bool()
        status_msg.data = self._active
        self.status_pub.publish(status_msg)

        if not self._active:
            return

        now = self.get_clock().now().to_msg()

        # Publish pose
        pose_msg = PoseWithCovarianceStamped()
        pose_msg.header.stamp = now
        pose_msg.header.frame_id = 'odom'
        pose_msg.pose.pose.position.x = self._position[0]
        pose_msg.pose.pose.position.y = self._position[1]
        pose_msg.pose.pose.position.z = self._position[2]
        pose_msg.pose.pose.orientation.x = self._orientation_quat[0]
        pose_msg.pose.pose.orientation.y = self._orientation_quat[1]
        pose_msg.pose.pose.orientation.z = self._orientation_quat[2]
        pose_msg.pose.pose.orientation.w = self._orientation_quat[3]
        # Higher covariance than GPS to indicate reduced confidence
        pose_msg.pose.covariance[0] = 0.1   # x
        pose_msg.pose.covariance[7] = 0.1   # y
        pose_msg.pose.covariance[14] = 0.1  # z
        pose_msg.pose.covariance[35] = 0.05 # yaw
        self.pose_pub.publish(pose_msg)

        # Publish odometry
        odom_msg = Odometry()
        odom_msg.header.stamp = now
        odom_msg.header.frame_id = 'odom'
        odom_msg.child_frame_id = 'base_link'
        odom_msg.pose.pose = pose_msg.pose.pose
        odom_msg.twist.twist.linear.x = self._velocity[0]
        odom_msg.twist.twist.linear.y = self._velocity[1]
        odom_msg.twist.twist.linear.z = self._velocity[2]
        self.odom_pub.publish(odom_msg)

        # Broadcast TF
        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = 'odom'
        t.child_frame_id = 'base_link_vio'
        t.transform.translation.x = self._position[0]
        t.transform.translation.y = self._position[1]
        t.transform.translation.z = self._position[2]
        t.transform.rotation.x = self._orientation_quat[0]
        t.transform.rotation.y = self._orientation_quat[1]
        t.transform.rotation.z = self._orientation_quat[2]
        t.transform.rotation.w = self._orientation_quat[3]
        self.tf_broadcaster.sendTransform(t)

    # ── Utility ──────────────────────────────────────────────────────────
    @staticmethod
    def _rotate_vector_by_quaternion(v: np.ndarray, q: np.ndarray) -> np.ndarray:
        """Rotate a 3D vector by a quaternion (x, y, z, w format)."""
        qx, qy, qz, qw = q
        # Quaternion rotation: v' = q * v * q^-1
        # Using the rotation matrix form for efficiency
        r00 = 1 - 2 * (qy * qy + qz * qz)
        r01 = 2 * (qx * qy - qz * qw)
        r02 = 2 * (qx * qz + qy * qw)
        r10 = 2 * (qx * qy + qz * qw)
        r11 = 1 - 2 * (qx * qx + qz * qz)
        r12 = 2 * (qy * qz - qx * qw)
        r20 = 2 * (qx * qz - qy * qw)
        r21 = 2 * (qy * qz + qx * qw)
        r22 = 1 - 2 * (qx * qx + qy * qy)

        return np.array([
            r00 * v[0] + r01 * v[1] + r02 * v[2],
            r10 * v[0] + r11 * v[1] + r12 * v[2],
            r20 * v[0] + r21 * v[1] + r22 * v[2],
        ])

    def destroy_node(self):
        self.get_logger().info('VIO fallback shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = VIOFallbackNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
