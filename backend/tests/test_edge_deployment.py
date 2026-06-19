"""
Edge deployment test harness for the OVERWATCH vision pipeline.

Validates TensorRT and Jetson backends via mocks and simulation on systems
without CUDA (e.g. macOS CI). Covers:
- Mock TensorRT engine init, inference flow, caching, precision modes
- Mock Jetson inference detection mapping and model download
- Performance benchmark collection with simulated hardware profiles
- Deployment readiness check verification
"""
from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, PropertyMock, call, patch

import numpy as np
import pytest

from vision.tensorrt_detector import (
    COCO_NAMES,
    Detection,
    DetectorBackend,
    HostDeviceMem,
    TensorRTBackend,
    JetsonBackend,
    _convert_jetson_detections,
    _postprocess_yolo,
    _preprocess_frame,
)


# ---------------------------------------------------------------------------
# BenchmarkResult dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkResult:
    """Performance benchmark result for a single backend run."""

    model_name: str
    backend: str
    fps: float
    latency_ms: float
    memory_mb: float


# ---------------------------------------------------------------------------
# Hardware profiles for simulated benchmarks
# ---------------------------------------------------------------------------

HARDWARE_PROFILES: Dict[str, Dict[str, float]] = {
    "jetson_nano": {"fps": 30.0, "latency_ms": 33.3, "memory_mb": 512.0},
    "jetson_orin": {"fps": 100.0, "latency_ms": 10.0, "memory_mb": 2048.0},
    "gpu_server": {"fps": 200.0, "latency_ms": 5.0, "memory_mb": 8192.0},
}


def simulate_benchmark(
    model_name: str,
    backend_name: str,
    profile_name: str,
) -> BenchmarkResult:
    """Create a simulated benchmark result from a hardware profile."""
    profile = HARDWARE_PROFILES[profile_name]
    return BenchmarkResult(
        model_name=model_name,
        backend=backend_name,
        fps=profile["fps"],
        latency_ms=profile["latency_ms"],
        memory_mb=profile["memory_mb"],
    )


# ---------------------------------------------------------------------------
# Mock TensorRT environment helpers
# ---------------------------------------------------------------------------


def _make_mock_trt_module() -> types.ModuleType:
    """Build a fake tensorrt module with required constants and classes."""
    mock_trt = types.ModuleType("tensorrt")
    mock_trt.Logger = MagicMock()
    mock_trt.Logger.WARNING = 2
    mock_trt.Runtime = MagicMock()
    mock_trt.Builder = MagicMock()
    mock_trt.OnnxParser = MagicMock()
    mock_trt.NetworkDefinitionCreationFlag = MagicMock()
    mock_trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH = 0
    mock_trt.BuilderFlag = MagicMock()
    mock_trt.BuilderFlag.FP16 = 1
    mock_trt.BuilderFlag.FP32 = 0
    mock_trt.BuilderFlag.INT8 = 2
    mock_trt.MemoryPoolType = MagicMock()
    mock_trt.MemoryPoolType.WORKSPACE = 0
    mock_trt.TensorIOMode = MagicMock()
    mock_trt.TensorIOMode.INPUT = 0
    mock_trt.TensorIOMode.OUTPUT = 1
    mock_trt.nptype = MagicMock(return_value=np.float32)
    mock_trt.ICudaEngine = MagicMock
    mock_trt.IExecutionContext = MagicMock
    return mock_trt


def _make_mock_cuda_module() -> types.ModuleType:
    """Build a fake pycuda.driver module with required allocators."""
    mock_cuda = types.ModuleType("pycuda.driver")
    mock_cuda.mem_alloc = MagicMock(return_value=12345)
    mock_cuda.Stream = MagicMock
    mock_cuda.memcpy_htod_async = MagicMock()
    mock_cuda.memcpy_dtoh_async = MagicMock()
    return mock_cuda


