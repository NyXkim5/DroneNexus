"""
ByteTrack multi-object tracker for the OVERWATCH vision pipeline.

Assigns persistent track IDs across frames using a Kalman filter for motion
prediction and two-stage IoU-based Hungarian assignment. High-confidence
detections are matched first, then low-confidence detections fill remaining
unmatched tracks. This prevents ID switches when the same drone is re-detected
at varying confidence levels.

Pure Python + numpy + scipy. No external tracking libraries required.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from vision.tensorrt_detector import Detection, DetectorBackend

logger = logging.getLogger("overwatch.vision.bytetrack")


# ---------------------------------------------------------------------------
# TrackedObject -- public output dataclass
# ---------------------------------------------------------------------------

@dataclass
class TrackedObject:
    """A detection with a persistent track identity across frames."""

    track_id: int
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    class_name: str
    confidence: float
    state: str  # NEW, TRACKED, LOST
    age: int  # frames since first seen
    hits: int  # total frames matched
    velocity: Tuple[float, float]  # (vx, vy) pixels/frame


# ---------------------------------------------------------------------------
# Track state enum
# ---------------------------------------------------------------------------

class TrackState(Enum):
    NEW = "NEW"
    TRACKED = "TRACKED"
    LOST = "LOST"
    REMOVED = "REMOVED"


# ---------------------------------------------------------------------------
# Kalman filter -- 8-state constant velocity model
# ---------------------------------------------------------------------------

class KalmanFilter:
    """Linear Kalman filter for bounding box tracking.

    State vector: [cx, cy, w, h, vcx, vcy, vw, vh]
    Measurement:  [cx, cy, w, h]
    """

    _NDIM = 4
    _DT = 1.0  # one frame

    def __init__(self) -> None:
        self._motion_mat = np.eye(8, dtype=np.float64)
        for i in range(self._NDIM):
            self._motion_mat[i, self._NDIM + i] = self._DT

        self._update_mat = np.eye(self._NDIM, 8, dtype=np.float64)

        self._std_weight_position = 1.0 / 20.0
        self._std_weight_velocity = 1.0 / 160.0

    def initiate(
        self, measurement: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Create a new track from an unassociated measurement."""
        mean_pos = measurement.copy()
        mean_vel = np.zeros(self._NDIM, dtype=np.float64)
        mean = np.concatenate([mean_pos, mean_vel])

        std = _init_covariance_std(measurement, self._std_weight_position)
        covariance = np.diag(np.square(std))
        return mean, covariance

    def predict(
        self, mean: np.ndarray, covariance: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run the Kalman filter prediction step."""
        std = _predict_noise_std(mean, self._std_weight_position, self._std_weight_velocity)
        motion_cov = np.diag(np.square(std))

        mean = self._motion_mat @ mean
        covariance = (
            self._motion_mat @ covariance @ self._motion_mat.T + motion_cov
        )
        return mean, covariance

    def update(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurement: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run the Kalman filter correction step."""
        std = _update_noise_std(mean, self._std_weight_position)
        innovation_cov = np.diag(np.square(std))

        projected_mean = self._update_mat @ mean
        projected_cov = (
            self._update_mat @ covariance @ self._update_mat.T + innovation_cov
        )

        kalman_gain = np.linalg.solve(
            projected_cov.T,
            (covariance @ self._update_mat.T).T,
        ).T

        innovation = measurement - projected_mean
        new_mean = mean + kalman_gain @ innovation
        new_covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return new_mean, new_covariance


def _init_covariance_std(
    measurement: np.ndarray, weight_pos: float,
) -> np.ndarray:
    """Standard deviations for initial covariance matrix."""
    h = measurement[3]
    return np.array([
        2.0 * weight_pos * h,
        2.0 * weight_pos * h,
        1e-2,
        2.0 * weight_pos * h,
        10.0 * weight_pos * h,
        10.0 * weight_pos * h,
        1e-5,
        10.0 * weight_pos * h,
    ])


def _predict_noise_std(
    mean: np.ndarray, weight_pos: float, weight_vel: float,
) -> np.ndarray:
    """Process noise standard deviations for prediction step."""
    h = mean[3]
    return np.array([
        weight_pos * h,
        weight_pos * h,
        1e-2,
        weight_pos * h,
        weight_vel * h,
        weight_vel * h,
        1e-5,
        weight_vel * h,
    ])


def _update_noise_std(
    mean: np.ndarray, weight_pos: float,
) -> np.ndarray:
    """Measurement noise standard deviations for update step."""
    h = mean[3]
    return np.array([
        weight_pos * h,
        weight_pos * h,
        1e-2,
        weight_pos * h,
    ])


# ---------------------------------------------------------------------------
# STrack -- single tracked object with Kalman state
# ---------------------------------------------------------------------------

_GLOBAL_TRACK_ID = 0


def _next_track_id() -> int:
    """Monotonically increasing track ID generator."""
    global _GLOBAL_TRACK_ID
    _GLOBAL_TRACK_ID += 1
    return _GLOBAL_TRACK_ID


def reset_track_id_counter() -> None:
    """Reset the global track ID counter. Useful for tests."""
    global _GLOBAL_TRACK_ID
    _GLOBAL_TRACK_ID = 0


class STrack:
    """Single track with Kalman filter state."""

    shared_kalman = KalmanFilter()

    def __init__(self, detection: Detection) -> None:
        self.track_id: int = 0  # assigned on activation
        self.state: TrackState = TrackState.NEW
        self.class_name: str = detection.class_name
        self.confidence: float = detection.confidence
        self.age: int = 0
        self.hits: int = 0
        self.time_since_update: int = 0

        self._mean: Optional[np.ndarray] = None
        self._covariance: Optional[np.ndarray] = None

        measurement = _bbox_to_measurement(detection.bbox)
        self._mean, self._covariance = self.shared_kalman.initiate(measurement)
        self.hits = 1
        self.age = 1

    def predict(self) -> None:
        """Advance state by one frame using the Kalman prediction."""
        if self._mean is not None and self._covariance is not None:
            self._mean, self._covariance = self.shared_kalman.predict(
                self._mean, self._covariance,
            )
        self.age += 1

    def update(self, detection: Detection) -> None:
        """Correct state with a matched detection."""
        measurement = _bbox_to_measurement(detection.bbox)
        if self._mean is not None and self._covariance is not None:
            self._mean, self._covariance = self.shared_kalman.update(
                self._mean, self._covariance, measurement,
            )
        self.class_name = detection.class_name
        self.confidence = detection.confidence
        self.hits += 1
        self.time_since_update = 0

    def activate(self) -> None:
        """Assign a track ID and mark as active."""
        self.track_id = _next_track_id()
        self.state = TrackState.NEW

    def mark_tracked(self) -> None:
        """Promote to TRACKED state after confirmation threshold."""
        self.state = TrackState.TRACKED

    def mark_lost(self) -> None:
        """Mark as LOST when no detection matches this frame."""
        self.state = TrackState.LOST
        self.time_since_update += 1

    def mark_removed(self) -> None:
        """Mark for deletion."""
        self.state = TrackState.REMOVED

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """Current bounding box as (x1, y1, x2, y2)."""
        if self._mean is None:
            return (0.0, 0.0, 0.0, 0.0)
        return _measurement_to_bbox(self._mean[:4])

    @property
    def velocity(self) -> Tuple[float, float]:
        """Velocity in pixels per frame (vx, vy)."""
        if self._mean is None:
            return (0.0, 0.0)
        return (float(self._mean[4]), float(self._mean[5]))

    def to_tracked_object(self) -> TrackedObject:
        """Convert to the public TrackedObject dataclass."""
        return TrackedObject(
            track_id=self.track_id,
            bbox=self.bbox,
            class_name=self.class_name,
            confidence=self.confidence,
            state=self.state.value,
            age=self.age,
            hits=self.hits,
            velocity=self.velocity,
        )


# ---------------------------------------------------------------------------
# Bbox conversion helpers
# ---------------------------------------------------------------------------

def _bbox_to_measurement(
    bbox: Tuple[float, float, float, float],
) -> np.ndarray:
    """Convert (x1, y1, x2, y2) to Kalman measurement [cx, cy, w, h]."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    cx = x1 + w / 2.0
    cy = y1 + h / 2.0
    return np.array([cx, cy, w, h], dtype=np.float64)


def _measurement_to_bbox(
    measurement: np.ndarray,
) -> Tuple[float, float, float, float]:
    """Convert Kalman state [cx, cy, w, h, ...] to (x1, y1, x2, y2)."""
    cx, cy, w, h = measurement[0], measurement[1], measurement[2], measurement[3]
    x1 = cx - w / 2.0
    y1 = cy - h / 2.0
    x2 = cx + w / 2.0
    y2 = cy + h / 2.0
    return (float(x1), float(y1), float(x2), float(y2))


# ---------------------------------------------------------------------------
# IoU computation -- vectorized
# ---------------------------------------------------------------------------

def iou_batch(
    boxes_a: np.ndarray, boxes_b: np.ndarray,
) -> np.ndarray:
    """Compute IoU matrix between two sets of (x1, y1, x2, y2) boxes.

    Args:
        boxes_a: (N, 4) array of boxes.
        boxes_b: (M, 4) array of boxes.

    Returns:
        (N, M) IoU matrix.
    """
    if boxes_a.size == 0 or boxes_b.size == 0:
        return np.zeros((len(boxes_a), len(boxes_b)), dtype=np.float64)

    a = boxes_a[:, np.newaxis, :]  # (N, 1, 4)
    b = boxes_b[np.newaxis, :, :]  # (1, M, 4)

    inter_x1 = np.maximum(a[..., 0], b[..., 0])
    inter_y1 = np.maximum(a[..., 1], b[..., 1])
    inter_x2 = np.minimum(a[..., 2], b[..., 2])
    inter_y2 = np.minimum(a[..., 3], b[..., 3])

    inter_w = np.maximum(0.0, inter_x2 - inter_x1)
    inter_h = np.maximum(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])

    union = area_a + area_b - inter_area
    return np.where(union > 0, inter_area / union, 0.0)


# ---------------------------------------------------------------------------
# Linear assignment with threshold gating
# ---------------------------------------------------------------------------

def linear_assignment_with_thresh(
    cost_matrix: np.ndarray, thresh: float,
) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """Hungarian assignment with a cost threshold gate.

    Args:
        cost_matrix: (N, M) cost matrix (lower is better).
        thresh: Maximum cost for a valid assignment.

    Returns:
        matches:      list of (row, col) pairs.
        unmatched_a:  unmatched row indices.
        unmatched_b:  unmatched column indices.
    """
    if cost_matrix.size == 0:
        return (
            [],
            list(range(cost_matrix.shape[0])),
            list(range(cost_matrix.shape[1])),
        )

    row_idx, col_idx = linear_sum_assignment(cost_matrix)

    matches: List[Tuple[int, int]] = []
    matched_rows: set = set()
    matched_cols: set = set()

    for r, c in zip(row_idx, col_idx):
        if cost_matrix[r, c] <= thresh:
            matches.append((r, c))
            matched_rows.add(r)
            matched_cols.add(c)

    unmatched_a = [i for i in range(cost_matrix.shape[0]) if i not in matched_rows]
    unmatched_b = [j for j in range(cost_matrix.shape[1]) if j not in matched_cols]

    return matches, unmatched_a, unmatched_b


# ---------------------------------------------------------------------------
# ByteTracker -- main tracker class
# ---------------------------------------------------------------------------

class ByteTracker:
    """ByteTrack multi-object tracker.

    Two-stage association:
    1. High-confidence detections matched to all active tracks via IoU.
    2. Low-confidence detections matched to remaining unmatched tracks.
    3. Unmatched high-conf detections start new tracks.
    4. Tracks with no match for track_buffer frames are removed.
    """

    def __init__(
        self,
        track_thresh: float = 0.5,
        match_thresh: float = 0.8,
        track_buffer: int = 30,
        confirm_frames: int = 3,
    ) -> None:
        self.track_thresh = track_thresh
        self.match_thresh = match_thresh
        self.track_buffer = track_buffer
        self.confirm_frames = confirm_frames

        self._tracked: List[STrack] = []
        self._lost: List[STrack] = []
        self._removed: List[STrack] = []
        self._frame_count: int = 0

    def update(self, detections: List[Detection]) -> List[TrackedObject]:
        """Process one frame of detections and return tracked objects."""
        self._frame_count += 1

        high_dets, low_dets = self._split_detections(detections)

        # Predict all active tracks forward
        for track in self._tracked:
            track.predict()
        for track in self._lost:
            track.predict()

        # Stage 1: match high-confidence detections to tracked + lost
        pool = self._tracked + self._lost
        matches_1, unmatched_tracks_1, unmatched_dets_1 = self._associate_stage(
            pool, high_dets, self.match_thresh,
        )

        # Apply stage 1 matches
        matched_tracks: List[STrack] = []
        for t_idx, d_idx in matches_1:
            track = pool[t_idx]
            track.update(high_dets[d_idx])
            self._maybe_confirm(track)
            matched_tracks.append(track)

        # Stage 2: match low-confidence to remaining unmatched tracks
        remaining_tracks = [pool[i] for i in unmatched_tracks_1]
        matches_2, still_unmatched, _ = self._associate_stage(
            remaining_tracks, low_dets, self.match_thresh,
        )

        for t_idx, d_idx in matches_2:
            track = remaining_tracks[t_idx]
            track.update(low_dets[d_idx])
            self._maybe_confirm(track)
            matched_tracks.append(track)

        # Handle unmatched tracks
        lost_this_frame: List[STrack] = []
        for t_idx in still_unmatched:
            track = remaining_tracks[t_idx]
            track.mark_lost()
            lost_this_frame.append(track)

        # Start new tracks from unmatched high-confidence detections
        new_tracks = self._create_new_tracks(high_dets, unmatched_dets_1)

        # Update internal lists
        self._update_track_lists(matched_tracks, lost_this_frame, new_tracks)

        return self._collect_output()

    def _split_detections(
        self, detections: List[Detection],
    ) -> Tuple[List[Detection], List[Detection]]:
        """Split detections into high and low confidence groups."""
        high = [d for d in detections if d.confidence >= self.track_thresh]
        low = [d for d in detections if d.confidence < self.track_thresh]
        return high, low

    def _associate_stage(
        self,
        tracks: List[STrack],
        dets: List[Detection],
        thresh: float,
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """Run IoU-based association between tracks and detections."""
        if not tracks or not dets:
            return [], list(range(len(tracks))), list(range(len(dets)))

        track_boxes = np.array([t.bbox for t in tracks], dtype=np.float64)
        det_boxes = np.array([d.bbox for d in dets], dtype=np.float64)

        iou_matrix = iou_batch(track_boxes, det_boxes)
        cost_matrix = 1.0 - iou_matrix

        return linear_assignment_with_thresh(cost_matrix, 1.0 - thresh)

    def _maybe_confirm(self, track: STrack) -> None:
        """Promote a track to TRACKED if it has enough hits."""
        if track.hits >= self.confirm_frames:
            track.mark_tracked()

    def _create_new_tracks(
        self,
        high_dets: List[Detection],
        unmatched_indices: List[int],
    ) -> List[STrack]:
        """Create new STrack objects from unmatched high-conf detections."""
        new_tracks: List[STrack] = []
        for idx in unmatched_indices:
            track = STrack(high_dets[idx])
            track.activate()
            new_tracks.append(track)
        return new_tracks

    def _update_track_lists(
        self,
        matched: List[STrack],
        lost_this_frame: List[STrack],
        new_tracks: List[STrack],
    ) -> None:
        """Rebuild tracked/lost/removed lists after association."""
        # Remove tracks that have been lost too long
        still_lost: List[STrack] = []
        for track in self._lost:
            if track in matched:
                continue
            if track in lost_this_frame:
                continue
            track.mark_lost()
            if track.time_since_update > self.track_buffer:
                track.mark_removed()
                self._removed.append(track)
            else:
                still_lost.append(track)

        # Add newly lost tracks
        for track in lost_this_frame:
            if track.time_since_update > self.track_buffer:
                track.mark_removed()
                self._removed.append(track)
            else:
                still_lost.append(track)

        self._tracked = matched + new_tracks
        self._lost = still_lost

    def _collect_output(self) -> List[TrackedObject]:
        """Return all non-removed tracks as TrackedObject list."""
        output: List[TrackedObject] = []
        for track in self._tracked:
            output.append(track.to_tracked_object())
        for track in self._lost:
            output.append(track.to_tracked_object())
        return output

    @property
    def frame_count(self) -> int:
        """Number of frames processed."""
        return self._frame_count


# ---------------------------------------------------------------------------
# TrackingDetector -- wraps DetectorBackend with ByteTrack
# ---------------------------------------------------------------------------

class TrackingDetector:
    """Wraps any DetectorBackend with ByteTrack tracking."""

    def __init__(
        self,
        detector: DetectorBackend,
        tracker: Optional[ByteTracker] = None,
    ) -> None:
        self.detector = detector
        self.tracker = tracker or ByteTracker()

    def detect_and_track(self, frame: np.ndarray) -> List[TrackedObject]:
        """Run detection then tracking on a single frame."""
        detections = self.detector.detect(frame)
        return self.tracker.update(detections)
