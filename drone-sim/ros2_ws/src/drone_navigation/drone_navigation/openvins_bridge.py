"""
openvins_bridge.py

Bridges OpenVINS ROS2 topics to DroneNexus's VIO interface (vio/pose,
vio/odom, vio/active, vio/source). Drop-in replacement for vio_fallback.py
when OpenVINS is running as the VIO backend.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Bool, String
from sensor_msgs.msg import Imu, NavSatFix
from geometry_msgs.msg import PoseWithCovarianceStamped, TransformStamped
from nav_msgs.msg import Odometry, Path

from tf2_ros import TransformBroadcaster


# Indices of the 6x6 covariance diagonal (row-major, 6 cols)
_COV_DIAG_INDICES = [0, 7, 14, 21, 28, 35]


class OpenVINSBridgeNode(Node):
    """
    Bridges OpenVINS output topics to DroneNexus VIO interface topics.
    Falls back to IMU dead reckoning when OpenVINS data goes stale.
    """

    def __init__(self) -> None:
        super().__init__("openvins_bridge")
        self._declare_parameters()
        self._load_parameters()
        self._init_state()
        self._create_subscriptions()
        self._create_publishers()
        self.tf_broadcaster = TransformBroadcaster(self)
        self.create_timer(1.0 / 30.0, self._update)
        self.create_timer(1.0, self._check_gps_status)
        self.get_logger().info(
            f"OpenVINS bridge started: drone_id={self._drone_id}, "
            f"ns={self._openvins_ns}"
        )

    # ── Parameter setup ──────────────────────────────────────────────────

    def _declare_parameters(self) -> None:
        self.declare_parameter("drone_id", "drone_0")
        self.declare_parameter("openvins_ns", "ov_msckf")
        self.declare_parameter("covariance_scale", 1.0)
        self.declare_parameter("max_age_sec", 0.5)
        self.declare_parameter("auto_fallback", True)
        self.declare_parameter("gps_topic", "mavros/global_position/global")
        self.declare_parameter("imu_topic", "mavros/imu/data")
        self.declare_parameter("gps_loss_timeout_sec", 3.0)

    def _load_parameters(self) -> None:
        self._drone_id = self._str_param("drone_id")
        self._openvins_ns = self._str_param("openvins_ns")
        self._covariance_scale = self._double_param("covariance_scale")
        self._max_age_sec = self._double_param("max_age_sec")
        self._auto_fallback = self._bool_param("auto_fallback")
        self._gps_loss_timeout = self._double_param("gps_loss_timeout_sec")

    def _str_param(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _double_param(self, name: str) -> float:
        return self.get_parameter(name).get_parameter_value().double_value

    def _bool_param(self, name: str) -> bool:
        return self.get_parameter(name).get_parameter_value().bool_value

    # ── State init ───────────────────────────────────────────────────────

    def _init_state(self) -> None:
        self._last_openvins_time: Optional[float] = None
        self._source = "openvins"
        self._active = False
        self._last_gps_time = time.time()
        self._gps_available = True

        # Cached latest messages from OpenVINS
        self._latest_pose: Optional[PoseWithCovarianceStamped] = None
        self._latest_odom: Optional[Odometry] = None

        # Dead reckoning state
        self._position = np.array([0.0, 0.0, 0.0])
        self._velocity = np.array([0.0, 0.0, 0.0])
        self._orientation_quat = np.array([0.0, 0.0, 0.0, 1.0])
        self._last_imu_time: Optional[float] = None
        self._accel_bias = np.array([0.0, 0.0, 0.0])
        self._gravity = np.array([0.0, 0.0, -9.81])

    # ── Subscriptions ────────────────────────────────────────────────────

    def _create_subscriptions(self) -> None:
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        ns = self._openvins_ns

        self.create_subscription(
            PoseWithCovarianceStamped,
            f"{ns}/poseimu",
            self._pose_cb,
            qos_sensor,
        )
        self.create_subscription(
            Odometry, f"{ns}/odomimu", self._odom_cb, qos_sensor
        )
        self.create_subscription(
            Path, f"{ns}/pathimu", self._path_cb, qos_sensor
        )

        imu_topic = self._str_param("imu_topic")
        gps_topic = self._str_param("gps_topic")
        self.create_subscription(Imu, imu_topic, self._imu_cb, qos_sensor)
        self.create_subscription(
            NavSatFix, gps_topic, self._gps_cb, qos_sensor
        )

    # ── Publishers ───────────────────────────────────────────────────────

    def _create_publishers(self) -> None:
        self.pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "vio/pose", 10
        )
        self.odom_pub = self.create_publisher(Odometry, "vio/odom", 10)
        self.status_pub = self.create_publisher(Bool, "vio/active", 10)
        self.source_pub = self.create_publisher(String, "vio/source", 10)

    # ── OpenVINS callbacks ───────────────────────────────────────────────

    def _pose_cb(self, msg: PoseWithCovarianceStamped) -> None:
        self._latest_pose = msg
        self._last_openvins_time = time.time()
        self._sync_dead_reckoning_from_pose(msg)

    def _odom_cb(self, msg: Odometry) -> None:
        self._latest_odom = msg
        self._last_openvins_time = time.time()

    def _path_cb(self, msg: Path) -> None:
        """Receive trajectory history. Currently unused beyond timestamp."""
        _ = msg
        self._last_openvins_time = time.time()

    # ── GPS / IMU callbacks ──────────────────────────────────────────────

    def _gps_cb(self, msg: NavSatFix) -> None:
        self._last_gps_time = time.time()
        if not self._gps_available:
            self._gps_available = True
            self.get_logger().info("GPS signal restored")

    def _imu_cb(self, msg: Imu) -> None:
        if self._source == "dead_reckoning":
            self._integrate_imu(msg)

    # ── GPS monitoring ───────────────────────────────────────────────────

    def _check_gps_status(self) -> None:
        gps_age = time.time() - self._last_gps_time
        if gps_age > self._gps_loss_timeout and self._gps_available:
            self._gps_available = False
            self.get_logger().warn(f"GPS signal lost ({gps_age:.1f}s)")

    # ── Dead reckoning (from vio_fallback.py) ────────────────────────────

    def _sync_dead_reckoning_from_pose(
        self, msg: PoseWithCovarianceStamped
    ) -> None:
        """Keep dead reckoning state in sync with OpenVINS pose."""
        p = msg.pose.pose.position
        o = msg.pose.pose.orientation
        self._position = np.array([p.x, p.y, p.z])
        self._orientation_quat = np.array([o.x, o.y, o.z, o.w])

    def _integrate_imu(self, msg: Imu) -> None:
        """Dead reckoning using IMU accelerometer data."""
        now = self.get_clock().now().nanoseconds * 1e-9

        if self._last_imu_time is None:
            self._last_imu_time = now
            return

        dt = now - self._last_imu_time
        self._last_imu_time = now

        if dt <= 0.0 or dt > 1.0:
            return

        accel_body = np.array([
            msg.linear_acceleration.x - self._accel_bias[0],
            msg.linear_acceleration.y - self._accel_bias[1],
            msg.linear_acceleration.z - self._accel_bias[2],
        ])

        self._orientation_quat = np.array([
            msg.orientation.x,
            msg.orientation.y,
            msg.orientation.z,
            msg.orientation.w,
        ])

        accel_world = rotate_vector_by_quaternion(
            accel_body, self._orientation_quat
        )
        accel_world += self._gravity

        self._velocity += accel_world * dt
        self._position += self._velocity * dt

    # ── Main update loop ─────────────────────────────────────────────────

    def _update(self) -> None:
        self._update_source()
        self._publish_status()

        if self._source == "openvins":
            self._publish_openvins()
        elif self._source == "dead_reckoning":
            self._publish_dead_reckoning()

    def _update_source(self) -> None:
        """Determine active estimator source based on staleness."""
        was = self._source
        openvins_stale = is_stale(
            self._last_openvins_time, self._max_age_sec
        )

        if openvins_stale and self._auto_fallback:
            self._source = "dead_reckoning"
            self._active = not self._gps_available
        else:
            self._source = "openvins"
            self._active = True

        if was != self._source:
            self.get_logger().warn(f"VIO source changed: {was} -> {self._source}")

    def _publish_status(self) -> None:
        status_msg = Bool()
        status_msg.data = self._active
        self.status_pub.publish(status_msg)

        source_msg = String()
        source_msg.data = self._source
        self.source_pub.publish(source_msg)

    # ── OpenVINS republish ───────────────────────────────────────────────

    def _publish_openvins(self) -> None:
        if self._latest_pose is not None:
            scaled = scale_covariance(
                self._latest_pose, self._covariance_scale
            )
            self.pose_pub.publish(scaled)
            self._broadcast_tf_from_pose(scaled)

        if self._latest_odom is not None:
            odom = scale_odom_covariance(
                self._latest_odom, self._covariance_scale
            )
            self.odom_pub.publish(odom)

    # ── Dead reckoning publish ───────────────────────────────────────────

    def _publish_dead_reckoning(self) -> None:
        now = self.get_clock().now().to_msg()
        pose_msg = build_pose_msg(
            now, self._position, self._orientation_quat
        )
        self.pose_pub.publish(pose_msg)

        odom_msg = build_odom_msg(
            now, self._position, self._orientation_quat, self._velocity
        )
        self.odom_pub.publish(odom_msg)
        self._broadcast_tf_from_pose(pose_msg)

    # ── TF broadcast ─────────────────────────────────────────────────────

    def _broadcast_tf_from_pose(
        self, pose_msg: PoseWithCovarianceStamped
    ) -> None:
        t = TransformStamped()
        t.header.stamp = pose_msg.header.stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link_vio"
        p = pose_msg.pose.pose.position
        o = pose_msg.pose.pose.orientation
        t.transform.translation.x = p.x
        t.transform.translation.y = p.y
        t.transform.translation.z = p.z
        t.transform.rotation.x = o.x
        t.transform.rotation.y = o.y
        t.transform.rotation.z = o.z
        t.transform.rotation.w = o.w
        self.tf_broadcaster.sendTransform(t)

    def destroy_node(self) -> None:
        self.get_logger().info("OpenVINS bridge shutting down")
        super().destroy_node()


# ── Pure functions (testable without ROS2) ───────────────────────────────


def is_stale(
    last_update_time: Optional[float], max_age_sec: float
) -> bool:
    """Return True if the last update is older than max_age_sec."""
    if last_update_time is None:
        return True
    return (time.time() - last_update_time) > max_age_sec


def scale_covariance(
    msg: PoseWithCovarianceStamped, scale: float
) -> PoseWithCovarianceStamped:
    """Return a copy of the pose message with covariance scaled."""
    out = PoseWithCovarianceStamped()
    out.header = msg.header
    out.pose.pose = msg.pose.pose
    cov = list(msg.pose.covariance)
    for i in range(len(cov)):
        cov[i] *= scale
    out.pose.covariance = cov
    return out


def scale_odom_covariance(msg: Odometry, scale: float) -> Odometry:
    """Return a copy of the odometry message with covariances scaled."""
    out = Odometry()
    out.header = msg.header
    out.child_frame_id = msg.child_frame_id
    out.pose.pose = msg.pose.pose
    out.twist.twist = msg.twist.twist

    pose_cov = list(msg.pose.covariance)
    twist_cov = list(msg.twist.covariance)
    for i in range(len(pose_cov)):
        pose_cov[i] *= scale
        twist_cov[i] *= scale
    out.pose.covariance = pose_cov
    out.twist.covariance = twist_cov
    return out


def build_pose_msg(
    stamp: object,
    position: np.ndarray,
    orientation: np.ndarray,
) -> PoseWithCovarianceStamped:
    """Build a PoseWithCovarianceStamped for dead reckoning output."""
    msg = PoseWithCovarianceStamped()
    msg.header.stamp = stamp
    msg.header.frame_id = "odom"
    msg.pose.pose.position.x = float(position[0])
    msg.pose.pose.position.y = float(position[1])
    msg.pose.pose.position.z = float(position[2])
    msg.pose.pose.orientation.x = float(orientation[0])
    msg.pose.pose.orientation.y = float(orientation[1])
    msg.pose.pose.orientation.z = float(orientation[2])
    msg.pose.pose.orientation.w = float(orientation[3])
    cov = [0.0] * 36
    cov[0] = 0.1
    cov[7] = 0.1
    cov[14] = 0.1
    cov[35] = 0.05
    msg.pose.covariance = cov
    return msg


def build_odom_msg(
    stamp: object,
    position: np.ndarray,
    orientation: np.ndarray,
    velocity: np.ndarray,
) -> Odometry:
    """Build an Odometry message for dead reckoning output."""
    msg = Odometry()
    msg.header.stamp = stamp
    msg.header.frame_id = "odom"
    msg.child_frame_id = "base_link"
    msg.pose.pose.position.x = float(position[0])
    msg.pose.pose.position.y = float(position[1])
    msg.pose.pose.position.z = float(position[2])
    msg.pose.pose.orientation.x = float(orientation[0])
    msg.pose.pose.orientation.y = float(orientation[1])
    msg.pose.pose.orientation.z = float(orientation[2])
    msg.pose.pose.orientation.w = float(orientation[3])
    msg.twist.twist.linear.x = float(velocity[0])
    msg.twist.twist.linear.y = float(velocity[1])
    msg.twist.twist.linear.z = float(velocity[2])
    return msg


def rotate_vector_by_quaternion(
    v: np.ndarray, q: np.ndarray
) -> np.ndarray:
    """Rotate a 3D vector by a quaternion (x, y, z, w format)."""
    qx, qy, qz, qw = q
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


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = OpenVINSBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
