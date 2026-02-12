"""
camera_node.py

ROS 2 node that subscribes to Gazebo camera topics and republishes
processed images to the NEXUS drone perception pipeline.
Handles image format conversion and camera info publishing.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image, CameraInfo, CompressedImage
from std_msgs.msg import Header


class CameraNode(Node):
    """Camera image publisher and processor for the perception pipeline."""

    def __init__(self):
        super().__init__('camera_node')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('camera_topic', '/camera')
        self.declare_parameter('depth_topic', '/depth_camera')
        self.declare_parameter('image_width', 640)
        self.declare_parameter('image_height', 480)
        self.declare_parameter('publish_rate', 30.0)
        self.declare_parameter('fov_horizontal_deg', 90.0)
        self.declare_parameter('publish_camera_info', True)
        self.declare_parameter('publish_compressed', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        camera_topic = self.get_parameter('camera_topic').get_parameter_value().string_value
        depth_topic = self.get_parameter('depth_topic').get_parameter_value().string_value
        self.image_width = self.get_parameter('image_width').get_parameter_value().integer_value
        self.image_height = self.get_parameter('image_height').get_parameter_value().integer_value
        fov_h = self.get_parameter('fov_horizontal_deg').get_parameter_value().double_value
        self.publish_info = self.get_parameter('publish_camera_info').get_parameter_value().bool_value
        self.publish_compressed = self.get_parameter('publish_compressed').get_parameter_value().bool_value

        self.get_logger().info(
            f'Camera node starting: drone_id={self.drone_id}, '
            f'resolution={self.image_width}x{self.image_height}'
        )

        # ── Compute camera intrinsics ────────────────────────────────────
        import math
        fx = self.image_width / (2.0 * math.tan(math.radians(fov_h / 2.0)))
        fy = fx  # Square pixels
        cx = self.image_width / 2.0
        cy = self.image_height / 2.0

        self._camera_info = CameraInfo()
        self._camera_info.width = self.image_width
        self._camera_info.height = self.image_height
        self._camera_info.distortion_model = 'plumb_bob'
        self._camera_info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        self._camera_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self._camera_info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        self._camera_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]

        # ── Frame counter ────────────────────────────────────────────────
        self._frame_count = 0

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions (from Gazebo bridge) ───────────────────────────
        self.create_subscription(Image, camera_topic, self._rgb_cb, qos_sensor)
        self.create_subscription(Image, depth_topic, self._depth_cb, qos_sensor)

        # ── Publishers ───────────────────────────────────────────────────
        self.rgb_pub = self.create_publisher(Image, 'perception/image_raw', 10)
        self.depth_pub = self.create_publisher(Image, 'perception/depth_raw', 10)
        self.info_pub = self.create_publisher(CameraInfo, 'perception/camera_info', 10)

        if self.publish_compressed:
            self.compressed_pub = self.create_publisher(
                CompressedImage, 'perception/image_compressed', 10)

        self.get_logger().info('Camera node initialized')

    def _rgb_cb(self, msg: Image):
        """Process and republish RGB image."""
        self._frame_count += 1

        # Update header
        msg.header.frame_id = f'{self.drone_id}/camera_optical_frame'

        # Republish
        self.rgb_pub.publish(msg)

        # Publish camera info
        if self.publish_info:
            self._camera_info.header = msg.header
            self.info_pub.publish(self._camera_info)

        if self._frame_count % 300 == 0:
            self.get_logger().debug(f'Published {self._frame_count} frames')

    def _depth_cb(self, msg: Image):
        """Process and republish depth image."""
        msg.header.frame_id = f'{self.drone_id}/camera_optical_frame'
        self.depth_pub.publish(msg)

    def destroy_node(self):
        self.get_logger().info(
            f'Camera node shutting down. Total frames: {self._frame_count}'
        )
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
