"""Thermal/IR camera support for OVERWATCH night-time drone detection.

Provides thermal image processing, hotspot detection, synthetic thermal
generation, camera source abstraction, and a DetectorBackend that fuses
YOLO detections with IR hotspot analysis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np

from vision.tensorrt_detector import Detection, DetectorBackend

logger = logging.getLogger("overwatch.vision.thermal")


# ---------------------------------------------------------------------------
# Palette definitions
# ---------------------------------------------------------------------------


class Palette(str, Enum):
    """Supported thermal color palettes."""

    IRONBOW = "IRONBOW"
    WHITE_HOT = "WHITE_HOT"
    BLACK_HOT = "BLACK_HOT"
    RAINBOW = "RAINBOW"


def _build_lut(palette: Palette) -> np.ndarray:
    """Build a 256-entry BGR lookup table for the given palette."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    x = np.linspace(0.0, 1.0, 256)

    if palette == Palette.IRONBOW:
        lut[:, 2] = np.clip(255 * _ironbow_r(x), 0, 255).astype(np.uint8)
        lut[:, 1] = np.clip(255 * _ironbow_g(x), 0, 255).astype(np.uint8)
        lut[:, 0] = np.clip(255 * _ironbow_b(x), 0, 255).astype(np.uint8)
    elif palette == Palette.WHITE_HOT:
        gray = (x * 255).astype(np.uint8)
        lut[:, 0] = lut[:, 1] = lut[:, 2] = gray
    elif palette == Palette.BLACK_HOT:
        gray = ((1.0 - x) * 255).astype(np.uint8)
        lut[:, 0] = lut[:, 1] = lut[:, 2] = gray
    elif palette == Palette.RAINBOW:
        lut[:, 2] = np.clip(255 * _rainbow_r(x), 0, 255).astype(np.uint8)
        lut[:, 1] = np.clip(255 * _rainbow_g(x), 0, 255).astype(np.uint8)
        lut[:, 0] = np.clip(255 * _rainbow_b(x), 0, 255).astype(np.uint8)

    return lut


def _ironbow_r(x: np.ndarray) -> np.ndarray:
    """Red channel curve for IRONBOW palette."""
    return np.where(x < 0.5, 2.0 * x, 1.0)


def _ironbow_g(x: np.ndarray) -> np.ndarray:
    """Green channel curve for IRONBOW palette."""
    return np.where(x < 0.5, 0.0, 2.0 * (x - 0.5))


def _ironbow_b(x: np.ndarray) -> np.ndarray:
    """Blue channel curve for IRONBOW palette."""
    return np.where(x < 0.5, 1.0 - 2.0 * x, 0.0)


def _rainbow_r(x: np.ndarray) -> np.ndarray:
    """Red channel for rainbow using sine approximation."""
    return 0.5 + 0.5 * np.sin(2.0 * np.pi * (x - 0.0))


def _rainbow_g(x: np.ndarray) -> np.ndarray:
    """Green channel for rainbow using sine approximation."""
    return 0.5 + 0.5 * np.sin(2.0 * np.pi * (x - 1.0 / 3.0))


def _rainbow_b(x: np.ndarray) -> np.ndarray:
    """Blue channel for rainbow using sine approximation."""
    return 0.5 + 0.5 * np.sin(2.0 * np.pi * (x - 2.0 / 3.0))


# Pre-build lookup tables
_PALETTE_LUTS: dict[Palette, np.ndarray] = {
    p: _build_lut(p) for p in Palette
}


# ---------------------------------------------------------------------------
# Hotspot dataclass
# ---------------------------------------------------------------------------


@dataclass
class Hotspot:
    """A detected thermal hotspot region."""

    center: Tuple[int, int]
    area: int
    peak_value: float
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    estimated_temp: float = 0.0


# ---------------------------------------------------------------------------
# ThermalProcessor
# ---------------------------------------------------------------------------