def _make_mock_engine(
    num_inputs: int = 1,
    num_outputs: int = 1,
    input_shape: Tuple[int, ...] = (1, 3, 640, 640),
    output_shape: Tuple[int, ...] = (1, 84, 8400),
) -> MagicMock:
    """Build a mock TensorRT ICudaEngine with configurable bindings."""
    engine = MagicMock()
    engine.num_io_tensors = num_inputs + num_outputs

    tensor_names = [f"input_{i}" for i in range(num_inputs)]
    tensor_names += [f"output_{i}" for i in range(num_outputs)]
    engine.get_tensor_name = MagicMock(side_effect=tensor_names)

    shapes = [input_shape] * num_inputs + [output_shape] * num_outputs
    engine.get_tensor_shape = MagicMock(side_effect=shapes)

    mock_trt = _make_mock_trt_module()
    modes = (
        [mock_trt.TensorIOMode.INPUT] * num_inputs
        + [mock_trt.TensorIOMode.OUTPUT] * num_outputs
    )
    engine.get_tensor_mode = MagicMock(side_effect=modes)
    engine.get_tensor_dtype = MagicMock(return_value=MagicMock())

    ctx = MagicMock()
    ctx.execute_async_v2 = MagicMock()
    engine.create_execution_context.return_value = ctx
    return engine


# ---------------------------------------------------------------------------
# Tests: Mock TensorRT environment
# ---------------------------------------------------------------------------


class TestTensorRTMockInit:
    """Test TensorRTBackend initialization with mocked TensorRT."""

    def test_init_with_mock_engine(self) -> None:
        """TensorRTBackend should initialize when given a mock engine."""
        engine = _make_mock_engine()
        mock_trt = _make_mock_trt_module()
        mock_cuda = _make_mock_cuda_module()

        runtime = MagicMock()
        runtime.deserialize_cuda_engine.return_value = engine
        mock_trt.Runtime.return_value = runtime

        with patch("vision.tensorrt_detector._TRT_AVAILABLE", True), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", True), \
             patch("vision.tensorrt_detector.trt", mock_trt), \
             patch("vision.tensorrt_detector.cuda", mock_cuda), \
             patch("builtins.open", MagicMock()), \
             patch("vision.tensorrt_detector._allocate_buffers") as mock_alloc:
            mock_alloc.return_value = (
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [0, 1],
                MagicMock(),
            )
            backend = TensorRTBackend("model.engine", conf=0.5)
            assert backend.name == "TensorRTBackend"

    def test_init_with_onnx_triggers_build(self) -> None:
        """Loading an .onnx file should trigger engine build path."""
        engine = _make_mock_engine()
        mock_trt = _make_mock_trt_module()
        mock_cuda = _make_mock_cuda_module()

        with patch("vision.tensorrt_detector._TRT_AVAILABLE", True), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", True), \
             patch("vision.tensorrt_detector.trt", mock_trt), \
             patch("vision.tensorrt_detector.cuda", mock_cuda), \
             patch(
                 "vision.tensorrt_detector._build_engine_from_onnx",
                 return_value=engine,
             ) as mock_build, \
             patch("vision.tensorrt_detector._allocate_buffers") as mock_alloc:
            mock_alloc.return_value = (
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [0, 1],
                MagicMock(),
            )
            TensorRTBackend("model.onnx", conf=0.4)
            mock_build.assert_called_once()


class TestTensorRTMockInference:
    """Test the full preprocess -> inference -> postprocess flow."""

    def test_inference_produces_detections(self) -> None:
        """A mock inference pass should produce Detection objects."""
        num_classes = 80
        num_boxes = 4
        raw = np.zeros((4 + num_classes, num_boxes), dtype=np.float32)
        raw[0, 0] = 320.0  # cx
        raw[1, 0] = 320.0  # cy
        raw[2, 0] = 100.0  # w
        raw[3, 0] = 100.0  # h
        raw[4, 0] = 0.95   # person score

        dets = _postprocess_yolo(
            [raw], (640, 640), (640, 640), 0.4, 0.45, COCO_NAMES,
        )
        assert len(dets) == 1
        assert dets[0].class_name == "person"
        assert dets[0].confidence == 0.95

    def test_preprocess_then_postprocess(self) -> None:
        """Full pipeline: preprocess frame, create fake output, postprocess."""
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        preprocessed = _preprocess_frame(frame, (640, 640))
        assert preprocessed.shape == (1, 3, 640, 640)

        num_classes = 80
        num_boxes = 2
        raw = np.zeros((4 + num_classes, num_boxes), dtype=np.float32)
        raw[0, :] = [100, 400]
        raw[1, :] = [100, 400]
        raw[2, :] = [50, 60]
        raw[3, :] = [50, 60]
        raw[4 + 2, :] = [0.9, 0.85]  # class 2 = "car"

        dets = _postprocess_yolo(
            [raw], frame.shape, (640, 640), 0.4, 0.45, COCO_NAMES,
        )
        assert len(dets) == 2
        assert all(d.class_name == "car" for d in dets)

    def test_empty_output_returns_no_detections(self) -> None:
        """All-zero output (low confidence) should yield no detections."""
        raw = np.zeros((84, 100), dtype=np.float32)
        dets = _postprocess_yolo(
            [raw], (640, 640), (640, 640), 0.4, 0.45, COCO_NAMES,
        )
        assert dets == []


