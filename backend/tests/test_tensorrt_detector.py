"""
Tests for the TensorRT detector adapter.

Covers the Detection dataclass, backend selection logic, factory fallback
behavior, and mocked TensorRT/Jetson backends.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from vision.tensorrt_detector import (
    COCO_NAMES,
    Detection,
    DetectorBackend,
    JetsonBackend,
    TensorRTBackend,
    UltralyticsBackend,
    _nms,
    _parse_ultralytics_results,
    _postprocess_yolo,
    _preprocess_frame,
    create_detector,
)


# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


class TestDetection:
    def test_fields(self) -> None:
        d = Detection(
            class_name="drone",
            confidence=0.92,
            bbox=(10.0, 20.0, 110.0, 120.0),
            class_id=5,
        )
        assert d.class_name == "drone"
        assert d.confidence == 0.92
        assert d.bbox == (10.0, 20.0, 110.0, 120.0)
        assert d.class_id == 5

    def test_default_class_id(self) -> None:
        d = Detection(class_name="person", confidence=0.5, bbox=(0, 0, 1, 1))
        assert d.class_id == 0

    def test_equality(self) -> None:
        a = Detection("car", 0.8, (1, 2, 3, 4), class_id=2)
        b = Detection("car", 0.8, (1, 2, 3, 4), class_id=2)
        assert a == b

    def test_inequality(self) -> None:
        a = Detection("car", 0.8, (1, 2, 3, 4), class_id=2)
        b = Detection("truck", 0.8, (1, 2, 3, 4), class_id=7)
        assert a != b


# ---------------------------------------------------------------------------
# DetectorBackend ABC
# ---------------------------------------------------------------------------


class TestDetectorBackendABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            DetectorBackend()  # type: ignore[abstract]

    def test_subclass_name(self) -> None:
        class Dummy(DetectorBackend):
            def detect(self, frame: np.ndarray):
                return []

            def warmup(self) -> None:
                pass

        d = Dummy()
        assert d.name == "Dummy"


# ---------------------------------------------------------------------------
# NMS helper
# ---------------------------------------------------------------------------


class TestNMS:
    def test_empty(self) -> None:
        boxes = np.zeros((0, 4), dtype=np.float32)
        scores = np.zeros(0, dtype=np.float32)
        assert _nms(boxes, scores, 0.5) == []

    def test_single_box(self) -> None:
        boxes = np.array([[10, 10, 50, 50]], dtype=np.float32)
        scores = np.array([0.9], dtype=np.float32)
        assert _nms(boxes, scores, 0.5) == [0]

    def test_overlapping_boxes_suppressed(self) -> None:
        boxes = np.array([
            [10, 10, 50, 50],
            [12, 12, 52, 52],
        ], dtype=np.float32)
        scores = np.array([0.9, 0.8], dtype=np.float32)
        keep = _nms(boxes, scores, 0.3)
        assert len(keep) == 1
        assert keep[0] == 0  # higher score kept

    def test_non_overlapping_boxes_kept(self) -> None:
        boxes = np.array([
            [0, 0, 10, 10],
            [100, 100, 110, 110],
        ], dtype=np.float32)
        scores = np.array([0.9, 0.8], dtype=np.float32)
        keep = _nms(boxes, scores, 0.5)
        assert len(keep) == 2


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


class TestPreprocess:
    def test_output_shape(self) -> None:
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        result = _preprocess_frame(frame, (640, 640))
        assert result.shape == (1, 3, 640, 640)
        assert result.dtype == np.float32

    def test_normalized_range(self) -> None:
        frame = np.ones((100, 100, 3), dtype=np.uint8) * 255
        result = _preprocess_frame(frame, (100, 100))
        assert result.max() <= 1.0
        assert result.min() >= 0.0


# ---------------------------------------------------------------------------
# Postprocessing
# ---------------------------------------------------------------------------


class TestPostprocess:
    def test_no_detections_below_threshold(self) -> None:
        # 2 classes, 3 boxes, all low-confidence
        num_classes = 2
        num_boxes = 3
        raw = np.zeros((4 + num_classes, num_boxes), dtype=np.float32)
        raw[0, :] = [50, 100, 200]  # cx
        raw[1, :] = [50, 100, 200]  # cy
        raw[2, :] = [20, 20, 20]    # w
        raw[3, :] = [20, 20, 20]    # h
        raw[4, :] = [0.1, 0.1, 0.1]  # class 0 scores
        raw[5, :] = [0.2, 0.2, 0.2]  # class 1 scores

        class_names = ["cat", "dog"]
        dets = _postprocess_yolo(
            [raw], (480, 640), (640, 640), 0.5, 0.45, class_names,
        )
        assert len(dets) == 0

    def test_detections_above_threshold(self) -> None:
        num_classes = 2
        num_boxes = 2
        raw = np.zeros((4 + num_classes, num_boxes), dtype=np.float32)
        raw[0, :] = [100, 300]  # cx
        raw[1, :] = [100, 300]  # cy
        raw[2, :] = [50, 50]    # w
        raw[3, :] = [50, 50]    # h
        raw[4, :] = [0.9, 0.1]  # class 0 scores
        raw[5, :] = [0.1, 0.85]  # class 1 scores

        class_names = ["cat", "dog"]
        dets = _postprocess_yolo(
            [raw], (640, 640), (640, 640), 0.4, 0.45, class_names,
        )
        assert len(dets) == 2
        names = {d.class_name for d in dets}
        assert names == {"cat", "dog"}


# ---------------------------------------------------------------------------
# UltralyticsBackend
# ---------------------------------------------------------------------------


class TestUltralyticsBackend:
    def test_parse_results_empty(self) -> None:
        mock_result = MagicMock()
        mock_result.boxes = None
        assert _parse_ultralytics_results([mock_result]) == []

    def test_parse_results_with_boxes(self) -> None:
        box = MagicMock()
        box.cls = [0]
        box.conf = [0.87]
        box.xyxy = [MagicMock()]
        box.xyxy[0].tolist.return_value = [10.0, 20.0, 100.0, 200.0]

        result = MagicMock()
        result.boxes = [box]
        result.names = {0: "person"}

        dets = _parse_ultralytics_results([result])
        assert len(dets) == 1
        assert dets[0].class_name == "person"
        assert dets[0].confidence == 0.87
        assert dets[0].bbox == (10.0, 20.0, 100.0, 200.0)
        assert dets[0].class_id == 0

    @patch("vision.tensorrt_detector.YOLO")
    def test_ultralytics_backend_init(self, mock_yolo_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model
        backend = UltralyticsBackend("fake_model.pt", device="cpu")
        assert backend.name == "UltralyticsBackend"
        mock_yolo_class.assert_called_once_with("fake_model.pt")

    @patch("vision.tensorrt_detector.YOLO")
    def test_ultralytics_detect(self, mock_yolo_class: MagicMock) -> None:
        box = MagicMock()
        box.cls = [2]
        box.conf = [0.75]
        box.xyxy = [MagicMock()]
        box.xyxy[0].tolist.return_value = [5.0, 5.0, 50.0, 50.0]

        result = MagicMock()
        result.boxes = [box]
        result.names = {2: "car"}

        mock_model = MagicMock()
        mock_model.return_value = [result]
        mock_yolo_class.return_value = mock_model

        backend = UltralyticsBackend("fake.pt")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        dets = backend.detect(frame)
        assert len(dets) == 1
        assert dets[0].class_name == "car"

    @patch("vision.tensorrt_detector.YOLO")
    def test_ultralytics_warmup(self, mock_yolo_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.return_value = []
        mock_yolo_class.return_value = mock_model
        backend = UltralyticsBackend("fake.pt")
        backend.warmup()
        assert mock_model.call_count == 1  # warmup runs detect once


# ---------------------------------------------------------------------------
# TensorRTBackend (mocked)
# ---------------------------------------------------------------------------


class TestTensorRTBackendMocked:
    def test_tensorrt_unavailable_raises(self) -> None:
        with patch("vision.tensorrt_detector._TRT_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="tensorrt"):
                TensorRTBackend("fake.engine")

    def test_cuda_unavailable_raises(self) -> None:
        with patch("vision.tensorrt_detector._TRT_AVAILABLE", True), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="pycuda"):
                TensorRTBackend("fake.engine")


# ---------------------------------------------------------------------------
# JetsonBackend (mocked)
# ---------------------------------------------------------------------------


class TestJetsonBackendMocked:
    def test_jetson_unavailable_raises(self) -> None:
        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="jetson"):
                JetsonBackend()

    def test_jetson_detect_converts_format(self) -> None:
        mock_jinf = MagicMock()
        mock_jutils = MagicMock()

        mock_det = MagicMock()
        mock_det.ClassID = 1
        mock_det.Confidence = 0.88
        mock_det.Left = 10.0
        mock_det.Top = 20.0
        mock_det.Right = 100.0
        mock_det.Bottom = 200.0

        mock_net = MagicMock()
        mock_net.Detect.return_value = [mock_det]
        mock_jinf.detectNet.return_value = mock_net

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf), \
             patch("vision.tensorrt_detector.jutils", mock_jutils):
            backend = JetsonBackend(model="ssd-mobilenet-v2", conf=0.3)
            frame = np.zeros((300, 300, 3), dtype=np.uint8)
            dets = backend.detect(frame)

        assert len(dets) == 1
        assert dets[0].class_id == 1
        assert dets[0].confidence == 0.88
        assert dets[0].bbox == (10.0, 20.0, 100.0, 200.0)


# ---------------------------------------------------------------------------
# Factory create_detector
# ---------------------------------------------------------------------------


class TestCreateDetector:
    @patch("vision.tensorrt_detector.YOLO")
    def test_auto_falls_back_to_ultralytics(
        self, mock_yolo_class: MagicMock,
    ) -> None:
        """On a system without TRT or Jetson, auto should pick Ultralytics."""
        mock_yolo_class.return_value = MagicMock()
        with patch("vision.tensorrt_detector._TRT_AVAILABLE", False), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", False), \
             patch("vision.tensorrt_detector._JETSON_AVAILABLE", False):
            backend = create_detector("fake.pt", prefer="auto")
        assert isinstance(backend, UltralyticsBackend)

    @patch("vision.tensorrt_detector.YOLO")
    def test_prefer_ultralytics(self, mock_yolo_class: MagicMock) -> None:
        mock_yolo_class.return_value = MagicMock()
        backend = create_detector("fake.pt", prefer="ultralytics")
        assert isinstance(backend, UltralyticsBackend)

    @patch("vision.tensorrt_detector.YOLO")
    def test_prefer_tensorrt_falls_back(
        self, mock_yolo_class: MagicMock,
    ) -> None:
        """When TRT is requested but unavailable, fall back to Ultralytics."""
        mock_yolo_class.return_value = MagicMock()
        with patch("vision.tensorrt_detector._TRT_AVAILABLE", False), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", False):
            backend = create_detector("fake.pt", prefer="tensorrt")
        assert isinstance(backend, UltralyticsBackend)

    @patch("vision.tensorrt_detector.YOLO")
    def test_prefer_jetson_falls_back(
        self, mock_yolo_class: MagicMock,
    ) -> None:
        """When Jetson is requested but unavailable, fall back to Ultralytics."""
        mock_yolo_class.return_value = MagicMock()
        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", False):
            backend = create_detector("fake.pt", prefer="jetson")
        assert isinstance(backend, UltralyticsBackend)

    @patch("vision.tensorrt_detector.YOLO")
    def test_auto_prefers_jetson_over_ultralytics(
        self, mock_yolo_class: MagicMock,
    ) -> None:
        """When Jetson is available, auto should pick it over Ultralytics."""
        mock_jinf = MagicMock()
        mock_net = MagicMock()
        mock_jinf.detectNet.return_value = mock_net

        mock_yolo_class.return_value = MagicMock()
        with patch("vision.tensorrt_detector._TRT_AVAILABLE", False), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", False), \
             patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf):
            backend = create_detector("ssd-mobilenet-v2", prefer="auto")
        assert isinstance(backend, JetsonBackend)


# ---------------------------------------------------------------------------
# COCO names sanity check
# ---------------------------------------------------------------------------


class TestCocoNames:
    def test_has_80_classes(self) -> None:
        assert len(COCO_NAMES) == 80

    def test_first_class_is_person(self) -> None:
        assert COCO_NAMES[0] == "person"

    def test_last_class_is_toothbrush(self) -> None:
        assert COCO_NAMES[-1] == "toothbrush"
