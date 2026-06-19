"""Tests for the thermal/IR camera support module.

Covers palette application, temperature estimation, hotspot detection,
synthetic thermal generation, and the ThermalDetectorBackend fusion logic.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from vision.thermal import (
    Hotspot,
    Palette,
    ThermalCameraSource,
    ThermalDetectorBackend,
    ThermalProcessor,
    simulate_thermal,
    _bbox_iou,
    _build_lut,
)
from vision.tensorrt_detector import Detection, DetectorBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def processor() -> ThermalProcessor:
    return ThermalProcessor()


@pytest.fixture()
def sample_thermal_16bit() -> np.ndarray:
    """A 120x160 frame with a gradient from 8000 to 48000."""
    frame = np.zeros((120, 160), dtype=np.uint16)
    for row in range(120):
        frame[row, :] = 8000 + int(row * (40000 / 119))
    return frame


@pytest.fixture()
def frame_with_known_hotspots() -> np.ndarray:
    """A 100x100 frame with two distinct bright regions."""
    frame = np.full((100, 100), 5000, dtype=np.uint16)
    # Hotspot 1: 5x5 block at (20, 20)
    frame[18:23, 18:23] = 60000
    # Hotspot 2: 6x6 block at (70, 70)
    frame[67:73, 67:73] = 55000
    return frame


# ---------------------------------------------------------------------------
# Palette tests
# ---------------------------------------------------------------------------


class TestPaletteApplication:
    """Test that palette application produces correct output shapes."""

    def test_ironbow_produces_3_channel(
        self, processor: ThermalProcessor, sample_thermal_16bit: np.ndarray,
    ) -> None:
        bgr = processor.apply_palette(sample_thermal_16bit, "IRONBOW")
        assert bgr.ndim == 3
        assert bgr.shape[2] == 3
        assert bgr.dtype == np.uint8

    def test_output_matches_input_spatial_dims(
        self, processor: ThermalProcessor, sample_thermal_16bit: np.ndarray,
    ) -> None:
        bgr = processor.apply_palette(sample_thermal_16bit, "WHITE_HOT")
        assert bgr.shape[0] == sample_thermal_16bit.shape[0]
        assert bgr.shape[1] == sample_thermal_16bit.shape[1]

    def test_white_hot_is_grayscale(
        self, processor: ThermalProcessor, sample_thermal_16bit: np.ndarray,
    ) -> None:
        bgr = processor.apply_palette(sample_thermal_16bit, "WHITE_HOT")
        # All three channels should be identical for a grayscale palette
        assert np.array_equal(bgr[:, :, 0], bgr[:, :, 1])
        assert np.array_equal(bgr[:, :, 1], bgr[:, :, 2])

    def test_black_hot_inverts_white_hot(
        self, processor: ThermalProcessor, sample_thermal_16bit: np.ndarray,
    ) -> None:
        white = processor.apply_palette(sample_thermal_16bit, "WHITE_HOT")
        black = processor.apply_palette(sample_thermal_16bit, "BLACK_HOT")
        # The sum of corresponding channels should be ~255 (inverted).
        # Allow tolerance of 1 for uint8 rounding.
        combined = white[:, :, 0].astype(int) + black[:, :, 0].astype(int)
        assert np.all(np.abs(combined - 255) <= 1)

    def test_rainbow_produces_color(
        self, processor: ThermalProcessor, sample_thermal_16bit: np.ndarray,
    ) -> None:
        bgr = processor.apply_palette(sample_thermal_16bit, "RAINBOW")
        assert bgr.ndim == 3
        assert bgr.shape[2] == 3
        # Rainbow should have at least some channel variation
        assert not np.array_equal(bgr[:, :, 0], bgr[:, :, 2])

    def test_all_palettes_valid(self) -> None:
        for pal in Palette:
            lut = _build_lut(pal)
            assert lut.shape == (256, 3)
            assert lut.dtype == np.uint8

    def test_flat_frame_returns_zero(
        self, processor: ThermalProcessor,
    ) -> None:
        flat = np.full((10, 10), 30000, dtype=np.uint16)
        bgr = processor.apply_palette(flat, "IRONBOW")
        # All same value normalizes to zero
        assert np.all(bgr == _build_lut(Palette.IRONBOW)[0])


# ---------------------------------------------------------------------------
# Temperature estimation tests
# ---------------------------------------------------------------------------


class TestTemperatureEstimation:
    """Test pixel_to_temp linear mapping."""

    def test_zero_maps_to_min(self) -> None:
        temp = ThermalProcessor.pixel_to_temp(0, -40.0, 150.0)
        assert temp == pytest.approx(-40.0)

    def test_max_maps_to_max(self) -> None:
        temp = ThermalProcessor.pixel_to_temp(65535, -40.0, 150.0)
        assert temp == pytest.approx(150.0, abs=0.01)

    def test_midpoint(self) -> None:
        mid = 65535 / 2.0
        temp = ThermalProcessor.pixel_to_temp(mid, 0.0, 100.0)
        assert temp == pytest.approx(50.0, abs=0.1)

    def test_narrow_range(self) -> None:
        temp = ThermalProcessor.pixel_to_temp(32768, 20.0, 22.0)
        assert 20.0 < temp < 22.0

    def test_same_min_max(self) -> None:
        temp = ThermalProcessor.pixel_to_temp(10000, 25.0, 25.0)
        assert temp == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Hotspot detection tests
# ---------------------------------------------------------------------------


class TestHotspotDetection:
    """Test hotspot detection on synthetic frames with known bright regions."""

    def test_finds_two_hotspots(
        self,
        processor: ThermalProcessor,
        frame_with_known_hotspots: np.ndarray,
    ) -> None:
        spots = processor.detect_hotspots(
            frame_with_known_hotspots,
            threshold_pct=90.0,
            min_area=5,
        )
        assert len(spots) == 2

    def test_hotspot_sorted_by_peak(
        self,
        processor: ThermalProcessor,
        frame_with_known_hotspots: np.ndarray,
    ) -> None:
        spots = processor.detect_hotspots(
            frame_with_known_hotspots,
            threshold_pct=90.0,
            min_area=5,
        )
        assert spots[0].peak_value >= spots[1].peak_value

    def test_hotspot_has_correct_peak(
        self,
        processor: ThermalProcessor,
        frame_with_known_hotspots: np.ndarray,
    ) -> None:
        spots = processor.detect_hotspots(
            frame_with_known_hotspots,
            threshold_pct=90.0,
            min_area=5,
        )
        assert spots[0].peak_value == 60000.0

    def test_hotspot_area_matches(
        self,
        processor: ThermalProcessor,
        frame_with_known_hotspots: np.ndarray,
    ) -> None:
        spots = processor.detect_hotspots(
            frame_with_known_hotspots,
            threshold_pct=90.0,
            min_area=5,
        )
        areas = sorted([s.area for s in spots])
        assert areas == [25, 36]

    def test_min_area_filters_small(
        self, processor: ThermalProcessor,
    ) -> None:
        frame = np.full((50, 50), 1000, dtype=np.uint16)
        # Tiny 2x2 hotspot
        frame[10:12, 10:12] = 60000
        spots = processor.detect_hotspots(
            frame, threshold_pct=90.0, min_area=10,
        )
        assert len(spots) == 0

    def test_hotspot_bbox_contains_center(
        self,
        processor: ThermalProcessor,
        frame_with_known_hotspots: np.ndarray,
    ) -> None:
        spots = processor.detect_hotspots(
            frame_with_known_hotspots,
            threshold_pct=90.0,
            min_area=5,
        )
        for s in spots:
            x, y = s.center
            x1, y1, x2, y2 = s.bbox
            assert x1 <= x <= x2
            assert y1 <= y <= y2

    def test_temperature_estimation_in_hotspot(
        self, processor: ThermalProcessor,
    ) -> None:
        frame = np.full((30, 30), 5000, dtype=np.uint16)
        frame[10:16, 10:16] = 50000
        spots = processor.detect_hotspots(
            frame, threshold_pct=90.0, min_area=5,
            min_temp=-20.0, max_temp=200.0,
        )
        assert len(spots) == 1
        assert spots[0].estimated_temp > 100.0

    def test_no_hotspots_in_uniform_frame(
        self, processor: ThermalProcessor,
    ) -> None:
        frame = np.full((50, 50), 30000, dtype=np.uint16)
        spots = processor.detect_hotspots(
            frame, threshold_pct=95.0, min_area=20,
        )
        assert len(spots) == 0


# ---------------------------------------------------------------------------
# Synthetic thermal generator tests
# ---------------------------------------------------------------------------


class TestSyntheticThermal:
    """Test synthetic thermal generation from RGB frames."""

    def test_output_shape_matches_input(self) -> None:
        rgb = np.random.randint(0, 255, (120, 160, 3), dtype=np.uint8)
        thermal = simulate_thermal(rgb)
        assert thermal.shape == (120, 160)

    def test_output_dtype_uint16(self) -> None:
        rgb = np.zeros((60, 80, 3), dtype=np.uint8)
        thermal = simulate_thermal(rgb)
        assert thermal.dtype == np.uint16

    def test_output_range(self) -> None:
        rgb = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        thermal = simulate_thermal(rgb, num_hotspots=2)
        assert thermal.min() >= 0
        assert thermal.max() <= 65535

    def test_hotspots_create_bright_regions(self) -> None:
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        thermal = simulate_thermal(rgb, num_hotspots=5, hotspot_radius=10)
        # With black input, base is ~8000. Hotspots should push above that.
        assert thermal.max() > 15000

    def test_different_rgb_produces_different_thermal(self) -> None:
        white = np.full((50, 50, 3), 255, dtype=np.uint8)
        black = np.zeros((50, 50, 3), dtype=np.uint8)
        t_white = simulate_thermal(white, num_hotspots=0, noise_std=0.0)
        t_black = simulate_thermal(black, num_hotspots=0, noise_std=0.0)
        assert float(t_white.mean()) > float(t_black.mean())


# ---------------------------------------------------------------------------
# ThermalCameraSource tests
# ---------------------------------------------------------------------------


class TestThermalCameraSource:
    """Test camera source fallback to synthetic mode."""

    def test_synthetic_fallback(self) -> None:
        source = ThermalCameraSource()
        source.open("/dev/nonexistent_thermal_device", 160, 120)
        assert source.is_synthetic is True

    def test_synthetic_read_shape(self) -> None:
        source = ThermalCameraSource()
        source.open("/dev/nonexistent_thermal_device", 160, 120)
        frame = source.read()
        assert frame.shape == (120, 160)
        assert frame.dtype == np.uint16

    def test_close_is_safe(self) -> None:
        source = ThermalCameraSource()
        source.close()  # Should not raise


# ---------------------------------------------------------------------------
# ThermalDetectorBackend tests
# ---------------------------------------------------------------------------


class _MockYoloBackend(DetectorBackend):
    """Stub YOLO backend returning preconfigured detections."""

    def __init__(self, detections: list[Detection] | None = None) -> None:
        self._detections = detections or []

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return list(self._detections)

    def warmup(self) -> None:
        pass


class TestThermalDetectorBackend:
    """Test the fused thermal + YOLO detection pipeline."""

    def test_yolo_only_passthrough(self) -> None:
        yolo_dets = [
            Detection("drone", 0.8, (10.0, 10.0, 50.0, 50.0), class_id=0),
        ]
        backend = ThermalDetectorBackend(
            _MockYoloBackend(yolo_dets),
            hotspot_threshold_pct=99.9,
            hotspot_min_area=1000,
        )
        # Uniform frame produces no hotspots
        frame = np.full((100, 100), 30000, dtype=np.uint16)
        results = backend.detect(frame)
        assert len(results) == 1
        assert results[0].class_name == "drone"
        assert results[0].confidence == 0.8

    def test_hotspot_boosted_confidence(self) -> None:
        # YOLO detects a drone at bbox overlapping with a hotspot
        yolo_dets = [
            Detection("drone", 0.7, (17.0, 17.0, 24.0, 24.0), class_id=0),
        ]
        mock = _MockYoloBackend(yolo_dets)
        backend = ThermalDetectorBackend(
            mock,
            hotspot_threshold_pct=90.0,
            hotspot_min_area=5,
            confidence_boost=0.15,
        )
        # Frame with a hotspot at (18:23, 18:23) overlapping the YOLO bbox
        frame = np.full((100, 100), 5000, dtype=np.uint16)
        frame[18:23, 18:23] = 60000
        results = backend.detect(frame)
        boosted = [d for d in results if d.class_name == "drone"]
        assert len(boosted) == 1
        assert boosted[0].confidence == pytest.approx(0.85, abs=0.01)
        assert boosted[0].is_thermal is True

    def test_standalone_hotspot_detection(self) -> None:
        # No YOLO detections, but hotspot exists
        backend = ThermalDetectorBackend(
            _MockYoloBackend([]),
            hotspot_threshold_pct=90.0,
            hotspot_min_area=5,
            thermal_only_conf=0.4,
        )
        frame = np.full((100, 100), 5000, dtype=np.uint16)
        frame[50:57, 50:57] = 55000
        results = backend.detect(frame)
        thermal_dets = [d for d in results if d.class_name == "thermal_hotspot"]
        assert len(thermal_dets) == 1
        assert thermal_dets[0].is_thermal is True
        assert thermal_dets[0].confidence == pytest.approx(0.4)

    def test_bgr_input_handled(self) -> None:
        backend = ThermalDetectorBackend(
            _MockYoloBackend([]),
            hotspot_threshold_pct=99.0,
            hotspot_min_area=500,
        )
        bgr = np.zeros((100, 100, 3), dtype=np.uint8)
        results = backend.detect(bgr)
        # Should not raise, results may be empty
        assert isinstance(results, list)

    def test_warmup_delegates_to_yolo(self) -> None:
        mock = _MockYoloBackend([])
        mock.warmup = MagicMock()  # type: ignore[method-assign]
        backend = ThermalDetectorBackend(mock)
        backend.warmup()
        mock.warmup.assert_called_once()

    def test_confidence_capped_at_one(self) -> None:
        yolo_dets = [
            Detection("drone", 0.95, (18.0, 18.0, 23.0, 23.0), class_id=0),
        ]
        backend = ThermalDetectorBackend(
            _MockYoloBackend(yolo_dets),
            hotspot_threshold_pct=90.0,
            hotspot_min_area=5,
            confidence_boost=0.2,
        )
        frame = np.full((100, 100), 5000, dtype=np.uint16)
        frame[18:23, 18:23] = 60000
        results = backend.detect(frame)
        for d in results:
            assert d.confidence <= 1.0


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------


class TestBboxIou:
    """Test bounding box IoU calculation."""

    def test_perfect_overlap(self) -> None:
        iou = _bbox_iou((0.0, 0.0, 10.0, 10.0), (0, 0, 10, 10))
        assert iou == pytest.approx(1.0)

    def test_no_overlap(self) -> None:
        iou = _bbox_iou((0.0, 0.0, 5.0, 5.0), (10, 10, 20, 20))
        assert iou == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        iou = _bbox_iou((0.0, 0.0, 10.0, 10.0), (5, 5, 15, 15))
        # Intersection: 5x5=25, Union: 100+100-25=175
        assert iou == pytest.approx(25.0 / 175.0, abs=0.01)

    def test_zero_area(self) -> None:
        iou = _bbox_iou((0.0, 0.0, 0.0, 0.0), (0, 0, 10, 10))
        assert iou == pytest.approx(0.0)
