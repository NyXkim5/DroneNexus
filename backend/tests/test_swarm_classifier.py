"""
Tests for visual swarm behavior classifier.

Validates formation detection using synthetic track positions for each
formation type, plus temporal smoothing behavior.
"""
from __future__ import annotations

import math
from typing import List, Tuple

import pytest

from vision.models import BoundingBox, TargetType
from vision.tracker import TrackedObject
from vision.swarm_classifier import (
    ALL_FORMATIONS,
    FORMATION_COLUMN,
    FORMATION_CONVERGING,
    FORMATION_DIAMOND,
    FORMATION_LINE,
    FORMATION_ORBIT,
    FORMATION_SCATTER,
    FORMATION_V,
    SwarmAnalysis,
    VisualSwarmClassifier,
    compute_alignment,
    compute_angular_uniformity,
    compute_centroid,
    compute_convergence_rate,
    compute_spread,
    compute_velocity_coherence,
    extract_features,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BBOX = BoundingBox(x=0, y=0, width=20, height=20)


def _make_track(
    track_id: str,
    x: float,
    y: float,
    z: float = 50.0,
    vx: float = 0.0,
    vy: float = 0.0,
) -> TrackedObject:
    return TrackedObject(
        track_id=track_id,
        bbox=_BBOX,
        position=(x, y, z),
        target_type=TargetType.VEHICLE_CAR,
        confidence=0.9,
        age=10,
        hits=10,
        misses=0,
        velocity_px=(vx, vy),
    )


def _make_v_tracks() -> List[TrackedObject]:
    """V-formation: symmetric spread around a leading point, all moving north."""
    return [
        _make_track("v0", 0.0, 100.0, vx=0.0, vy=5.0),     # tip
        _make_track("v1", -20.0, 70.0, vx=0.0, vy=5.0),     # left wing
        _make_track("v2", 20.0, 70.0, vx=0.0, vy=5.0),      # right wing
        _make_track("v3", -40.0, 40.0, vx=0.0, vy=5.0),     # outer left
        _make_track("v4", 40.0, 40.0, vx=0.0, vy=5.0),      # outer right
    ]


def _make_line_tracks() -> List[TrackedObject]:
    """Line abreast: arranged along x-axis, advancing north (perpendicular)."""
    return [
        _make_track("l0", 0.0, 0.0, vx=0.0, vy=5.0),
        _make_track("l1", 30.0, 0.0, vx=0.0, vy=5.0),
        _make_track("l2", 60.0, 0.0, vx=0.0, vy=5.0),
        _make_track("l3", 90.0, 0.0, vx=0.0, vy=5.0),
        _make_track("l4", 120.0, 0.0, vx=0.0, vy=5.0),
    ]


def _make_orbit_tracks(n: int = 6, radius: float = 50.0) -> List[TrackedObject]:
    """Orbit: evenly spaced on a circle, tangential velocities."""
    tracks = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        # Tangential velocity (perpendicular to radius)
        vx = -math.sin(angle) * 3.0
        vy = math.cos(angle) * 3.0
        tracks.append(_make_track(f"o{i}", x, y, vx=vx, vy=vy))
    return tracks


def _make_scatter_tracks() -> List[TrackedObject]:
    """Scatter: moderate alignment, random velocities, no clear pattern."""
    return [
        _make_track("s0", 10.0, 50.0, vx=3.0, vy=-1.0),
        _make_track("s1", 40.0, 80.0, vx=-2.0, vy=4.0),
        _make_track("s2", 80.0, 30.0, vx=1.0, vy=1.0),
        _make_track("s3", 120.0, 90.0, vx=-4.0, vy=-2.0),
        _make_track("s4", 60.0, 110.0, vx=0.0, vy=-5.0),
    ]


def _make_diamond_tracks() -> List[TrackedObject]:
    """Diamond: 4 tracks in a symmetric diamond shape."""
    return [
        _make_track("d0", 0.0, 50.0, vx=1.0, vy=0.5),    # top
        _make_track("d1", -40.0, 0.0, vx=1.0, vy=0.5),   # left
        _make_track("d2", 40.0, 0.0, vx=1.0, vy=0.5),    # right
        _make_track("d3", 0.0, -50.0, vx=1.0, vy=0.5),   # bottom
    ]


def _make_converging_tracks(
    spread_scale: float,
) -> List[TrackedObject]:
    """Tracks at distance spread_scale from origin, approaching center."""
    return [
        _make_track("c0", 0.0, spread_scale, vx=0.0, vy=-2.0),
        _make_track("c1", spread_scale, 0.0, vx=-2.0, vy=0.0),
        _make_track("c2", 0.0, -spread_scale, vx=0.0, vy=2.0),
        _make_track("c3", -spread_scale, 0.0, vx=2.0, vy=0.0),
        _make_track("c4", spread_scale * 0.7, spread_scale * 0.7, vx=-1.4, vy=-1.4),
    ]


def _make_column_tracks() -> List[TrackedObject]:
    """Column: high alignment along y-axis, velocity along axis (following)."""
    return [
        _make_track("col0", 2.0, 0.0, vx=0.1, vy=5.0),
        _make_track("col1", -1.0, 30.0, vx=-0.1, vy=5.0),
        _make_track("col2", 1.0, 60.0, vx=0.05, vy=5.0),
        _make_track("col3", -2.0, 90.0, vx=-0.05, vy=5.0),
        _make_track("col4", 0.0, 120.0, vx=0.0, vy=5.0),
    ]


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------


class TestFeatureExtraction:
    def test_centroid_simple(self) -> None:
        tracks = [
            _make_track("a", 0.0, 0.0),
            _make_track("b", 10.0, 20.0),
        ]
        cx, cy, cz = compute_centroid(tracks)
        assert cx == pytest.approx(5.0)
        assert cy == pytest.approx(10.0)

    def test_spread_zero_for_coincident(self) -> None:
        tracks = [_make_track("a", 5.0, 5.0), _make_track("b", 5.0, 5.0)]
        centroid = compute_centroid(tracks)
        assert compute_spread(tracks, centroid) == pytest.approx(0.0)

    def test_alignment_collinear(self) -> None:
        """Points on a line should have alignment near 1.0."""
        tracks = [_make_track(f"t{i}", float(i * 10), 0.0) for i in range(5)]
        alignment, axis = compute_alignment(tracks)
        assert alignment > 0.99

    def test_alignment_circular(self) -> None:
        """Points on a circle should have low alignment."""
        tracks = _make_orbit_tracks(8)
        alignment, _ = compute_alignment(tracks)
        assert alignment < 0.6

    def test_velocity_coherence_identical(self) -> None:
        """All same velocity direction gives coherence near 1.0."""
        tracks = [_make_track(f"t{i}", float(i), 0.0, vx=3.0, vy=0.0) for i in range(5)]
        assert compute_velocity_coherence(tracks) > 0.95

    def test_velocity_coherence_opposite(self) -> None:
        """Opposing velocities give low coherence."""
        tracks = [
            _make_track("a", 0.0, 0.0, vx=5.0, vy=0.0),
            _make_track("b", 10.0, 0.0, vx=-5.0, vy=0.0),
        ]
        assert compute_velocity_coherence(tracks) < 0.1

    def test_angular_uniformity_even(self) -> None:
        """Evenly spaced around a circle gives high uniformity."""
        tracks = _make_orbit_tracks(8)
        centroid = compute_centroid(tracks)
        uniformity = compute_angular_uniformity(tracks, centroid)
        assert uniformity > 0.8

    def test_angular_uniformity_clustered(self) -> None:
        """All tracks in the same angular sector gives low uniformity."""
        # Place centroid at origin. All tracks in the +x,+y quadrant.
        centroid = (0.0, 0.0, 50.0)
        tracks = [
            _make_track("t0", 10.0, 11.0),
            _make_track("t1", 12.0, 10.0),
            _make_track("t2", 15.0, 13.0),
            _make_track("t3", 11.0, 14.0),
            _make_track("t4", 13.0, 12.0),
        ]
        uniformity = compute_angular_uniformity(tracks, centroid)
        assert uniformity < 0.3

    def test_convergence_rate_closing(self) -> None:
        """Tracks moving inward show positive convergence rate."""
        prev = _make_converging_tracks(100.0)
        curr = _make_converging_tracks(60.0)
        rate, target = compute_convergence_rate(prev, curr)
        assert rate > 0.2
        assert target is not None

    def test_convergence_rate_static(self) -> None:
        """Same positions across frames give zero convergence."""
        tracks = _make_scatter_tracks()
        rate, _ = compute_convergence_rate(tracks, tracks)
        assert rate == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------


class TestClassification:
    def test_v_formation_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_v_tracks()
        result = clf.update(tracks)
        assert result.formation == FORMATION_V
        assert result.confidence > 0.5

    def test_line_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_line_tracks()
        result = clf.update(tracks)
        assert result.formation == FORMATION_LINE
        assert result.confidence > 0.5

    def test_orbit_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_orbit_tracks(8)
        result = clf.update(tracks)
        assert result.formation == FORMATION_ORBIT
        assert result.confidence > 0.4

    def test_scatter_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_scatter_tracks()
        result = clf.update(tracks)
        assert result.formation == FORMATION_SCATTER

    def test_diamond_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_diamond_tracks()
        result = clf.update(tracks)
        assert result.formation == FORMATION_DIAMOND
        assert result.confidence > 0.4

    def test_convergence_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        prev = _make_converging_tracks(100.0)
        curr = _make_converging_tracks(50.0)
        clf.update(prev)
        result = clf.update(curr)
        assert result.formation == FORMATION_CONVERGING
        assert result.convergence_rate > 0.2
        assert result.converging_toward is not None

    def test_column_detected(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_column_tracks()
        result = clf.update(tracks)
        assert result.formation == FORMATION_COLUMN
        assert result.confidence > 0.4

    def test_few_tracks_returns_scatter(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = [_make_track("solo", 0.0, 0.0)]
        result = clf.update(tracks)
        assert result.formation == FORMATION_SCATTER

    def test_empty_tracks(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        result = clf.update([])
        assert result.formation == FORMATION_SCATTER
        assert result.track_count == 0


# ---------------------------------------------------------------------------
# Temporal smoothing tests
# ---------------------------------------------------------------------------


class TestTemporalSmoothing:
    def test_no_flip_on_single_frame(self) -> None:
        """A single anomalous frame must not change the classification."""
        clf = VisualSwarmClassifier(window_size=10, vote_threshold=7)
        line_tracks = _make_line_tracks()

        # Fill the window with LINE (10 frames)
        for _ in range(10):
            clf.update(line_tracks)
        assert clf.current_formation == FORMATION_LINE

        # Inject one scatter frame
        scatter_tracks = _make_scatter_tracks()
        result = clf.update(scatter_tracks)

        # Should still report LINE (9 LINE + 1 SCATTER, LINE wins)
        assert result.formation == FORMATION_LINE

    def test_sustained_change_switches_formation(self) -> None:
        """After enough consistent frames the classification switches."""
        clf = VisualSwarmClassifier(window_size=10, vote_threshold=7)
        line_tracks = _make_line_tracks()
        scatter_tracks = _make_scatter_tracks()

        # Establish LINE
        for _ in range(10):
            clf.update(line_tracks)
        assert clf.current_formation == FORMATION_LINE

        # Switch to scatter for 10 frames
        for _ in range(10):
            result = clf.update(scatter_tracks)
        assert result.formation == FORMATION_SCATTER

    def test_window_size_one_immediate_change(self) -> None:
        """With window_size=1, classification changes immediately."""
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        line_tracks = _make_line_tracks()
        scatter_tracks = _make_scatter_tracks()

        clf.update(line_tracks)
        assert clf.current_formation == FORMATION_LINE

        result = clf.update(scatter_tracks)
        assert result.formation == FORMATION_SCATTER

    def test_reset_clears_state(self) -> None:
        clf = VisualSwarmClassifier()
        clf.update(_make_line_tracks())
        clf.reset()
        assert clf.current_formation == FORMATION_SCATTER
        assert clf._prev_tracks is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# SwarmAnalysis output tests
# ---------------------------------------------------------------------------


class TestSwarmAnalysisOutput:
    def test_analysis_fields_populated(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = _make_line_tracks()
        result = clf.update(tracks)

        assert isinstance(result, SwarmAnalysis)
        assert result.track_count == 5
        assert result.spread_m >= 0.0
        assert 0.0 <= result.velocity_coherence <= 1.0
        assert isinstance(result.centroid, tuple)
        assert len(result.centroid) == 3
        assert result.formation in ALL_FORMATIONS

    def test_centroid_matches_tracks(self) -> None:
        clf = VisualSwarmClassifier(window_size=1, vote_threshold=1)
        tracks = [
            _make_track("a", 10.0, 20.0),
            _make_track("b", 30.0, 40.0),
            _make_track("c", 20.0, 30.0),
        ]
        result = clf.update(tracks)
        assert result.centroid[0] == pytest.approx(20.0)
        assert result.centroid[1] == pytest.approx(30.0)