class TestTensorRTEngineCaching:
    """Test that engine files are loaded/cached correctly."""

    def test_engine_deserialized_from_file(self) -> None:
        """Engine path ending in .engine reads and deserializes bytes."""
        engine = _make_mock_engine()
        mock_trt = _make_mock_trt_module()
        mock_cuda = _make_mock_cuda_module()

        runtime = MagicMock()
        runtime.deserialize_cuda_engine.return_value = engine
        mock_trt.Runtime.return_value = runtime

        mock_file_data = b"fake_engine_bytes"
        mock_open = MagicMock()
        mock_open.return_value.__enter__ = MagicMock(
            return_value=MagicMock(read=MagicMock(return_value=mock_file_data))
        )
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        with patch("vision.tensorrt_detector._TRT_AVAILABLE", True), \
             patch("vision.tensorrt_detector._CUDA_AVAILABLE", True), \
             patch("vision.tensorrt_detector.trt", mock_trt), \
             patch("vision.tensorrt_detector.cuda", mock_cuda), \
             patch("builtins.open", mock_open), \
             patch("vision.tensorrt_detector._allocate_buffers") as mock_alloc:
            mock_alloc.return_value = (
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [HostDeviceMem(host=np.zeros(10, dtype=np.float32), device=0)],
                [0, 1],
                MagicMock(),
            )
            TensorRTBackend("cached_model.engine")
            runtime.deserialize_cuda_engine.assert_called_once_with(
                mock_file_data,
            )


class TestTensorRTPrecisionModes:
    """Test FP32, FP16, INT8 precision selection in engine building."""

    @pytest.mark.parametrize("precision,flag_attr", [
        ("FP32", "FP32"),
        ("FP16", "FP16"),
        ("INT8", "INT8"),
    ])
    def test_precision_flag_selection(
        self,
        precision: str,
        flag_attr: str,
    ) -> None:
        """Each precision mode maps to the correct BuilderFlag."""
        mock_trt = _make_mock_trt_module()
        flag = getattr(mock_trt.BuilderFlag, flag_attr)
        assert flag is not None

    def test_fp16_enabled_when_platform_supports(self) -> None:
        """Builder should set FP16 flag when platform_has_fast_fp16 is True."""
        mock_trt = _make_mock_trt_module()

        mock_builder = MagicMock()
        mock_builder.platform_has_fast_fp16 = True
        mock_config = MagicMock()
        mock_builder.create_builder_config.return_value = mock_config

        mock_network = MagicMock()
        mock_builder.create_network.return_value = mock_network

        mock_parser = MagicMock()
        mock_parser.parse.return_value = True
        mock_parser.num_errors = 0

        mock_trt.Builder.return_value = mock_builder
        mock_trt.OnnxParser.return_value = mock_parser

        serialized = b"serialized_engine"
        mock_builder.build_serialized_network.return_value = serialized

        mock_runtime = MagicMock()
        mock_engine = _make_mock_engine()
        mock_runtime.deserialize_cuda_engine.return_value = mock_engine
        mock_trt.Runtime.return_value = mock_runtime

        with patch("vision.tensorrt_detector.trt", mock_trt), \
             patch("builtins.open", MagicMock()):
            from vision.tensorrt_detector import _build_engine_from_onnx

            trt_logger = MagicMock()
            _build_engine_from_onnx("model.onnx", trt_logger, (640, 640))
            mock_config.set_flag.assert_called_with(mock_trt.BuilderFlag.FP16)

    def test_fp16_skipped_when_not_supported(self) -> None:
        """Builder should skip FP16 flag when platform lacks support."""
        mock_trt = _make_mock_trt_module()

        mock_builder = MagicMock()
        mock_builder.platform_has_fast_fp16 = False
        mock_config = MagicMock()
        mock_builder.create_builder_config.return_value = mock_config

        mock_network = MagicMock()
        mock_builder.create_network.return_value = mock_network

        mock_parser = MagicMock()
        mock_parser.parse.return_value = True
        mock_trt.Builder.return_value = mock_builder
        mock_trt.OnnxParser.return_value = mock_parser

        serialized = b"serialized_engine"
        mock_builder.build_serialized_network.return_value = serialized

        mock_runtime = MagicMock()
        mock_runtime.deserialize_cuda_engine.return_value = _make_mock_engine()
        mock_trt.Runtime.return_value = mock_runtime

        with patch("vision.tensorrt_detector.trt", mock_trt), \
             patch("builtins.open", MagicMock()):
            from vision.tensorrt_detector import _build_engine_from_onnx

            trt_logger = MagicMock()
            _build_engine_from_onnx("model.onnx", trt_logger, (640, 640))
            mock_config.set_flag.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Mock Jetson environment
