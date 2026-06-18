"""
Unit tests for DetectorNode detection logic.

Tests focus on _extract_detections, _build_detection_array,
and _publish_summary since those are pure logic functions.
The YOLO model and ROS infrastructure are mocked.
"""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

import numpy as np


def _make_mock_box(cls_id: int, conf: float, x1: float, y1: float, x2: float, y2: float):
    """Create a mock YOLO box object."""
    box = MagicMock()
    box.cls = MagicMock()
    box.cls.item.return_value = cls_id
    box.conf = MagicMock()
    box.conf.item.return_value = conf
    box.xyxy = [np.array([x1, y1, x2, y2])]
    return box


def _make_mock_results(boxes_data: list):
    """
    Create a mock ultralytics Results object.

    boxes_data: list of (cls_id, conf, x1, y1, x2, y2)
    """
    if boxes_data is None:
        result = MagicMock()
        result.boxes = None
        return [result]

    boxes = [_make_mock_box(*b) for b in boxes_data]
    mock_boxes = MagicMock()
    mock_boxes.__iter__ = MagicMock(return_value=iter(boxes))
    mock_boxes.__len__ = MagicMock(return_value=len(boxes))
    result = MagicMock()
    result.boxes = mock_boxes
    return [result]


class TestExtractDetections(unittest.TestCase):
    """Test _extract_detections without ROS dependencies."""

    def setUp(self):
        """Create a minimal DetectorNode-like object for testing."""
        self.node = SimpleNamespace(
            _class_names=['person', 'car', 'truck', 'building', 'tree'],
        )

    def _call(self, results):
        from drone_perception.detector_node import DetectorNode
        return DetectorNode._extract_detections(self.node, results)

    def test_empty_results_list(self):
        detections = self._call([])
        self.assertEqual(detections, [])

    def test_none_results(self):
        detections = self._call(None)
        self.assertEqual(detections, [])

    def test_none_boxes(self):
        results = _make_mock_results(None)
        detections = self._call(results)
        self.assertEqual(detections, [])

    def test_no_boxes(self):
        results = _make_mock_results([])
        detections = self._call(results)
        self.assertEqual(detections, [])

    def test_single_detection(self):
        results = _make_mock_results([
            (0, 0.95, 10.0, 20.0, 110.0, 120.0),
        ])
        detections = self._call(results)
        self.assertEqual(len(detections), 1)
        cls_id, name, conf, bbox = detections[0]
        self.assertEqual(cls_id, 0)
        self.assertEqual(name, 'person')
        self.assertAlmostEqual(conf, 0.95)
        self.assertAlmostEqual(bbox[0], 10.0)
        self.assertAlmostEqual(bbox[1], 20.0)
        self.assertAlmostEqual(bbox[2], 100.0)  # w = 110 - 10
        self.assertAlmostEqual(bbox[3], 100.0)  # h = 120 - 20

    def test_multiple_detections(self):
        results = _make_mock_results([
            (0, 0.9, 0.0, 0.0, 50.0, 50.0),
            (1, 0.8, 100.0, 100.0, 200.0, 200.0),
        ])
        detections = self._call(results)
        self.assertEqual(len(detections), 2)
        self.assertEqual(detections[0][1], 'person')
        self.assertEqual(detections[1][1], 'car')

    def test_unknown_class_id(self):
        results = _make_mock_results([
            (999, 0.7, 0.0, 0.0, 10.0, 10.0),
        ])
        detections = self._call(results)
        self.assertEqual(detections[0][1], 'unknown')

    def test_bbox_width_height_calculation(self):
        results = _make_mock_results([
            (2, 0.85, 50.0, 30.0, 150.0, 230.0),
        ])
        detections = self._call(results)
        _, _, _, bbox = detections[0]
        self.assertAlmostEqual(bbox[2], 100.0)
        self.assertAlmostEqual(bbox[3], 200.0)


class TestBuildDetectionArray(unittest.TestCase):
    """Test _build_detection_array message construction."""

    def setUp(self):
        self.node = SimpleNamespace()

    def _call(self, msg, detections):
        from drone_perception.detector_node import DetectorNode
        return DetectorNode._build_detection_array(self.node, msg, detections)

    def test_empty_detections(self):
        msg = MagicMock()
        msg.header = MagicMock()
        result = self._call(msg, [])
        self.assertEqual(len(result.detections), 0)

    def test_detection_center_calculation(self):
        msg = MagicMock()
        msg.header = MagicMock()
        detections = [
            (0, 'person', 0.9, (100.0, 200.0, 50.0, 80.0)),
        ]
        result = self._call(msg, detections)
        det = result.detections[0]
        self.assertAlmostEqual(det.bbox.center.position.x, 125.0)
        self.assertAlmostEqual(det.bbox.center.position.y, 240.0)
        self.assertAlmostEqual(det.bbox.size_x, 50.0)
        self.assertAlmostEqual(det.bbox.size_y, 80.0)

    def test_hypothesis_class_id_is_string(self):
        msg = MagicMock()
        msg.header = MagicMock()
        detections = [(3, 'building', 0.75, (0.0, 0.0, 10.0, 10.0))]
        result = self._call(msg, detections)
        hyp = result.detections[0].results[0]
        self.assertEqual(hyp.hypothesis.class_id, '3')
        self.assertAlmostEqual(hyp.hypothesis.score, 0.75)


class TestPublishSummary(unittest.TestCase):
    """Test _publish_summary counts logic."""

    def setUp(self):
        self.mock_pub = MagicMock()
        self.node = SimpleNamespace(summary_pub=self.mock_pub)

    def _call(self, detections):
        from drone_perception.detector_node import DetectorNode
        DetectorNode._publish_summary(self.node, detections)

    def test_empty_detections_no_publish(self):
        self._call([])
        self.mock_pub.publish.assert_not_called()

    def test_single_class_summary(self):
        dets = [
            (0, 'person', 0.9, (0, 0, 10, 10)),
            (0, 'person', 0.8, (20, 20, 10, 10)),
        ]
        self._call(dets)
        published = self.mock_pub.publish.call_args[0][0]
        self.assertEqual(published.data, 'person:2')

    def test_multiple_classes_summary(self):
        dets = [
            (0, 'person', 0.9, (0, 0, 10, 10)),
            (1, 'car', 0.8, (20, 20, 10, 10)),
            (0, 'person', 0.7, (40, 40, 10, 10)),
        ]
        self._call(dets)
        published = self.mock_pub.publish.call_args[0][0]
        self.assertIn('person:2', published.data)
        self.assertIn('car:1', published.data)


class TestLoadModelGracefulDegradation(unittest.TestCase):
    """Test that missing ultralytics or cv_bridge is handled gracefully."""

    def test_module_level_flags_exist(self):
        import drone_perception.detector_node as mod
        self.assertIsInstance(mod._ULTRALYTICS_AVAILABLE, bool)
        self.assertIsInstance(mod._CV_BRIDGE_AVAILABLE, bool)


class TestDetectWithoutModel(unittest.TestCase):
    """Test _detect returns empty when model is not loaded."""

    def test_returns_empty_when_not_loaded(self):
        node = SimpleNamespace(_model_loaded=False)
        from drone_perception.detector_node import DetectorNode
        result = DetectorNode._detect(node, MagicMock())
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
