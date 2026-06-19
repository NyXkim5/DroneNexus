"""
TensorRT-based detector adapter for OVERWATCH.

Provides a unified DetectorBackend interface with three implementations:
- UltralyticsBackend: CPU/GPU via ultralytics YOLO (works everywhere)
- TensorRTBackend: NVIDIA TensorRT engine for high-throughput inference
- JetsonBackend: jetson.inference for Jetson Nano/Xavier/Orin

Use create_detector() to auto-select the best available backend.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger("overwatch.vision.tensorrt")

# Optional dependency: ultralytics
_ULTRALYTICS_AVAILABLE = False
try:
    from ultralytics import YOLO  # type: ignore[import-untyped]
    _ULTRALYTICS_AVAILABLE = True
except ImportError:
    YOLO = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """Single object detection result."""

    class_name: str
    confidence: float
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    class_id: int = 0


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------


class DetectorBackend(ABC):
    """Base class for all detection backends."""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run inference on a BGR numpy frame and return detections."""
        ...

    @abstractmethod
    def warmup(self) -> None:
        """Run a dummy inference pass to warm up the engine."""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Ultralytics backend (fallback)
# ---------------------------------------------------------------------------


class UltralyticsBackend(DetectorBackend):
    """Detection via ultralytics YOLO. Works on CPU, CUDA, and MPS."""

    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        conf: float = 0.4,
        iou: float = 0.45,
        device: Optional[str] = None,
    ) -> None:
        if not _ULTRALYTICS_AVAILABLE or YOLO is None:
            raise RuntimeError(
                "ultralytics is required for UltralyticsBackend"
            )
        self._model_path = model_path
        self._conf = conf
        self._iou = iou
        self._model = YOLO(model_path)
        if device:
            self._model.to(device)
        logger.info(
            "UltralyticsBackend loaded model=%s device=%s",
            model_path,
            device or "auto",
        )

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run YOLO inference and return standardized detections."""
        results = self._model(
            frame, verbose=False, conf=self._conf, iou=self._iou,
        )
        return _parse_ultralytics_results(results)

    def warmup(self) -> None:
        """Warm up with a dummy 640x640 frame."""
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.detect(dummy)
        logger.info("UltralyticsBackend warmup complete")


def _parse_ultralytics_results(results: list) -> List[Detection]:
    """Convert ultralytics Results list into Detection objects."""
    detections: List[Detection] = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls[0])
            cls_name = r.names.get(cls_id, "unknown")
            conf = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(Detection(
                class_name=cls_name,
                confidence=round(conf, 4),
                bbox=(x1, y1, x2, y2),
                class_id=cls_id,
            ))
    return detections


# ---------------------------------------------------------------------------
# TensorRT backend
# ---------------------------------------------------------------------------

_TRT_AVAILABLE = False
try:
    import tensorrt as trt  # type: ignore[import-untyped]
    _TRT_AVAILABLE = True
except ImportError:
    trt = None

_CUDA_AVAILABLE = False
try:
    import pycuda.driver as cuda  # type: ignore[import-untyped]
    import pycuda.autoinit  # type: ignore[import-untyped]  # noqa: F401
    _CUDA_AVAILABLE = True
except ImportError:
    cuda = None


# COCO class names (80 classes) used when the engine has no metadata
COCO_NAMES: List[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


class TensorRTBackend(DetectorBackend):
    """Detection via TensorRT engine for NVIDIA GPUs."""

    def __init__(
        self,
        engine_path: str,
        conf: float = 0.4,
        iou: float = 0.45,
        input_size: Tuple[int, int] = (640, 640),
        class_names: Optional[List[str]] = None,
    ) -> None:
        if not _TRT_AVAILABLE or not _CUDA_AVAILABLE:
            raise RuntimeError(
                "tensorrt and pycuda are required for TensorRTBackend"
            )
        self._conf = conf
        self._iou = iou
        self._input_size = input_size
        self._class_names = class_names or COCO_NAMES
        self._engine = self._load_engine(engine_path)
        self._context = self._engine.create_execution_context()
        self._inputs, self._outputs, self._bindings, self._stream = (
            _allocate_buffers(self._engine)
        )
        logger.info("TensorRTBackend loaded engine=%s", engine_path)

    def _load_engine(self, path: str) -> trt.ICudaEngine:
        """Deserialize a TensorRT engine from file."""
        trt_logger = trt.Logger(trt.Logger.WARNING)
        if path.endswith(".onnx"):
            return _build_engine_from_onnx(path, trt_logger, self._input_size)
        with open(path, "rb") as f:
            runtime = trt.Runtime(trt_logger)
            return runtime.deserialize_cuda_engine(f.read())

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run TensorRT inference on a BGR frame."""
        img = _preprocess_frame(frame, self._input_size)
        np.copyto(self._inputs[0].host, img.ravel())
        output = _do_inference(
            self._context,
            self._bindings,
            self._inputs,
            self._outputs,
            self._stream,
        )
        return _postprocess_yolo(
            output,
            frame.shape,
            self._input_size,
            self._conf,
            self._iou,
            self._class_names,
        )

    def warmup(self) -> None:
        """Warm up with repeated dummy inferences."""
        dummy = np.zeros(
            (self._input_size[1], self._input_size[0], 3), dtype=np.uint8,
        )
        for _ in range(3):
            self.detect(dummy)
        logger.info("TensorRTBackend warmup complete")