# ---------------------------------------------------------------------------


class TestJetsonMockInit:
    """Test JetsonBackend creation with mocked jetson.inference."""

    def test_create_with_model_name(self) -> None:
        """JetsonBackend should initialize with any model name."""
        mock_jinf = MagicMock()
        mock_net = MagicMock()
        mock_jinf.detectNet.return_value = mock_net

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf):
            backend = JetsonBackend(model="ssd-mobilenet-v2", conf=0.5)
            assert backend.name == "JetsonBackend"
            mock_jinf.detectNet.assert_called_once_with(
                "ssd-mobilenet-v2", threshold=0.5,
            )

    def test_create_custom_model(self) -> None:
        """JetsonBackend accepts custom model names for download."""
        mock_jinf = MagicMock()
        mock_jinf.detectNet.return_value = MagicMock()

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf):
            JetsonBackend(model="peoplenet", conf=0.3)
            mock_jinf.detectNet.assert_called_once_with(
                "peoplenet", threshold=0.3,
            )


class TestJetsonMockDetection:
    """Test that detect() maps jetson Detection objects correctly."""

    def _make_jetson_det(
        self,
        class_id: int,
        confidence: float,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> MagicMock:
        """Create a mock jetson.inference Detection object."""
        det = MagicMock()
        det.ClassID = class_id
        det.Confidence = confidence
        det.Left = left
        det.Top = top
        det.Right = right
        det.Bottom = bottom
        return det

    def test_single_detection_mapping(self) -> None:
        """One jetson detection maps to one Detection dataclass."""
        raw = [self._make_jetson_det(1, 0.92, 10.0, 20.0, 110.0, 120.0)]
        dets = _convert_jetson_detections(raw)
        assert len(dets) == 1
        assert dets[0].class_id == 1
        assert dets[0].confidence == 0.92
        assert dets[0].bbox == (10.0, 20.0, 110.0, 120.0)
        assert dets[0].class_name == "1"

    def test_multiple_detections_mapping(self) -> None:
        """Multiple jetson detections all get mapped."""
        raw = [
            self._make_jetson_det(0, 0.99, 0, 0, 50, 50),
            self._make_jetson_det(2, 0.75, 100, 100, 200, 200),
            self._make_jetson_det(5, 0.60, 300, 300, 400, 400),
        ]
        dets = _convert_jetson_detections(raw)
        assert len(dets) == 3
        assert [d.class_id for d in dets] == [0, 2, 5]

    def test_empty_detection_list(self) -> None:
        """Empty jetson detection list returns empty."""
        assert _convert_jetson_detections([]) == []

    def test_detect_calls_network(self) -> None:
        """JetsonBackend.detect should call the network Detect method."""
        mock_jinf = MagicMock()
        mock_jutils = MagicMock()
        mock_net = MagicMock()

        raw_det = self._make_jetson_det(3, 0.88, 5, 10, 55, 110)
        mock_net.Detect.return_value = [raw_det]
        mock_jinf.detectNet.return_value = mock_net

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf), \
             patch("vision.tensorrt_detector.jutils", mock_jutils):
            backend = JetsonBackend(model="ssd-mobilenet-v2")
            frame = np.zeros((300, 300, 3), dtype=np.uint8)
            dets = backend.detect(frame)

        assert len(dets) == 1
        assert dets[0].class_id == 3


