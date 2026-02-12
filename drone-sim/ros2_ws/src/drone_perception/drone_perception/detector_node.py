"""
detector_node.py

ROS 2 node for object detection using YOLO (placeholder).
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


class DetectorNode(Node):
    """YOLO-based object detection node (placeholder for model integration)."""

    def __init__(self):
        super().__init__('detector_node')

        # ── Parameters ───────────────────────────────────────────────────
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

        # ── Model loading ────────────────────────────────────────────────
        self._model_loaded = False
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

        # ── Stats ────────────────────────────────────────────────────────
        self._frame_count = 0
        self._detection_count = 0

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            Image, 'perception/image_raw', self._image_cb, qos_sensor)

        # ── Publishers ───────────────────────────────────────────────────
        self.detection_pub = self.create_publisher(
            Detection2DArray, 'perception/detections', 10)
        self.annotated_pub = self.create_publisher(
            Image, 'perception/annotated_image', 10)
        self.summary_pub = self.create_publisher(
            String, 'perception/detection_summary', 10)

        # ── Stats timer ──────────────────────────────────────────────────
        self.create_timer(10.0, self._publish_stats)

        self.get_logger().info('Detector node initialized')

    def _load_model(self):
        """
        Load YOLO model. Placeholder for actual model loading.
        In production:
          - Use ultralytics YOLO, or ONNX runtime, or TensorRT
          - Load weights from self.model_path
          - Move to self.device
        """
        try:
            # Placeholder: model loading would happen here
            # from ultralytics import YOLO
            # self._model = YOLO(self.model_path)
            self._model_loaded = False  # Will be True when real model loads
            self.get_logger().info(f'Model loading from {self.model_path} (placeholder)')
        except Exception as e:
            self.get_logger().error(f'Failed to load model: {e}')
            self._model_loaded = False

    def _image_cb(self, msg: Image):
        """Run detection on incoming image."""
        if not self.enabled:
            return

        self._frame_count += 1

        # Run inference
        detections = self._detect(msg)

        # Publish results
        det_array = Detection2DArray()
        det_array.header = msg.header

        for class_id, class_name, confidence, bbox in detections:
            det = Detection2D()
            det.header = msg.header

            # Bounding box (center + size format)
            det.bbox.center.position.x = bbox[0] + bbox[2] / 2.0
            det.bbox.center.position.y = bbox[1] + bbox[3] / 2.0
            det.bbox.size_x = float(bbox[2])
            det.bbox.size_y = float(bbox[3])

            # Classification result
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = str(class_id)
            hyp.hypothesis.score = confidence
            det.results.append(hyp)

            det_array.detections.append(det)

        self.detection_pub.publish(det_array)
        self._detection_count += len(detections)

        # Publish summary
        if detections:
            summary = String()
            counts = {}
            for _, name, _, _ in detections:
                counts[name] = counts.get(name, 0) + 1
            summary.data = ', '.join(f'{name}:{cnt}' for name, cnt in counts.items())
            self.summary_pub.publish(summary)

    def _detect(
        self, msg: Image
    ) -> List[Tuple[int, str, float, Tuple[float, float, float, float]]]:
        """
        Run detection inference.
        Returns: list of (class_id, class_name, confidence, (x, y, w, h))

        Placeholder: returns empty list. Real implementation would:
        1. Convert ROS Image to numpy/tensor
        2. Preprocess (resize, normalize)
        3. Run model forward pass
        4. Apply NMS
        5. Return filtered detections
        """
        if not self._model_loaded:
            return []

        # Real inference would go here:
        # img = self._bridge.imgmsg_to_cv2(msg, 'bgr8')
        # results = self._model(img, conf=self.conf_thresh)
        # ...

        return []

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
