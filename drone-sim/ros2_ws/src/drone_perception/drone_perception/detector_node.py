"""
detector_node.py

ROS 2 node for object detection using YOLOv11x.
Subscribes to camera images, runs detection inference,
publishes bounding boxes and class labels.
"""

from typing import List, Tuple

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image
from vision_msgs.msg import (
    Detection2D,
    Detection2DArray,
    ObjectHypothesisWithPose,
)
from std_msgs.msg import String

try:
    from ultralytics import YOLO
    _ULTRALYTICS_AVAILABLE = True
except ImportError:
    _ULTRALYTICS_AVAILABLE = False

try:
    from cv_bridge import CvBridge
    _CV_BRIDGE_AVAILABLE = True
except ImportError:
    _CV_BRIDGE_AVAILABLE = False


class DetectorNode(Node):
    """YOLO-based object detection node."""

    def __init__(self):
        super().__init__('detector_node')

        # -- Parameters -------------------------------------------------------
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('model_path', '')
        self.declare_parameter('confidence_threshold', 0.5)
        self.declare_parameter('nms_threshold', 0.4)
        self.declare_parameter('input_size', 640)
        self.declare_parameter('device', 'cpu')  # 'cpu', 'cuda:0'
        self.declare_parameter('class_names_file', '')
        self.declare_parameter('max_detections', 100)
        self.declare_parameter('enabled', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        self.model_path = self.get_parameter('model_path').get_parameter_value().string_value
        self.conf_thresh = self.get_parameter('confidence_threshold').get_parameter_value().double_value
        self.nms_thresh = self.get_parameter('nms_threshold').get_parameter_value().double_value
        self.input_size = self.get_parameter('input_size').get_parameter_value().integer_value
        self.device = self.get_parameter('device').get_parameter_value().string_value
        self.max_detections = self.get_parameter('max_detections').get_parameter_value().integer_value
        self.enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        self.get_logger().info(f'Detector node starting: drone_id={self.drone_id}')

        # -- Model loading -----------------------------------------------------
        self._model = None
        self._model_loaded = False
        self._bridge = None
        self._last_results = None
        self._class_names = [
            'person', 'car', 'truck', 'building', 'tree',
            'drone', 'animal', 'landing_pad', 'obstacle', 'unknown',
        ]

        if self.model_path:
            self._load_model()
        else:
            self.get_logger().warn(
                'No model path specified. Running in placeholder mode.'
            )

        # -- Stats -------------------------------------------------------------
        self._frame_count = 0
        self._detection_count = 0

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # -- Subscriptions -----------------------------------------------------
        self.create_subscription(
            Image, 'perception/image_raw', self._image_cb, qos_sensor)

        # -- Publishers --------------------------------------------------------
        self.detection_pub = self.create_publisher(
            Detection2DArray, 'perception/detections', 10)
        self.annotated_pub = self.create_publisher(
            Image, 'perception/annotated_image', 10)
        self.summary_pub = self.create_publisher(
            String, 'perception/detection_summary', 10)

        # -- Stats timer -------------------------------------------------------
        self.create_timer(10.0, self._publish_stats)

        self.get_logger().info('Detector node initialized')

    def _load_model(self):
        """Load YOLOv11x model from self.model_path."""
        if not _ULTRALYTICS_AVAILABLE:
            self.get_logger().error(
                'ultralytics is not installed. Cannot load YOLO model.'
            )
            return

        if not _CV_BRIDGE_AVAILABLE:
            self.get_logger().error(
                'cv_bridge is not installed. Cannot convert ROS images.'
            )
            return

        try:
            self._model = YOLO(self.model_path)
            self._model.to(self.device)
            self._bridge = CvBridge()
            self._class_names = list(self._model.names.values())
            self._model_loaded = True
            self.get_logger().info(
                f'Model loaded from {self.model_path} on {self.device} '
                f'with {len(self._class_names)} classes'
            )
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {e}')
            self._model_loaded = False

    def _image_cb(self, msg: Image):
        """Run detection on incoming image and publish results."""
        if not self.enabled:
            return

        self._frame_count += 1
        detections = self._detect(msg)

        det_array = self._build_detection_array(msg, detections)
        self.detection_pub.publish(det_array)
        self._detection_count += len(detections)

        self._publish_annotated_image(msg)
        self._publish_summary(detections)

    def _detect(
        self, msg: Image
    ) -> List[Tuple[int, str, float, Tuple[float, float, float, float]]]:
        """
        Run YOLO inference on a ROS Image message.

        Returns list of (class_id, class_name, confidence, (x, y, w, h))
        where x, y is the top-left corner of the bounding box.
        """
        if not self._model_loaded:
            return []

        try:
            img = self._bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'cv_bridge conversion failed: {e}')
            return []

        results = self._model(
            img,
            conf=self.conf_thresh,
            iou=self.nms_thresh,
            imgsz=self.input_size,
            max_det=self.max_detections,
            verbose=False,
        )
        self._last_results = results
        return self._extract_detections(results)

    def _extract_detections(
        self, results: list
    ) -> List[Tuple[int, str, float, Tuple[float, float, float, float]]]:
        """Parse YOLO results into a list of detection tuples."""
        if not results or len(results) == 0:
            return []

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        detections: List[Tuple[int, str, float, Tuple[float, float, float, float]]] = []
        for box in boxes:
            cls_id = int(box.cls.item())
            conf = float(box.conf.item())
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            w = x2 - x1
            h = y2 - y1
            class_name = self._class_names[cls_id] if cls_id < len(self._class_names) else 'unknown'
            detections.append((cls_id, class_name, conf, (x1, y1, w, h)))

        return detections

    def _build_detection_array(
        self, msg: Image, detections: list
    ) -> Detection2DArray:
        """Convert detection tuples into a Detection2DArray message."""
        det_array = Detection2DArray()
        det_array.header = msg.header

        for class_id, _class_name, confidence, bbox in detections:
            det = Detection2D()
            det.header = msg.header
            det.bbox.center.position.x = bbox[0] + bbox[2] / 2.0
            det.bbox.center.position.y = bbox[1] + bbox[3] / 2.0
            det.bbox.size_x = float(bbox[2])
            det.bbox.size_y = float(bbox[3])

            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = str(class_id)
            hyp.hypothesis.score = confidence
            det.results.append(hyp)
            det_array.detections.append(det)

        return det_array

    def _publish_annotated_image(self, msg: Image):
        """Publish annotated image if there are subscribers."""
        if not self._model_loaded or not self._bridge:
            return
        if self.annotated_pub.get_subscription_count() == 0:
            return
        if self._last_results is None or len(self._last_results) == 0:
            return

        try:
            annotated = self._last_results[0].plot()
            ann_msg = self._bridge.cv2_to_imgmsg(annotated, encoding='bgr8')
            ann_msg.header = msg.header
            self.annotated_pub.publish(ann_msg)
        except Exception as e:
            self.get_logger().error(f'Failed to publish annotated image: {e}')

    def _publish_summary(self, detections: list):
        """Publish a human-readable detection summary string."""
        if not detections:
            return
        summary = String()
        counts: dict = {}
        for _, name, _, _ in detections:
            counts[name] = counts.get(name, 0) + 1
        summary.data = ', '.join(f'{name}:{cnt}' for name, cnt in counts.items())
        self.summary_pub.publish(summary)

    def _publish_stats(self):
        """Periodically log detection statistics."""
        if self._frame_count > 0:
            self.get_logger().debug(
                f'Detection stats: {self._frame_count} frames, '
                f'{self._detection_count} total detections'
            )

    def destroy_node(self):
        self.get_logger().info(
            f'Detector shutting down. Processed {self._frame_count} frames, '
            f'{self._detection_count} detections'
        )
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