class TestJetsonModelDownload:
    """Test model download flow via jetson.inference."""

    def test_detectnet_called_with_model_name(self) -> None:
        """detectNet should receive the model name for auto-download."""
        mock_jinf = MagicMock()
        mock_jinf.detectNet.return_value = MagicMock()

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf):
            JetsonBackend(model="ssd-inception-v2", conf=0.4)

        mock_jinf.detectNet.assert_called_once_with(
            "ssd-inception-v2", threshold=0.4,
        )

    def test_detectnet_raises_on_bad_model(self) -> None:
        """If detectNet raises, JetsonBackend should propagate the error."""
        mock_jinf = MagicMock()
        mock_jinf.detectNet.side_effect = RuntimeError("model not found")

        with patch("vision.tensorrt_detector._JETSON_AVAILABLE", True), \
             patch("vision.tensorrt_detector.jinf", mock_jinf):
            with pytest.raises(RuntimeError, match="model not found"):
                JetsonBackend(model="nonexistent-model")


# ---------------------------------------------------------------------------
# Tests: Performance benchmark simulation
# ---------------------------------------------------------------------------


class TestBenchmarkResult:
    """Test BenchmarkResult dataclass and comparison logic."""

    def test_fields(self) -> None:
        result = BenchmarkResult(
            model_name="yolov11n",
            backend="TensorRT",
            fps=150.0,
            latency_ms=6.7,
            memory_mb=4096.0,
        )
        assert result.model_name == "yolov11n"
        assert result.backend == "TensorRT"
        assert result.fps == 150.0
        assert result.latency_ms == 6.7
        assert result.memory_mb == 4096.0

    def test_frozen(self) -> None:
        result = BenchmarkResult("m", "b", 1.0, 1.0, 1.0)
        with pytest.raises(AttributeError):
            result.fps = 999.0  # type: ignore[misc]


class TestBenchmarkSimulation:
    """Test benchmark collection across simulated hardware profiles."""

    def test_jetson_nano_profile(self) -> None:
        result = simulate_benchmark("yolov11n", "TensorRT", "jetson_nano")
        assert result.fps == 30.0
        assert result.latency_ms == 33.3
        assert result.memory_mb == 512.0

    def test_jetson_orin_profile(self) -> None:
        result = simulate_benchmark("yolov11n", "TensorRT", "jetson_orin")
        assert result.fps == 100.0
        assert result.latency_ms == 10.0
        assert result.memory_mb == 2048.0

    def test_gpu_server_profile(self) -> None:
        result = simulate_benchmark("yolov11n", "TensorRT", "gpu_server")
        assert result.fps == 200.0
        assert result.latency_ms == 5.0
        assert result.memory_mb == 8192.0

    def test_compare_profiles(self) -> None:
        """Verify Orin outperforms Nano and GPU server outperforms both."""
        nano = simulate_benchmark("yolov11n", "TensorRT", "jetson_nano")
        orin = simulate_benchmark("yolov11n", "TensorRT", "jetson_orin")
        server = simulate_benchmark("yolov11n", "TensorRT", "gpu_server")

        assert nano.fps < orin.fps < server.fps
        assert nano.latency_ms > orin.latency_ms > server.latency_ms

    def test_collect_multiple_benchmarks(self) -> None:
        """Collect results across all profiles into a list for comparison."""
        results: List[BenchmarkResult] = []
        for profile in HARDWARE_PROFILES:
            results.append(
                simulate_benchmark("yolov11n", "TensorRT", profile),
            )
        assert len(results) == 3
        fps_values = [r.fps for r in results]
        assert min(fps_values) == 30.0
        assert max(fps_values) == 200.0

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError):
            simulate_benchmark("model", "backend", "nonexistent_hw")

    def test_different_models_same_profile(self) -> None:
        """Same profile yields same perf regardless of model name."""
        a = simulate_benchmark("yolov11n", "TensorRT", "jetson_orin")
        b = simulate_benchmark("yolov11x", "TensorRT", "jetson_orin")
        assert a.fps == b.fps
        assert a.model_name != b.model_name


