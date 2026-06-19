"""
Visual-to-RF track association module.

Links camera blob detections (pixel bbox, bearing) to RF sensor identities
(ASTM F3411 ODID, DJI DroneID, ADS-B) by bearing match, temporal correlation,
and consistency scoring across frames. Produces 1-to-1 LinkedTrack assignments
using the Hungarian algorithm.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

logger = logging.getLogger("overwatch.fusion.rf_visual_linker")

# Cost matrix sentinel for forbidden assignments.
_INF_COST = 1e9

# Bearing match decays as a Gaussian with this sigma (degrees).
_BEARING_SIGMA_DEG = 10.0

# Range mismatch gate (meters).
_RANGE_GATE_M = 50.0

# Frames needed for a link to become confirmed.
_CONFIRM_FRAMES = 5

# Frames of absence before a confirmed link is deleted.
_DECAY_FRAMES = 3

# Confidence floor for a confirmed link.
_CONFIRM_THRESHOLD = 0.9


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RFTrackInfo:
    """Identity and kinematic state from an RF sensor."""

    track_id: str
    serial: str
    position_enu: Tuple[float, float, float]
    velocity: Tuple[float, float, float]
    source: str  # "ODID", "DJI_DRONEID", "ADS-B"
    last_seen: float
    operator_position: Optional[Tuple[float, float, float]] = None


@dataclass(frozen=True)
class VisualTrackInfo:
    """Detection from a camera with estimated bearing and range."""

    detection_id: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    class_name: str
    confidence: float
    estimated_bearing_deg: float  # compass bearing from camera center
    estimated_range_m: Optional[float] = None  # inferred from bbox size
    track_id: Optional[str] = None  # from centroid tracker


@dataclass
class LinkedTrack:
    """A visual detection fused with an RF identity."""

    visual: VisualTrackInfo
    rf: RFTrackInfo
    link_confidence: float  # 0-1, association quality this frame
    link_method: str  # "bearing", "position", "temporal"
    combined_confidence: float  # fused confidence from both sources


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _normalize_angle(deg: float) -> float:
    """Normalize an angle to [0, 360)."""
    return deg % 360.0


def _angular_diff(a_deg: float, b_deg: float) -> float:
    """Shortest unsigned angular difference in degrees."""
    diff = abs(_normalize_angle(a_deg) - _normalize_angle(b_deg))
    return min(diff, 360.0 - diff)


def _bearing_from_origin(
    position_enu: Tuple[float, float, float],
    camera_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> float:
    """Compass bearing (0=N, 90=E) from camera to a position in ENU."""
    dx = position_enu[0] - camera_enu[0]
    dy = position_enu[1] - camera_enu[1]
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _range_from_origin(
    position_enu: Tuple[float, float, float],
    camera_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> float:
    """Horizontal range (meters) from camera to a position in ENU."""
    dx = position_enu[0] - camera_enu[0]
    dy = position_enu[1] - camera_enu[1]
    return math.hypot(dx, dy)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _bearing_score(
    visual_bearing: float,
    rf_bearing: float,
    fov_deg: float,
) -> float:
    """Gaussian score for bearing match. Returns 0 if outside FOV/2 gate."""
    diff = _angular_diff(visual_bearing, rf_bearing)
    if diff > fov_deg / 2.0:
        return 0.0
    return math.exp(-0.5 * (diff / _BEARING_SIGMA_DEG) ** 2)


def _range_score(
    visual_range_m: Optional[float],
    rf_range_m: float,
) -> float:
    """Score for range agreement. Returns 1.0 if visual range is unknown."""
    if visual_range_m is None:
        return 1.0
    diff = abs(visual_range_m - rf_range_m)
    if diff > _RANGE_GATE_M:
        return 0.0
    return 1.0 - diff / _RANGE_GATE_M


def _temporal_score(
    visual_time: float,
    rf_time: float,
    max_lag_s: float = 2.0,
) -> float:
    """Score boosting for temporally coincident detections."""
    lag = abs(visual_time - rf_time)
    if lag > max_lag_s:
        return 0.0
    return 1.0 - lag / max_lag_s


# ---------------------------------------------------------------------------
# RFVisualLinker
# ---------------------------------------------------------------------------

class RFVisualLinker:
    """Associates visual detections with RF track identities per frame.

    Uses bearing match as the primary cue, with range gating and temporal
    correlation as secondary factors. Solves the 1-to-1 assignment via the
    Hungarian algorithm (scipy linear_sum_assignment).
    """

    def __init__(
        self,
        camera_enu: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self._camera_enu = camera_enu

    def link(
        self,
        visual_tracks: List[VisualTrackInfo],
        rf_tracks: List[RFTrackInfo],
        camera_bearing_deg: float,
        camera_fov_deg: float,
        current_time: Optional[float] = None,
    ) -> List[LinkedTrack]:
        """Compute best 1-to-1 visual-to-RF assignments."""
        if not visual_tracks or not rf_tracks:
            return []
        now = current_time if current_time is not None else time.time()
        cost = self._build_cost_matrix(
            visual_tracks, rf_tracks,
            camera_bearing_deg, camera_fov_deg, now,
        )
        return self._solve_assignment(
            cost, visual_tracks, rf_tracks,
            camera_bearing_deg, camera_fov_deg, now,
        )

    # -- internal -----------------------------------------------------------

    def _build_cost_matrix(
        self,
        visuals: List[VisualTrackInfo],
        rfs: List[RFTrackInfo],
        cam_bearing: float,
        cam_fov: float,
        now: float,
    ) -> np.ndarray:
        """Build an n_visual x n_rf cost matrix (lower is better)."""
        n_v = len(visuals)
        n_r = len(rfs)
        cost = np.full((n_v, n_r), _INF_COST)
        for i, vis in enumerate(visuals):
            for j, rf in enumerate(rfs):
                score = self._pair_score(
                    vis, rf, cam_bearing, cam_fov, now,
                )
                if score > 0.0:
                    cost[i, j] = 1.0 - score
        return cost

    def _pair_score(
        self,
        vis: VisualTrackInfo,
        rf: RFTrackInfo,
        cam_bearing: float,
        cam_fov: float,
        now: float,
    ) -> float:
        """Combined score for a single visual-RF pair."""
        rf_bearing = _bearing_from_origin(rf.position_enu, self._camera_enu)
        abs_visual_bearing = _normalize_angle(
            cam_bearing + vis.estimated_bearing_deg,
        )
        b_score = _bearing_score(abs_visual_bearing, rf_bearing, cam_fov)
        if b_score == 0.0:
            return 0.0
        rf_range = _range_from_origin(rf.position_enu, self._camera_enu)
        r_score = _range_score(vis.estimated_range_m, rf_range)
        if r_score == 0.0:
            return 0.0
        t_score = _temporal_score(now, rf.last_seen)
        return 0.6 * b_score + 0.2 * r_score + 0.2 * t_score

    def _solve_assignment(
        self,
        cost: np.ndarray,
        visuals: List[VisualTrackInfo],
        rfs: List[RFTrackInfo],
        cam_bearing: float,
        cam_fov: float,
        now: float,
    ) -> List[LinkedTrack]:
        """Run Hungarian algorithm and build LinkedTrack list."""
        row_idx, col_idx = linear_sum_assignment(cost)
        results: List[LinkedTrack] = []
        for r, c in zip(row_idx, col_idx):
            if cost[r, c] >= _INF_COST:
                continue
            score = 1.0 - cost[r, c]
            method = self._dominant_method(
                visuals[r], rfs[c], cam_bearing, cam_fov, now,
            )
            combined = _fuse_confidence(visuals[r].confidence, score)
            results.append(LinkedTrack(
                visual=visuals[r],
                rf=rfs[c],
                link_confidence=score,
                link_method=method,
                combined_confidence=combined,
            ))
        return results

    def _dominant_method(
        self,
        vis: VisualTrackInfo,
        rf: RFTrackInfo,
        cam_bearing: float,
        cam_fov: float,
        now: float,
    ) -> str:
        """Identify which scoring factor contributed most."""
        rf_bearing = _bearing_from_origin(rf.position_enu, self._camera_enu)
        abs_vis = _normalize_angle(cam_bearing + vis.estimated_bearing_deg)
        b = _bearing_score(abs_vis, rf_bearing, cam_fov)
        rf_range = _range_from_origin(rf.position_enu, self._camera_enu)
        r = _range_score(vis.estimated_range_m, rf_range)
        t = _temporal_score(now, rf.last_seen)
        best = max(b, r, t)
        if best == b:
            return "bearing"
        if best == r:
            return "position"
        return "temporal"


def _fuse_confidence(visual_conf: float, link_conf: float) -> float:
    """Fuse visual detection confidence with link association confidence.

    Uses the noisy-OR model: P(both wrong) = (1-a)*(1-b).
    """
    return 1.0 - (1.0 - visual_conf) * (1.0 - link_conf)


# ---------------------------------------------------------------------------
# LinkHistory -- persistence across frames
# ---------------------------------------------------------------------------

@dataclass
class _HistoryEntry:
    """Internal bookkeeping for a single persistent link."""

    visual_id: str
    rf_id: str
    hit_count: int = 0
    miss_count: int = 0
    running_confidence: float = 0.0
    last_link: Optional[LinkedTrack] = None


class LinkHistory:
    """Maintains link persistence across frames.

    Links that hold for CONFIRM_FRAMES consecutive frames are promoted to
    confirmed (confidence > 0.9). Links that break decay over DECAY_FRAMES
    before deletion.
    """

    def __init__(
        self,
        confirm_frames: int = _CONFIRM_FRAMES,
        decay_frames: int = _DECAY_FRAMES,
        confirm_threshold: float = _CONFIRM_THRESHOLD,
    ) -> None:
        self._confirm_frames = confirm_frames
        self._decay_frames = decay_frames
        self._confirm_threshold = confirm_threshold
        self._entries: Dict[str, _HistoryEntry] = {}

    def update(self, links: List[LinkedTrack]) -> None:
        """Ingest a new frame of links and update running scores."""
        seen_keys = self._apply_hits(links)
        self._apply_misses(seen_keys)

    def _apply_hits(self, links: List[LinkedTrack]) -> set:
        """Record hits for current-frame links. Returns seen keys."""
        seen: set = set()
        for link in links:
            key = _link_key(link)
            seen.add(key)
            entry = self._entries.get(key)
            if entry is None:
                entry = _HistoryEntry(
                    visual_id=link.visual.detection_id,
                    rf_id=link.rf.track_id,
                )
                self._entries[key] = entry
            entry.hit_count += 1
            entry.miss_count = 0
            entry.running_confidence = _ema(
                entry.running_confidence, link.link_confidence,
            )
            entry.last_link = link
        return seen

    def _apply_misses(self, seen_keys: set) -> None:
        """Decay or delete entries not seen this frame."""
        to_delete: List[str] = []
        for key, entry in self._entries.items():
            if key in seen_keys:
                continue
            entry.miss_count += 1
            entry.running_confidence *= 0.7
            if entry.miss_count > self._decay_frames:
                to_delete.append(key)
        for key in to_delete:
            del self._entries[key]

    def get_confirmed_links(self) -> List[LinkedTrack]:
        """Return links that have been stable for enough frames."""
        results: List[LinkedTrack] = []
        for entry in self._entries.values():
            if not _is_confirmed(entry, self._confirm_frames, self._confirm_threshold):
                continue
            if entry.last_link is None:
                continue
            promoted = LinkedTrack(
                visual=entry.last_link.visual,
                rf=entry.last_link.rf,
                link_confidence=entry.running_confidence,
                link_method=entry.last_link.link_method,
                combined_confidence=_fuse_confidence(
                    entry.last_link.visual.confidence,
                    entry.running_confidence,
                ),
            )
            results.append(promoted)
        return results

    def get_all_entries(self) -> Dict[str, _HistoryEntry]:
        """Expose internal state for diagnostics."""
        return dict(self._entries)


def _link_key(link: LinkedTrack) -> str:
    """Stable key for a visual-RF pair."""
    return f"{link.visual.detection_id}::{link.rf.track_id}"


def _ema(old: float, new: float, alpha: float = 0.3) -> float:
    """Exponential moving average update. Seeds with first value."""
    if old == 0.0:
        return new
    return alpha * new + (1.0 - alpha) * old


def _is_confirmed(
    entry: _HistoryEntry,
    confirm_frames: int,
    threshold: float,
) -> bool:
    """Check if a history entry qualifies as confirmed."""
    return (
        entry.hit_count >= confirm_frames
        and entry.running_confidence >= threshold
    )
