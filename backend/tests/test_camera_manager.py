"""Tests for the multi-camera management system."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from vision.camera_manager import (
    CameraConfig,
    CameraFeed,
    CameraManager,
    FrameHealth,
    PanoramaStitcher,
    _angle_in_sector,
    _angular_range,
    _normalize_angle,
    default_site_defense_configs,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_config(
    camera_id: str = "cam-test",
    bearing: float = 0.0,
    fov: float = 90.0,
    enabled: bool = True,
) -> CameraConfig:
    return CameraConfig(
        camera_id=camera_id,
        source="0",
        position=(0.0, 0.0, 3.0),
        bearing_deg=bearing,
        fov_deg=fov,
        resolution=(640, 480),
        enabled=enabled,
    )


def _solid_frame(
    color: tuple[int, int, int],
    w: int = 640,
    h: int = 480,
) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


# ---------------------------------------------------------------------------
# CameraConfig tests
# ---------------------------------------------------------------------------


class TestCameraConfig:
    def test_creation_with_defaults(self) -> None:
        cfg = CameraConfig(
            camera_id="cam-1",
            source="rtsp://host/stream",
            position=(10.0, 20.0, 3.0),
            bearing_deg=45.0,
        )
        assert cfg.camera_id == "cam-1"
        assert cfg.source == "rtsp://host/stream"
        assert cfg.position == (10.0, 20.0, 3.0)
        assert cfg.bearing_deg == 45.0
        assert cfg.fov_deg == 60.0
        assert cfg.resolution == (1280, 720)
        assert cfg.enabled is True

    def test_creation_with_all_fields(self) -> None:
        cfg = CameraConfig(
            camera_id="cam-2",
            source="/dev/video0",
            position=(0.0, 0.0, 5.0),
            bearing_deg=180.0,
            fov_deg=120.0,
            resolution=(1920, 1080),
            enabled=False,
        )
        assert cfg.fov_deg == 120.0
        assert cfg.resolution == (1920, 1080)
        assert cfg.enabled is False

    def test_device_index_source(self) -> None:
        cfg = _make_config(camera_id="cam-dev", bearing=90.0)
        assert cfg.source == "0"

    def test_bearing_wraps(self) -> None:
        cfg = _make_config(bearing=359.0)
        assert cfg.bearing_deg == 359.0


# ---------------------------------------------------------------------------
# Angle math tests
# ---------------------------------------------------------------------------


class TestAngleMath:
    def test_normalize_positive(self) -> None:
        assert _normalize_angle(90.0) == 90.0

    def test_normalize_negative(self) -> None:
        assert _normalize_angle(-10.0) == 350.0

    def test_normalize_over_360(self) -> None:
        assert _normalize_angle(370.0) == 10.0

    def test_angular_range_simple(self) -> None:
        start, end = _angular_range(90.0, 60.0)
        assert start == 60.0
        assert end == 120.0

    def test_angular_range_wraparound(self) -> None:
        start, end = _angular_range(350.0, 40.0)
        assert start == 330.0
        assert end == 10.0

    def test_angle_in_sector_simple(self) -> None:
        assert _angle_in_sector(90.0, 60.0, 120.0)
        assert not _angle_in_sector(130.0, 60.0, 120.0)

    def test_angle_in_sector_wraparound(self) -> None:
        assert _angle_in_sector(355.0, 330.0, 10.0)
        assert _angle_in_sector(5.0, 330.0, 10.0)
        assert not _angle_in_sector(180.0, 330.0, 10.0)


# ---------------------------------------------------------------------------
# CameraFeed tests (cv2 mocked)
# ---------------------------------------------------------------------------


class TestCameraFeed:
    def test_initial_state(self) -> None:
        config = _make_config()
        feed = CameraFeed(config)
        assert feed.config is config
        assert not feed.is_running
        assert feed.get_frame() is None

    def test_health_defaults(self) -> None:
        feed = CameraFeed(_make_config())
        health = feed.health
        assert health.fps == 0.0
        assert health.dropped_frames == 0
        assert health.total_frames == 0

    def test_frame_buffer_returns_copy(self) -> None:
        feed = CameraFeed(_make_config())
        test_frame = _solid_frame((255, 0, 0))
        feed._lock.acquire()
        feed._frame = test_frame
        feed._lock.release()
        frame = feed.get_frame()
        assert frame is not None
        assert np.array_equal(frame, test_frame)
        assert frame is not test_frame


# ---------------------------------------------------------------------------
# CameraManager tests
# ---------------------------------------------------------------------------


class TestCameraManager:
    def test_add_camera(self) -> None:
        mgr = CameraManager()
        config = _make_config(camera_id="cam-n", bearing=0.0, enabled=False)
        mgr.add_camera(config)
        assert "cam-n" in mgr.camera_ids

    def test_add_duplicate_raises(self) -> None:
        mgr = CameraManager()
        config = _make_config(camera_id="cam-n", enabled=False)
        mgr.add_camera(config)
        with pytest.raises(KeyError, match="already exists"):
            mgr.add_camera(config)

    def test_remove_camera(self) -> None:
        mgr = CameraManager()
        config = _make_config(camera_id="cam-n", enabled=False)
        mgr.add_camera(config)
        mgr.remove_camera("cam-n")
        assert "cam-n" not in mgr.camera_ids

    def test_remove_missing_raises(self) -> None:
        mgr = CameraManager()
        with pytest.raises(KeyError, match="not found"):
            mgr.remove_camera("nonexistent")

    def test_get_frame_missing_camera(self) -> None:
        mgr = CameraManager()
        assert mgr.get_frame("nonexistent") is None

    def test_get_all_frames_empty(self) -> None:
        mgr = CameraManager()
        assert mgr.get_all_frames() == {}

    def test_get_health_missing(self) -> None:
        mgr = CameraManager()
        assert mgr.get_health("nonexistent") is None


# ---------------------------------------------------------------------------
# Coverage map and sector tests
# ---------------------------------------------------------------------------


class TestCoverageMap:
    def _setup_quad_manager(self) -> CameraManager:
        mgr = CameraManager()
        for cfg in default_site_defense_configs():
            cfg_disabled = CameraConfig(
                camera_id=cfg.camera_id,
                source=cfg.source,
                position=cfg.position,
                bearing_deg=cfg.bearing_deg,
                fov_deg=cfg.fov_deg,
                resolution=cfg.resolution,
                enabled=False,
            )
            mgr.add_camera(cfg_disabled)
        for cam_id in mgr.camera_ids:
            mgr._configs[cam_id].enabled = True
        return mgr

    def test_coverage_map_four_cameras(self) -> None:
        mgr = self._setup_quad_manager()
        coverage = mgr.get_coverage_map()
        assert len(coverage) == 4
        bearings = sorted(c["bearing_deg"] for c in coverage)
        assert bearings == [0.0, 90.0, 180.0, 270.0]

    def test_coverage_map_excludes_disabled(self) -> None:
        mgr = CameraManager()
        cfg = _make_config(camera_id="cam-off", enabled=False)
        mgr.add_camera(cfg)
        coverage = mgr.get_coverage_map()
        assert len(coverage) == 0

    def test_coverage_map_fields(self) -> None:
        mgr = CameraManager()
        cfg = _make_config(camera_id="cam-x", bearing=45.0, fov=90.0, enabled=False)
        mgr.add_camera(cfg)
        mgr._configs["cam-x"].enabled = True
        coverage = mgr.get_coverage_map()
        assert len(coverage) == 1
        entry = coverage[0]
        assert entry["camera_id"] == "cam-x"
        assert entry["bearing_deg"] == 45.0
        assert entry["fov_deg"] == 90.0
        assert "sector_start" in entry
        assert "sector_end" in entry
        assert "position" in entry


class TestSectorAssignment:
    def _setup_quad_manager(self) -> CameraManager:
        mgr = CameraManager()
        for cfg in default_site_defense_configs():
            cfg_disabled = CameraConfig(
                camera_id=cfg.camera_id,
                source=cfg.source,
                position=cfg.position,
                bearing_deg=cfg.bearing_deg,
                fov_deg=cfg.fov_deg,
                resolution=cfg.resolution,
                enabled=False,
            )
            mgr.add_camera(cfg_disabled)
        for cam_id in mgr.camera_ids:
            mgr._configs[cam_id].enabled = True
        return mgr

    def test_full_coverage_no_gaps(self) -> None:
        mgr = self._setup_quad_manager()
        gaps = mgr.detect_gaps(sector_size_deg=10.0)
        assert gaps == []

    def test_sector_assignment_full_coverage(self) -> None:
        mgr = self._setup_quad_manager()
        assignments = mgr.get_sector_assignments(sector_size_deg=10.0)
        for i, cameras in assignments.items():
            assert len(cameras) >= 1, (
                f"Sector {i} ({i * 10}-{i * 10 + 10} deg) has no coverage"
            )

    def test_single_camera_partial_coverage(self) -> None:
        mgr = CameraManager()
        cfg = _make_config(camera_id="cam-n", bearing=0.0, fov=90.0, enabled=False)
        mgr.add_camera(cfg)
        mgr._configs["cam-n"].enabled = True
        gaps = mgr.detect_gaps(sector_size_deg=10.0)
        assert len(gaps) > 0
        total_gap = sum(end - start for start, end in gaps)
        assert abs(total_gap - 270.0) < 15.0

    def test_gap_detection_empty(self) -> None:
        mgr = CameraManager()
        gaps = mgr.detect_gaps(sector_size_deg=10.0)
        assert gaps == [(0.0, 360.0)]

    def test_overlapping_cameras(self) -> None:
        mgr = CameraManager()
        cfg1 = _make_config(camera_id="cam-a", bearing=0.0, fov=120.0, enabled=False)
        cfg2 = _make_config(camera_id="cam-b", bearing=90.0, fov=120.0, enabled=False)
        mgr.add_camera(cfg1)
        mgr.add_camera(cfg2)
        mgr._configs["cam-a"].enabled = True
        mgr._configs["cam-b"].enabled = True
        assignments = mgr.get_sector_assignments(sector_size_deg=10.0)
        overlap_count = sum(
            1 for cams in assignments.values() if len(cams) > 1
        )
        assert overlap_count > 0


# ---------------------------------------------------------------------------
# PanoramaStitcher tests (no cv2 needed, frames are already target height)
# ---------------------------------------------------------------------------


class TestPanoramaStitcher:
    def test_empty_frames(self) -> None:
        stitcher = PanoramaStitcher(target_height=100)
        result = stitcher.stitch({}, {})
        assert result.shape == (100, 1, 3)

    def test_single_frame(self) -> None:
        stitcher = PanoramaStitcher(target_height=100)
        frame = _solid_frame((255, 0, 0), w=200, h=100)
        configs = {"cam-n": _make_config(camera_id="cam-n", bearing=0.0)}
        result = stitcher.stitch({"cam-n": frame}, configs)
        assert result.shape[0] == 100
        assert result.shape[1] == 200
        assert np.all(result[:, :, 0] == 255)

    def test_frames_sorted_by_bearing(self) -> None:
        stitcher = PanoramaStitcher(target_height=100, divider_width=0)
        red = _solid_frame((255, 0, 0), w=100, h=100)
        green = _solid_frame((0, 255, 0), w=100, h=100)
        blue = _solid_frame((0, 0, 255), w=100, h=100)
        configs = {
            "cam-s": _make_config(camera_id="cam-s", bearing=180.0),
            "cam-n": _make_config(camera_id="cam-n", bearing=0.0),
            "cam-e": _make_config(camera_id="cam-e", bearing=90.0),
        }
        frames = {"cam-n": red, "cam-e": green, "cam-s": blue}
        result = stitcher.stitch(frames, configs)
        assert result.shape == (100, 300, 3)
        assert np.all(result[:, 0, 0] == 255)
        assert np.all(result[:, 100, 1] == 255)
        assert np.all(result[:, 200, 2] == 255)

    def test_divider_between_frames(self) -> None:
        stitcher = PanoramaStitcher(
            target_height=100,
            divider_width=4,
            divider_color=(0, 255, 0),
        )
        frame_a = _solid_frame((100, 100, 100), w=50, h=100)
        frame_b = _solid_frame((200, 200, 200), w=50, h=100)
        configs = {
            "cam-a": _make_config(camera_id="cam-a", bearing=0.0),
            "cam-b": _make_config(camera_id="cam-b", bearing=90.0),
        }
        result = stitcher.stitch(
            {"cam-a": frame_a, "cam-b": frame_b}, configs,
        )
        expected_width = 50 + 4 + 50
        assert result.shape == (100, expected_width, 3)
        divider_col = result[:, 50, :]
        assert np.all(divider_col[:, 1] == 255)

    def test_stitch_with_resize_fallback(self) -> None:
        stitcher = PanoramaStitcher(target_height=50)
        frame = _solid_frame((128, 64, 32), w=200, h=100)
        configs = {"cam-x": _make_config(camera_id="cam-x", bearing=0.0)}
        result = stitcher.stitch({"cam-x": frame}, configs)
        assert result.shape[0] == 50
        assert result.shape[1] == 100


# ---------------------------------------------------------------------------
# Default site defense config tests
# ---------------------------------------------------------------------------


class TestDefaultSiteDefense:
    def test_returns_four_cameras(self) -> None:
        configs = default_site_defense_configs()
        assert len(configs) == 4

    def test_bearings(self) -> None:
        configs = default_site_defense_configs()
        bearings = sorted(c.bearing_deg for c in configs)
        assert bearings == [0.0, 90.0, 180.0, 270.0]

    def test_fov_90(self) -> None:
        configs = default_site_defense_configs()
        for cfg in configs:
            assert cfg.fov_deg == 90.0

    def test_full_360_coverage(self) -> None:
        configs = default_site_defense_configs()
        mgr = CameraManager()
        for cfg in configs:
            disabled = CameraConfig(
                camera_id=cfg.camera_id,
                source=cfg.source,
                position=cfg.position,
                bearing_deg=cfg.bearing_deg,
                fov_deg=cfg.fov_deg,
                resolution=cfg.resolution,
                enabled=False,
            )
            mgr.add_camera(disabled)
        for cam_id in mgr.camera_ids:
            mgr._configs[cam_id].enabled = True
        gaps = mgr.detect_gaps(sector_size_deg=10.0)
        assert gaps == []

    def test_custom_origin(self) -> None:
        configs = default_site_defense_configs(origin=(100.0, 200.0, 5.0))
        for cfg in configs:
            assert cfg.position == (100.0, 200.0, 5.0)

    def test_all_enabled(self) -> None:
        configs = default_site_defense_configs()
        for cfg in configs:
            assert cfg.enabled is True

    def test_camera_ids(self) -> None:
        configs = default_site_defense_configs()
        ids = [c.camera_id for c in configs]
        assert "cam-north" in ids
        assert "cam-east" in ids
        assert "cam-south" in ids
        assert "cam-west" in ids
