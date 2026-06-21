"""
Tests for the engagement replay analyzer with virtual camera detection.

Covers FOV checks, apparent size computation, detection rate calculation,
projection, bbox synthesis, and full analyzer flow with mock frame data.
"""
from __future__ import annotations

import gzip
import json
import math
import tempfile
from pathlib import Path
from typing import List

import pytest

from scripts.replay_with_detection import (
    FrameAnalysis,
    ReplayAnalysis,
    ReplayAnalyzer,
    VirtualCamera,
    auto_bearing,
    compute_apparent_size,
    compute_range,
    is_in_fov,
    project_to_pixel,
    synthesize_bbox,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_camera(
    bearing: float = 0.0,
    fov: float = 60.0,
    max_range: float = 2000.0,
) -> VirtualCamera:
    """Create a virtual camera at the origin looking in a given direction."""
    return VirtualCamera(
        position=(0.0, 0.0, 0.0),
        bearing_deg=bearing,
        fov_deg=fov,
        max_range_m=max_range,
    )


def _make_track(
    track_id: str,
    enu: list,
    confidence: float = 0.9,
    classification: str = "HOSTILE",
) -> dict:
    """Build a minimal track dict matching the wargame frame shape."""
    return {
        "id": track_id,
        "enu": enu,
        "confidence": confidence,
        "classification": classification,
        "velocity": [0, 0, 0],
    }


def _make_frame(
    tick: int,
    tracks: list | None = None,
    site_enu: list | None = None,
) -> dict:
    """Build a minimal frame dict matching wargame recorder output."""
    return {
        "type": "WARGAME_FRAME",
        "scenario": "test",
        "done": False,
        "metrics": {
            "tick": tick,
            "sim_time_s": tick * 0.5,
            "active_hostiles": len(tracks) if tracks else 0,
            "tracks_held": len(tracks) if tracks else 0,
            "leakers": 0,
            "engagements_made": 0,
            "intercepts": 0,
            "intercept_rate": 0.0,
            "defender_spent": 0.0,
            "attacker_destroyed": 0.0,
            "cost_exchange_ratio": None,
        },
        "tracks": tracks or [],
        "defenders": [],
        "assignments": [],
        "site": {"enu": site_enu or [0, 0, 0]},
    }


def _write_recording(tmp_path: Path, frames: List[dict]) -> Path:
    """Write a synthetic gzipped recording to disk."""
    path = tmp_path / "test_recording.json.gz"
    payload = {
        "metadata": {
            "scenario_name": "test_scenario",
            "start_time": 0.0,
            "end_time": len(frames) * 0.5,
            "total_frames": len(frames),
            "version": "1.0",
        },
        "frames": frames,
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


# ---------------------------------------------------------------------------
# FOV tests
# ---------------------------------------------------------------------------


class TestIsInFov:
    """Virtual camera FOV boundary checks."""

    def test_track_directly_ahead_is_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        # North of origin, directly ahead
        assert is_in_fov(camera, (0.0, 500.0, 50.0)) is True

    def test_track_behind_camera_is_not_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        # South of origin, behind the camera
        assert is_in_fov(camera, (0.0, -500.0, 50.0)) is False

    def test_track_at_fov_edge_is_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        # 29 degrees off-axis (within 30-degree half-FOV)
        dist = 500.0
        angle = math.radians(29.0)
        enu = (dist * math.sin(angle), dist * math.cos(angle), 0.0)
        assert is_in_fov(camera, enu) is True

    def test_track_outside_fov_edge_is_not_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        # 35 degrees off-axis (outside 30-degree half-FOV)
        dist = 500.0
        angle = math.radians(35.0)
        enu = (dist * math.sin(angle), dist * math.cos(angle), 0.0)
        assert is_in_fov(camera, enu) is False

    def test_track_beyond_max_range_is_not_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0, max_range=1000.0)
        assert is_in_fov(camera, (0.0, 1500.0, 0.0)) is False

    def test_track_at_camera_position_is_visible(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        assert is_in_fov(camera, (0.0, 0.0, 0.0)) is True

    def test_bearing_rotates_fov(self) -> None:
        # Camera points East (bearing 90)
        camera = _make_camera(bearing=90.0, fov=60.0)
        # Track to the East
        assert is_in_fov(camera, (500.0, 0.0, 0.0)) is True
        # Track to the North (90 degrees off the East-facing camera)
        assert is_in_fov(camera, (0.0, 500.0, 0.0)) is False

    def test_wide_fov_sees_more(self) -> None:
        camera = _make_camera(bearing=0.0, fov=120.0)
        dist = 500.0
        angle = math.radians(55.0)
        enu = (dist * math.sin(angle), dist * math.cos(angle), 0.0)
        assert is_in_fov(camera, enu) is True


# ---------------------------------------------------------------------------
# Apparent size tests
# ---------------------------------------------------------------------------


class TestApparentSize:
    """Inverse-range scaling of apparent bbox size."""

    def test_closer_objects_are_larger(self) -> None:
        near = compute_apparent_size(50.0)
        far = compute_apparent_size(500.0)
        assert near > far

    def test_reference_range_returns_reference_size(self) -> None:
        size = compute_apparent_size(100.0, ref_range=100.0, ref_size=80.0)
        assert size == pytest.approx(80.0)

    def test_double_range_halves_size(self) -> None:
        base = compute_apparent_size(100.0, ref_range=100.0, ref_size=80.0)
        half = compute_apparent_size(200.0, ref_range=100.0, ref_size=80.0)
        assert half == pytest.approx(base / 2.0)

    def test_zero_range_returns_reference(self) -> None:
        size = compute_apparent_size(0.0)
        assert size > 0


# ---------------------------------------------------------------------------
# Range computation
# ---------------------------------------------------------------------------


class TestComputeRange:
    """3D slant range from camera to track."""

    def test_horizontal_range(self) -> None:
        camera = _make_camera()
        rng = compute_range(camera, (300.0, 400.0, 0.0))
        assert rng == pytest.approx(500.0)

    def test_includes_altitude(self) -> None:
        camera = _make_camera()
        rng = compute_range(camera, (0.0, 0.0, 100.0))
        assert rng == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Projection tests
# ---------------------------------------------------------------------------


class TestProjectToPixel:
    """Virtual camera pixel projection."""

    def test_center_track_projects_to_center(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        px, py = project_to_pixel(camera, (0.0, 500.0, 0.0))
        assert px == pytest.approx(camera.width_px / 2.0, abs=1.0)
        assert py == pytest.approx(camera.height_px / 2.0, abs=1.0)

    def test_right_of_center_has_higher_px(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        _, _ = project_to_pixel(camera, (0.0, 500.0, 0.0))
        px_right, _ = project_to_pixel(camera, (50.0, 500.0, 0.0))
        assert px_right > camera.width_px / 2.0


# ---------------------------------------------------------------------------
# Bbox synthesis
# ---------------------------------------------------------------------------


class TestSynthesizeBbox:
    """Synthetic bounding box generation."""

    def test_visible_track_gets_bbox(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0, max_range=2000.0)
        bbox = synthesize_bbox(camera, (0.0, 200.0, 50.0))
        assert bbox is not None
        x1, y1, x2, y2 = bbox
        assert x2 > x1
        assert y2 > y1

    def test_out_of_fov_returns_none(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0)
        bbox = synthesize_bbox(camera, (0.0, -500.0, 0.0))
        assert bbox is None

    def test_very_far_track_too_small(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0, max_range=50000.0)
        # At 50 km range, apparent size should be < 4 px
        bbox = synthesize_bbox(camera, (0.0, 40000.0, 0.0))
        assert bbox is None

    def test_close_track_has_larger_bbox(self) -> None:
        camera = _make_camera(bearing=0.0, fov=60.0, max_range=5000.0)
        bbox_near = synthesize_bbox(camera, (0.0, 100.0, 0.0))
        bbox_far = synthesize_bbox(camera, (0.0, 1000.0, 0.0))
        assert bbox_near is not None
        assert bbox_far is not None
        near_width = bbox_near[2] - bbox_near[0]
        far_width = bbox_far[2] - bbox_far[0]
        assert near_width > far_width


# ---------------------------------------------------------------------------
# Auto bearing
# ---------------------------------------------------------------------------


class TestAutoBearing:
    """Auto-compute bearing toward track centroid."""

    def test_tracks_to_north_give_zero_bearing(self) -> None:
        tracks = [_make_track("t1", [0, 500, 50]), _make_track("t2", [0, 600, 50])]
        bearing = auto_bearing((0, 0, 0), tracks)
        assert bearing == pytest.approx(0.0, abs=1.0)

    def test_tracks_to_east_give_90_bearing(self) -> None:
        tracks = [_make_track("t1", [500, 0, 50])]
        bearing = auto_bearing((0, 0, 0), tracks)
        assert bearing == pytest.approx(90.0, abs=1.0)

    def test_empty_tracks_give_zero(self) -> None:
        assert auto_bearing((0, 0, 0), []) == 0.0


# ---------------------------------------------------------------------------
# Detection rate calculation
# ---------------------------------------------------------------------------


class TestDetectionRate:
    """End-to-end detection rate from the analyzer."""

    def test_all_visible_tracks_detected(self) -> None:
        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        tracks = [
            _make_track("t1", [0, 200, 50]),
            _make_track("t2", [50, 300, 30]),
        ]
        frame = _make_frame(0, tracks=tracks)
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames([frame])
        assert analysis.tracks_detected == 2
        assert analysis.tracks_missed == 0
        assert analysis.detection_rate == pytest.approx(1.0)

    def test_out_of_fov_tracks_missed(self) -> None:
        camera = _make_camera(bearing=0.0, fov=30.0, max_range=5000.0)
        tracks = [
            _make_track("t1", [0, 200, 50]),     # in FOV
            _make_track("t2", [0, -500, 50]),     # behind camera
        ]
        frame = _make_frame(0, tracks=tracks)
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames([frame])
        assert analysis.tracks_detected == 1
        # Out-of-FOV tracks are not in_fov, so they count against total but
        # not as in-FOV misses. Detection rate = detected / total_tracks.
        assert analysis.detection_rate == pytest.approx(0.5)

    def test_empty_frame_zero_rate(self) -> None:
        camera = _make_camera()
        frame = _make_frame(0, tracks=[])
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames([frame])
        assert analysis.detection_rate == pytest.approx(0.0)
        assert analysis.frames_with_detections == 0


# ---------------------------------------------------------------------------
# Multi-frame analysis
# ---------------------------------------------------------------------------


class TestMultiFrameAnalysis:
    """Analyzer across multiple frames."""

    def test_per_frame_breakdown_length(self) -> None:
        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        frames = [
            _make_frame(0, tracks=[_make_track("t1", [0, 200, 50])]),
            _make_frame(1, tracks=[_make_track("t2", [0, 300, 50])]),
            _make_frame(2, tracks=[]),
        ]
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames(frames)
        assert analysis.total_frames == 3
        assert len(analysis.per_frame) == 3

    def test_frames_with_detections_count(self) -> None:
        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        frames = [
            _make_frame(0, tracks=[_make_track("t1", [0, 200, 50])]),
            _make_frame(1, tracks=[]),
            _make_frame(2, tracks=[_make_track("t2", [0, 300, 50])]),
        ]
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames(frames)
        assert analysis.frames_with_detections == 2

    def test_max_detection_range(self) -> None:
        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        frames = [
            _make_frame(0, tracks=[
                _make_track("t1", [0, 200, 0]),
                _make_track("t2", [0, 800, 0]),
            ]),
        ]
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames(frames)
        assert analysis.max_detection_range == pytest.approx(800.0, abs=1.0)


# ---------------------------------------------------------------------------
# Recording file integration
# ---------------------------------------------------------------------------


class TestRecordingFile:
    """Load and analyze from a gzipped recording file."""

    def test_analyze_recording_from_file(self, tmp_path: Path) -> None:
        tracks = [_make_track("t1", [0, 300, 50])]
        frames = [_make_frame(i, tracks=tracks) for i in range(5)]
        rec_path = _write_recording(tmp_path, frames)

        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_recording(rec_path)
        assert analysis.total_frames == 5
        assert analysis.tracks_detected == 5

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        camera = _make_camera()
        analyzer = ReplayAnalyzer(camera=camera)
        with pytest.raises(FileNotFoundError):
            analyzer.analyze_recording(tmp_path / "nonexistent.json.gz")


# ---------------------------------------------------------------------------
# Classification accuracy
# ---------------------------------------------------------------------------


class TestClassificationAccuracy:
    """DroneClassifier integration via synthetic detections."""

    def test_high_confidence_tracks_classified(self) -> None:
        camera = _make_camera(bearing=0.0, fov=90.0, max_range=5000.0)
        tracks = [_make_track("t1", [0, 200, 50], confidence=0.95)]
        frame = _make_frame(0, tracks=tracks)
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames([frame])
        assert analysis.classification_accuracy == pytest.approx(1.0)

    def test_zero_tracks_gives_zero_accuracy(self) -> None:
        camera = _make_camera()
        frame = _make_frame(0, tracks=[])
        analyzer = ReplayAnalyzer(camera=camera)
        analysis = analyzer.analyze_frames([frame])
        # No classifications attempted, default to 0
        assert analysis.classification_accuracy == pytest.approx(0.0)
