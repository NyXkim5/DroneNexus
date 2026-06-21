"""
Monocular 3D localization for estimating drone GPS position from a single camera.

Estimates 3D world position (ENU meters) from a 2D bounding box detection
using pinhole camera geometry. Combines range estimation (from apparent size)
with camera bearing to produce a full 3D position. Optionally converts ENU
to geodetic coordinates via WGS84.

Range estimation supports three modes:
  SIZE_BASED  -- focal_length * known_size / bbox_pixels (default)
  ALTITUDE_BASED -- elevation angle + known altitude ceiling
  MULTI_CUE -- combines size + temporal consistency smoothing
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("overwatch.vision.localizer_3d")

# WGS84 ellipsoid parameters
_WGS84_A = 6378137.0  # semi-major axis in meters
_WGS84_E2 = 6.6943799901377997e-3  # first eccentricity squared


# ---------------------------------------------------------------------------
# Range estimation method
# ---------------------------------------------------------------------------


class RangeMethod(Enum):
    """Strategy for estimating range to a detected drone."""

    SIZE_BASED = "size_based"
    ALTITUDE_BASED = "altitude_based"
    MULTI_CUE = "multi_cue"


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class LocalizedDetection:
    """Result of monocular 3D localization from a single bounding box."""

    detection_id: str
    position_enu: Tuple[float, float, float]  # (east, north, up) meters
    position_geo: Tuple[float, float, float]  # (lat, lon, alt) if origin provided
    range_m: float
    bearing_deg: float  # compass bearing from camera (0=N, 90=E)
    elevation_deg: float  # angle above horizon
    size_estimate_m: float  # estimated real-world size
    confidence: float  # 0-1, decreases with range


# ---------------------------------------------------------------------------
# Camera intrinsics for localization
# ---------------------------------------------------------------------------


@dataclass
class CameraIntrinsics:
    """Pinhole camera parameters for 3D localization.

    focal_length_px: focal length in pixels (assumes square pixels).
    image_width: sensor width in pixels.
    image_height: sensor height in pixels.
    """

    focal_length_px: float
    image_width: int = 1920
    image_height: int = 1080

    @property
    def cx(self) -> float:
        """Principal point x (image center)."""
        return self.image_width / 2.0

    @property
    def cy(self) -> float:
        """Principal point y (image center)."""
        return self.image_height / 2.0


# ---------------------------------------------------------------------------
# ENU to geodetic (WGS84)
# ---------------------------------------------------------------------------


def enu_to_geodetic(
    east: float,
    north: float,
    up: float,
    origin_lat: float,
    origin_lon: float,
    origin_alt: float = 0.0,
) -> Tuple[float, float, float]:
    """Convert ENU meters to geodetic (lat, lon, alt) using WGS84.

    Uses the standard linearized ENU-to-geodetic transform. Accurate to
    centimeter level within a few kilometers of the origin.
    """
    lat_rad = math.radians(origin_lat)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)

    n_radius = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    m_radius = n_radius * (1.0 - _WGS84_E2) / (1.0 - _WGS84_E2 * sin_lat * sin_lat)

    dlat = math.degrees(north / m_radius)
    dlon = math.degrees(east / (n_radius * cos_lat))

    lat = origin_lat + dlat
    lon = origin_lon + dlon
    alt = origin_alt + up
    return (lat, lon, alt)


# ---------------------------------------------------------------------------
# Confidence model
# ---------------------------------------------------------------------------

# Range at which confidence drops to 0.5 (inverse-square falloff reference)
_CONF_HALF_RANGE_M = 500.0

# Minimum bbox dimension in pixels for full pixel confidence
_CONF_MIN_BBOX_PX = 10.0

# Maximum pixel dimension that gives full pixel confidence
_CONF_MAX_BBOX_PX = 100.0


def compute_confidence(
    range_m: float,
    bbox_size_px: float,
    temporal_hits: int = 1,
) -> float:
    """Compute detection confidence from range, bbox size, and temporal hits.

    Confidence decreases with range via inverse-square falloff referenced to
    _CONF_HALF_RANGE_M. Small bounding boxes (fewer pixels) reduce confidence.
    Temporal consistency (seeing the target across multiple frames) boosts it.
    """
    range_conf = _range_confidence(range_m)
    pixel_conf = _pixel_confidence(bbox_size_px)
    temporal_conf = _temporal_confidence(temporal_hits)
    return max(0.0, min(1.0, range_conf * pixel_conf * temporal_conf))


def _range_confidence(range_m: float) -> float:
    """Inverse-square confidence falloff with range."""
    if range_m <= 0:
        return 1.0
    return _CONF_HALF_RANGE_M**2 / (_CONF_HALF_RANGE_M**2 + range_m**2)


def _pixel_confidence(bbox_size_px: float) -> float:
    """Confidence factor from bounding box pixel size."""
    if bbox_size_px <= 0:
        return 0.1
    clamped = min(bbox_size_px, _CONF_MAX_BBOX_PX)
    return max(0.1, clamped / _CONF_MAX_BBOX_PX)


def _temporal_confidence(hits: int) -> float:
    """Confidence boost from temporal consistency (multiple frame detections)."""
    if hits <= 0:
        return 0.5
    # Saturates at 1.0 after about 5 consecutive hits
    return min(1.0, 0.6 + 0.1 * hits)


# ---------------------------------------------------------------------------
# Range estimation functions
# ---------------------------------------------------------------------------


def estimate_range_size_based(
    focal_length_px: float,
    known_size_m: float,
    bbox_size_px: float,
) -> float:
    """Estimate range using pinhole geometry: range = f * real_size / pixel_size."""
    if bbox_size_px <= 0:
        return float("inf")
    return (focal_length_px * known_size_m) / bbox_size_px


def estimate_range_altitude_based(
    elevation_deg: float,
    altitude_ceiling_m: float,
) -> float:
    """Estimate range from elevation angle and a known altitude ceiling.

    Assumes the drone is at the altitude ceiling and the camera looks upward.
    range = altitude / sin(elevation).
    """
    if elevation_deg <= 0:
        return float("inf")
    sin_elev = math.sin(math.radians(elevation_deg))
    if sin_elev < 1e-6:
        return float("inf")
    return altitude_ceiling_m / sin_elev


def estimate_range_multi_cue(
    focal_length_px: float,
    known_size_m: float,
    bbox_size_px: float,
    elevation_deg: float,
    altitude_ceiling_m: float,
    prev_range_m: Optional[float] = None,
    temporal_weight: float = 0.3,
) -> float:
    """Combine size-based and altitude-based range with temporal smoothing.

    Averages the two geometric estimates (when both are finite), then blends
    with the previous frame's range estimate for temporal consistency.
    """
    r_size = estimate_range_size_based(focal_length_px, known_size_m, bbox_size_px)
    r_alt = estimate_range_altitude_based(elevation_deg, altitude_ceiling_m)

    fused = _fuse_range_estimates(r_size, r_alt)
    if fused == float("inf"):
        return fused

    if prev_range_m is not None and not math.isinf(prev_range_m):
        fused = (1.0 - temporal_weight) * fused + temporal_weight * prev_range_m

    return fused


def _fuse_range_estimates(r1: float, r2: float) -> float:
    """Average two range estimates, ignoring infinite values."""
    r1_valid = not math.isinf(r1) and r1 > 0
    r2_valid = not math.isinf(r2) and r2 > 0
    if r1_valid and r2_valid:
        return (r1 + r2) / 2.0
    if r1_valid:
        return r1
    if r2_valid:
        return r2
    return float("inf")


# ---------------------------------------------------------------------------
# Bearing and elevation from pixel coordinates
# ---------------------------------------------------------------------------


def compute_bearing_deg(
    u: float,
    camera: CameraIntrinsics,
    camera_heading_deg: float = 0.0,
) -> float:
    """Compute compass bearing to a detection from its horizontal pixel offset.

    camera_heading_deg is the compass direction the camera center points toward.
    Positive pixel offset (right of center) adds to bearing.
    """
    dx = u - camera.cx
    angle_offset = math.degrees(math.atan2(dx, camera.focal_length_px))
    bearing = (camera_heading_deg + angle_offset) % 360.0
    return bearing


def compute_elevation_deg(
    v: float,
    camera: CameraIntrinsics,
    camera_tilt_deg: float = 0.0,
) -> float:
    """Compute elevation angle (above horizon) from vertical pixel position.

    camera_tilt_deg is the tilt of the camera above the horizon (positive = up).
    Objects above image center have positive elevation relative to the camera.
    """
    dy = camera.cy - v  # flip: pixel y increases downward
    angle_offset = math.degrees(math.atan2(dy, camera.focal_length_px))
    return camera_tilt_deg + angle_offset


# ---------------------------------------------------------------------------
# Bounding box helpers
# ---------------------------------------------------------------------------


def _bbox_max_dim(bbox: Tuple[float, float, float, float]) -> float:
    """Return the larger dimension (width or height) of a bbox in pixels."""
    w = abs(bbox[2] - bbox[0])
    h = abs(bbox[3] - bbox[1])
    return max(w, h)


def _bbox_center(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Return the pixel center of a bounding box (x1, y1, x2, y2)."""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