class ThermalProcessor:
    """Converts raw 16-bit thermal data to visual output and detects hotspots."""

    def apply_palette(
        self,
        raw_frame: np.ndarray,
        palette: str = "IRONBOW",
    ) -> np.ndarray:
        """Map a 16-bit grayscale frame to an 8-bit BGR image via auto-gain.

        Args:
            raw_frame: 2D array of raw thermal pixel values.
            palette: One of IRONBOW, WHITE_HOT, BLACK_HOT, RAINBOW.

        Returns:
            3-channel BGR uint8 image.
        """
        normalized = self._auto_gain(raw_frame)
        pal = Palette(palette)
        lut = _PALETTE_LUTS[pal]
        return lut[normalized]

    def _auto_gain(self, frame: np.ndarray) -> np.ndarray:
        """Normalize a raw thermal frame to 0-255 uint8 for max contrast."""
        fmin = float(frame.min())
        fmax = float(frame.max())
        if fmax <= fmin:
            return np.zeros(frame.shape, dtype=np.uint8)
        scaled = (frame.astype(np.float64) - fmin) / (fmax - fmin) * 255.0
        return scaled.astype(np.uint8)

    @staticmethod
    def pixel_to_temp(
        value: float,
        min_temp: float,
        max_temp: float,
    ) -> float:
        """Linear mapping from a 16-bit pixel value to temperature in Celsius.

        Assumes the sensor maps min_temp..max_temp across the full 16-bit range.
        """
        return min_temp + (value / 65535.0) * (max_temp - min_temp)

    def detect_hotspots(
        self,
        frame: np.ndarray,
        threshold_pct: float = 95.0,
        min_area: int = 20,
        max_hotspots: int = 50,
        min_temp: Optional[float] = None,
        max_temp: Optional[float] = None,
    ) -> List[Hotspot]:
        """Find the brightest connected regions in a thermal frame.

        Args:
            frame: 2D raw thermal array.
            threshold_pct: Percentile above which pixels are considered hot.
            min_area: Minimum contiguous pixel count to qualify.
            max_hotspots: Maximum number of hotspots to return.
            min_temp: Optional calibration floor temperature.
            max_temp: Optional calibration ceiling temperature.

        Returns:
            List of Hotspot objects sorted by peak_value descending.
        """
        threshold = float(np.percentile(frame, threshold_pct))
        mask = (frame > threshold).astype(np.uint8)
        components = _label_connected(mask)
        return _extract_hotspots(
            components, frame, min_area, max_hotspots,
            min_temp, max_temp,
        )


def _label_connected(mask: np.ndarray) -> np.ndarray:
    """Simple 4-connected component labeling without OpenCV."""
    labels = np.zeros_like(mask, dtype=np.int32)
    current_label = 0
    h, w = mask.shape

    for y in range(h):
        for x in range(w):
            if mask[y, x] == 0 or labels[y, x] != 0:
                continue
            current_label += 1
            _flood_fill(mask, labels, x, y, current_label, w, h)

    return labels


def _flood_fill(
    mask: np.ndarray,
    labels: np.ndarray,
    sx: int,
    sy: int,
    label: int,
    w: int,
    h: int,
) -> None:
    """Iterative flood fill to label a connected component."""
    stack = [(sx, sy)]
    while stack:
        cx, cy = stack.pop()
        if cx < 0 or cx >= w or cy < 0 or cy >= h:
            continue
        if mask[cy, cx] == 0 or labels[cy, cx] != 0:
            continue
        labels[cy, cx] = label
        stack.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])


def _extract_hotspots(
    components: np.ndarray,
    frame: np.ndarray,
    min_area: int,
    max_hotspots: int,
    min_temp: Optional[float],
    max_temp: Optional[float],
) -> List[Hotspot]:
    """Build Hotspot objects from labeled component image."""
    unique_labels = set(components.flatten()) - {0}
    hotspots: List[Hotspot] = []

    for label_id in unique_labels:
        ys, xs = np.where(components == label_id)
        area = len(xs)
        if area < min_area:
            continue
        peak = float(frame[ys, xs].max())
        cx = int(np.mean(xs))
        cy = int(np.mean(ys))
        x1, y1 = int(xs.min()), int(ys.min())
        x2, y2 = int(xs.max()), int(ys.max())
        temp = 0.0
        if min_temp is not None and max_temp is not None:
            temp = ThermalProcessor.pixel_to_temp(peak, min_temp, max_temp)
        hotspots.append(Hotspot(
            center=(cx, cy), area=area, peak_value=peak,
            bbox=(x1, y1, x2, y2), estimated_temp=temp,
        ))

    hotspots.sort(key=lambda h: h.peak_value, reverse=True)
    return hotspots[:max_hotspots]


# ---------------------------------------------------------------------------
# ThermalDetectorBackend
# ---------------------------------------------------------------------------


