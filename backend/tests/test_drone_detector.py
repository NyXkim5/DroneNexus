"""Tests for the OVERWATCH drone-specific detection pipeline.

Covers DroneClassifier reclassification logic, size-based filtering,
confidence boosting for ambiguous aerial detections, and camera
geometry estimation.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision.tensorrt_detector import Detection
from vision.drone_detector import (
    CameraParams,
    DroneClass,
    DroneClassifier,
    DroneDetection,
    DRONE_DATASET_CLASS_MAP,
    DRONE_SIZE_MAX_M,
    DRONE_SIZE_MIN_M,
    estimate_object_size_m,
    is_drone_sized,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _det(
    class_name: str = "bird",
    confidence: float = 0.6,
    bbox: tuple = (100, 100, 150, 150),
    class_id: int = 14,
) -> Detection:
    """Create a base Detection for testing."""
    return Detection(
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        class_id=class_id,
    )


def _camera(
    fov_h: float = 62.2,
    width: int = 1920,
    height: int = 1080,
    slant_range: float = 100.0,
    altitude: float = 0.0,
) -> CameraParams:
    """Create CameraParams for testing."""
    return CameraParams(
        fov_horizontal_deg=fov_h,
        sensor_width_px=width,
        sensor_height_px=height,
        slant_range_m=slant_range,
        altitude_m=altitude,
    )


# ---------------------------------------------------------------------------
# DroneClassifier: reclassification from COCO classes
# ---------------------------------------------------------------------------


class TestDroneClassifierReclassification:
    """Test reclassification of COCO detections into drone-specific classes."""

    def test_bird_reclassified_to_bird_class(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="bird", confidence=0.7, class_id=14)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.BIRD
        assert results[0].reclassified is True

    def test_airplane_reclassified_to_fixed_wing(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="airplane", confidence=0.8, class_id=4)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.FIXED_WING
        assert results[0].reclassified is True

    def test_kite_reclassified_to_uas(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="kite", confidence=0.5, class_id=33)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.UAS
        assert results[0].reclassified is True

    def test_irrelevant_class_filtered_out(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="person", confidence=0.9, class_id=0)
        results = classifier.classify([det])
        assert len(results) == 0

    def test_car_filtered_out(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="car", confidence=0.95, class_id=2)
        results = classifier.classify([det])
        assert len(results) == 0

    def test_drone_model_class_direct_mapping(self) -> None:
        classifier = DroneClassifier(
            size_filter_enabled=False,
            drone_model_classes=DRONE_DATASET_CLASS_MAP,
        )
        det = _det(class_name="drone", confidence=0.85, class_id=0)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.UAS
        assert results[0].reclassified is False

    def test_quadrotor_class_direct_mapping(self) -> None:
        classifier = DroneClassifier(
            size_filter_enabled=False,
            drone_model_classes=DRONE_DATASET_CLASS_MAP,
        )
        det = _det(class_name="quadrotor", confidence=0.9, class_id=1)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.QUADROTOR

    def test_helicopter_class_direct_mapping(self) -> None:
        classifier = DroneClassifier(
            size_filter_enabled=False,
            drone_model_classes=DRONE_DATASET_CLASS_MAP,
        )
        det = _det(class_name="helicopter", confidence=0.75, class_id=3)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.HELICOPTER

    def test_low_confidence_filtered(self) -> None:
        classifier = DroneClassifier(
            size_filter_enabled=False,
            drone_model_classes={},
            min_confidence=0.5,
        )
        det = _det(class_name="bird", confidence=0.2, class_id=14)
        results = classifier.classify([det])
        assert len(results) == 0

    def test_multiple_detections_classified(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        dets = [
            _det(class_name="bird", confidence=0.7, class_id=14),
            _det(class_name="airplane", confidence=0.6, class_id=4),
            _det(class_name="person", confidence=0.9, class_id=0),
        ]
        results = classifier.classify(dets)
        assert len(results) == 2
        classes = {r.drone_class for r in results}
        assert DroneClass.BIRD in classes
        assert DroneClass.FIXED_WING in classes

    def test_original_class_preserved(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        det = _det(class_name="bird", confidence=0.7, class_id=14)
        results = classifier.classify([det])
        assert results[0].original_class == "bird"
        assert results[0].original_confidence == 0.7


# ---------------------------------------------------------------------------
# Confidence boosting for bird/airplane at altitude
# ---------------------------------------------------------------------------


class TestConfidenceBoosting:
    """Test confidence boost logic for ambiguous aerial detections."""

    def test_bird_gets_confidence_boost(self) -> None:
        classifier = DroneClassifier(
            camera=_camera(),
            altitude_boost=0.15,
            size_filter_enabled=False,
            drone_model_classes={},
        )
        det = _det(class_name="bird", confidence=0.5, class_id=14)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].confidence > 0.5
        assert results[0].boost_applied > 0

    def test_airplane_gets_confidence_boost(self) -> None:
        classifier = DroneClassifier(
            camera=_camera(),
            altitude_boost=0.15,
            size_filter_enabled=False,
            drone_model_classes={},
        )
        det = _det(class_name="airplane", confidence=0.6, class_id=4)
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].confidence > 0.6
        assert results[0].boost_applied > 0

    def test_boost_higher_at_altitude(self) -> None:
        low_cam = _camera(altitude=0.0)
        high_cam = _camera(altitude=50.0)

        cls_low = DroneClassifier(
            camera=low_cam, altitude_boost=0.15,
            size_filter_enabled=False, drone_model_classes={},
        )
        cls_high = DroneClassifier(
            camera=high_cam, altitude_boost=0.15,
            size_filter_enabled=False, drone_model_classes={},
        )

        det = _det(class_name="bird", confidence=0.5, class_id=14)
        r_low = cls_low.classify([det])[0]
        r_high = cls_high.classify([det])[0]

        assert r_high.boost_applied >= r_low.boost_applied

    def test_boost_capped_at_035(self) -> None:
        classifier = DroneClassifier(
            camera=_camera(altitude=500.0),
            altitude_boost=0.50,  # very high base boost
            size_filter_enabled=False,
            drone_model_classes={},
        )
        det = _det(
            class_name="bird", confidence=0.5, class_id=14,
            bbox=(10, 10, 20, 20),  # tiny bbox, upper frame
        )
        results = classifier.classify([det])
        assert results[0].boost_applied <= 0.35

    def test_confidence_never_exceeds_one(self) -> None:
        classifier = DroneClassifier(
            camera=_camera(altitude=100.0),
            altitude_boost=0.30,
            size_filter_enabled=False,
            drone_model_classes={},
        )
        det = _det(class_name="bird", confidence=0.95, class_id=14)
        results = classifier.classify([det])
        assert results[0].confidence <= 1.0

    def test_no_boost_for_drone_model_classes(self) -> None:
        classifier = DroneClassifier(
            camera=_camera(),
            altitude_boost=0.15,
            size_filter_enabled=False,
            drone_model_classes=DRONE_DATASET_CLASS_MAP,
        )
        det = _det(class_name="drone", confidence=0.8, class_id=0)
        results = classifier.classify([det])
        assert results[0].boost_applied == 0
        assert results[0].confidence == 0.8


# ---------------------------------------------------------------------------
# Size-based filtering
# ---------------------------------------------------------------------------


class TestSizeFiltering:
    """Test camera FOV size estimation and drone-size filtering."""

    def test_estimate_size_basic_geometry(self) -> None:
        cam = _camera(fov_h=60.0, width=1920, slant_range=100.0)
        # At 100m range with 60deg FOV: frame width ~ 115.47m
        # 50px object out of 1920px ~ 3.0m
        bbox = (0, 0, 50, 50)
        size = estimate_object_size_m(bbox, cam)
        assert 2.0 < size < 4.0

    def test_estimate_size_zero_range_returns_zero(self) -> None:
        cam = _camera(slant_range=0.0)
        bbox = (100, 100, 150, 150)
        size = estimate_object_size_m(bbox, cam)
        assert size == 0.0

    def test_estimate_size_zero_bbox_returns_zero(self) -> None:
        cam = _camera(slant_range=100.0)
        bbox = (100, 100, 100, 100)
        size = estimate_object_size_m(bbox, cam)
        assert size == 0.0

    def test_is_drone_sized_in_range(self) -> None:
        assert is_drone_sized(0.5) is True
        assert is_drone_sized(1.5) is True
        assert is_drone_sized(3.0) is True

    def test_is_drone_sized_out_of_range(self) -> None:
        assert is_drone_sized(0.1) is False
        assert is_drone_sized(5.0) is False
        assert is_drone_sized(10.0) is False

    def test_is_drone_sized_at_boundaries(self) -> None:
        assert is_drone_sized(DRONE_SIZE_MIN_M) is True
        assert is_drone_sized(DRONE_SIZE_MAX_M) is True

    def test_size_filter_removes_oversized(self) -> None:
        # Large bbox at close range = large object = not a drone
        cam = _camera(fov_h=62.2, width=1920, slant_range=50.0)
        classifier = DroneClassifier(
            camera=cam,
            size_filter_enabled=True,
            drone_model_classes={},
        )
        # 500px bbox at 50m range = very large object
        det = _det(class_name="bird", confidence=0.8, class_id=14, bbox=(0, 0, 500, 500))
        results = classifier.classify([det])
        if results:
            # Should be reclassified to UNKNOWN_AIR with penalized confidence
            assert results[0].drone_class == DroneClass.UNKNOWN_AIR or results[0].confidence < 0.8

    def test_size_filter_passes_drone_sized(self) -> None:
        # Small bbox at medium range = plausible drone
        cam = _camera(fov_h=62.2, width=1920, slant_range=200.0)
        classifier = DroneClassifier(
            camera=cam,
            size_filter_enabled=True,
            drone_model_classes={},
        )
        # ~10px bbox at 200m: ~0.63m, within drone range
        det = _det(class_name="bird", confidence=0.7, class_id=14, bbox=(500, 500, 510, 510))
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.BIRD

    def test_size_filter_disabled_passes_all(self) -> None:
        cam = _camera(slant_range=50.0)
        classifier = DroneClassifier(
            camera=cam,
            size_filter_enabled=False,
            drone_model_classes={},
        )
        det = _det(class_name="bird", confidence=0.8, class_id=14, bbox=(0, 0, 500, 500))
        results = classifier.classify([det])
        assert len(results) == 1
        # Confidence should not be penalized
        assert results[0].confidence > 0.7

    def test_filter_by_size_method(self) -> None:
        classifier = DroneClassifier(size_filter_enabled=False, drone_model_classes={})
        drones = [
            DroneDetection(
                drone_class=DroneClass.UAS,
                confidence=0.8,
                bbox=(0, 0, 50, 50),
                original_class="drone",
                original_confidence=0.8,
                estimated_size_m=1.5,
            ),
            DroneDetection(
                drone_class=DroneClass.FIXED_WING,
                confidence=0.7,
                bbox=(0, 0, 50, 50),
                original_class="airplane",
                original_confidence=0.7,
                estimated_size_m=15.0,  # too big
            ),
            DroneDetection(
                drone_class=DroneClass.BIRD,
                confidence=0.6,
                bbox=(0, 0, 5, 5),
                original_class="bird",
                original_confidence=0.6,
                estimated_size_m=None,  # unknown size passes
            ),
        ]
        filtered = classifier.filter_by_size(drones)
        assert len(filtered) == 2
        assert filtered[0].estimated_size_m == 1.5
        assert filtered[1].estimated_size_m is None


# ---------------------------------------------------------------------------
# Camera geometry: estimate_object_size_m
# ---------------------------------------------------------------------------


class TestCameraGeometry:
    """Test the camera FOV size estimation math."""

    def test_known_geometry(self) -> None:
        # 90 deg FOV at 100m range: frame width = 200m
        # 1920px wide -> 200/1920 = 0.1042 m/px
        # 100px object -> 10.42m
        cam = _camera(fov_h=90.0, width=1920, slant_range=100.0)
        bbox = (0, 0, 100, 50)
        size = estimate_object_size_m(bbox, cam)
        expected = (200.0 / 1920.0) * 100.0
        assert abs(size - expected) < 0.01

    def test_narrow_fov_smaller_estimate(self) -> None:
        cam_wide = _camera(fov_h=90.0, width=1920, slant_range=100.0)
        cam_narrow = _camera(fov_h=30.0, width=1920, slant_range=100.0)
        bbox = (0, 0, 50, 50)
        size_wide = estimate_object_size_m(bbox, cam_wide)
        size_narrow = estimate_object_size_m(bbox, cam_narrow)
        assert size_narrow < size_wide

    def test_closer_range_larger_pixel(self) -> None:
        cam_close = _camera(fov_h=62.2, width=1920, slant_range=50.0)
        cam_far = _camera(fov_h=62.2, width=1920, slant_range=200.0)
        bbox = (0, 0, 30, 30)
        size_close = estimate_object_size_m(bbox, cam_close)
        size_far = estimate_object_size_m(bbox, cam_far)
        # Same pixel footprint at closer range = smaller real object
        assert size_close < size_far

    def test_uses_larger_bbox_dimension(self) -> None:
        cam = _camera(fov_h=62.2, width=1920, slant_range=100.0)
        # Width is 80px, height is 20px -> uses 80px
        bbox_wide = (0, 0, 80, 20)
        # Width is 20px, height is 80px -> also uses 80px
        bbox_tall = (0, 0, 20, 80)
        assert estimate_object_size_m(bbox_wide, cam) == estimate_object_size_m(bbox_tall, cam)


# ---------------------------------------------------------------------------
# DroneClass enum
# ---------------------------------------------------------------------------


class TestDroneClassEnum:
    """Test DroneClass enum coverage."""

    def test_all_classes_exist(self) -> None:
        expected = {"UAS", "QUADROTOR", "FIXED_WING", "HELICOPTER", "BIRD", "UNKNOWN_AIR"}
        actual = {c.name for c in DroneClass}
        assert actual == expected

    def test_values_are_lowercase(self) -> None:
        for c in DroneClass:
            assert c.value == c.value.lower()


# ---------------------------------------------------------------------------
# Integration: classifier with size filter and boost together
# ---------------------------------------------------------------------------


class TestClassifierIntegration:
    """Test the full classification pipeline with all features active."""

    def test_full_pipeline_bird_at_drone_size(self) -> None:
        cam = _camera(fov_h=62.2, width=1920, slant_range=200.0)
        classifier = DroneClassifier(
            camera=cam,
            altitude_boost=0.15,
            size_filter_enabled=True,
            drone_model_classes={},
        )
        # ~15px bbox at 200m ~ 0.95m, within drone range
        det = _det(class_name="bird", confidence=0.5, class_id=14, bbox=(500, 400, 515, 415))
        results = classifier.classify([det])
        assert len(results) == 1
        assert results[0].drone_class == DroneClass.BIRD
        assert results[0].confidence > 0.5
        assert results[0].estimated_size_m is not None

    def test_full_pipeline_mixed_detections(self) -> None:
        cam = _camera(fov_h=62.2, width=1920, slant_range=150.0)
        classifier = DroneClassifier(
            camera=cam,
            altitude_boost=0.15,
            size_filter_enabled=True,
            drone_model_classes={},
        )
        dets = [
            _det(class_name="bird", confidence=0.7, class_id=14, bbox=(100, 100, 120, 120)),
            _det(class_name="person", confidence=0.9, class_id=0, bbox=(200, 200, 280, 400)),
            _det(class_name="airplane", confidence=0.6, class_id=4, bbox=(500, 500, 510, 510)),
        ]
        results = classifier.classify(dets)
        # person should be filtered out
        class_names = {r.original_class for r in results}
        assert "person" not in class_names
        # bird and airplane should be present
        assert "bird" in class_names or "airplane" in class_names
