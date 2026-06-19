"""
Visual-RF sensor fusion correlator.

Merges pixel-space camera detections with RF sensor tracks (ODID, DJI DroneID)
into unified multi-sensor tracks. Uses camera calibration to project bounding
boxes to 3D rays, then correlates with existing RF tracks by angular proximity.

Optimal assignment via the Hungarian algorithm keeps identity stable when
multiple camera detections and RF tracks overlap in the field of view.
"""
from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from csontology import Detection, Track, TrackClass, Vec3

logger = logging.getLogger("overwatch.fusion.visual_correlator")

# Counter for visual-only track IDs.
_visual_id_counter = itertools.count(1)

# ---- Dataclasses ----


@dataclass(frozen=True)
class CameraDetection:
    """A single camera detection in pixel space.

    bbox is (x1, y1, x2, y2) in pixels. class_name and confidence come from
    the detector model. frame_timestamp is the capture time.
    """

    id: str
    bbox: Tuple[float, float, float, float]
    confidence: float
    class_name: str = "drone"
    frame_timestamp: float = 0.0


@dataclass
class CameraModel:
    """Pinhole camera calibration for pixel-to-world projection.

    focal_length_px: focal length in pixels (scalar, assumes square pixels).
    principal_point: (cx, cy) image center in pixels.
    position: camera position in ENU meters.
    rotation: 3x3 rotation matrix, camera-to-world (columns are camera axes
              expressed in ENU).
    image_size: (width, height) in pixels.
    """

    focal_length_px: float
    principal_point: Tuple[float, float]
    position: Vec3
    rotation: np.ndarray  # 3x3
    image_size: Tuple[int, int]

    def pixel_to_ray(self, u: float, v: float) -> np.ndarray:
        """Project a pixel coordinate to a unit-length 3D ray in ENU.

        Builds a direction vector in camera frame from the pinhole model, then
        rotates it into the world (ENU) frame using the extrinsic rotation.
        """
        cx, cy = self.principal_point
        f = self.focal_length_px
        cam_dir = np.array([(u - cx) / f, (v - cy) / f, 1.0])
        world_dir = self.rotation @ cam_dir
        norm = np.linalg.norm(world_dir)
        if norm < 1e-12:
            return np.array([0.0, 0.0, 1.0])
        return world_dir / norm

    def world_to_pixel(self, enu_pos: Vec3) -> Tuple[float, float]:
        """Project a world point (ENU) to pixel coordinates.

        Inverts the extrinsic rotation to get camera-frame coordinates, then
        applies the pinhole projection. Returns (u, v) in pixels.
        """
        rel = np.array(enu_pos) - np.array(self.position)
        cam_coords = self.rotation.T @ rel
        if abs(cam_coords[2]) < 1e-9:
            return (float("nan"), float("nan"))
        cx, cy = self.principal_point
        f = self.focal_length_px
        u = f * cam_coords[0] / cam_coords[2] + cx
        v = f * cam_coords[1] / cam_coords[2] + cy
        return (u, v)

    def estimate_range(
        self, bbox_height_px: float, target_height_m: float = 0.3
    ) -> float:
        """Estimate range to target from its apparent size in pixels.

        Uses the pinhole geometry: range = (f * real_height) / pixel_height.
        """
        if bbox_height_px <= 0:
            return float("inf")
        return (self.focal_length_px * target_height_m) / bbox_height_px


@dataclass(frozen=True)
class CorrelationResult:
    """One camera-to-RF-track correlation pair with scoring metadata."""

    camera_det_id: str
    track_id: str
    score: float
    range_m: float
    bearing_deg: float


# ---- Helper functions ----