# ---------------------------------------------------------------------------
# TensorRT helper functions
# ---------------------------------------------------------------------------


@dataclass
class HostDeviceMem:
    """Pair of host (CPU) and device (GPU) memory allocations."""

    host: np.ndarray
    device: int


def _allocate_buffers(
    engine: trt.ICudaEngine,
) -> Tuple[List[HostDeviceMem], List[HostDeviceMem], List[int], cuda.Stream]:
    """Allocate host and device buffers for all engine bindings."""
    inputs: List[HostDeviceMem] = []
    outputs: List[HostDeviceMem] = []
    bindings: List[int] = []
    stream = cuda.Stream()

    for i in range(engine.num_io_tensors):
        name = engine.get_tensor_name(i)
        shape = engine.get_tensor_shape(name)
        dtype = trt.nptype(engine.get_tensor_dtype(name))
        size = int(np.prod(shape))
        host_mem = np.empty(size, dtype=dtype)
        device_mem = cuda.mem_alloc(host_mem.nbytes)
        bindings.append(int(device_mem))
        mode = engine.get_tensor_mode(name)
        if mode == trt.TensorIOMode.INPUT:
            inputs.append(HostDeviceMem(host=host_mem, device=int(device_mem)))
        else:
            outputs.append(HostDeviceMem(host=host_mem, device=int(device_mem)))

    return inputs, outputs, bindings, stream


def _do_inference(
    context: trt.IExecutionContext,
    bindings: List[int],
    inputs: List[HostDeviceMem],
    outputs: List[HostDeviceMem],
    stream: cuda.Stream,
) -> List[np.ndarray]:
    """Execute TensorRT inference synchronously."""
    for inp in inputs:
        cuda.memcpy_htod_async(inp.device, inp.host, stream)
    context.execute_async_v2(
        bindings=bindings, stream_handle=stream.handle,
    )
    for out in outputs:
        cuda.memcpy_dtoh_async(out.host, out.device, stream)
    stream.synchronize()
    return [out.host.copy() for out in outputs]


def _build_engine_from_onnx(
    onnx_path: str,
    trt_logger: trt.Logger,
    input_size: Tuple[int, int],
) -> trt.ICudaEngine:
    """Build a TensorRT engine from an ONNX model file."""
    builder = trt.Builder(trt_logger)
    network_flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    network = builder.create_network(network_flags)
    parser = trt.OnnxParser(network, trt_logger)

    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                logger.error("ONNX parse error: %s", parser.get_error(i))
            raise RuntimeError(f"Failed to parse ONNX model: {onnx_path}")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)
    if builder.platform_has_fast_fp16:
        config.set_flag(trt.BuilderFlag.FP16)
        logger.info("TensorRT FP16 enabled")

    engine = builder.build_serialized_network(network, config)
    if engine is None:
        raise RuntimeError("TensorRT engine build failed")

    runtime = trt.Runtime(trt_logger)
    return runtime.deserialize_cuda_engine(engine)


# ---------------------------------------------------------------------------
# Pre/post-processing
# ---------------------------------------------------------------------------


def _preprocess_frame(
    frame: np.ndarray,
    input_size: Tuple[int, int],
) -> np.ndarray:
    """Resize, normalize, and transpose a BGR frame for YOLO input."""
    import cv2

    resized = cv2.resize(frame, input_size)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    normalized = rgb.astype(np.float32) / 255.0
    # HWC -> CHW, add batch dim -> (1, 3, H, W)
    transposed = np.transpose(normalized, (2, 0, 1))
    return np.expand_dims(transposed, axis=0).astype(np.float32)


def _nms(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
) -> List[int]:
    """Non-maximum suppression on (N, 4) boxes and (N,) scores."""
    if len(boxes) == 0:
        return []

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: List[int] = []

    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        mask = np.where(iou <= iou_threshold)[0]
        order = order[mask + 1]

    return keep


