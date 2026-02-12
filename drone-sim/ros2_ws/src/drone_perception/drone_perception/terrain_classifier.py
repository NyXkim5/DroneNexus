"""
terrain_classifier.py

ROS 2 node for terrain type classification from aerial camera images.
Placeholder for a CNN-based terrain classifier that identifies:
  - Urban, Rural, Forest, Water, Road, Building, Grass, Sand, etc.
Used for adaptive flight behavior and landing zone assessment.
"""

from enum import Enum
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import Image
from std_msgs.msg import String, Float32MultiArray


class TerrainType(Enum):
    """Terrain classification categories."""
    UNKNOWN = 'unknown'
    URBAN = 'urban'
    RURAL = 'rural'
    FOREST = 'forest'
    WATER = 'water'
    ROAD = 'road'
    BUILDING = 'building'
    GRASS = 'grass'
    SAND = 'sand'
    FARMLAND = 'farmland'
    CONCRETE = 'concrete'
    GRAVEL = 'gravel'


class TerrainClassifierNode(Node):
    """
    Terrain classification node for adaptive flight and landing assessment.
    Uses aerial imagery to classify terrain type below the drone.
    """

    def __init__(self):
        super().__init__('terrain_classifier')

        # ── Parameters ───────────────────────────────────────────────────
        self.declare_parameter('drone_id', 'drone_0')
        self.declare_parameter('classify_rate', 1.0)
        self.declare_parameter('model_path', '')
        self.declare_parameter('confidence_threshold', 0.6)
        self.declare_parameter('safe_landing_types',
                               ['grass', 'concrete', 'sand', 'gravel', 'farmland'])
        self.declare_parameter('enabled', True)

        self.drone_id = self.get_parameter('drone_id').get_parameter_value().string_value
        classify_rate = self.get_parameter('classify_rate').get_parameter_value().double_value
        self.model_path = self.get_parameter('model_path').get_parameter_value().string_value
        self.conf_threshold = self.get_parameter('confidence_threshold').get_parameter_value().double_value
        self.safe_types = self.get_parameter('safe_landing_types').get_parameter_value().string_array_value
        self.enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        self.get_logger().info(
            f'Terrain classifier starting: drone_id={self.drone_id}, '
            f'rate={classify_rate}Hz'
        )

        # ── Model ────────────────────────────────────────────────────────
        self._model_loaded = False
        self._latest_image: Optional[Image] = None
        self._current_terrain = TerrainType.UNKNOWN
        self._current_confidence = 0.0
        self._terrain_probabilities = {t: 0.0 for t in TerrainType}

        if self.model_path:
            self._load_model()
        else:
            self.get_logger().warn(
                'No model path specified. Running in placeholder mode.'
            )

        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,  # Only keep latest
        )

        # ── Subscriptions ────────────────────────────────────────────────
        self.create_subscription(
            Image, 'perception/image_raw', self._image_cb, qos_sensor)

        # ── Publishers ───────────────────────────────────────────────────
        self.terrain_pub = self.create_publisher(
            String, 'perception/terrain_type', 10)
        self.prob_pub = self.create_publisher(
            Float32MultiArray, 'perception/terrain_probabilities', 10)
        self.landing_safe_pub = self.create_publisher(
            String, 'perception/landing_safety', 10)

        # ── Classification timer ─────────────────────────────────────────
        self.create_timer(1.0 / classify_rate, self._classify)

        self.get_logger().info('Terrain classifier node initialized')

    def _load_model(self):
        """
        Load terrain classification model.
        Placeholder for actual model loading (e.g., TorchVision, TFLite).
        """
        try:
            # self._model = load_model(self.model_path)
            self._model_loaded = False  # True when real model loads
            self.get_logger().info(f'Model loading from {self.model_path} (placeholder)')
        except Exception as e:
            self.get_logger().error(f'Failed to load terrain model: {e}')

    def _image_cb(self, msg: Image):
        """Buffer the latest image for classification."""
        self._latest_image = msg

    def _classify(self):
        """Run terrain classification on the latest image."""
        if not self.enabled or self._latest_image is None:
            return

        # Run inference (placeholder)
        terrain, confidence, probabilities = self._run_inference(self._latest_image)

        self._current_terrain = terrain
        self._current_confidence = confidence
        self._terrain_probabilities = probabilities

        # Publish terrain type
        terrain_msg = String()
        terrain_msg.data = f'{terrain.value}:{confidence:.2f}'
        self.terrain_pub.publish(terrain_msg)

        # Publish probability distribution
        prob_msg = Float32MultiArray()
        prob_msg.data = [probabilities.get(t, 0.0) for t in TerrainType]
        self.prob_pub.publish(prob_msg)

        # Assess landing safety
        safe_msg = String()
        if terrain.value in self.safe_types and confidence > self.conf_threshold:
            safe_msg.data = f'SAFE:{terrain.value}:{confidence:.2f}'
        elif confidence < self.conf_threshold:
            safe_msg.data = f'UNCERTAIN:{terrain.value}:{confidence:.2f}'
        else:
            safe_msg.data = f'UNSAFE:{terrain.value}:{confidence:.2f}'
        self.landing_safe_pub.publish(safe_msg)

    def _run_inference(self, msg: Image):
        """
        Run terrain classification inference.
        Placeholder: returns UNKNOWN with 0 confidence.

        Real implementation would:
        1. Convert Image to numpy
        2. Preprocess (resize, normalize)
        3. Run model forward pass
        4. Softmax for probabilities
        5. Return top class with confidence
        """
        default_probs = {t: 0.0 for t in TerrainType}
        default_probs[TerrainType.UNKNOWN] = 1.0

        if not self._model_loaded:
            return TerrainType.UNKNOWN, 0.0, default_probs

        # Real inference placeholder
        return TerrainType.UNKNOWN, 0.0, default_probs

    def destroy_node(self):
        self.get_logger().info('Terrain classifier shutting down')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = TerrainClassifierNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
