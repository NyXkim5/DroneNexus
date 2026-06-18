"""
ByteTrack-inspired multi-object visual tracker for OVERWATCH/BULWARK.

Maintains target identity across frames, handles occlusions, and reduces
false positives via two-stage IoU-based Hungarian assignment.

Two-stage association (ByteTrack):
  1. High-confidence detections get first pick against all active tracks.
  2. Low-confidence detections fill remaining unmatched tracks.
  3. Unmatched high-confidence detections start new tracks.
  4. Tracks exceeding max_misses are deleted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from csontology import Vec3
from vision.models import BoundingBox, TargetType, VisualTarget


@dataclass
class TrackedObject:
    track_id: str
    bbox: BoundingBox
    position: Vec3
    target_type: TargetType
    confidence: float
    age: int
    hits: int
    misses: int
    velocity_px: Tuple[float, float] = (0.0, 0.0)


def _bbox_center(bbox: BoundingBox) -> Tuple[float, float]:
    return bbox.x + bbox.width / 2.0, bbox.y + bbox.height / 2.0


def _shift_bbox(bbox: BoundingBox, dx: float, dy: float) -> BoundingBox:
    return BoundingBox(
        x=int(round(bbox.x + dx)),
        y=int(round(bbox.y + dy)),
        width=bbox.width,
        height=bbox.height,
    )


class SimpleTracker:
    """
    ByteTrack-inspired multi-object tracker.

    Each frame:
    1. Predict tracked positions forward using velocity.
    2. Associate detections to tracks (IoU-based Hungarian assignment).
    3. Update matched tracks.
    4. Start new tracks from unmatched high-confidence detections.
    5. Age out tracks with too many consecutive misses.
    """

    def __init__(
        self,
        max_misses: int = 10,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        high_confidence: float = 0.6,
        low_confidence: float = 0.3,
    ) -> None:
        self._tracks: Dict[str, TrackedObject] = {}
        self._next_id: int = 0
        self._max_misses = max_misses
        self._min_hits = min_hits
        self._iou_threshold = iou_threshold
        self._high_conf = high_confidence
        self._low_conf = low_confidence

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, detections: List[VisualTarget]) -> List[TrackedObject]:
        """
        Process one frame of detections.

        Returns confirmed tracks (hits >= min_hits).
        """
        self._predict()

        high = [d for d in detections if d.confidence >= self._high_conf]
        low = [
            d for d in detections
            if self._low_conf <= d.confidence < self._high_conf
        ]

        all_track_ids = list(self._tracks.keys())

        # --- Stage 1: match high-confidence detections against all tracks ---
        matches1, unmatched_tracks1, unmatched_high = self._associate(high, all_track_ids)

        for track_id, det_idx in matches1:
            self._update_track(track_id, high[det_idx])

        # --- Stage 2: match low-confidence detections against remaining tracks ---
        matches2, unmatched_tracks2, _ = self._associate(low, unmatched_tracks1)

        for track_id, det_idx in matches2:
            self._update_track(track_id, low[det_idx])

        # --- Increment misses for all still-unmatched tracks ---
        for track_id in unmatched_tracks2:
            self._tracks[track_id].misses += 1
            self._tracks[track_id].age += 1

        # --- Start new tracks from unmatched high-confidence detections ---
        for det_idx in unmatched_high:
            self._create_track(high[det_idx])

        # --- Prune dead tracks ---
        dead = [tid for tid, t in self._tracks.items() if t.misses > self._max_misses]
        for tid in dead:
            del self._tracks[tid]

        return self.confirmed_tracks

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_tracks(self) -> List[TrackedObject]:
        """All tracks including unconfirmed."""
        return list(self._tracks.values())

    @property
    def confirmed_tracks(self) -> List[TrackedObject]:
        """Only tracks with hits >= min_hits."""
        return [t for t in self._tracks.values() if t.hits >= self._min_hits]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _predict(self) -> None:
        """Advance each track's bbox by its estimated pixel velocity."""
        for track in self._tracks.values():
            vx, vy = track.velocity_px
            if vx != 0.0 or vy != 0.0:
                track.bbox = _shift_bbox(track.bbox, vx, vy)

    def _associate(
        self,
        detections: List[VisualTarget],
        track_ids: List[str],
    ) -> Tuple[List[Tuple[str, int]], List[str], List[int]]:
        """
        IoU-based Hungarian assignment.

        Returns:
            matches              -- list of (track_id, detection_index)
            unmatched_track_ids  -- track IDs with no assigned detection
            unmatched_det_idxs   -- detection indices with no assigned track
        """
        if not detections or not track_ids:
            return [], list(track_ids), list(range(len(detections)))

        n_tracks = len(track_ids)
        n_dets = len(detections)

        cost = np.zeros((n_tracks, n_dets), dtype=np.float64)
        for ti, tid in enumerate(track_ids):
            for di, det in enumerate(detections):
                cost[ti, di] = 1.0 - self._iou(self._tracks[tid].bbox, det.bounding_box)

        row_ind, col_ind = linear_sum_assignment(cost)

        matched_track_set: set[int] = set()
        matched_det_set: set[int] = set()
        matches: List[Tuple[str, int]] = []

        for ri, ci in zip(row_ind, col_ind):
            if (1.0 - cost[ri, ci]) >= self._iou_threshold:
                matches.append((track_ids[ri], ci))
                matched_track_set.add(ri)
                matched_det_set.add(ci)

        unmatched_tracks = [track_ids[i] for i in range(n_tracks) if i not in matched_track_set]
        unmatched_dets = [i for i in range(n_dets) if i not in matched_det_set]

        return matches, unmatched_tracks, unmatched_dets

    def _iou(self, bb1: BoundingBox, bb2: BoundingBox) -> float:
        """Intersection-over-Union for two axis-aligned bounding boxes."""
        x1 = max(bb1.x, bb2.x)
        y1 = max(bb1.y, bb2.y)
        x2 = min(bb1.x + bb1.width, bb2.x + bb2.width)
        y2 = min(bb1.y + bb1.height, bb2.y + bb2.height)

        inter_w = max(0, x2 - x1)
        inter_h = max(0, y2 - y1)
        intersection = inter_w * inter_h

        if intersection == 0:
            return 0.0

        area1 = bb1.width * bb1.height
        area2 = bb2.width * bb2.height
        union = area1 + area2 - intersection
        return intersection / union if union > 0 else 0.0

    def _update_track(self, track_id: str, detection: VisualTarget) -> None:
        """Update a matched track with the new detection."""
        track = self._tracks[track_id]
        prev_cx, prev_cy = _bbox_center(track.bbox)
        new_bbox = detection.bounding_box
        new_cx, new_cy = _bbox_center(new_bbox)

        vx = new_cx - prev_cx
        vy = new_cy - prev_cy

        track.bbox = new_bbox
        track.position = detection.position
        track.confidence = detection.confidence
        track.target_type = detection.target_type
        track.velocity_px = (vx, vy)
        track.hits += 1
        track.misses = 0
        track.age += 1

    def _create_track(self, detection: VisualTarget) -> None:
        """Initialise a new track from an unmatched detection."""
        track_id = f"trk-{self._next_id}"
        self._next_id += 1
        self._tracks[track_id] = TrackedObject(
            track_id=track_id,
            bbox=detection.bounding_box,
            position=detection.position,
            target_type=detection.target_type,
            confidence=detection.confidence,
            age=1,
            hits=1,
            misses=0,
            velocity_px=(0.0, 0.0),
        )
