"""
aruco_detector.py

ROS 2 node for ArUco marker detection, used for precision landing
and indoor positioning. Detects markers in camera images, estimates
their pose relative to the camera, and publishes the results.
"""

import math
from typing import Optional

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import PoseStamped, PoseArray, TransformStamped
from std_msgs.msg import Int32MultiArray

from tf2_ros import TransformBroadcaster


class ArucoDetectorNode(Node):
    """
    ArUco marker detection for precision landing and localization.
    Detects ArUco markers and publishes their 6-DOF pose.
    """

    # Standard ArUco dictionary mappings
    DICT_NAMES = {
        0: 'DICT_4X4_50',
        1: 'DICT_4X4_100',
        2: 'DICT_4X4_250',
        3: 'DICT_4X4_1000',
        4: 'DICT_5X5_50',
        5: 'DICT_5X5_100',
        6: 'DICT_5X5_250',
        7: 'DICT_5X5_1000',
        8: 'DICT_6X6_50',
        9: 'DICT_6X6_100',
        10: 'DICT_6X6_250',
        16: 'DICT_ARUCO_ORIGINAL',
    }

    def __init__(self):
        super().__init__('aruco_detector')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('marker_size_m', 0.15)
        self.declare_parameter('dictionary_id', 0)
        self.declare_parameter('landing_marker_id', 0)
        self.declare_parameter('max_detection_distance_m', 20.0)
        self.declare_parameter('enabled', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.marker_size = self.get_parameter('marker_size_m').get_parameter_value().double_value
        self.dict_id = self.get_parameter('dictionary_id').get_parameter_value().integer_value
        self.landing_id = self.get_parameter('landing_marker_id').get_parameter_value().integer_value
        self.max_dist = self.get_parameter('max_detection_distance_m').get_parameter_value().double_value
        self.enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        dict_name = self.DICT_NAMES.get(self.dict_id, f'UNKNOWN({self.dict_id})')
        self.get_logger().info(
            f'ArUco detector starting: drone_id={self.drone_id}, '
            f'dict={dict_name}, marker_size={self.marker_size}m'
        )

        # ── OpenCV ArUco setup ───────────────────────────────────────────
        self._cv_bridge = None
        self._aruco_dict = None
        self._aruco_params = None
        self._camera_matrix: Optional[np.ndarray] = None
        self._dist_coeffs: Optional[np.ndarray] = None

        try:
            import cv2
            from cv_bridge import CvBridge
            self._cv_bridge = CvBridge()
            self._aruco_dict = cv2.aruco.getPredefinedDictionary(self.dict_id)
            self._aruco_params = cv2.aruco.DetectorParameters()
            self.get_logger().info('OpenCV ArUco initialized')
        except ImportError as e:
            self.get_logger().warn(
                f'OpenCV/cv_bridge not available: {e}. Running in placeholder mode.'
            )

        # ── Stats ────────────────────────────────────────────────────────
        self._detection_count = 0
        self._landing_marker_visible = False

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            Image, 'perception/image_raw', self._image_cb, qos_sensor)
        self.create_subscription(
            CameraInfo, 'perception/camera_info', self._camera_info_cb, 10)

        # ── Publishers ───────────────────────────────────────────────────
        self.marker_poses_pub = self.create_publisher(
            PoseArray, 'perception/aruco/poses', 10)
        self.landing_pose_pub = self.create_publisher(
            PoseStamped, 'perception/aruco/landing_target', 10)
        self.marker_ids_pub = self.create_publisher(
            Int32MultiArray, 'perception/aruco/detected_ids', 10)
        self.annotated_pub = self.create_publisher(
            Image, 'perception/aruco/annotated', 10)

        # ── TF ───────────────────────────────────────────────────────────
        self.tf_broadcaster = TransformBroadcaster(self)

        self.get_logger().info('ArUco detector node initialized')

    def _camera_info_cb(self, msg: CameraInfo):
        """Store camera intrinsics for pose estimation."""
        if self._camera_matrix is None:
            self._camera_matrix = np.array(msg.k).reshape(3, 3)
            self._dist_coeffs = np.array(msg.d)
            self.get_logger().info('Camera intrinsics received')

    def _image_cb(self, msg: Image):
        """Detect ArUco markers in the image."""
        if not self.enabled:
            return

        if self._cv_bridge is None or self._aruco_dict is None:
            return

        if self._camera_matrix is None:
            return

        try:
            import cv2

            # Convert ROS image to OpenCV
            cv_image = self._cv_bridge.imgmsg_to_cv2(msg, 'bgr8')

            # Detect markers
            detector = cv2.aruco.ArucoDetector(self._aruco_dict, self._aruco_params)
            corners, ids, rejected = detector.detectMarkers(cv_image)

            if ids is None or len(ids) == 0:
                self._landing_marker_visible = False
                return

            self._detection_count += len(ids)

            # Publish detected IDs
            id_msg = Int32MultiArray()
            id_msg.data = [int(i) for i in ids.flatten()]
            self.marker_ids_pub.publish(id_msg)

            # Estimate pose for each marker
            pose_array = PoseArray()
            pose_array.header = msg.header

            for i, marker_id in enumerate(ids.flatten()):
                # Estimate pose using solvePnP
                obj_points = np.array([
                    [-self.marker_size / 2, self.marker_size / 2, 0],
                    [self.marker_size / 2, self.marker_size / 2, 0],
                    [self.marker_size / 2, -self.marker_size / 2, 0],
                    [-self.marker_size / 2, -self.marker_size / 2, 0],
                ], dtype=np.float64)

                success, rvec, tvec = cv2.solvePnP(
                    obj_points, corners[i], self._camera_matrix, self._dist_coeffs
                )

                if not success:
                    continue

                # Check distance
                distance = np.linalg.norm(tvec)
                if distance > self.max_dist:
                    continue

                # Convert rotation vector to quaternion
                rmat, _ = cv2.Rodrigues(rvec)
                quat = self._rotation_matrix_to_quaternion(rmat)

                # Create pose
                pose = PoseStamped()
                pose.header = msg.header
                pose.pose.position.x = float(tvec[0])
                pose.pose.position.y = float(tvec[1])
                pose.pose.position.z = float(tvec[2])
                pose.pose.orientation.x = quat[0]
                pose.pose.orientation.y = quat[1]
                pose.pose.orientation.z = quat[2]
                pose.pose.orientation.w = quat[3]

                pose_array.poses.append(pose.pose)

                # If this is the landing marker, publish separately
                if marker_id == self.landing_id:
                    self._landing_marker_visible = True
                    self.landing_pose_pub.publish(pose)

                    # Broadcast TF for landing target
                    t = TransformStamped()
                    t.header = msg.header
                    t.child_frame_id = 'landing_target'
                    t.transform.translation.x = float(tvec[0])
                    t.transform.translation.y = float(tvec[1])
                    t.transform.translation.z = float(tvec[2])
                    t.transform.rotation = pose.pose.orientation
                    self.tf_broadcaster.sendTransform(t)

            self.marker_poses_pub.publish(pose_array)

            # Publish annotated image
            cv2.aruco.drawDetectedMarkers(cv_image, corners, ids)
            annotated_msg = self._cv_bridge.cv2_to_imgmsg(cv_image, 'bgr8')
            annotated_msg.header = msg.header
            self.annotated_pub.publish(annotated_msg)

        except Exception as e:
            self.get_logger().error(f'ArUco detection failed: {e}')

    @staticmethod
    def _rotation_matrix_to_quaternion(R: np.ndarray):
        """Convert 3x3 rotation matrix to quaternion (x, y, z, w)."""
        trace = R[0, 0] + R[1, 1] + R[2, 2]
        if trace > 0:
            s = 0.5 / math.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (R[2, 1] - R[1, 2]) * s
            y = (R[0, 2] - R[2, 0]) * s
            z = (R[1, 0] - R[0, 1]) * s
        elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
            s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
            w = (R[2, 1] - R[1, 2]) / s
            x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s
            z = (R[0, 2] + R[2, 0]) / s
        elif R[1, 1] > R[2, 2]:
            s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
            w = (R[0, 2] - R[2, 0]) / s
            x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s
            z = (R[1, 2] + R[2, 1]) / s
        else:
            s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
            w = (R[1, 0] - R[0, 1]) / s
            x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s
            z = 0.25 * s
        return (x, y, z, w)

    def destroy_node(self):
        self.get_logger().info(
            f'ArUco detector shutting down. Total detections: {self._detection_count}'
        )
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ArucoDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