class ThermalDetectorBackend(DetectorBackend):
    """Fuses YOLO-based detection with thermal hotspot analysis.

    Preprocesses raw thermal frames through palette mapping before passing
    to a wrapped YOLO backend.  Hotspot detections are merged as a secondary
    signal: overlapping hotspots boost YOLO confidence and non-overlapping
    hotspots generate standalone thermal detections.
    """

    def __init__(
        self,
        yolo_backend: DetectorBackend,
        palette: str = "IRONBOW",
        hotspot_threshold_pct: float = 95.0,
        hotspot_min_area: int = 20,
        confidence_boost: float = 0.1,
        thermal_only_conf: float = 0.35,
    ) -> None:
        self._yolo = yolo_backend
        self._palette = palette
        self._processor = ThermalProcessor()
        self._hotspot_threshold = hotspot_threshold_pct
        self._hotspot_min_area = hotspot_min_area
        self._conf_boost = confidence_boost
        self._thermal_only_conf = thermal_only_conf

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run combined thermal + YOLO detection.

        Args:
            frame: Raw 16-bit thermal frame (2D) or 8-bit BGR (3-channel).

        Returns:
            Unified detection list with is_thermal flag on thermal-origin items.
        """
        raw_thermal, bgr = self._prepare_frames(frame)
        yolo_dets = self._yolo.detect(bgr)
        hotspots = self._processor.detect_hotspots(
            raw_thermal, self._hotspot_threshold, self._hotspot_min_area,
        )
        return _merge_detections(
            yolo_dets, hotspots, self._conf_boost, self._thermal_only_conf,
        )

    def _prepare_frames(
        self, frame: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Produce raw thermal (2D) and BGR (3-channel) from input."""
        if frame.ndim == 2:
            raw = frame
            bgr = self._processor.apply_palette(frame, self._palette)
        else:
            raw = _bgr_to_grayscale_16(frame)
            bgr = frame
        return raw, bgr

    def warmup(self) -> None:
        """Warm up the underlying YOLO backend."""
        self._yolo.warmup()
        logger.info("ThermalDetectorBackend warmup complete")


def _bgr_to_grayscale_16(bgr: np.ndarray) -> np.ndarray:
    """Convert 8-bit BGR to pseudo 16-bit grayscale."""
    gray = (
        0.114 * bgr[:, :, 0].astype(np.float64)
        + 0.587 * bgr[:, :, 1].astype(np.float64)
        + 0.299 * bgr[:, :, 2].astype(np.float64)
    )
    return (gray * 257.0).astype(np.uint16)


def _bbox_iou(
    a: Tuple[float, float, float, float],
    b: Tuple[int, int, int, int],
) -> float:
    """Compute IoU between two (x1, y1, x2, y2) bounding boxes."""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _merge_detections(
    yolo_dets: List[Detection],
    hotspots: List[Hotspot],
    conf_boost: float,
    thermal_only_conf: float,
) -> List[Detection]:
    """Merge YOLO and hotspot detections into a unified list.

    Overlapping hotspots boost YOLO confidence. Non-overlapping hotspots
    produce standalone thermal detections.
    """
    matched_hotspots: set[int] = set()
    results: List[Detection] = []

    for det in yolo_dets:
        boosted = _try_boost(det, hotspots, matched_hotspots, conf_boost)
        results.append(boosted)

    for idx, hs in enumerate(hotspots):
        if idx in matched_hotspots:
            continue
        results.append(_hotspot_to_detection(hs, thermal_only_conf))

    return results


def _try_boost(
    det: Detection,
    hotspots: List[Hotspot],
    matched: set[int],
    boost: float,
) -> Detection:
    """If a hotspot overlaps the YOLO detection, boost its confidence."""
    for idx, hs in enumerate(hotspots):
        if idx in matched:
            continue
        if _bbox_iou(det.bbox, hs.bbox) > 0.2:
            matched.add(idx)
            return Detection(
                class_name=det.class_name,
                confidence=min(1.0, round(det.confidence + boost, 4)),
                bbox=det.bbox,
                class_id=det.class_id,
                is_thermal=True,
            )
    return det


def _hotspot_to_detection(
    hs: Hotspot,
    conf: float,
) -> Detection:
    """Convert a standalone hotspot into a Detection."""
    return Detection(
        class_name="thermal_hotspot",
        confidence=round(conf, 4),
        bbox=(float(hs.bbox[0]), float(hs.bbox[1]),
              float(hs.bbox[2]), float(hs.bbox[3])),
        class_id=-1,
        is_thermal=True,
    )


# ---------------------------------------------------------------------------
# ThermalCameraSource
# ---------------------------------------------------------------------------