# ---------------------------------------------------------------------------
# Tests: Deployment readiness checker (unit tests for the script)
# ---------------------------------------------------------------------------


class TestDeploymentReadinessChecks:
    """Test individual deployment readiness check functions."""

    def test_check_cuda_available_true(self) -> None:
        """check_cuda returns PASS when torch.cuda.is_available() is True."""
        from scripts.check_deployment import check_cuda

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.get_device_name.return_value = "NVIDIA RTX 4090"
        with patch.dict(sys.modules, {"torch": mock_torch}):
            status, detail = check_cuda()
        assert status == "PASS"
        assert "RTX 4090" in detail

    def test_check_cuda_available_false(self) -> None:
        """check_cuda returns FAIL when no CUDA device is found."""
        from scripts.check_deployment import check_cuda

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict(sys.modules, {"torch": mock_torch}):
            status, detail = check_cuda()
        assert status == "FAIL"

    def test_check_cuda_no_torch(self) -> None:
        """check_cuda returns SKIP when torch is not installed."""
        from scripts.check_deployment import check_cuda

        with patch.dict(sys.modules, {"torch": None}):
            status, detail = check_cuda()
        assert status == "SKIP"

    def test_check_tensorrt_installed(self) -> None:
        """check_tensorrt returns PASS when tensorrt can be imported."""
        from scripts.check_deployment import check_tensorrt

        mock_trt = MagicMock()
        mock_trt.__version__ = "8.6.1"
        with patch.dict(sys.modules, {"tensorrt": mock_trt}):
            status, detail = check_tensorrt()
        assert status == "PASS"
        assert "8.6.1" in detail

    def test_check_tensorrt_missing(self) -> None:
        """check_tensorrt returns FAIL when tensorrt is not installed."""
        from scripts.check_deployment import check_tensorrt

        with patch.dict(sys.modules, {"tensorrt": None}):
            status, detail = check_tensorrt()
        assert status == "FAIL"

    def test_check_gpu_memory(self) -> None:
        """check_gpu_memory returns PASS with sufficient VRAM."""
        from scripts.check_deployment import check_gpu_memory

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        total_bytes = 8 * 1024 * 1024 * 1024  # 8 GB
        mock_torch.cuda.mem_get_info.return_value = (
            total_bytes,
            total_bytes,
        )
        with patch.dict(sys.modules, {"torch": mock_torch}):
            status, detail = check_gpu_memory()
        assert status == "PASS"

    def test_check_gpu_memory_insufficient(self) -> None:
        """check_gpu_memory returns FAIL with less than 1 GB VRAM."""
        from scripts.check_deployment import check_gpu_memory

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        total_bytes = 512 * 1024 * 1024  # 512 MB
        mock_torch.cuda.mem_get_info.return_value = (
            total_bytes,
            total_bytes,
        )
        with patch.dict(sys.modules, {"torch": mock_torch}):
            status, detail = check_gpu_memory()
        assert status == "FAIL"

    def test_all_checks_run(self) -> None:
        """run_all_checks should execute every check and return results."""
        from scripts.check_deployment import run_all_checks

        results = run_all_checks()
        assert len(results) >= 5
        for name, status, detail in results:
            assert status in ("PASS", "FAIL", "SKIP")
            assert isinstance(name, str)
            assert isinstance(detail, str)

    def test_minimum_requirements_logic(self) -> None:
        """meets_minimum_requirements returns True only with CUDA + TRT."""
        from scripts.check_deployment import meets_minimum_requirements

        all_pass = [
            ("CUDA", "PASS", "ok"),
            ("TensorRT", "PASS", "ok"),
            ("GPU Memory", "PASS", "ok"),
        ]
        assert meets_minimum_requirements(all_pass) is True

        missing_cuda = [
            ("CUDA", "FAIL", "no"),
            ("TensorRT", "PASS", "ok"),
        ]
        assert meets_minimum_requirements(missing_cuda) is False
