"""
Tests for ByteTrack multi-object tracker.

Covers Kalman filter, IoU computation, track lifecycle, two-stage
association, persistent IDs, crossing paths, and the TrackingDetector wrapper.
"""
from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import numpy as np
import pytest

from vision.bytetrack import (
    ByteTracker,
    KalmanFilter,
    STrack,
    TrackState,
    TrackedObject,
    TrackingDetector,
    iou_batch,
    linear_assignment_with_thresh,
    reset_track_id_counter,
)
from vision.tensorrt_detector import Detection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_ids():
    """Reset global track ID counter before each test."""
    reset_track_id_counter()
    yield
    reset_track_id_counter()


def _det(
    x1: float, y1: float, x2: float, y2: float,
    conf: float = 0.9, cls: str = "drone",
) -> Detection:
    """Shorthand for creating a Detection."""
    return Detection(
        class_name=cls,
        confidence=conf,
        bbox=(x1, y1, x2, y2),
    )


# ---------------------------------------------------------------------------
# Kalman filter tests
# ---------------------------------------------------------------------------

class TestKalmanFilter:
    def test_initiate_sets_state(self):
        kf = KalmanFilter()
        measurement = np.array([100.0, 200.0, 50.0, 80.0])
        mean, cov = kf.initiate(measurement)

        assert mean.shape == (8,)
        assert cov.shape == (8, 8)
        assert mean[0] == pytest.approx(100.0)
        assert mean[1] == pytest.approx(200.0)
        assert mean[2] == pytest.approx(50.0)
        assert mean[3] == pytest.approx(80.0)
        # Velocities start at zero
        assert mean[4] == pytest.approx(0.0)
        assert mean[5] == pytest.approx(0.0)

    def test_predict_advances_state(self):
        kf = KalmanFilter()
        measurement = np.array([100.0, 200.0, 50.0, 80.0])
        mean, cov = kf.initiate(measurement)
        # Inject a known velocity
        mean[4] = 5.0  # vx
        mean[5] = -3.0  # vy

        predicted_mean, predicted_cov = kf.predict(mean, cov)

        # Position should advance by velocity * dt (dt=1)
        assert predicted_mean[0] == pytest.approx(105.0)
        assert predicted_mean[1] == pytest.approx(197.0)
        # Covariance should grow (more uncertainty)
        assert np.all(np.diag(predicted_cov) >= np.diag(cov) - 1e-12)

    def test_update_corrects_toward_measurement(self):
        kf = KalmanFilter()
        measurement = np.array([100.0, 200.0, 50.0, 80.0])
        mean, cov = kf.initiate(measurement)
        mean, cov = kf.predict(mean, cov)

        new_measurement = np.array([110.0, 210.0, 50.0, 80.0])
        updated_mean, updated_cov = kf.update(mean, cov, new_measurement)

        # Updated position should be closer to new_measurement than predicted
        assert abs(updated_mean[0] - 110.0) < abs(mean[0] - 110.0)
        assert abs(updated_mean[1] - 210.0) < abs(mean[1] - 210.0)

    def test_predict_update_cycle_converges(self):
        kf = KalmanFilter()
        measurement = np.array([50.0, 50.0, 30.0, 30.0])
        mean, cov = kf.initiate(measurement)

        # Feed consistent measurements moving right
        for i in range(10):
            mean, cov = kf.predict(mean, cov)
            m = np.array([50.0 + (i + 1) * 5.0, 50.0, 30.0, 30.0])
            mean, cov = kf.update(mean, cov, m)

        # Velocity should approximate 5 px/frame
        assert mean[4] == pytest.approx(5.0, abs=1.0)


# ---------------------------------------------------------------------------
# IoU computation tests
# ---------------------------------------------------------------------------

