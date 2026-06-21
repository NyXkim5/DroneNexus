"""
Visual swarm behavior classifier for OVERWATCH/BULWARK.

Analyzes spatial distribution and motion patterns of TrackedObjects to classify
swarm formations. Uses PCA-based feature extraction and rule-based classification
with temporal smoothing to avoid frame-to-frame jitter.

Formation types:
  V_FORMATION : high alignment, symmetric spread, coherent velocity
  LINE        : very high alignment, low perpendicular spread
  COLUMN      : high alignment, low velocity coherence (following each other)
  DIAMOND     : 4 tracks, symmetric, centered
  ORBIT       : circular angular distribution, static centroid, moving tracks
  CONVERGING  : tracks approaching a common point over time
  SCATTER     : low alignment, low velocity coherence
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

import numpy as np

from vision.tracker import TrackedObject


# -- Formation type constants ------------------------------------------------

FORMATION_V = "V_FORMATION"
FORMATION_LINE = "LINE"
FORMATION_COLUMN = "COLUMN"
FORMATION_DIAMOND = "DIAMOND"
FORMATION_ORBIT = "ORBIT"
FORMATION_CONVERGING = "CONVERGING"
FORMATION_SCATTER = "SCATTER"

ALL_FORMATIONS = (
    FORMATION_V,
    FORMATION_LINE,
    FORMATION_COLUMN,
    FORMATION_DIAMOND,
    FORMATION_ORBIT,
    FORMATION_CONVERGING,
    FORMATION_SCATTER,
)

# -- Thresholds --------------------------------------------------------------

ALIGNMENT_V_MIN = 0.55
ALIGNMENT_LINE_MIN = 0.85
ALIGNMENT_COLUMN_MIN = 0.7
ALIGNMENT_ORBIT_MAX = 0.65
VELOCITY_COHERENCE_HIGH = 0.7
VELOCITY_COHERENCE_LOW = 0.4
CONVERGENCE_RATE_MIN = 0.3
ANGULAR_UNIFORMITY_MIN = 0.6
DIAMOND_COUNT = 4
DIAMOND_SYMMETRY_MAX = 0.35
SMOOTHING_WINDOW = 10
SMOOTHING_VOTE_THRESHOLD = 7
MIN_TRACKS_FOR_CLASSIFICATION = 3
VELOCITY_ALONG_AXIS_MIN = 0.7


@dataclass
class SwarmAnalysis:
    """Result of a single-frame swarm classification."""

    formation: str
    confidence: float
    centroid: Tuple[float, float, float]
    spread_m: float
    velocity_coherence: float
    convergence_rate: float
    track_count: int
    converging_toward: Optional[Tuple[float, float, float]]


@dataclass
class SwarmFeatures:
    """Extracted geometric and kinematic features for one frame."""

    centroid: Tuple[float, float, float]
    spread: float
    alignment: float
    convergence_rate: float
    angular_uniformity: float
    velocity_coherence: float
    count: int
    principal_axis: Optional[Tuple[float, float]]
    symmetry_ratio: float
    velocity_along_axis: float


# -- Feature extraction ------------------------------------------------------


def compute_centroid(tracks: List[TrackedObject]) -> Tuple[float, float, float]:
    """Mean position across all tracks."""
    xs = [t.position[0] for t in tracks]
    ys = [t.position[1] for t in tracks]
    zs = [t.position[2] for t in tracks]
    n = len(tracks)
    return (sum(xs) / n, sum(ys) / n, sum(zs) / n)


def compute_spread(
    tracks: List[TrackedObject],
    centroid: Tuple[float, float, float],
) -> float:
    """Standard deviation of horizontal distance from centroid."""
    dists = [
        math.hypot(
            t.position[0] - centroid[0],
            t.position[1] - centroid[1],
        )
        for t in tracks
    ]
    arr = np.array(dists)
    return float(np.std(arr))


def compute_alignment(tracks: List[TrackedObject]) -> Tuple[float, Optional[Tuple[float, float]]]:
    """PCA on 2D positions. Returns (alignment_ratio, principal_axis).

    alignment_ratio is the fraction of variance along the first principal
    component. A value near 1.0 means all tracks lie on a line.
    """
    if len(tracks) < 2:
        return 0.0, None
    pts = np.array([[t.position[0], t.position[1]] for t in tracks])
    pts_centered = pts - pts.mean(axis=0)
    cov = np.cov(pts_centered, rowvar=False)
    eigenvalues = np.linalg.eigvalsh(cov)
    eigenvalues = np.sort(eigenvalues)[::-1]
    total = eigenvalues.sum()
    if total < 1e-12:
        return 0.0, None
    alignment = float(eigenvalues[0] / total)
    # Principal axis from eigenvector of largest eigenvalue
    _, eigvecs = np.linalg.eigh(cov)
    axis = eigvecs[:, -1]
    return alignment, (float(axis[0]), float(axis[1]))


def compute_velocity_coherence(tracks: List[TrackedObject]) -> float:
    """How aligned are all track velocities? 0 = random, 1 = identical direction.

    Uses the magnitude of the mean unit velocity vector. If all velocities
    point the same way the mean unit vector has magnitude 1.
    """
    vels = []
    for t in tracks:
        vx, vy = t.velocity_px
        mag = math.hypot(vx, vy)
        if mag > 1e-6:
            vels.append((vx / mag, vy / mag))
    if len(vels) < 2:
        return 0.0
    mean_ux = sum(v[0] for v in vels) / len(vels)
    mean_uy = sum(v[1] for v in vels) / len(vels)
    return float(math.hypot(mean_ux, mean_uy))


def compute_angular_uniformity(
    tracks: List[TrackedObject],
    centroid: Tuple[float, float, float],
) -> float:
    """How evenly are tracks distributed angularly around the centroid?

    Returns 0 for clustered, 1 for perfectly uniform.
    Uses the Rayleigh test statistic: R = |mean unit vector|.
    Uniformity = 1 - R.
    """
    angles = []
    for t in tracks:
        dx = t.position[0] - centroid[0]
        dy = t.position[1] - centroid[1]
        if math.hypot(dx, dy) > 1e-6:
            angles.append(math.atan2(dy, dx))
    if len(angles) < 3:
        return 0.0
    mean_cos = sum(math.cos(a) for a in angles) / len(angles)
    mean_sin = sum(math.sin(a) for a in angles) / len(angles)
    r = math.hypot(mean_cos, mean_sin)
    return float(1.0 - r)


def compute_convergence_rate(
    prev_tracks: List[TrackedObject],
    curr_tracks: List[TrackedObject],
) -> Tuple[float, Optional[Tuple[float, float, float]]]:
    """Rate at which tracks are converging toward a common point.

    Matches tracks by track_id between frames and measures the change in
    spread. Positive values mean converging. Also estimates the convergence
    target as the mean position weighted by inward velocity.

    Returns (convergence_rate, estimated_target_point).
    """
    if len(curr_tracks) < 2 or not prev_tracks:
        return 0.0, None
    prev_map = {t.track_id: t for t in prev_tracks}
    curr_map = {t.track_id: t for t in curr_tracks}
    common_ids = set(prev_map) & set(curr_map)
    if len(common_ids) < 2:
        return 0.0, None
    prev_common = [prev_map[tid] for tid in common_ids]
    curr_common = [curr_map[tid] for tid in common_ids]
    prev_centroid = compute_centroid(prev_common)
    curr_centroid = compute_centroid(curr_common)
    prev_spread = compute_spread(prev_common, prev_centroid)
    curr_spread = compute_spread(curr_common, curr_centroid)
    if prev_spread < 1e-6:
        return 0.0, None
    rate = float((prev_spread - curr_spread) / prev_spread)
    target = curr_centroid if rate > 0 else None
    return rate, target


def compute_symmetry_ratio(
    tracks: List[TrackedObject],
    centroid: Tuple[float, float, float],
    axis: Optional[Tuple[float, float]],
) -> float:
    """Measure symmetry of track positions about the principal axis.

    Returns 0 for perfect symmetry, 1 for fully asymmetric.
    Projects each point onto the axis and its perpendicular, then compares
    the distribution of signed perpendicular distances.
    """
    if axis is None or len(tracks) < 3:
        return 1.0
    ax, ay = axis
    mag = math.hypot(ax, ay)
    if mag < 1e-12:
        return 1.0
    ax, ay = ax / mag, ay / mag
    perp_dists = []
    for t in tracks:
        dx = t.position[0] - centroid[0]
        dy = t.position[1] - centroid[1]
        perp = -dx * ay + dy * ax
        perp_dists.append(perp)
    if not perp_dists:
        return 1.0
    pos_sum = sum(d for d in perp_dists if d > 0)
    neg_sum = sum(abs(d) for d in perp_dists if d < 0)
    total = pos_sum + neg_sum
    if total < 1e-12:
        return 0.0
    return float(abs(pos_sum - neg_sum) / total)


def compute_velocity_along_axis(
    tracks: List[TrackedObject],
    axis: Optional[Tuple[float, float]],
) -> float:
    """How much of the mean velocity is aligned with the principal spatial axis.

    Returns 0 when velocity is perpendicular to the formation axis, 1 when
    parallel. Used to distinguish column (moving along the axis) from line
    (moving perpendicular or together sideways).
    """
    if axis is None or len(tracks) < 2:
        return 0.0
    ax, ay = axis
    mag = math.hypot(ax, ay)
    if mag < 1e-12:
        return 0.0
    ax, ay = ax / mag, ay / mag
    vels = [(t.velocity_px[0], t.velocity_px[1]) for t in tracks]
    mean_vx = sum(v[0] for v in vels) / len(vels)
    mean_vy = sum(v[1] for v in vels) / len(vels)
    vmag = math.hypot(mean_vx, mean_vy)
    if vmag < 1e-6:
        return 0.0
    dot = abs(mean_vx * ax + mean_vy * ay) / vmag
    return float(min(1.0, dot))


def extract_features(
    tracks: List[TrackedObject],
    prev_tracks: Optional[List[TrackedObject]] = None,
) -> SwarmFeatures:
    """Extract all geometric and kinematic features from one frame."""
    centroid = compute_centroid(tracks)
    spread = compute_spread(tracks, centroid)
    alignment, axis = compute_alignment(tracks)
    vel_coherence = compute_velocity_coherence(tracks)
    angular_unif = compute_angular_uniformity(tracks, centroid)
    conv_rate, _ = compute_convergence_rate(
        prev_tracks or [], tracks,
    )
    symmetry = compute_symmetry_ratio(tracks, centroid, axis)
    vel_along = compute_velocity_along_axis(tracks, axis)
    return SwarmFeatures(
        centroid=centroid,
        spread=spread,
        alignment=alignment,
        convergence_rate=conv_rate,
        angular_uniformity=angular_unif,
        velocity_coherence=vel_coherence,
        count=len(tracks),
        principal_axis=axis,
        symmetry_ratio=symmetry,
        velocity_along_axis=vel_along,
    )


# -- Classification rules ---------------------------------------------------


def _classify_single_frame(features: SwarmFeatures) -> Tuple[str, float]:
    """Apply rule-based classification to extracted features.

    Returns (formation_type, confidence).
    """
    if features.count < MIN_TRACKS_FOR_CLASSIFICATION:
        return FORMATION_SCATTER, 0.3

    # Check diamond first (specific count constraint)
    if _is_diamond(features):
        return FORMATION_DIAMOND, _diamond_confidence(features)

    # Convergence overrides spatial formation
    if _is_converging(features):
        return FORMATION_CONVERGING, _converging_confidence(features)

    # Column: high alignment, velocity along axis, varied speeds
    if _is_column(features):
        return FORMATION_COLUMN, _column_confidence(features)

    # Line: very high alignment, coherent velocity
    if _is_line(features):
        return FORMATION_LINE, _line_confidence(features)

    # V formation: moderate alignment + symmetric + coherent velocity
    if _is_v_formation(features):
        return FORMATION_V, _v_confidence(features)

    # Orbit: circular angular distribution, low alignment (actual circle)
    if _is_orbit(features):
        return FORMATION_ORBIT, _orbit_confidence(features)

    return FORMATION_SCATTER, _scatter_confidence(features)


def _is_v_formation(f: SwarmFeatures) -> bool:
    return (
        f.alignment >= ALIGNMENT_V_MIN
        and f.symmetry_ratio < DIAMOND_SYMMETRY_MAX
        and f.velocity_coherence >= VELOCITY_COHERENCE_HIGH
    )


def _v_confidence(f: SwarmFeatures) -> float:
    return min(1.0, (f.alignment + f.velocity_coherence + (1 - f.symmetry_ratio)) / 3)


def _is_line(f: SwarmFeatures) -> bool:
    return (
        f.alignment >= ALIGNMENT_LINE_MIN
        and f.velocity_coherence >= VELOCITY_COHERENCE_HIGH
        and f.velocity_along_axis < VELOCITY_ALONG_AXIS_MIN
    )


def _line_confidence(f: SwarmFeatures) -> float:
    return min(1.0, f.alignment * 0.6 + f.velocity_coherence * 0.4)


def _is_column(f: SwarmFeatures) -> bool:
    return (
        f.alignment >= ALIGNMENT_COLUMN_MIN
        and f.velocity_along_axis >= VELOCITY_ALONG_AXIS_MIN
    )


def _column_confidence(f: SwarmFeatures) -> float:
    return min(1.0, f.alignment * 0.7 + (1 - f.velocity_coherence) * 0.3)


def _is_diamond(f: SwarmFeatures) -> bool:
    return (
        f.count == DIAMOND_COUNT
        and f.symmetry_ratio < DIAMOND_SYMMETRY_MAX
        and f.alignment < ALIGNMENT_LINE_MIN
    )


def _diamond_confidence(f: SwarmFeatures) -> float:
    return min(1.0, (1 - f.symmetry_ratio) * 0.6 + (1 - f.alignment) * 0.4)


def _is_orbit(f: SwarmFeatures) -> bool:
    return (
        f.angular_uniformity >= ANGULAR_UNIFORMITY_MIN
        and f.alignment <= ALIGNMENT_ORBIT_MAX
        and f.velocity_coherence < VELOCITY_COHERENCE_HIGH
    )


def _orbit_confidence(f: SwarmFeatures) -> float:
    return min(1.0, f.angular_uniformity * 0.7 + (1 - f.velocity_coherence) * 0.3)


def _is_converging(f: SwarmFeatures) -> bool:
    return f.convergence_rate >= CONVERGENCE_RATE_MIN


def _converging_confidence(f: SwarmFeatures) -> float:
    return min(1.0, f.convergence_rate)


def _scatter_confidence(f: SwarmFeatures) -> float:
    return min(1.0, (1 - f.alignment) * 0.5 + (1 - f.velocity_coherence) * 0.5)


# -- Classifier with temporal smoothing -------------------------------------


class VisualSwarmClassifier:
    """Classifies swarm formation from tracked drone positions per frame.

    Maintains a rolling window of per-frame classifications and only changes
    the reported formation when a new type wins a supermajority of recent
    frames. This prevents jitter from single noisy frames.
    """

    def __init__(
        self,
        window_size: int = SMOOTHING_WINDOW,
        vote_threshold: int = SMOOTHING_VOTE_THRESHOLD,
    ) -> None:
        self._window_size = window_size
        self._vote_threshold = vote_threshold
        self._history: Deque[str] = deque(maxlen=window_size)
        self._current_formation: str = FORMATION_SCATTER
        self._prev_tracks: Optional[List[TrackedObject]] = None
        self._converging_target: Optional[Tuple[float, float, float]] = None

    @property
    def current_formation(self) -> str:
        return self._current_formation

    def update(self, tracks: List[TrackedObject]) -> SwarmAnalysis:
        """Process one frame of tracked objects and return the analysis."""
        if len(tracks) < 2:
            return self._empty_analysis(tracks)

        features = extract_features(tracks, self._prev_tracks)
        raw_formation, confidence = _classify_single_frame(features)

        # Update convergence target
        _, conv_target = compute_convergence_rate(
            self._prev_tracks or [], tracks,
        )
        if conv_target is not None:
            self._converging_target = conv_target

        # Temporal smoothing
        self._history.append(raw_formation)
        smoothed = self._smooth_formation(raw_formation)
        self._current_formation = smoothed

        self._prev_tracks = list(tracks)

        return SwarmAnalysis(
            formation=smoothed,
            confidence=confidence,
            centroid=features.centroid,
            spread_m=features.spread,
            velocity_coherence=features.velocity_coherence,
            convergence_rate=features.convergence_rate,
            track_count=features.count,
            converging_toward=self._converging_target if smoothed == FORMATION_CONVERGING else None,
        )

    def _smooth_formation(self, raw: str) -> str:
        """Vote over the rolling window to decide formation.

        Before the window is full, return the raw per-frame classification
        for responsiveness during startup. Once the window is full, require
        a supermajority vote to change the reported formation.
        """
        if len(self._history) < self._window_size:
            return raw
        counts: dict[str, int] = {}
        for f in self._history:
            counts[f] = counts.get(f, 0) + 1
        for formation, count in counts.items():
            if count >= self._vote_threshold:
                return formation
        return self._current_formation

    def _empty_analysis(self, tracks: List[TrackedObject]) -> SwarmAnalysis:
        """Return a scatter analysis for insufficient tracks."""
        if tracks:
            centroid = compute_centroid(tracks)
        else:
            centroid = (0.0, 0.0, 0.0)
        return SwarmAnalysis(
            formation=FORMATION_SCATTER,
            confidence=0.0,
            centroid=centroid,
            spread_m=0.0,
            velocity_coherence=0.0,
            convergence_rate=0.0,
            track_count=len(tracks),
            converging_toward=None,
        )

    def reset(self) -> None:
        """Clear all state."""
        self._history.clear()
        self._current_formation = FORMATION_SCATTER
        self._prev_tracks = None
        self._converging_target = None
