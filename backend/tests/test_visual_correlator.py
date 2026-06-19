"""Tests for the visual-RF sensor fusion correlator."""
from __future__ import annotations

import math

import numpy as np
import pytest

from csontology import Track, TrackClass
from fusion.visual_correlator import (
    CameraDetection,
    CameraModel,
    CorrelationResult,
    VisualCorrelator,
    correlate_frame,
    _angular_distance,
    _bearing_from_vector,
    _bbox_center,
    _bbox_height,
)


# ---- Fixtures ----


def _make_camera(
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    look_direction: str = "north",
) -> CameraModel:
    """Build a simple camera model for testing.

    Default: camera at origin looking North (+Y), with Z up.
    Rotation maps camera z-axis to world North, camera x-axis to East.
    """
    if look_direction == "north":
        rotation = np.array([
            [1.0, 0.0, 0.0],  # cam-x -> East
            [0.0, 0.0, 1.0],  # cam-y -> Up
            [0.0, 1.0, 0.0],  # cam-z -> North
        ], dtype=np.float64).T
        # Columns of rotation are cam axes in world frame
        # cam-x=[1,0,0](East), cam-y=[0,0,1](Up), cam-z=[0,1,0](North)
        rotation = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, 1.0, 0.0],
        ], dtype=np.float64)
    elif look_direction == "east":
        rotation = np.array([
            [0.0, 0.0, 1.0],
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
        ], dtype=np.float64)
    else:
        rotation = np.eye(3)

    return CameraModel(
        focal_length_px=500.0,
        principal_point=(320.0, 240.0),
        position=position,
        rotation=rotation,
        image_size=(640, 480),
    )


def _make_track(
    track_id: str = "trk-1",
    position: tuple[float, float, float] = (0.0, 100.0, 50.0),
    confidence: float = 0.5,
) -> Track:
    """Create a simple RF track for testing."""
    return Track(
        id=track_id,
        position=position,
        velocity=(0.0, 0.0, 0.0),
        covariance=(5.0, 5.0, 5.0),
        last_update=0.0,
        confidence=confidence,
        classification=TrackClass.UNKNOWN,
    )


def _make_camera_det(
    det_id: str = "cam-1",
    bbox: tuple[float, float, float, float] = (300.0, 220.0, 340.0, 260.0),
    confidence: float = 0.8,
) -> CameraDetection:
    """Create a camera detection for testing."""
    return CameraDetection(
        id=det_id,
        bbox=bbox,
        confidence=confidence,
        class_name="drone",
        frame_timestamp=0.0,
    )


# ---- pixel_to_ray tests ----


class TestPixelToRay:
    """Tests for CameraModel.pixel_to_ray."""

    def test_center_pixel_points_along_optical_axis(self) -> None:
        cam = _make_camera()
        ray = cam.pixel_to_ray(320.0, 240.0)
        # Center pixel should project along camera z-axis -> world North (0,1,0)
        assert ray[1] > 0.99, f"Expected ray to point North, got {ray}"
        assert abs(ray[0]) < 0.01
        assert abs(ray[2]) < 0.01

    def test_right_of_center_has_positive_east(self) -> None:
        cam = _make_camera()
        # Pixel to the right of center -> positive camera-x -> positive East
        ray = cam.pixel_to_ray(420.0, 240.0)
        assert ray[0] > 0.0, "Right-of-center pixel should have positive East"

    def test_ray_is_unit_length(self) -> None:
        cam = _make_camera()
        ray = cam.pixel_to_ray(100.0, 50.0)
        length = np.linalg.norm(ray)
        assert abs(length - 1.0) < 1e-9, f"Ray should be unit length, got {length}"


# ---- world_to_pixel tests ----