# ---------------------------------------------------------------------------
# ENU position from range + bearing + elevation
# ---------------------------------------------------------------------------


def range_bearing_elev_to_enu(
    range_m: float,
    bearing_deg: float,
    elevation_deg: float,
    camera_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Tuple[float, float, float]:
    """Convert range/bearing/elevation to an ENU position offset from camera."""
    bearing_rad = math.radians(bearing_deg)
    elev_rad = math.radians(elevation_deg)

    horiz_range = range_m * math.cos(elev_rad)
    east = camera_enu[0] + horiz_range * math.sin(bearing_rad)
    north = camera_enu[1] + horiz_range * math.cos(bearing_rad)
    up = camera_enu[2] + range_m * math.sin(elev_rad)
    return (east, north, up)


# ---------------------------------------------------------------------------
# MonocularLocalizer
# ---------------------------------------------------------------------------


class MonocularLocalizer:
    """Estimates 3D world position from a 2D bounding box detection.

    Uses known/estimated drone size to compute range from apparent size,
    then combines range + camera bearing to produce an ENU position.
    """

    def __init__(
        self,
        camera: CameraIntrinsics,
        known_size_m: float = 0.5,
        range_method: RangeMethod = RangeMethod.SIZE_BASED,
        camera_heading_deg: float = 0.0,
        camera_tilt_deg: float = 0.0,
        camera_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        origin_lat: Optional[float] = None,
        origin_lon: Optional[float] = None,
        origin_alt: float = 0.0,
        altitude_ceiling_m: float = 120.0,
    ) -> None:
        self._camera = camera
        self._known_size_m = known_size_m
        self._range_method = range_method
        self._heading = camera_heading_deg
        self._tilt = camera_tilt_deg
        self._camera_enu = camera_enu
        self._origin_lat = origin_lat
        self._origin_lon = origin_lon
        self._origin_alt = origin_alt
        self._altitude_ceiling = altitude_ceiling_m
        self._prev_ranges: Dict[str, float] = {}
        self._temporal_hits: Dict[str, int] = {}

    @property
    def camera(self) -> CameraIntrinsics:
        return self._camera

    @property
    def range_method(self) -> RangeMethod:
        return self._range_method

    @range_method.setter
    def range_method(self, value: RangeMethod) -> None:
        self._range_method = value

    def localize(
        self,
        bbox: Tuple[float, float, float, float],
        detection_id: Optional[str] = None,
    ) -> LocalizedDetection:
        """Estimate 3D position from a single bounding box detection.

        Args:
            bbox: (x1, y1, x2, y2) in pixels.
            detection_id: optional ID to track across frames.

        Returns:
            LocalizedDetection with ENU position, geodetic position, range,
            bearing, elevation, estimated size, and confidence.
        """
        det_id = detection_id or str(uuid.uuid4())
        cu, cv = _bbox_center(bbox)
        bbox_dim = _bbox_max_dim(bbox)

        bearing = compute_bearing_deg(cu, self._camera, self._heading)
        elevation = compute_elevation_deg(cv, self._camera, self._tilt)
        range_m = self._estimate_range(det_id, bbox_dim, elevation)

        return self._build_result(det_id, bbox_dim, range_m, bearing, elevation)

    def _estimate_range(
        self,
        det_id: str,
        bbox_dim: float,
        elevation_deg: float,
    ) -> float:
        """Dispatch to the configured range estimation method."""
        prev = self._prev_ranges.get(det_id)

        if self._range_method == RangeMethod.SIZE_BASED:
            r = estimate_range_size_based(
                self._camera.focal_length_px, self._known_size_m, bbox_dim,
            )
        elif self._range_method == RangeMethod.ALTITUDE_BASED:
            r = estimate_range_altitude_based(elevation_deg, self._altitude_ceiling)
        else:
            r = estimate_range_multi_cue(
                self._camera.focal_length_px, self._known_size_m, bbox_dim,
                elevation_deg, self._altitude_ceiling, prev,
            )

        self._update_tracking(det_id, r)
        return r

    def _update_tracking(self, det_id: str, range_m: float) -> None:
        """Update temporal tracking state for a detection ID."""
        self._prev_ranges[det_id] = range_m
        self._temporal_hits[det_id] = self._temporal_hits.get(det_id, 0) + 1

    def _build_result(
        self,
        det_id: str,
        bbox_dim: float,
        range_m: float,
        bearing_deg: float,
        elevation_deg: float,
    ) -> LocalizedDetection:
        """Assemble the final LocalizedDetection from computed values."""
        enu = range_bearing_elev_to_enu(
            range_m, bearing_deg, elevation_deg, self._camera_enu,
        )
        geo = self._to_geodetic(enu)
        size_est = self._estimate_real_size(bbox_dim, range_m)
        hits = self._temporal_hits.get(det_id, 1)
        conf = compute_confidence(range_m, bbox_dim, hits)

        return LocalizedDetection(
            detection_id=det_id,
            position_enu=enu,
            position_geo=geo,
            range_m=range_m,
            bearing_deg=bearing_deg,
            elevation_deg=elevation_deg,
            size_estimate_m=size_est,
            confidence=conf,
        )

    def _to_geodetic(
        self, enu: Tuple[float, float, float]
    ) -> Tuple[float, float, float]:
        """Convert ENU to geodetic if origin is configured."""
        if self._origin_lat is None or self._origin_lon is None:
            return (0.0, 0.0, 0.0)
        return enu_to_geodetic(
            enu[0], enu[1], enu[2],
            self._origin_lat, self._origin_lon, self._origin_alt,
        )

    def _estimate_real_size(self, bbox_dim_px: float, range_m: float) -> float:
        """Back-compute estimated real-world size from range and bbox pixels."""
        if bbox_dim_px <= 0 or math.isinf(range_m) or range_m <= 0:
            return 0.0
        return (bbox_dim_px * range_m) / self._camera.focal_length_px

    def clear_tracking(self) -> None:
        """Reset temporal tracking state."""
        self._prev_ranges.clear()
        self._temporal_hits.clear()
