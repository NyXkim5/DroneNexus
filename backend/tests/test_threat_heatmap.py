"""Tests for the geospatial threat heatmap."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Track, TrackClass, Vec3
from threat.heatmap import Hotspot, ThreatHeatmap


def _track(
    tid: str,
    position: Vec3,
    classification: TrackClass = TrackClass.HOSTILE,
) -> Track:
    return Track(
        id=tid,
        position=position,
        velocity=(0.0, 0.0, 0.0),
        covariance=(1.0, 1.0, 1.0),
        last_update=0.0,
        classification=classification,
    )


class TestHeatmapBasics:
    def test_empty_update(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([], 0.0)
        assert hm.get_hotspots() == []

    def test_single_hostile_creates_cell(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (100.0, 200.0, 50.0))], 1.0)
        spots = hm.get_hotspots(top_n=1)
        assert len(spots) == 1
        assert spots[0].total_transits == 1

    def test_friendly_tracks_ignored(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("f1", (100.0, 200.0, 50.0),
                           TrackClass.FRIENDLY)], 1.0)
        assert hm.get_hotspots() == []

    def test_out_of_bounds_ignored(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0, grid_radius_m=100.0)
        hm.update([_track("t1", (5000.0, 5000.0, 0.0))], 1.0)
        assert hm.get_hotspots() == []

    def test_transit_counted_once_per_track(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        t = _track("t1", (100.0, 200.0, 50.0))
        hm.update([t], 1.0)
        hm.update([t], 2.0)
        spots = hm.get_hotspots(top_n=1)
        assert spots[0].total_transits == 1

    def test_new_track_increments_transit(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (100.0, 200.0, 50.0))], 1.0)
        hm.update([_track("t2", (100.0, 200.0, 50.0))], 2.0)
        spots = hm.get_hotspots(top_n=1)
        assert spots[0].total_transits == 2


class TestDwellAndPeak:
    def test_dwell_accumulates(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        t = _track("t1", (25.0, 25.0, 0.0))
        hm.update([t], 0.0)
        hm.update([t], 5.0)
        hm.update([t], 10.0)
        d = hm.to_dict()
        cell = d["cells"][0]
        assert cell["dwell_s"] > 0

    def test_peak_simultaneous(self) -> None:
        hm = ThreatHeatmap(cell_size_m=100.0)
        tracks = [_track(f"t{i}", (10.0, 10.0, 0.0)) for i in range(5)]
        hm.update(tracks, 1.0)
        spots = hm.get_hotspots(top_n=1)
        assert spots[0].peak_count == 5

    def test_peak_not_lowered(self) -> None:
        hm = ThreatHeatmap(cell_size_m=100.0)
        tracks = [_track(f"t{i}", (10.0, 10.0, 0.0)) for i in range(5)]
        hm.update(tracks, 1.0)
        hm.update(tracks[:2], 2.0)
        spots = hm.get_hotspots(top_n=1)
        assert spots[0].peak_count == 5


class TestDecay:
    def test_decay_reduces_dwell(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        t = _track("t1", (25.0, 25.0, 0.0))
        hm.update([t], 0.0)
        hm.update([t], 10.0)
        before = hm.to_dict()["cells"][0]["dwell_s"]
        hm.decay(factor=0.5)
        after = hm.to_dict()["cells"][0]["dwell_s"]
        assert after < before

    def test_heavy_decay_prunes_empty_cells(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        t = _track("t1", (25.0, 25.0, 0.0))
        hm.update([t], 0.0)
        # Reset transits so decay can prune it
        for cell in hm._cells.values():
            cell.total_transits = 0
            cell.peak_simultaneous = 0
            cell.dwell_time_s = 0.001
        hm.decay(factor=0.001)
        assert len(hm._cells) == 0


class TestHotspots:
    def test_ordering_by_density(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        # One track in cell A, three in cell B
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        hm.update([
            _track("t2", (125.0, 125.0, 0.0)),
            _track("t3", (125.0, 125.0, 0.0)),
            _track("t4", (125.0, 125.0, 0.0)),
        ], 1.0)
        spots = hm.get_hotspots(top_n=2)
        assert len(spots) == 2
        assert spots[0].threat_density >= spots[1].threat_density

    def test_top_n_limits(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        for i in range(10):
            hm.update([_track(f"t{i}", (i * 60.0, 0.0, 0.0))], 1.0)
        assert len(hm.get_hotspots(top_n=3)) == 3

    def test_hotspot_center_position(self) -> None:
        hm = ThreatHeatmap(cell_size_m=100.0)
        hm.update([_track("t1", (50.0, 50.0, 0.0))], 1.0)
        spot = hm.get_hotspots(top_n=1)[0]
        assert spot.center_position[0] == 50.0
        assert spot.center_position[1] == 50.0


class TestCorridors:
    def test_corridor_needs_two_cells(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        corridors = hm.get_approach_corridors()
        assert len(corridors) == 0

    def test_adjacent_cells_form_corridor(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        # Place tracks in adjacent cells along the x-axis
        for i in range(4):
            hm.update([_track(f"t{i}", (i * 50.0 + 25.0, 25.0, 0.0))], 1.0)
        corridors = hm.get_approach_corridors()
        assert len(corridors) >= 1
        assert len(corridors[0]) >= 2

    def test_separated_clusters_are_separate_corridors(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        # Cluster A near origin
        for i in range(3):
            hm.update([_track(f"a{i}", (i * 50.0 + 25.0, 25.0, 0.0))], 1.0)
        # Cluster B far away
        for i in range(3):
            hm.update([_track(f"b{i}", (800.0 + i * 50.0, 800.0, 0.0))], 1.0)
        corridors = hm.get_approach_corridors()
        assert len(corridors) == 2


class TestEffectorSuggestion:
    def test_suggest_uncovered(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        spot = hm.suggest_effector_position()
        assert spot is not None

    def test_suggest_skips_covered(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        # Cover the hotspot
        covered = [(25.0, 25.0, 0.0)]
        spot = hm.suggest_effector_position(covered, cover_radius_m=100.0)
        assert spot is None

    def test_suggest_returns_second_best_when_first_covered(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        # High density at A
        for i in range(5):
            hm.update([_track(f"a{i}", (25.0, 25.0, 0.0))], float(i))
        # Lower density at B
        hm.update([_track("b1", (225.0, 225.0, 0.0))], 1.0)
        # Cover A
        covered = [(25.0, 25.0, 0.0)]
        spot = hm.suggest_effector_position(covered, cover_radius_m=100.0)
        assert spot is not None
        assert spot.grid_x == 4  # 225 / 50 = 4


class TestSerialization:
    def test_to_dict_structure(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        d = hm.to_dict()
        assert "cell_size_m" in d
        assert "total_cells" in d
        assert "cells" in d
        assert "hotspots" in d
        assert "corridors" in d
        assert d["cell_size_m"] == 50.0
        assert d["total_cells"] == 1

    def test_cell_dict_fields(self) -> None:
        hm = ThreatHeatmap(cell_size_m=50.0)
        hm.update([_track("t1", (25.0, 25.0, 0.0))], 1.0)
        cell = hm.to_dict()["cells"][0]
        for key in ("gx", "gy", "center", "density", "peak",
                     "transits", "dwell_s"):
            assert key in cell, f"missing {key}"

    def test_empty_to_dict(self) -> None:
        hm = ThreatHeatmap()
        d = hm.to_dict()
        assert d["total_cells"] == 0
        assert d["cells"] == []