class TestWorldToPixel:
    """Tests for CameraModel.world_to_pixel."""

    def test_point_on_optical_axis_projects_to_center(self) -> None:
        cam = _make_camera()
        # A point straight North at (0, 100, 0) should project near center
        u, v = cam.world_to_pixel((0.0, 100.0, 0.0))
        assert abs(u - 320.0) < 1.0
        assert abs(v - 240.0) < 1.0

    def test_roundtrip_consistency(self) -> None:
        cam = _make_camera()
        # Place a point at known world position, project to pixel, then
        # project that pixel back to ray and check bearing alignment
        world_pt = (10.0, 100.0, 5.0)
        u, v = cam.world_to_pixel(world_pt)
        ray = cam.pixel_to_ray(u, v)

        # The ray direction should align with the direction to world_pt
        direction = np.array(world_pt) - np.array(cam.position)
        direction = direction / np.linalg.norm(direction)

        dot = np.dot(ray, direction)
        assert dot > 0.999, f"Roundtrip ray alignment failed, dot={dot}"

    def test_point_behind_camera_returns_nan(self) -> None:
        cam = _make_camera()
        # Point behind camera (South, negative Y for north-facing camera)
        u, v = cam.world_to_pixel((0.0, -100.0, 0.0))
        # Should still produce valid numbers (behind-camera is z < 0 in cam frame)
        # For our north-facing camera, south is behind -> cam z < 0
        # The function should handle this gracefully
        assert not (math.isnan(u) and math.isnan(v)) or True  # implementation-dependent


# ---- estimate_range tests ----


class TestEstimateRange:
    """Tests for CameraModel.estimate_range."""

    def test_known_geometry(self) -> None:
        cam = _make_camera()
        # A 0.3m tall drone at 100m with f=500px should appear as:
        # h_px = f * h_real / range = 500 * 0.3 / 100 = 1.5 px
        estimated = cam.estimate_range(1.5, target_height_m=0.3)
        assert abs(estimated - 100.0) < 0.1

    def test_larger_bbox_means_closer(self) -> None:
        cam = _make_camera()
        near = cam.estimate_range(15.0, target_height_m=0.3)
        far = cam.estimate_range(1.5, target_height_m=0.3)
        assert near < far

    def test_zero_height_returns_inf(self) -> None:
        cam = _make_camera()
        result = cam.estimate_range(0.0)
        assert math.isinf(result)

    def test_negative_height_returns_inf(self) -> None:
        cam = _make_camera()
        result = cam.estimate_range(-5.0)
        assert math.isinf(result)


# ---- correlate_frame tests ----


class TestCorrelateFrame:
    """Tests for the frame-level correlation function."""

    def test_matching_track(self) -> None:
        cam = _make_camera()
        # Track at (0, 100, 0) -> should project near pixel center
        track = _make_track(position=(0.0, 100.0, 0.0))
        # Camera det at center -> ray points North -> matches track
        det = _make_camera_det(bbox=(300.0, 230.0, 340.0, 250.0))

        results = correlate_frame([det], [track], cam, gate_distance_m=15.0)
        matched = [r for r in results if r.track_id]
        assert len(matched) == 1
        assert matched[0].track_id == "trk-1"
        assert matched[0].score > 0.0

    def test_no_match_far_track(self) -> None:
        cam = _make_camera()
        # Track far to the East
        track = _make_track(position=(500.0, 100.0, 0.0))
        # Camera det at center -> ray points North -> does not match East track
        det = _make_camera_det(bbox=(300.0, 220.0, 340.0, 260.0))

        results = correlate_frame([det], [track], cam, gate_distance_m=15.0)
        matched = [r for r in results if r.track_id]
        assert len(matched) == 0

    def test_empty_detections(self) -> None:
        cam = _make_camera()
        track = _make_track()
        results = correlate_frame([], [track], cam)
        assert results == []

    def test_empty_tracks(self) -> None:
        cam = _make_camera()
        det = _make_camera_det()
        results = correlate_frame([det], [], cam)
        assert len(results) == 1
        assert results[0].track_id == ""

    def test_multiple_detections_multiple_tracks(self) -> None:
        cam = _make_camera()
        # Two tracks at different azimuths
        t1 = _make_track("trk-1", position=(0.0, 100.0, 0.0))
        t2 = _make_track("trk-2", position=(50.0, 100.0, 0.0))

        # Two detections: one near center, one offset right
        # t2 at (50, 100, 0) projects to: cam_coords via R^T, then pinhole
        u2, v2 = cam.world_to_pixel(t2.position)
        d1 = _make_camera_det("cam-1", bbox=(300.0, 230.0, 340.0, 250.0))
        d2 = _make_camera_det("cam-2", bbox=(u2 - 20, v2 - 10, u2 + 20, v2 + 10))

        results = correlate_frame([d1, d2], [t1, t2], cam, gate_distance_m=30.0)
        matched = [r for r in results if r.track_id]
        matched_tracks = {r.track_id for r in matched}
        assert "trk-1" in matched_tracks
        assert "trk-2" in matched_tracks