class ThermalCameraSource:
    """Abstraction over common thermal camera hardware.

    Supports FLIR Lepton (pure-thermal USB), Seek Thermal, and generic
    V4L2 thermal devices.  Falls back to synthetic thermal generation
    when no hardware is available.
    """

    def __init__(self) -> None:
        self._cap: object | None = None
        self._width: int = 160
        self._height: int = 120
        self._synthetic: bool = False

    def open(
        self,
        device: str = "/dev/video0",
        width: int = 160,
        height: int = 120,
    ) -> None:
        """Open a thermal camera device.

        Falls back to synthetic mode if the device cannot be opened.
        """
        self._width = width
        self._height = height
        self._cap = _try_open_v4l2(device, width, height)
        if self._cap is None:
            logger.warning(
                "Cannot open %s, falling back to synthetic thermal", device,
            )
            self._synthetic = True
        else:
            self._synthetic = False
            logger.info("Thermal camera opened: %s (%dx%d)", device, width, height)

    def read(self) -> np.ndarray:
        """Read a raw 16-bit thermal frame.

        Returns synthetic thermal data if no hardware is available.
        """
        if self._synthetic or self._cap is None:
            return _generate_blank_thermal(self._width, self._height)
        return _read_v4l2_frame(self._cap, self._width, self._height)

    def close(self) -> None:
        """Release the camera device."""
        if self._cap is not None:
            try:
                self._cap.release()  # type: ignore[union-attr]
            except Exception:
                pass
            self._cap = None
        logger.info("Thermal camera closed")

    @property
    def is_synthetic(self) -> bool:
        """True when running in synthetic fallback mode."""
        return self._synthetic


def _try_open_v4l2(
    device: str,
    width: int,
    height: int,
) -> object | None:
    """Attempt to open a V4L2 camera via OpenCV."""
    try:
        import cv2
        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        return cap
    except Exception:
        return None


def _read_v4l2_frame(
    cap: object,
    width: int,
    height: int,
) -> np.ndarray:
    """Read one frame from a V4L2 capture and return as 16-bit grayscale."""
    ret, frame = cap.read()  # type: ignore[union-attr]
    if not ret or frame is None:
        return _generate_blank_thermal(width, height)
    if frame.ndim == 3:
        frame = frame[:, :, 0].astype(np.uint16) * 257
    if frame.dtype != np.uint16:
        frame = frame.astype(np.uint16)
    return frame


def _generate_blank_thermal(width: int, height: int) -> np.ndarray:
    """Generate a blank 16-bit thermal frame with ambient noise."""
    rng = np.random.default_rng()
    base = np.full((height, width), 8000, dtype=np.uint16)
    noise = rng.integers(-200, 200, size=(height, width), dtype=np.int16)
    return (base.astype(np.int32) + noise.astype(np.int32)).clip(0, 65535).astype(np.uint16)


# ---------------------------------------------------------------------------
# Synthetic thermal generator
# ---------------------------------------------------------------------------


def simulate_thermal(
    rgb_frame: np.ndarray,
    num_hotspots: int = 3,
    hotspot_radius: int = 8,
    noise_std: float = 300.0,
) -> np.ndarray:
    """Convert an RGB image to a synthetic 16-bit thermal frame.

    Useful for development and demos when no thermal camera is available.

    Args:
        rgb_frame: H x W x 3 uint8 RGB image.
        num_hotspots: Number of random bright spots to inject.
        hotspot_radius: Pixel radius of each synthetic hotspot.
        noise_std: Standard deviation of additive Gaussian noise.

    Returns:
        H x W uint16 synthetic thermal image.
    """
    gray = _rgb_to_gray_float(rgb_frame)
    thermal = (gray * 40000 + 8000).astype(np.float64)
    rng = np.random.default_rng()
    thermal += rng.normal(0, noise_std, thermal.shape)
    thermal = _inject_hotspots(
        thermal, num_hotspots, hotspot_radius, rng,
    )
    return thermal.clip(0, 65535).astype(np.uint16)


def _rgb_to_gray_float(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB uint8 to normalized grayscale float."""
    return (
        0.299 * rgb[:, :, 0].astype(np.float64)
        + 0.587 * rgb[:, :, 1].astype(np.float64)
        + 0.114 * rgb[:, :, 2].astype(np.float64)
    ) / 255.0


def _inject_hotspots(
    thermal: np.ndarray,
    count: int,
    radius: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Place bright gaussian spots at random positions in the thermal image."""
    h, w = thermal.shape
    for _ in range(count):
        cy = rng.integers(radius, max(h - radius, radius + 1))
        cx = rng.integers(radius, max(w - radius, radius + 1))
        y_coords, x_coords = np.ogrid[
            max(0, cy - radius):min(h, cy + radius),
            max(0, cx - radius):min(w, cx + radius),
        ]
        dist_sq = (y_coords - cy) ** 2 + (x_coords - cx) ** 2
        spot = 20000.0 * np.exp(-dist_sq / (2.0 * (radius / 2.0) ** 2))
        thermal[
            max(0, cy - radius):min(h, cy + radius),
            max(0, cx - radius):min(w, cx + radius),
        ] += spot
    return thermal
