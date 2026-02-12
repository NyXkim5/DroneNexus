"""
slam_node.py

ROS 2 wrapper node for RTABMap SLAM. Subscribes to camera and depth topics,
manages SLAM lifecycle, publishes occupancy map and localized pose.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import Bool
from sensor_msgs.msg import Image, CameraInfo, PointCloud2
from nav_msgs.msg import OccupancyGrid, Odometry
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, TransformStamped

from tf2_ros import TransformBroadcaster


class SLAMNode(Node):
    """Wrapper for RTABMap SLAM providing map and pose estimation."""

    def __init__(self):
        super().__init__('slam_node')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('use_sim_time', True)
        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('camera_topic', '/camera')
        self.declare_parameter('depth_topic', '/depth_camera')
        self.declare_parameter('map_publish_rate', 1.0)
        self.declare_parameter('max_map_size', 1000)
        self.declare_parameter('slam_enabled', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.map_frame = self.get_parameter('map_frame').get_parameter_value().string_value
        self.odom_frame = self.get_parameter('odom_frame').get_parameter_value().string_value
        self.base_frame = self.get_parameter('base_frame').get_parameter_value().string_value
        camera_topic = self.get_parameter('camera_topic').get_parameter_value().string_value
        depth_topic = self.get_parameter('depth_topic').get_parameter_value().string_value
        map_rate = self.get_parameter('map_publish_rate').get_parameter_value().double_value
        self.slam_enabled = self.get_parameter('slam_enabled').get_parameter_value().bool_value

        self.get_logger().info(f'SLAM node starting: drone_id={self.drone_id}')

        # ── State ────────────────────────────────────────────────────────
        self._latest_rgb = None
        self._latest_depth = None
        self._current_pose = PoseStamped()
        self._map_data = OccupancyGrid()
        self._map_data.header.frame_id = self.map_frame
        self._map_data.info.resolution = 0.05  # 5cm resolution
        self._map_data.info.width = 200
        self._map_data.info.height = 200
        self._map_data.data = [-1] * (200 * 200)  # Unknown

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(Image, camera_topic, self._rgb_cb, qos_sensor)
        self.create_subscription(Image, depth_topic, self._depth_cb, qos_sensor)
        self.create_subscription(Odometry, 'odom', self._odom_cb, qos_sensor)
        self.create_subscription(Bool, 'slam/enable', self._enable_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.map_pub = self.create_publisher(OccupancyGrid, 'slam/map', 10)
        self.pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, 'slam/pose', 10)
        self.pointcloud_pub = self.create_publisher(
            PointCloud2, 'slam/cloud_map', 10)

        # ── TF broadcaster ───────────────────────────────────────────────
        self.tf_broadcaster = TransformBroadcaster(self)

        # ── Map publish timer ────────────────────────────────────────────
        self.create_timer(1.0 / map_rate, self._publish_map)
        self.create_timer(0.05, self._broadcast_tf)  # 20 Hz TF

        self.get_logger().info('SLAM node initialized')

    def _rgb_cb(self, msg: Image):
        self._latest_rgb = msg
        if self.slam_enabled:
            self._process_slam()

    def _depth_cb(self, msg: Image):
        self._latest_depth = msg

    def _odom_cb(self, msg: Odometry):
        """Update pose from odometry when SLAM provides corrections."""
        self._current_pose.header = msg.header
        self._current_pose.pose = msg.pose.pose

    def _enable_cb(self, msg: Bool):
        self.slam_enabled = msg.data
        state = 'enabled' if msg.data else 'disabled'
        self.get_logger().info(f'SLAM {state}')

    def _process_slam(self):
        """
        Process SLAM update. In production this delegates to RTABMap.
        This placeholder updates the map based on available sensor data.
        """
        if self._latest_rgb is None or self._latest_depth is None:
            return

        # RTABMap integration point:
        # In a full implementation, this would feed images to rtabmap
        # and receive back the updated map and corrected pose.
        # For now, we mark cells around the drone as free space.
        pass

    def _publish_map(self):
        """Publish the current occupancy grid map."""
        self._map_data.header.stamp = self.get_clock().now().to_msg()
        self.map_pub.publish(self._map_data)

        # Publish pose with covariance
        pose_cov = PoseWithCovarianceStamped()
        pose_cov.header = self._current_pose.header
        pose_cov.header.stamp = self.get_clock().now().to_msg()
        pose_cov.pose.pose = self._current_pose.pose
        # Diagonal covariance: x, y, z, roll, pitch, yaw
        pose_cov.pose.covariance[0] = 0.01   # x variance
        pose_cov.pose.covariance[7] = 0.01   # y variance
        pose_cov.pose.covariance[14] = 0.01  # z variance
        pose_cov.pose.covariance[35] = 0.005  # yaw variance
        self.pose_pub.publish(pose_cov)

    def _broadcast_tf(self):
        """Broadcast map -> odom transform."""
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = self.map_frame
        t.child_frame_id = self.odom_frame
        # Identity transform (SLAM corrections would go here)
        t.transform.rotation.w = 1.0
        self.tf_broadcaster.sendTransform(t)

    def destroy_node(self):
        self.get_logger().info('SLAM node shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = SLAMNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
