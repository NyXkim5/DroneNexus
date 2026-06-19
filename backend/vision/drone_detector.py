"""
Drone-specific detection model adapter for OVERWATCH.

Wraps Ultralytics YOLO with drone-specific class mapping, confidence
boosting for ambiguous aerial detections, and size-based filtering
using camera FOV geometry.

Implements DetectorBackend so it plugs into the existing vision pipeline.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

from vision.tensorrt_detector import Detection, DetectorBackend

logger = logging.getLogger("overwatch.vision.drone_detector")

# Optional dependency: ultralytics
_ULTRALYTICS_AVAILABLE = False
try:
    from ultralytics import YOLO  # type: ignore[import-untyped]
    _ULTRALYTICS_AVAILABLE = True
except ImportError:
    YOLO = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Drone-specific class taxonomy
# ---------------------------------------------------------------------------


class DroneClass(Enum):
    """Drone-specific detection classes for OVERWATCH."""

    UAS = "uas"
    QUADROTOR = "quadrotor"
    FIXED_WING = "fixed_wing"
    HELICOPTER = "helicopter"
    BIRD = "bird"
    UNKNOWN_AIR = "unknown_air"


# Map from custom YOLO training class indices to DroneClass
DRONE_DATASET_CLASS_MAP: Dict[int, DroneClass] = {
    0: DroneClass.UAS,
    1: DroneClass.QUADROTOR,
    2: DroneClass.FIXED_WING,
    3: DroneClass.HELICOPTER,
    4: DroneClass.BIRD,
}

# Map from COCO class names to potential DroneClass reclassifications
COCO_REMAP: Dict[str, DroneClass] = {
    "bird": DroneClass.BIRD,
    "airplane": DroneClass.FIXED_WING,
    "kite": DroneClass.UAS,
}

# COCO classes that may actually be drones when seen at altitude
ALTITUDE_AMBIGUOUS_CLASSES = frozenset({"bird", "airplane", "kite"})

# Typical drone wingspan/body size range in meters
DRONE_SIZE_MIN_M = 0.3
DRONE_SIZE_MAX_M = 3.0


# ---------------------------------------------------------------------------
# Drone detection dataclass (extends base Detection)
# ---------------------------------------------------------------------------


@dataclass
class DroneDetection:
    """A detection reclassified through the drone-specific pipeline."""

    drone_class: DroneClass
    confidence: float
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    original_class: str
    original_confidence: float
    estimated_size_m: Optional[float] = None
    reclassified: bool = False
    boost_applied: float = 0.0


# ---------------------------------------------------------------------------
# Camera geometry for size estimation
# ---------------------------------------------------------------------------


@dataclass
class CameraParams:
    """Camera intrinsics needed for size-based filtering."""

    fov_horizontal_deg: float = 62.2
    fov_vertical_deg: float = 48.8
    sensor_width_px: int = 1920
    sensor_height_px: int = 1080
    altitude_m: float = 0.0
    slant_range_m: float = 100.0


def estimate_object_size_m(
    bbox: Tuple[float, float, float, float],
    camera: CameraParams,
) -> float:
    """Estimate real-world size of a detected object using camera FOV.

    Uses the larger bbox dimension (width or height in pixels) and the
    slant range to compute estimated physical size in meters.
    """
    bbox_width_px = abs(bbox[2] - bbox[0])
    bbox_height_px = abs(bbox[3] - bbox[1])
    max_dim_px = max(bbox_width_px, bbox_height_px)

    if max_dim_px < 1.0 or camera.slant_range_m <= 0:
        return 0.0

    fov_rad = math.radians(camera.fov_horizontal_deg)
    frame_width_at_range = 2.0 * camera.slant_range_m * math.tan(fov_rad / 2.0)
    meters_per_pixel = frame_width_at_range / camera.sensor_width_px

    return max_dim_px * meters_per_pixel


def is_drone_sized(
    estimated_size_m: float,
    min_m: float = DRONE_SIZE_MIN_M,
    max_m: float = DRONE_SIZE_MAX_M,
) -> bool:
    """Check whether estimated size falls within typical drone dimensions."""
    return min_m <= estimated_size_m <= max_m


# ---------------------------------------------------------------------------
# DroneClassifier: reclassification logic
# ---------------------------------------------------------------------------


class DroneClassifier:
    """Post-detection classifier that reclassifies raw YOLO detections
    using drone-specific heuristics.

    Pipeline:
    1. Map known drone-trained classes directly
    2. Reclassify COCO birds/airplanes as potential UAS with confidence boost
    3. Filter by estimated physical size using camera FOV
    """

    def __init__(
        self,
        camera: Optional[CameraParams] = None,
        altitude_boost: float = 0.15,
        min_confidence: float = 0.3,
        size_filter_enabled: bool = True,
        drone_model_classes: Optional[Dict[int, DroneClass]] = None,
    ) -> None:
        self._camera = camera or CameraParams()
        self._altitude_boost = altitude_boost
        self._min_confidence = min_confidence
        self._size_filter_enabled = size_filter_enabled
        self._class_map = drone_model_classes if drone_model_classes is not None else DRONE_DATASET_CLASS_MAP

    @property
    def camera(self) -> CameraParams:
        return self._camera

    @camera.setter
    def camera(self, value: CameraParams) -> None:
        self._camera = value

    def classify(
        self,
        detections: List[Detection],
    ) -> List[DroneDetection]:
        """Reclassify a list of raw YOLO detections through the drone pipeline."""
        results: List[DroneDetection] = []
        for det in detections:
            drone_det = self._reclassify_single(det)
            if drone_det is None:
                continue
            if drone_det.confidence < self._min_confidence:
                continue
            results.append(drone_det)
        return results

    def _reclassify_single(
        self,
        det: Detection,
    ) -> Optional[DroneDetection]:
        """Reclassify a single detection."""
        drone_class: DroneClass
        confidence = det.confidence
        reclassified = False
        boost = 0.0

        # Path 1: detection came from a drone-trained model
        if det.class_id in self._class_map:
            drone_class = self._class_map[det.class_id]

        # Path 2: detection came from COCO model, check remap table
        elif det.class_name in COCO_REMAP:
            drone_class = COCO_REMAP[det.class_name]
            reclassified = True

            # Boost confidence for ambiguous aerial classes
            if det.class_name in ALTITUDE_AMBIGUOUS_CLASSES:
                boost = self._compute_altitude_boost(det)
                confidence = min(1.0, confidence + boost)

        # Path 3: not a relevant class at all
        else:
            return None

        # Size-based filtering
        estimated_size = estimate_object_size_m(det.bbox, self._camera)

        if self._size_filter_enabled and estimated_size > 0:
            if not is_drone_sized(estimated_size):
                # If too large, it is probably a real airplane or large bird
                if estimated_size > DRONE_SIZE_MAX_M:
                    if drone_class != DroneClass.BIRD:
                        drone_class = DroneClass.UNKNOWN_AIR
                    confidence *= 0.5
                # If too small, likely noise
                elif estimated_size < DRONE_SIZE_MIN_M:
                    confidence *= 0.3

        return DroneDetection(
            drone_class=drone_class,
            confidence=round(confidence, 4),
            bbox=det.bbox,
            original_class=det.class_name,
            original_confidence=det.confidence,
            estimated_size_m=round(estimated_size, 3) if estimated_size > 0 else None,
            reclassified=reclassified,
            boost_applied=round(boost, 4),
        )

    def _compute_altitude_boost(self, det: Detection) -> float:
        """Compute confidence boost for ambiguous aerial detections.

        Higher boost when:
        - Camera is at altitude (likely looking at sky, not ground birds)
        - Object is in upper portion of frame (above horizon)
        - Detection bbox is small (distant aerial object)
        """
        boost = self._altitude_boost

        # Altitude factor: more boost if camera is elevated
        if self._camera.altitude_m > 10:
            boost *= 1.2

        # Vertical position factor: objects higher in frame get more boost
        frame_center_y = self._camera.sensor_height_px / 2.0
        det_center_y = (det.bbox[1] + det.bbox[3]) / 2.0
        if det_center_y < frame_center_y:
            boost *= 1.1

        # Size factor: smaller bbox in pixels gets more boost (distant target)
        bbox_area = abs(det.bbox[2] - det.bbox[0]) * abs(det.bbox[3] - det.bbox[1])
        frame_area = self._camera.sensor_width_px * self._camera.sensor_height_px
        area_ratio = bbox_area / max(frame_area, 1)
        if area_ratio < 0.01:
            boost *= 1.3

        return min(boost, 0.35)  # cap boost at 0.35

    def filter_by_size(
        self,
        detections: List[DroneDetection],
        min_m: float = DRONE_SIZE_MIN_M,
        max_m: float = DRONE_SIZE_MAX_M,
    ) -> List[DroneDetection]:
        """Keep only detections whose estimated size is within drone range."""
        return [
            d for d in detections
            if d.estimated_size_m is None or is_drone_sized(d.estimated_size_m, min_m, max_m)
        ]


# ---------------------------------------------------------------------------
# DroneDetectorBackend: full DetectorBackend implementation
# ---------------------------------------------------------------------------


class DroneDetectorBackend(DetectorBackend):
    """YOLO-based detector specialized for drone/UAS targets.

    Wraps Ultralytics YOLO with custom weights trained on drone data and
    pipes detections through DroneClassifier for reclassification.
    """

    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        conf: float = 0.25,
        iou: float = 0.45,
        device: Optional[str] = None,
        camera: Optional[CameraParams] = None,
        altitude_boost: float = 0.15,
        size_filter_enabled: bool = True,
        use_drone_classes: bool = True,
    ) -> None:
        if not _ULTRALYTICS_AVAILABLE or YOLO is None:
            raise RuntimeError(
                "ultralytics is required for DroneDetectorBackend"
            )

        self._model_path = model_path
        self._conf = conf
        self._iou = iou
        self._model = YOLO(model_path)
        self._device = device
        if device:
            self._model.to(device)

        # Determine class mapping based on model type
        class_map: Optional[Dict[int, DroneClass]] = None
        if use_drone_classes:
            class_map = DRONE_DATASET_CLASS_MAP
        else:
            # COCO model: no direct class map, rely on remap
            class_map = {}

        self._classifier = DroneClassifier(
            camera=camera,
            altitude_boost=altitude_boost,
            size_filter_enabled=size_filter_enabled,
            drone_model_classes=class_map,
        )

        logger.info(
            "DroneDetectorBackend loaded model=%s device=%s drone_classes=%s",
            model_path,
            device or "auto",
            use_drone_classes,
        )

    @property
    def classifier(self) -> DroneClassifier:
        """Access the underlying DroneClassifier for parameter tuning."""
        return self._classifier

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run inference and return base Detection objects.

        For drone-specific DroneDetection objects, use detect_drones().
        """
        results = self._model(
            frame, verbose=False, conf=self._conf, iou=self._iou,
        )
        return _parse_yolo_results(results)

    def detect_drones(self, frame: np.ndarray) -> List[DroneDetection]:
        """Run inference and return drone-classified detections."""
        raw_detections = self.detect(frame)
        return self._classifier.classify(raw_detections)

    def warmup(self) -> None:
        """Warm up with a dummy 640x640 frame."""
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.detect(dummy)
        logger.info("DroneDetectorBackend warmup complete")

    def update_camera(self, camera: CameraParams) -> None:
        """Update camera parameters for size-based filtering."""
        self._classifier.camera = camera


def _parse_yolo_results(results: list) -> List[Detection]:
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
# Factory function
# ---------------------------------------------------------------------------


def create_drone_detector(
    model_path: str = "yolo11n.pt",
    conf: float = 0.25,
    iou: float = 0.45,
    device: Optional[str] = None,
    camera: Optional[CameraParams] = None,
    use_drone_classes: bool = True,
) -> DroneDetectorBackend:
    """Create a drone-specific detector with sensible defaults.

    Args:
        model_path: Path to YOLO .pt weights (drone-trained or COCO).
        conf: Confidence threshold for raw YOLO detections.
        iou: IoU threshold for NMS.
        device: Torch device ("cpu", "cuda:0", etc).
        camera: Camera parameters for size filtering.
        use_drone_classes: True if model was trained on drone classes.

    Returns:
        An initialized DroneDetectorBackend.
    """
    return DroneDetectorBackend(
        model_path=model_path,
        conf=conf,
        iou=iou,
        device=device,
        camera=camera,
        use_drone_classes=use_drone_classes,
    )
