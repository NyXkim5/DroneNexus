import pytest
import numpy as np
from vision.models import VisualTarget, TargetType, BoundingBox, TARGET_DEFAULTS
from vision.detector import Detector, SimDetector, SimTargetPlacement


def _make_placement(
    id: str,
    target_type: TargetType,
    position: tuple,
) -> SimTargetPlacement:
    return SimTargetPlacement(
        id=id,
        target_type=target_type,
        position=position,
    )


class TestSimDetector:
    def test_returns_all_placed_targets(self):
        placements = [
            _make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0)),
            _make_placement("t2", TargetType.VEHICLE_TRUCK, (300, 400, 0)),
        ]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {"t1", "t2"}

    def test_uses_target_defaults(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_FUEL_TANKER, (0, 0, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        defaults = TARGET_DEFAULTS[TargetType.VEHICLE_FUEL_TANKER]
        assert results[0].base_value == defaults["base_value"]
        assert results[0].blast_radius_m == defaults["blast_radius_m"]

    def test_noise_perturbs_position(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=5.0, false_positive_rate=0.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        pos = results[0].position
        assert pos != (100.0, 200.0, 0.0)
        assert abs(pos[0] - 100.0) < 30
        assert abs(pos[1] - 200.0) < 30

    def test_false_positives(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=1.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) > 1
        false_ids = [r.id for r in results if r.id.startswith("fp-")]
        assert len(false_ids) > 0

    def test_zero_false_positive_rate(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) == 1

    def test_all_results_have_bounding_boxes(self):
        placements = [
            _make_placement("t1", TargetType.INFRA_BRIDGE, (0, 0, 0)),
            _make_placement("t2", TargetType.PERSONNEL_GROUP, (50, 50, 0)),
        ]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        for r in results:
            assert r.bounding_box.width > 0
            assert r.bounding_box.height > 0

    def test_confidence_below_one_with_noise(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=5.0, false_positive_rate=0.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert results[0].confidence < 1.0

    def test_detector_is_abstract(self):
        with pytest.raises(TypeError):
            Detector()