class TestIoU:
    def test_identical_boxes(self):
        boxes = np.array([[10, 20, 50, 60]], dtype=np.float64)
        result = iou_batch(boxes, boxes)
        assert result[0, 0] == pytest.approx(1.0)

    def test_non_overlapping_boxes(self):
        a = np.array([[0, 0, 10, 10]], dtype=np.float64)
        b = np.array([[20, 20, 30, 30]], dtype=np.float64)
        result = iou_batch(a, b)
        assert result[0, 0] == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = np.array([[0, 0, 10, 10]], dtype=np.float64)
        b = np.array([[5, 5, 15, 15]], dtype=np.float64)
        result = iou_batch(a, b)
        # Intersection: 5x5=25, Union: 100+100-25=175
        assert result[0, 0] == pytest.approx(25.0 / 175.0, abs=1e-6)

    def test_batch_computation(self):
        a = np.array([
            [0, 0, 10, 10],
            [50, 50, 60, 60],
        ], dtype=np.float64)
        b = np.array([
            [0, 0, 10, 10],
            [100, 100, 110, 110],
        ], dtype=np.float64)
        result = iou_batch(a, b)
        assert result.shape == (2, 2)
        assert result[0, 0] == pytest.approx(1.0)
        assert result[0, 1] == pytest.approx(0.0)
        assert result[1, 0] == pytest.approx(0.0)
        assert result[1, 1] == pytest.approx(0.0)

    def test_empty_input(self):
        a = np.zeros((0, 4), dtype=np.float64)
        b = np.array([[0, 0, 10, 10]], dtype=np.float64)
        result = iou_batch(a, b)
        assert result.shape == (0, 1)

    def test_contained_box(self):
        outer = np.array([[0, 0, 100, 100]], dtype=np.float64)
        inner = np.array([[25, 25, 75, 75]], dtype=np.float64)
        result = iou_batch(outer, inner)
        # Inner area: 50*50=2500, Outer area: 100*100=10000
        # Intersection=2500, Union=10000
        assert result[0, 0] == pytest.approx(2500.0 / 10000.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Linear assignment tests
# ---------------------------------------------------------------------------

class TestLinearAssignment:
    def test_simple_assignment(self):
        cost = np.array([[0.1, 0.9], [0.9, 0.2]], dtype=np.float64)
        matches, ua, ub = linear_assignment_with_thresh(cost, 0.5)
        assert len(matches) == 2
        assert (0, 0) in matches
        assert (1, 1) in matches
        assert ua == []
        assert ub == []

    def test_threshold_filters(self):
        cost = np.array([[0.8, 0.9], [0.9, 0.85]], dtype=np.float64)
        matches, ua, ub = linear_assignment_with_thresh(cost, 0.5)
        assert len(matches) == 0
        assert len(ua) == 2
        assert len(ub) == 2

    def test_empty_matrix(self):
        cost = np.zeros((0, 3), dtype=np.float64)
        matches, ua, ub = linear_assignment_with_thresh(cost, 0.5)
        assert matches == []
        assert ua == []
        assert ub == [0, 1, 2]


# ---------------------------------------------------------------------------
# Track lifecycle tests
# ---------------------------------------------------------------------------

class TestTrackLifecycle:
    def test_new_to_tracked(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3, confirm_frames=3,
        )
        det = _det(100, 100, 150, 150, conf=0.9)

        result = tracker.update([det])
        assert len(result) == 1
        assert result[0].state == "NEW"

        # Feed same detection for 2 more frames to confirm
        tracker.update([det])
        result = tracker.update([det])
        confirmed = [r for r in result if r.state == "TRACKED"]
        assert len(confirmed) == 1
        assert confirmed[0].hits == 3

    def test_tracked_to_lost(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3,
            confirm_frames=1, track_buffer=5,
        )
        det = _det(100, 100, 150, 150, conf=0.9)
        tracker.update([det])

        # No detections for one frame
        result = tracker.update([])
        lost = [r for r in result if r.state == "LOST"]
        assert len(lost) == 1

    def test_lost_to_removed(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3,
            confirm_frames=1, track_buffer=3,
        )
        det = _det(100, 100, 150, 150, conf=0.9)
        tracker.update([det])

        # No detections for track_buffer + 1 frames
        for _ in range(5):
            result = tracker.update([])

        # Track should be removed (not in output)
        assert len(result) == 0

    def test_lost_track_recovered(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3,
            confirm_frames=1, track_buffer=10,
        )
        det = _det(100, 100, 150, 150, conf=0.9)
        result1 = tracker.update([det])
        track_id = result1[0].track_id

        # Miss for 2 frames
        tracker.update([])
        tracker.update([])

        # Re-detect at slightly shifted position
        det2 = _det(103, 103, 153, 153, conf=0.9)
        result2 = tracker.update([det2])

        # Should recover the same track ID
        recovered_ids = [r.track_id for r in result2]
        assert track_id in recovered_ids


# ---------------------------------------------------------------------------
# Two-stage association tests
# ---------------------------------------------------------------------------

class TestTwoStageAssociation:
    def test_high_conf_matched_first(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3, confirm_frames=1,
        )
        # Establish a track
        det1 = _det(100, 100, 150, 150, conf=0.9)
        result1 = tracker.update([det1])
        track_id = result1[0].track_id

        # Feed both a high-conf and low-conf detection at same location
        high = _det(102, 102, 152, 152, conf=0.8)
        low = _det(102, 102, 152, 152, conf=0.3)
        result2 = tracker.update([high, low])

        # The existing track should be matched to the high-conf detection
        matched = [r for r in result2 if r.track_id == track_id]
        assert len(matched) == 1
        assert matched[0].confidence == pytest.approx(0.8)

    def test_low_conf_fills_remaining(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3, confirm_frames=1,
        )
        # Establish two tracks
        dets = [
            _det(100, 100, 150, 150, conf=0.9),
            _det(300, 300, 350, 350, conf=0.9),
        ]
        result1 = tracker.update(dets)
        ids = {r.track_id for r in result1}
        assert len(ids) == 2

        # One high-conf at first location, one low-conf at second
        high = _det(102, 102, 152, 152, conf=0.8)
        low = _det(302, 302, 352, 352, conf=0.3)
        result2 = tracker.update([high, low])

        # Both tracks should survive (low-conf matches remaining track)
        survived_ids = {r.track_id for r in result2 if r.state != "LOST"}
        assert len(survived_ids) >= 2


# ---------------------------------------------------------------------------
# Persistent ID tests
# ---------------------------------------------------------------------------

class TestPersistentIDs:
    def test_stable_id_across_10_frames(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3, confirm_frames=1,
        )
        track_ids_seen: set = set()

        for i in range(10):
            x = 100.0 + i * 5.0
            det = _det(x, 100, x + 50, 150, conf=0.9)
            result = tracker.update([det])
            for r in result:
                track_ids_seen.add(r.track_id)

        # Only one unique track ID should exist
        assert len(track_ids_seen) == 1

    def test_two_objects_stable_ids(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.3, confirm_frames=1,
        )

        for i in range(10):
            dets = [
                _det(50 + i * 3, 50, 100 + i * 3, 100, conf=0.9, cls="drone"),
                _det(300 + i * 3, 300, 350 + i * 3, 350, conf=0.9, cls="bird"),
            ]
            result = tracker.update(dets)

        # Should have exactly 2 tracked objects
        tracked = [r for r in result if r.state in ("TRACKED", "NEW")]
        assert len(tracked) == 2
        ids = {r.track_id for r in tracked}
        assert len(ids) == 2

    def test_crossing_paths_maintain_ids(self):
        tracker = ByteTracker(
            track_thresh=0.5, match_thresh=0.2, confirm_frames=1,
        )

        # Object A starts at (50, 200), moves right
        # Object B starts at (400, 200), moves left
        # They cross around x=225 at frame ~10

        first_result = tracker.update([
            _det(50, 180, 100, 230, conf=0.9, cls="drone_a"),
            _det(400, 180, 450, 230, conf=0.9, cls="drone_b"),
        ])
        id_a = first_result[0].track_id
        id_b = first_result[1].track_id

        for i in range(1, 20):
            ax = 50 + i * 20  # moves right at 20px/frame
            bx = 400 - i * 20  # moves left at 20px/frame
            dets = [
                _det(ax, 180, ax + 50, 230, conf=0.9, cls="drone_a"),
                _det(bx, 180, bx + 50, 230, conf=0.9, cls="drone_b"),
            ]
            result = tracker.update(dets)

        # After crossing, IDs should still be distinct
        final_ids = {r.track_id for r in result}
        assert len(final_ids) == 2


# ---------------------------------------------------------------------------
# STrack unit tests
# ---------------------------------------------------------------------------

class TestSTrack:
    def test_activate_assigns_id(self):
        det = _det(10, 20, 60, 80, conf=0.7)
        track = STrack(det)
        track.activate()
        assert track.track_id > 0
        assert track.state == TrackState.NEW

    def test_bbox_round_trip(self):
        det = _det(100.0, 200.0, 300.0, 400.0, conf=0.9)
        track = STrack(det)
        bbox = track.bbox
        assert bbox[0] == pytest.approx(100.0, abs=1.0)
        assert bbox[1] == pytest.approx(200.0, abs=1.0)
        assert bbox[2] == pytest.approx(300.0, abs=1.0)
        assert bbox[3] == pytest.approx(400.0, abs=1.0)

    def test_velocity_initially_zero(self):
        det = _det(10, 20, 60, 80)
        track = STrack(det)
        assert track.velocity == pytest.approx((0.0, 0.0))

    def test_to_tracked_object_fields(self):
        det = _det(10, 20, 60, 80, conf=0.85, cls="bird")
        track = STrack(det)
        track.activate()
        obj = track.to_tracked_object()
        assert isinstance(obj, TrackedObject)
        assert obj.class_name == "bird"
        assert obj.confidence == pytest.approx(0.85)
        assert obj.state == "NEW"
        assert obj.hits == 1
        assert obj.age == 1


# ---------------------------------------------------------------------------
# TrackingDetector wrapper test
# ---------------------------------------------------------------------------

class TestTrackingDetector:
    def test_detect_and_track(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            _det(100, 100, 200, 200, conf=0.9, cls="drone"),
        ]

        td = TrackingDetector(
            detector=mock_detector,
            tracker=ByteTracker(track_thresh=0.5, confirm_frames=1),
        )

        result = td.detect_and_track(np.zeros((480, 640, 3), dtype=np.uint8))

        mock_detector.detect.assert_called_once()
        assert len(result) == 1
        assert result[0].class_name == "drone"
        assert result[0].track_id > 0

    def test_detect_and_track_multiple_frames(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        mock_detector = MagicMock()

        # Simulate a drone moving across 5 frames
        detections_per_frame = [
            [_det(100 + i * 10, 100, 200 + i * 10, 200, conf=0.9)]
            for i in range(5)
        ]
        mock_detector.detect.side_effect = detections_per_frame

        td = TrackingDetector(
            detector=mock_detector,
            tracker=ByteTracker(track_thresh=0.5, confirm_frames=1),
        )

        all_ids: set = set()
        for _ in range(5):
            result = td.detect_and_track(frame)
            for r in result:
                all_ids.add(r.track_id)

        # Same drone across 5 frames should keep one ID
        assert len(all_ids) == 1

    def test_default_tracker_created(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = []
        td = TrackingDetector(detector=mock_detector)
        assert td.tracker is not None
        result = td.detect_and_track(np.zeros((100, 100, 3), dtype=np.uint8))
        assert result == []


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_detections(self):
        tracker = ByteTracker()
        result = tracker.update([])
        assert result == []

    def test_single_detection_single_frame(self):
        tracker = ByteTracker(track_thresh=0.5)
        det = _det(0, 0, 10, 10, conf=0.9)
        result = tracker.update([det])
        assert len(result) == 1
        assert result[0].track_id > 0

    def test_many_objects(self):
        tracker = ByteTracker(track_thresh=0.5, confirm_frames=1)
        dets = [
            _det(i * 100, 0, i * 100 + 50, 50, conf=0.9, cls=f"obj_{i}")
            for i in range(20)
        ]
        result = tracker.update(dets)
        assert len(result) == 20
        ids = {r.track_id for r in result}
        assert len(ids) == 20

    def test_frame_count_increments(self):
        tracker = ByteTracker()
        assert tracker.frame_count == 0
        tracker.update([])
        assert tracker.frame_count == 1
        tracker.update([_det(0, 0, 10, 10, conf=0.9)])
        assert tracker.frame_count == 2

    def test_all_below_threshold_no_new_tracks(self):
        tracker = ByteTracker(track_thresh=0.5)
        low_dets = [
            _det(100, 100, 200, 200, conf=0.2),
            _det(300, 300, 400, 400, conf=0.1),
        ]
        result = tracker.update(low_dets)
        # Low-conf detections cannot start new tracks
        assert len(result) == 0