def _postprocess_yolo(
    outputs: List[np.ndarray],
    orig_shape: Tuple[int, ...],
    input_size: Tuple[int, int],
    conf_threshold: float,
    iou_threshold: float,
    class_names: List[str],
) -> List[Detection]:
    """Parse raw YOLO output tensor into Detection objects.

    Handles YOLOv8/v11 output format: (1, 4+num_classes, num_boxes)
    where rows 0-3 are cx, cy, w, h and rows 4+ are class scores.
    """
    raw = outputs[0]
    # Reshape to (4+num_classes, num_boxes) if flat
    num_classes = len(class_names)
    expected_rows = 4 + num_classes
    if raw.ndim == 1:
        num_boxes = raw.size // expected_rows
        raw = raw.reshape(expected_rows, num_boxes)
    elif raw.ndim == 3:
        raw = raw[0]  # remove batch dim

    # Transpose if needed so shape is (num_boxes, 4+num_classes)
    if raw.shape[0] == expected_rows and raw.shape[1] != expected_rows:
        raw = raw.T

    return _extract_detections_from_grid(
        raw, orig_shape, input_size,
        conf_threshold, iou_threshold, class_names,
    )


def _extract_detections_from_grid(
    grid: np.ndarray,
    orig_shape: Tuple[int, ...],
    input_size: Tuple[int, int],
    conf_threshold: float,
    iou_threshold: float,
    class_names: List[str],
) -> List[Detection]:
    """Extract detections from a (num_boxes, 4+C) grid array."""
    cx = grid[:, 0]
    cy = grid[:, 1]
    w = grid[:, 2]
    h = grid[:, 3]
    class_scores = grid[:, 4:]

    max_scores = class_scores.max(axis=1)
    mask = max_scores > conf_threshold
    if not np.any(mask):
        return []

    filtered_cx = cx[mask]
    filtered_cy = cy[mask]
    filtered_w = w[mask]
    filtered_h = h[mask]
    filtered_scores = max_scores[mask]
    filtered_classes = class_scores[mask].argmax(axis=1)

    # Convert cxcywh to xyxy
    x1 = filtered_cx - filtered_w / 2
    y1 = filtered_cy - filtered_h / 2
    x2 = filtered_cx + filtered_w / 2
    y2 = filtered_cy + filtered_h / 2
    boxes = np.stack([x1, y1, x2, y2], axis=1)

    keep = _nms(boxes, filtered_scores, iou_threshold)

    # Scale boxes back to original image dimensions
    scale_x = orig_shape[1] / input_size[0]
    scale_y = orig_shape[0] / input_size[1]

    detections: List[Detection] = []
    for idx in keep:
        cls_id = int(filtered_classes[idx])
        name = class_names[cls_id] if cls_id < len(class_names) else "unknown"
        bx1 = float(boxes[idx, 0]) * scale_x
        by1 = float(boxes[idx, 1]) * scale_y
        bx2 = float(boxes[idx, 2]) * scale_x
        by2 = float(boxes[idx, 3]) * scale_y
        detections.append(Detection(
            class_name=name,
            confidence=round(float(filtered_scores[idx]), 4),
            bbox=(bx1, by1, bx2, by2),
            class_id=cls_id,
        ))

    return detections


# ---------------------------------------------------------------------------
# Jetson backend
# ---------------------------------------------------------------------------

_JETSON_AVAILABLE = False
try:
    import jetson.inference as jinf  # type: ignore[import-untyped]
    import jetson.utils as jutils  # type: ignore[import-untyped]
    _JETSON_AVAILABLE = True
except ImportError:
    jinf = None
    jutils = None


