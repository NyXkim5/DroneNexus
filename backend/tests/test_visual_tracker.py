"""
Tests for the ByteTrack-style multi-object visual tracker.
"""
from __future__ import annotations

from typing import List

import pytest

from vision.models import BoundingBox, TargetType, VisualTarget
from vision.tracker import SimpleTracker, TrackedObject


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_POS = (0.0, 0.0, 0.0)


def make_detection(
    bbox: BoundingBox,
    confidence: float = 0.9,
    target_type: TargetType = TargetType.VEHICLE_CAR,
    det_id: str = "d-0",
) -> VisualTarget:
    return VisualTarget(
        id=det_id,
        target_type=target_type,
        position=_DEFAULT_POS,
        bounding_box=bbox,
        confidence=confidence,
        occupancy_estimate=1,
        base_value=30_000.0,
        blast_radius_m=10.0,
    )


def bbox_at(x: int, y: int, w: int = 60, h: int = 40) -> BoundingBox:
    return BoundingBox(x=x, y=y, width=w, height=h)


def run_frames(
    tracker: SimpleTracker,
    detections_per_frame: List[List[VisualTarget]],
) -> List[List[TrackedObject]]:
    return [tracker.update(frame) for frame in detections_per_frame]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_target_tracked() -> None:
    """One detection across 5 frames keeps the same track_id."""
    tracker = SimpleTracker(min_hits=1)
    track_ids: List[str] = []
    for i in range(5):
        det = make_detection(bbox_at(100 + i * 2, 100))
        confirmed = tracker.update([det])
        assert len(confirmed) == 1
        track_ids.append(confirmed[0].track_id)

    assert len(set(track_ids)) == 1, "track_id must be stable across all frames"


def test_two_targets_maintain_identity() -> None:
    """Two well-separated targets never swap IDs across 5 frames."""
    tracker = SimpleTracker(min_hits=1)

    # Start them far apart so IoU between them is always 0.
    ids_a: List[str] = []
    ids_b: List[str] = []

    for i in range(5):
        det_a = make_detection(bbox_at(50 + i, 50), det_id="a")
        det_b = make_detection(bbox_at(400 + i, 400), det_id="b")
        confirmed = tracker.update([det_a, det_b])
        assert len(confirmed) == 2

        # Sort by x-position to deterministically identify which is which.
        confirmed.sort(key=lambda t: t.bbox.x)
        ids_a.append(confirmed[0].track_id)
        ids_b.append(confirmed[1].track_id)

    assert len(set(ids_a)) == 1, "target A identity must be stable"
    assert len(set(ids_b)) == 1, "target B identity must be stable"
    assert ids_a[0] != ids_b[0], "targets must have different track_ids"


def test_new_track_from_high_confidence() -> None:
    """An unmatched high-confidence detection creates a new track."""
    tracker = SimpleTracker(min_hits=1, high_confidence=0.6)
    det = make_detection(bbox_at(100, 100), confidence=0.9)
    confirmed = tracker.update([det])
    assert len(confirmed) == 1
    assert confirmed[0].track_id.startswith("trk-")


def test_low_confidence_ignored_initially() -> None:
    """A low-confidence detection alone must not start a track."""
    tracker = SimpleTracker(
        min_hits=1,
        high_confidence=0.6,
        low_confidence=0.3,
    )
    det = make_detection(bbox_at(100, 100), confidence=0.4)
    confirmed = tracker.update([det])
    assert len(confirmed) == 0
    assert len(tracker.active_tracks) == 0


def test_track_deleted_after_max_misses() -> None:
    """A track disappears after max_misses consecutive frames with no match."""
    max_misses = 3
    tracker = SimpleTracker(min_hits=1, max_misses=max_misses)

    # Establish the track.
    tracker.update([make_detection(bbox_at(100, 100))])
    assert len(tracker.active_tracks) == 1

    # Feed empty frames to exhaust misses (max_misses + 1 triggers deletion).
    for _ in range(max_misses + 1):
        tracker.update([])

    assert len(tracker.active_tracks) == 0


def test_confirmed_after_min_hits() -> None:
    """A track only appears in confirmed_tracks after min_hits matches."""
    min_hits = 3
    tracker = SimpleTracker(min_hits=min_hits)

    det = make_detection(bbox_at(100, 100))

    for frame_num in range(1, min_hits + 2):
        tracker.update([det])
        if frame_num < min_hits:
            assert len(tracker.confirmed_tracks) == 0, (
                f"should not be confirmed at frame {frame_num}"
            )
        else:
            assert len(tracker.confirmed_tracks) == 1, (
                f"should be confirmed at frame {frame_num}"
            )


def test_velocity_prediction() -> None:
    """Predicted bbox center stays close to actual for a constant-velocity target."""
    tracker = SimpleTracker(min_hits=1)
    vx_px, vy_px = 5, 3  # pixels per frame

    # Feed two frames so the tracker has an estimated velocity.
    tracker.update([make_detection(bbox_at(100, 100))])
    tracker.update([make_detection(bbox_at(100 + vx_px, 100 + vy_px))])

    # After _predict() runs internally on the next update, the predicted bbox
    # center should be approximately one velocity step ahead.
    # We check by running update with an empty frame and inspecting active track bbox.
    tracker._predict()  # noqa: SLF001 — direct call to verify prediction math
    track = tracker.active_tracks[0]
    cx = track.bbox.x + track.bbox.width / 2.0
    cy = track.bbox.y + track.bbox.height / 2.0

    # After two updates (v estimated) plus one predict, center should be at ~115, 109.
    expected_cx = 100 + vx_px * 2 + track.bbox.width / 2.0
    expected_cy = 100 + vy_px * 2 + track.bbox.height / 2.0
    assert abs(cx - expected_cx) <= 2, f"cx {cx} far from expected {expected_cx}"
    assert abs(cy - expected_cy) <= 2, f"cy {cy} far from expected {expected_cy}"


def test_occlusion_recovery() -> None:
    """
    Target disappears for several frames then reappears nearby.
    The same track_id is recovered (misses < max_misses).
    """
    max_misses = 10
    tracker = SimpleTracker(min_hits=1, max_misses=max_misses, iou_threshold=0.1)

    # Establish track for 3 frames.
    for _ in range(3):
        tracker.update([make_detection(bbox_at(200, 200))])

    assert len(tracker.active_tracks) == 1
    original_id = tracker.active_tracks[0].track_id

    # Occlude for 5 frames (below max_misses=10).
    for _ in range(5):
        tracker.update([])

    assert len(tracker.active_tracks) == 1, "track must survive occlusion"
    assert tracker.active_tracks[0].track_id == original_id

    # Reappear nearby (slight drift, still high IoU).
    tracker.update([make_detection(bbox_at(205, 203))])

    assert len(tracker.active_tracks) == 1
    assert tracker.active_tracks[0].track_id == original_id, (
        "track_id must be the same after recovery"
    )
    assert tracker.active_tracks[0].misses == 0, "misses must reset on re-match"