def _bbox_center(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Return the pixel center of a bounding box (x1, y1, x2, y2)."""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_height(bbox: Tuple[float, float, float, float]) -> float:
    """Return the pixel height of a bounding box."""
    return abs(bbox[3] - bbox[1])


def _angular_distance(ray_a: np.ndarray, ray_b: np.ndarray) -> float:
    """Angular distance in radians between two unit vectors."""
    dot = float(np.clip(np.dot(ray_a, ray_b), -1.0, 1.0))
    return math.acos(dot)


def _bearing_from_vector(direction: np.ndarray) -> float:
    """Compute bearing in degrees (0=North, 90=East) from an ENU direction.

    ENU: x=East, y=North. Bearing is measured clockwise from North.
    """
    east, north = float(direction[0]), float(direction[1])
    bearing = math.degrees(math.atan2(east, north))
    return bearing % 360.0


def _ray_to_track(
    camera_pos: Vec3, track_pos: Vec3
) -> np.ndarray:
    """Unit direction vector from camera position to track position."""
    diff = np.array(track_pos) - np.array(camera_pos)
    norm = np.linalg.norm(diff)
    if norm < 1e-12:
        return np.array([0.0, 0.0, 1.0])
    return diff / norm


def _build_cost_matrix(
    camera_rays: List[np.ndarray],
    track_rays: List[np.ndarray],
) -> np.ndarray:
    """Build a cost matrix of angular distances (radians).

    Rows are camera detections, columns are RF tracks.
    """
    n_cam = len(camera_rays)
    n_trk = len(track_rays)
    cost = np.full((n_cam, n_trk), fill_value=math.pi, dtype=np.float64)
    for i in range(n_cam):
        for j in range(n_trk):
            cost[i, j] = _angular_distance(camera_rays[i], track_rays[j])
    return cost


def correlate_frame(
    camera_dets: List[CameraDetection],
    active_tracks: List[Track],
    camera_model: CameraModel,
    gate_distance_m: float = 15.0,
    target_height_m: float = 0.3,
) -> List[CorrelationResult]:
    """Correlate camera detections with RF tracks for one frame.

    Uses the Hungarian algorithm for optimal assignment. The cost is the angular
    distance between the camera detection ray and the bearing to each track from
    the camera position. Pairs beyond the angular gate are rejected.

    Returns a CorrelationResult for every camera detection, including unmatched
    ones (track_id will be empty string, score 0).
    """
    if not camera_dets:
        return []

    camera_rays = _compute_camera_rays(camera_dets, camera_model)
    track_rays = _compute_track_rays(active_tracks, camera_model)
    ranges = _estimate_ranges(camera_dets, camera_model, target_height_m)

    if not active_tracks:
        return _unmatched_results(camera_dets, camera_rays, ranges)

    gate_rad = _range_to_angular_gate(gate_distance_m, ranges)
    cost = _build_cost_matrix(camera_rays, track_rays)
    return _assign_and_score(
        camera_dets, active_tracks, camera_rays, track_rays,
        cost, gate_rad, ranges,
    )


def _compute_camera_rays(
    dets: List[CameraDetection], cam: CameraModel
) -> List[np.ndarray]:
    """Compute world-frame rays for each camera detection."""
    rays: List[np.ndarray] = []
    for det in dets:
        cu, cv = _bbox_center(det.bbox)
        rays.append(cam.pixel_to_ray(cu, cv))
    return rays


def _compute_track_rays(
    tracks: List[Track], cam: CameraModel
) -> List[np.ndarray]:
    """Compute direction vectors from camera to each track."""
    return [_ray_to_track(cam.position, t.position) for t in tracks]


def _estimate_ranges(
    dets: List[CameraDetection],
    cam: CameraModel,
    target_height_m: float,
) -> List[float]:
    """Estimate range to each camera detection from bbox size."""
    return [
        cam.estimate_range(_bbox_height(d.bbox), target_height_m)
        for d in dets
    ]


def _range_to_angular_gate(
    gate_m: float, ranges: List[float]
) -> List[float]:
    """Convert a linear gate distance to per-detection angular gates.

    gate_rad = atan(gate_m / range). Capped at pi/4 for very close targets.
    """
    gates: List[float] = []
    for r in ranges:
        if r <= 0 or math.isinf(r):
            gates.append(math.pi / 4.0)
        else:
            gates.append(min(math.atan(gate_m / r), math.pi / 4.0))
    return gates


def _unmatched_results(
    dets: List[CameraDetection],
    rays: List[np.ndarray],
    ranges: List[float],
) -> List[CorrelationResult]:
    """Build results when there are no RF tracks to match against."""
    results: List[CorrelationResult] = []
    for i, det in enumerate(dets):
        bearing = _bearing_from_vector(rays[i])
        results.append(CorrelationResult(
            camera_det_id=det.id,
            track_id="",
            score=0.0,
            range_m=ranges[i],
            bearing_deg=bearing,
        ))
    return results


def _assign_and_score(
    dets: List[CameraDetection],
    tracks: List[Track],
    cam_rays: List[np.ndarray],
    trk_rays: List[np.ndarray],
    cost: np.ndarray,
    gate_rad: List[float],
    ranges: List[float],
) -> List[CorrelationResult]:
    """Run Hungarian assignment and build scored results."""
    row_idx, col_idx = linear_sum_assignment(cost)
    matched_cams: set[int] = set()
    results: List[CorrelationResult] = []

    for r, c in zip(row_idx, col_idx):
        if cost[r, c] <= gate_rad[r]:
            score = max(0.0, 1.0 - cost[r, c] / gate_rad[r])
            bearing = _bearing_from_vector(cam_rays[r])
            results.append(CorrelationResult(
                camera_det_id=dets[r].id,
                track_id=tracks[c].id,
                score=score,
                range_m=ranges[r],
                bearing_deg=bearing,
            ))
            matched_cams.add(r)

    for i, det in enumerate(dets):
        if i not in matched_cams:
            bearing = _bearing_from_vector(cam_rays[i])
            results.append(CorrelationResult(
                camera_det_id=det.id,
                track_id="",
                score=0.0,
                range_m=ranges[i],
                bearing_deg=bearing,
            ))

    return results


# ---- VisualCorrelator class ----


class VisualCorrelator:
    """Stateful correlator that fuses camera and RF tracks over time.

    Maintains a set of visual-only tracks for camera detections that have no
    RF match, and boosts confidence on RF tracks that receive camera correlation.
    """

    def __init__(
        self,
        camera_model: CameraModel,
        gate_distance_m: float = 15.0,
        confidence_boost: float = 0.15,
        target_height_m: float = 0.3,
    ) -> None:
        self._camera = camera_model
        self._gate_m = gate_distance_m
        self._boost = confidence_boost
        self._target_h = target_height_m
        self._visual_tracks: dict[str, Track] = {}
        self._id_counter = itertools.count(1)

    @property
    def visual_tracks(self) -> List[Track]:
        """Return current visual-only tracks."""
        return list(self._visual_tracks.values())

    def update(
        self,
        camera_dets: List[CameraDetection],
        rf_tracks: List[Track],
        timestamp: float = 0.0,
    ) -> List[CorrelationResult]:
        """Correlate one frame of camera detections against RF tracks.

        Matched RF tracks receive a confidence boost (capped at 1.0).
        Unmatched camera detections spawn or update visual-only tracks.

        Returns the full list of CorrelationResults for this frame.
        """
        results = correlate_frame(
            camera_dets, rf_tracks, self._camera,
            gate_distance_m=self._gate_m,
            target_height_m=self._target_h,
        )
        self._apply_boosts(results, rf_tracks)
        self._update_visual_tracks(results, camera_dets, timestamp)
        return results

    def _apply_boosts(
        self,
        results: List[CorrelationResult],
        rf_tracks: List[Track],
    ) -> None:
        """Boost confidence on RF tracks that correlate with camera."""
        track_map = {t.id: t for t in rf_tracks}
        for cr in results:
            if cr.track_id and cr.track_id in track_map:
                trk = track_map[cr.track_id]
                trk.confidence = min(1.0, trk.confidence + self._boost)

    def _update_visual_tracks(
        self,
        results: List[CorrelationResult],
        camera_dets: List[CameraDetection],
        timestamp: float,
    ) -> None:
        """Spawn or update visual-only tracks for unmatched detections."""
        det_map = {d.id: d for d in camera_dets}
        seen_ids: set[str] = set()

        for cr in results:
            if cr.track_id:
                continue
            det = det_map.get(cr.camera_det_id)
            if det is None:
                continue
            self._upsert_visual_track(cr, det, timestamp)
            seen_ids.add(cr.camera_det_id)

        self._expire_stale_visual_tracks(seen_ids, timestamp)

    def _upsert_visual_track(
        self,
        cr: CorrelationResult,
        det: CameraDetection,
        timestamp: float,
    ) -> None:
        """Create or update a visual-only track from camera detection."""
        cu, cv = _bbox_center(det.bbox)
        ray = self._camera.pixel_to_ray(cu, cv)
        pos = _ray_position(self._camera.position, ray, cr.range_m)

        if det.id in self._visual_tracks:
            trk = self._visual_tracks[det.id]
            trk.position = pos
            trk.last_update = timestamp
            trk.confidence = min(1.0, det.confidence)
        else:
            trk = Track(
                id=f"vis-{next(self._id_counter)}",
                position=pos,
                velocity=(0.0, 0.0, 0.0),
                covariance=(25.0, 25.0, 25.0),
                last_update=timestamp,
                confidence=det.confidence,
                classification=TrackClass.UNKNOWN,
                source_detection_ids=[det.id],
            )
            self._visual_tracks[det.id] = trk

    def _expire_stale_visual_tracks(
        self, seen_ids: set[str], timestamp: float
    ) -> None:
        """Remove visual tracks not seen this frame after a grace period."""
        stale = [
            k for k, v in self._visual_tracks.items()
            if k not in seen_ids and (timestamp - v.last_update) > 2.0
        ]
        for k in stale:
            del self._visual_tracks[k]


def _ray_position(
    origin: Vec3, ray: np.ndarray, distance: float
) -> Vec3:
    """Compute a 3D position along a ray from origin at given distance."""
    if math.isinf(distance) or distance <= 0:
        distance = 100.0  # fallback
    pos = np.array(origin) + ray * distance
    return (float(pos[0]), float(pos[1]), float(pos[2]))