class JetsonBackend(DetectorBackend):
    """Detection via jetson.inference detectNet for Jetson devices."""

    def __init__(
        self,
        model: str = "ssd-mobilenet-v2",
        conf: float = 0.4,
    ) -> None:
        if not _JETSON_AVAILABLE:
            raise RuntimeError(
                "jetson.inference is required for JetsonBackend"
            )
        self._conf = conf
        self._net = jinf.detectNet(model, threshold=conf)
        logger.info("JetsonBackend loaded model=%s", model)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run jetson.inference detection on a BGR frame."""
        rgba = _bgr_to_jetson_cuda(frame)
        raw_dets = self._net.Detect(rgba, overlay="none")
        return _convert_jetson_detections(raw_dets)

    def warmup(self) -> None:
        """Warm up jetson.inference with a dummy frame."""
        dummy = np.zeros((300, 300, 3), dtype=np.uint8)
        self.detect(dummy)
        logger.info("JetsonBackend warmup complete")


def _bgr_to_jetson_cuda(frame: np.ndarray) -> object:
    """Convert BGR numpy array to jetson.utils CUDA image."""
    import cv2

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgba = cv2.cvtColor(rgb, cv2.COLOR_RGB2RGBA)
    return jutils.cudaFromNumpy(rgba)


def _convert_jetson_detections(
    raw_dets: list,
) -> List[Detection]:
    """Map jetson.inference Detection objects to our Detection format."""
    detections: List[Detection] = []
    for d in raw_dets:
        detections.append(Detection(
            class_name=str(d.ClassID),
            confidence=round(float(d.Confidence), 4),
            bbox=(float(d.Left), float(d.Top), float(d.Right), float(d.Bottom)),
            class_id=int(d.ClassID),
        ))
    return detections


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _detect_nvidia_gpu() -> bool:
    """Check if an NVIDIA GPU is available."""
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def create_detector(
    model_path: str = "yolo11n.pt",
    prefer: str = "auto",
    conf: float = 0.4,
    iou: float = 0.45,
    input_size: Tuple[int, int] = (640, 640),
) -> DetectorBackend:
    """Auto-select the best available detection backend.

    Priority when prefer="auto": tensorrt > jetson > ultralytics.

    Args:
        model_path: Path to model file (.engine, .onnx, or .pt).
        prefer: One of "auto", "tensorrt", "jetson", "ultralytics".
        conf: Confidence threshold.
        iou: IoU threshold for NMS.
        input_size: Input resolution for TensorRT backend.

    Returns:
        An initialized DetectorBackend instance.
    """
    if prefer == "tensorrt":
        return _create_tensorrt(model_path, conf, iou, input_size)

    if prefer == "jetson":
        return _create_jetson(model_path, conf)

    if prefer == "ultralytics":
        return _create_ultralytics(model_path, conf, iou)

    # Auto-detect best backend
    return _auto_select(model_path, conf, iou, input_size)


def _auto_select(
    model_path: str,
    conf: float,
    iou: float,
    input_size: Tuple[int, int],
) -> DetectorBackend:
    """Try backends in priority order, falling back gracefully."""
    engine_path = _find_engine_path(model_path)

    if _TRT_AVAILABLE and _CUDA_AVAILABLE and engine_path:
        try:
            backend = TensorRTBackend(
                engine_path, conf=conf, iou=iou, input_size=input_size,
            )
            logger.info("Auto-selected TensorRTBackend")
            return backend
        except Exception as exc:
            logger.warning("TensorRT init failed, trying next: %s", exc)

    if _JETSON_AVAILABLE:
        try:
            backend = JetsonBackend(conf=conf)
            logger.info("Auto-selected JetsonBackend")
            return backend
        except Exception as exc:
            logger.warning("Jetson init failed, trying next: %s", exc)

    logger.info("Auto-selected UltralyticsBackend (fallback)")
    return UltralyticsBackend(model_path, conf=conf, iou=iou)


def _find_engine_path(model_path: str) -> Optional[str]:
    """Resolve a .engine file path from the given model path."""
    p = Path(model_path)
    if p.suffix == ".engine" and p.exists():
        return str(p)
    engine_candidate = p.with_suffix(".engine")
    if engine_candidate.exists():
        return str(engine_candidate)
    if p.suffix == ".onnx" and p.exists():
        return str(p)
    return None


def _create_tensorrt(
    model_path: str,
    conf: float,
    iou: float,
    input_size: Tuple[int, int],
) -> DetectorBackend:
    """Create TensorRTBackend or fall back to Ultralytics."""
    if not _TRT_AVAILABLE or not _CUDA_AVAILABLE:
        logger.warning(
            "TensorRT requested but not available, falling back to Ultralytics"
        )
        return UltralyticsBackend(model_path, conf=conf, iou=iou)
    engine_path = _find_engine_path(model_path)
    if not engine_path:
        logger.warning(
            "No .engine/.onnx file found for %s, falling back to Ultralytics",
            model_path,
        )
        return UltralyticsBackend(model_path, conf=conf, iou=iou)
    return TensorRTBackend(engine_path, conf=conf, iou=iou, input_size=input_size)


def _create_jetson(model_path: str, conf: float) -> DetectorBackend:
    """Create JetsonBackend or fall back to Ultralytics."""
    if not _JETSON_AVAILABLE:
        logger.warning(
            "Jetson requested but not available, falling back to Ultralytics"
        )
        return UltralyticsBackend(model_path, conf=conf)
    return JetsonBackend(model=model_path, conf=conf)


def _create_ultralytics(
    model_path: str, conf: float, iou: float,
) -> DetectorBackend:
    """Create UltralyticsBackend."""
    return UltralyticsBackend(model_path, conf=conf, iou=iou)