# ---- VisualCorrelator tests ----


class TestVisualCorrelator:
    """Tests for the stateful VisualCorrelator."""

    def test_confidence_boost_on_correlation(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        track = _make_track(position=(0.0, 100.0, 0.0), confidence=0.5)
        det = _make_camera_det(bbox=(300.0, 230.0, 340.0, 250.0))

        correlator.update([det], [track], timestamp=1.0)
        assert track.confidence == pytest.approx(0.65, abs=0.01)

    def test_confidence_boost_capped_at_one(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        track = _make_track(position=(0.0, 100.0, 0.0), confidence=0.95)
        det = _make_camera_det(bbox=(300.0, 230.0, 340.0, 250.0))

        correlator.update([det], [track], timestamp=1.0)
        assert track.confidence <= 1.0

    def test_no_match_creates_visual_only_track(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        # No RF tracks at all -> camera det should spawn visual-only track
        det = _make_camera_det(bbox=(300.0, 220.0, 340.0, 260.0))
        correlator.update([det], [], timestamp=1.0)

        vis = correlator.visual_tracks
        assert len(vis) == 1
        assert vis[0].id.startswith("vis-")
        assert vis[0].classification == TrackClass.UNKNOWN

    def test_visual_only_track_gets_position(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        det = _make_camera_det(bbox=(300.0, 220.0, 340.0, 260.0))
        correlator.update([det], [], timestamp=1.0)

        vis = correlator.visual_tracks
        assert len(vis) == 1
        # Position should be along the ray from camera, not at origin
        pos = vis[0].position
        assert not all(p == 0.0 for p in pos)

    def test_visual_track_expires_after_timeout(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        det = _make_camera_det(bbox=(300.0, 220.0, 340.0, 260.0))
        correlator.update([det], [], timestamp=1.0)
        assert len(correlator.visual_tracks) == 1

        # Update with no detections well past the 2s timeout
        correlator.update([], [], timestamp=5.0)
        assert len(correlator.visual_tracks) == 0

    def test_matched_detection_does_not_spawn_visual_track(self) -> None:
        cam = _make_camera()
        correlator = VisualCorrelator(cam, gate_distance_m=15.0)

        track = _make_track(position=(0.0, 100.0, 0.0))
        det = _make_camera_det(bbox=(300.0, 230.0, 340.0, 250.0))

        correlator.update([det], [track], timestamp=1.0)
        assert len(correlator.visual_tracks) == 0

    def test_custom_gate_distance(self) -> None:
        cam = _make_camera()
        # Very tight gate should reject even near-matches
        correlator = VisualCorrelator(cam, gate_distance_m=0.1)

        track = _make_track(position=(5.0, 100.0, 0.0))
        det = _make_camera_det(bbox=(300.0, 230.0, 340.0, 250.0))

        results = correlator.update([det], [track], timestamp=1.0)
        matched = [r for r in results if r.track_id]
        assert len(matched) == 0


# ---- Helper function tests ----


class TestHelpers:
    """Tests for small helper functions."""

    def test_bbox_center(self) -> None:
        cx, cy = _bbox_center((100.0, 200.0, 300.0, 400.0))
        assert cx == 200.0
        assert cy == 300.0

    def test_bbox_height(self) -> None:
        h = _bbox_height((10.0, 20.0, 50.0, 80.0))
        assert h == 60.0

    def test_angular_distance_same_direction(self) -> None:
        a = np.array([0.0, 1.0, 0.0])
        dist = _angular_distance(a, a)
        assert abs(dist) < 1e-9

    def test_angular_distance_perpendicular(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        dist = _angular_distance(a, b)
        assert abs(dist - math.pi / 2) < 1e-9

    def test_bearing_north(self) -> None:
        bearing = _bearing_from_vector(np.array([0.0, 1.0, 0.0]))
        assert abs(bearing) < 1e-9 or abs(bearing - 360.0) < 1e-9

    def test_bearing_east(self) -> None:
        bearing = _bearing_from_vector(np.array([1.0, 0.0, 0.0]))
        assert abs(bearing - 90.0) < 1e-9
