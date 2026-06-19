"""Tests for the visual-to-RF track association module."""
from __future__ import annotations

import math
import time

import pytest

from fusion.rf_visual_linker import (
    LinkedTrack,
    LinkHistory,
    RFTrackInfo,
    RFVisualLinker,
    VisualTrackInfo,
    _angular_diff,
    _bearing_from_origin,
    _bearing_score,
    _fuse_confidence,
    _range_score,
    _temporal_score,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _rf(
    track_id: str = "rf-1",
    serial: str = "DJI123ABC",
    pos: tuple = (100.0, 100.0, 50.0),
    vel: tuple = (0.0, 0.0, 0.0),
    source: str = "DJI_DRONEID",
    last_seen: float = 0.0,
    operator_pos: tuple | None = None,
) -> RFTrackInfo:
    return RFTrackInfo(
        track_id=track_id,
        serial=serial,
        position_enu=pos,
        velocity=vel,
        source=source,
        last_seen=last_seen,
        operator_position=operator_pos,
    )


def _vis(
    detection_id: str = "v-1",
    bbox: tuple = (380, 280, 40, 40),
    class_name: str = "drone",
    confidence: float = 0.85,
    bearing_deg: float = 0.0,
    range_m: float | None = None,
    track_id: str | None = None,
) -> VisualTrackInfo:
    return VisualTrackInfo(
        detection_id=detection_id,
        bbox=bbox,
        class_name=class_name,
        confidence=confidence,
        estimated_bearing_deg=bearing_deg,
        estimated_range_m=range_m,
        track_id=track_id,
    )


# ---------------------------------------------------------------------------
# Geometry helper tests
# ---------------------------------------------------------------------------

class TestAngularDiff:
    def test_same_angle(self) -> None:
        assert _angular_diff(45.0, 45.0) == pytest.approx(0.0)

    def test_opposite(self) -> None:
        assert _angular_diff(0.0, 180.0) == pytest.approx(180.0)

    def test_wraparound(self) -> None:
        assert _angular_diff(350.0, 10.0) == pytest.approx(20.0)

    def test_negative_input(self) -> None:
        assert _angular_diff(-10.0, 10.0) == pytest.approx(20.0)


class TestBearingFromOrigin:
    def test_due_north(self) -> None:
        bearing = _bearing_from_origin((0.0, 100.0, 0.0))
        assert bearing == pytest.approx(0.0, abs=0.1)

    def test_due_east(self) -> None:
        bearing = _bearing_from_origin((100.0, 0.0, 0.0))
        assert bearing == pytest.approx(90.0, abs=0.1)

    def test_northeast(self) -> None:
        bearing = _bearing_from_origin((100.0, 100.0, 0.0))
        assert bearing == pytest.approx(45.0, abs=0.1)


# ---------------------------------------------------------------------------
# Scoring function tests
# ---------------------------------------------------------------------------

class TestBearingScore:
    def test_exact_match(self) -> None:
        score = _bearing_score(45.0, 45.0, fov_deg=60.0)
        assert score == pytest.approx(1.0)

    def test_slight_offset(self) -> None:
        score = _bearing_score(45.0, 50.0, fov_deg=60.0)
        assert 0.5 < score < 1.0

    def test_outside_fov_gate(self) -> None:
        score = _bearing_score(45.0, 180.0, fov_deg=60.0)
        assert score == 0.0

    def test_edge_of_fov(self) -> None:
        score = _bearing_score(45.0, 75.0, fov_deg=60.0)
        # diff = 30 = fov/2; Gaussian is tiny but nonzero at the gate edge
        assert score < 0.05


class TestRangeScore:
    def test_exact_match(self) -> None:
        assert _range_score(100.0, 100.0) == pytest.approx(1.0)

    def test_none_range(self) -> None:
        assert _range_score(None, 200.0) == pytest.approx(1.0)

    def test_within_gate(self) -> None:
        score = _range_score(100.0, 125.0)
        assert 0.0 < score < 1.0

    def test_beyond_gate(self) -> None:
        score = _range_score(100.0, 200.0)
        assert score == 0.0


class TestTemporalScore:
    def test_simultaneous(self) -> None:
        assert _temporal_score(10.0, 10.0) == pytest.approx(1.0)

    def test_lag(self) -> None:
        score = _temporal_score(10.0, 9.0)
        assert 0.0 < score < 1.0

    def test_too_far(self) -> None:
        assert _temporal_score(10.0, 5.0) == 0.0


class TestFuseConfidence:
    def test_both_high(self) -> None:
        result = _fuse_confidence(0.9, 0.9)
        assert result == pytest.approx(0.99)

    def test_one_zero(self) -> None:
        result = _fuse_confidence(0.0, 0.8)
        assert result == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Linker tests
# ---------------------------------------------------------------------------

class TestRFVisualLinker:
    """Integration tests for the linker."""

    def test_bearing_match_high_score(self) -> None:
        """RF at 45 deg, camera det at 45 deg from center = high score."""
        linker = RFVisualLinker()
        # RF track at ENU (100, 100, 50) => bearing ~45 deg from origin
        rf = _rf(pos=(100.0, 100.0, 50.0), last_seen=10.0)
        # Visual detection at 45 deg offset from camera bearing=0
        vis = _vis(bearing_deg=45.0)
        links = linker.link([vis], [rf], camera_bearing_deg=0.0,
                            camera_fov_deg=120.0, current_time=10.0)
        assert len(links) == 1
        assert links[0].link_confidence > 0.5
        assert links[0].link_method == "bearing"

    def test_bearing_mismatch_no_link(self) -> None:
        """RF at 45 deg, camera det at 180 deg = no link."""
        linker = RFVisualLinker()
        rf = _rf(pos=(100.0, 100.0, 50.0), last_seen=10.0)
        vis = _vis(bearing_deg=180.0)
        links = linker.link([vis], [rf], camera_bearing_deg=0.0,
                            camera_fov_deg=60.0, current_time=10.0)
        assert len(links) == 0

    def test_range_gate_rejection(self) -> None:
        """Visual range estimate differs by >50m from RF range."""
        linker = RFVisualLinker()
        # RF at (100, 0, 0) => range 100m, bearing 90
        rf = _rf(pos=(100.0, 0.0, 0.0), last_seen=10.0)
        # Visual bearing matches (90 deg) but range way off
        vis = _vis(bearing_deg=90.0, range_m=200.0)
        links = linker.link([vis], [rf], camera_bearing_deg=0.0,
                            camera_fov_deg=120.0, current_time=10.0)
        assert len(links) == 0

    def test_temporal_correlation(self) -> None:
        """Recent RF reading scores higher than stale one."""
        linker = RFVisualLinker()
        rf_fresh = _rf(track_id="rf-fresh", pos=(100.0, 100.0, 50.0),
                        last_seen=10.0)
        rf_stale = _rf(track_id="rf-stale", pos=(100.0, 100.0, 50.0),
                        last_seen=7.0)
        vis = _vis(bearing_deg=45.0)
        links_fresh = linker.link([vis], [rf_fresh],
                                   camera_bearing_deg=0.0,
                                   camera_fov_deg=120.0, current_time=10.0)
        links_stale = linker.link([vis], [rf_stale],
                                   camera_bearing_deg=0.0,
                                   camera_fov_deg=120.0, current_time=10.0)
        assert len(links_fresh) == 1
        # Stale may or may not link but should have lower confidence
        if links_stale:
            assert links_fresh[0].link_confidence >= links_stale[0].link_confidence

    def test_one_to_one_assignment(self) -> None:
        """Two visuals and two RFs produce exactly two links, no double."""
        linker = RFVisualLinker()
        rf1 = _rf(track_id="rf-1", pos=(100.0, 100.0, 0.0), last_seen=10.0)
        rf2 = _rf(track_id="rf-2", pos=(0.0, 100.0, 0.0), last_seen=10.0)
        vis1 = _vis(detection_id="v-1", bearing_deg=45.0)
        vis2 = _vis(detection_id="v-2", bearing_deg=0.0)
        links = linker.link(
            [vis1, vis2], [rf1, rf2],
            camera_bearing_deg=0.0,
            camera_fov_deg=120.0,
            current_time=10.0,
        )
        assert len(links) == 2
        rf_ids = {l.rf.track_id for l in links}
        vis_ids = {l.visual.detection_id for l in links}
        assert len(rf_ids) == 2, "Each RF used exactly once"
        assert len(vis_ids) == 2, "Each visual used exactly once"

    def test_more_visuals_than_rfs(self) -> None:
        """Three visuals, one RF: only one link returned."""
        linker = RFVisualLinker()
        rf = _rf(pos=(100.0, 100.0, 0.0), last_seen=10.0)
        visuals = [
            _vis(detection_id="v-1", bearing_deg=45.0),
            _vis(detection_id="v-2", bearing_deg=46.0),
            _vis(detection_id="v-3", bearing_deg=180.0),
        ]
        links = linker.link(visuals, [rf], camera_bearing_deg=0.0,
                            camera_fov_deg=120.0, current_time=10.0)
        assert len(links) == 1

    def test_empty_inputs(self) -> None:
        """No crash on empty visual or RF lists."""
        linker = RFVisualLinker()
        assert linker.link([], [], 0.0, 60.0) == []
        assert linker.link([_vis()], [], 0.0, 60.0) == []
        assert linker.link([], [_rf()], 0.0, 60.0) == []

    def test_camera_bearing_offset(self) -> None:
        """Camera pointing south (180 deg). Visual at 0 offset = 180 abs."""
        linker = RFVisualLinker()
        # RF due south at (0, -100, 0) => bearing 180
        rf = _rf(pos=(0.0, -100.0, 0.0), last_seen=10.0)
        vis = _vis(bearing_deg=0.0)  # center of camera FOV
        links = linker.link([vis], [rf], camera_bearing_deg=180.0,
                            camera_fov_deg=60.0, current_time=10.0)
        assert len(links) == 1
        assert links[0].link_confidence > 0.5

    def test_combined_confidence_higher(self) -> None:
        """Combined confidence should be >= max of individual confidences."""
        linker = RFVisualLinker()
        rf = _rf(pos=(100.0, 100.0, 50.0), last_seen=10.0)
        vis = _vis(bearing_deg=45.0, confidence=0.8)
        links = linker.link([vis], [rf], camera_bearing_deg=0.0,
                            camera_fov_deg=120.0, current_time=10.0)
        assert len(links) == 1
        link = links[0]
        assert link.combined_confidence >= max(
            link.visual.confidence, link.link_confidence,
        )


# ---------------------------------------------------------------------------
# LinkHistory tests
# ---------------------------------------------------------------------------

class TestLinkHistory:
    def _make_link(
        self,
        vis_id: str = "v-1",
        rf_id: str = "rf-1",
        confidence: float = 0.95,
    ) -> LinkedTrack:
        vis = _vis(detection_id=vis_id, confidence=0.85)
        rf = _rf(track_id=rf_id, last_seen=10.0)
        return LinkedTrack(
            visual=vis, rf=rf,
            link_confidence=confidence,
            link_method="bearing",
            combined_confidence=_fuse_confidence(0.85, confidence),
        )

    def test_link_persistence_promotes_to_confirmed(self) -> None:
        """A link held for 5+ frames becomes confirmed."""
        history = LinkHistory(confirm_frames=5, confirm_threshold=0.9)
        link = self._make_link(confidence=0.95)
        for _ in range(5):
            history.update([link])
        confirmed = history.get_confirmed_links()
        assert len(confirmed) == 1
        assert confirmed[0].link_confidence >= 0.9

    def test_insufficient_frames_not_confirmed(self) -> None:
        """A link held for only 3 frames is not yet confirmed."""
        history = LinkHistory(confirm_frames=5, confirm_threshold=0.9)
        link = self._make_link(confidence=0.95)
        for _ in range(3):
            history.update([link])
        confirmed = history.get_confirmed_links()
        assert len(confirmed) == 0

    def test_broken_link_decays_and_deletes(self) -> None:
        """A link that stops appearing decays and is eventually removed."""
        history = LinkHistory(confirm_frames=2, decay_frames=3,
                              confirm_threshold=0.5)
        link = self._make_link(confidence=0.95)
        # Build up
        for _ in range(5):
            history.update([link])
        assert len(history.get_confirmed_links()) == 1

        # Stop seeing the link
        for _ in range(4):
            history.update([])
        # After decay_frames + 1 misses, entry is deleted
        assert len(history.get_confirmed_links()) == 0
        assert len(history.get_all_entries()) == 0

    def test_decay_reduces_confidence(self) -> None:
        """Missing frames reduce running confidence."""
        history = LinkHistory(confirm_frames=2, decay_frames=5,
                              confirm_threshold=0.5)
        link = self._make_link(confidence=0.95)
        for _ in range(5):
            history.update([link])
        entries_before = history.get_all_entries()
        key = list(entries_before.keys())[0]
        conf_before = entries_before[key].running_confidence

        # One miss
        history.update([])
        entries_after = history.get_all_entries()
        conf_after = entries_after[key].running_confidence
        assert conf_after < conf_before

    def test_multiple_links_tracked_independently(self) -> None:
        """Two distinct visual-RF pairs track independently."""
        history = LinkHistory(confirm_frames=3, confirm_threshold=0.5)
        link_a = self._make_link(vis_id="v-a", rf_id="rf-a", confidence=0.9)
        link_b = self._make_link(vis_id="v-b", rf_id="rf-b", confidence=0.9)
        for _ in range(4):
            history.update([link_a, link_b])
        confirmed = history.get_confirmed_links()
        assert len(confirmed) == 2
        rf_ids = {c.rf.track_id for c in confirmed}
        assert rf_ids == {"rf-a", "rf-b"}

    def test_reappearing_link_resets_miss_count(self) -> None:
        """A link that reappears after a miss resets its miss counter."""
        history = LinkHistory(confirm_frames=2, decay_frames=3,
                              confirm_threshold=0.5)
        link = self._make_link(confidence=0.95)
        # Build up
        for _ in range(3):
            history.update([link])
        # Miss twice (below decay_frames=3)
        history.update([])
        history.update([])
        # Reappear
        history.update([link])
        entries = history.get_all_entries()
        key = list(entries.keys())[0]
        assert entries[key].miss_count == 0
