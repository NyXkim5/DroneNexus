"""
Tests for wargame replay recording and after-action report export.

Covers WargameRecorder, WargamePlayer, and AARExporter end-to-end.
"""
from __future__ import annotations

import csv
import gzip
import json
import tempfile
from pathlib import Path

import pytest

from wargame.recorder import RecordingMetadata, WargameRecorder, WargamePlayer
from wargame.aar_export import AARExporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(tick: int, dt: float = 0.1) -> dict:
    """Build a minimal frame dict that mirrors the real Frame.to_dict() shape."""
    t = round(tick * dt, 2)
    return {
        "type": "WARGAME_FRAME",
        "scenario": "test_scenario",
        "done": False,
        "metrics": {
            "tick": tick,
            "sim_time_s": t,
            "active_hostiles": max(0, 5 - tick),
            "tracks_held": max(0, 5 - tick),
            "leakers": 0 if tick < 8 else 1,
            "engagements_made": tick,
            "intercepts": max(0, tick - 2),
            "intercept_rate": 0.8,
            "defender_spent": tick * 1000.0,
            "attacker_destroyed": tick * 900.0,
            "cost_exchange_ratio": round(1000.0 / 900.0, 4) if tick > 0 else None,
            "cost_exchange_win": False,
        },
        "tracks": [],
        "defenders": [],
        "assignments": [],
        "cascade_results": [],
        "engagement_order": None,
        "visual_targets": [],
        "heatmap_data": None,
    }


def _record_n_frames(n: int, path: Path) -> WargameRecorder:
    """Record n frames to path and call stop+save."""
    recorder = WargameRecorder(path)
    recorder.start("test_scenario", 0.0)
    for i in range(1, n + 1):
        recorder.record_frame(_make_frame(i))
    recorder.stop(n * 0.1)
    recorder.save()
    return recorder


# ---------------------------------------------------------------------------
# WargameRecorder tests
# ---------------------------------------------------------------------------

def test_record_and_load_roundtrip():
    """Record 10 frames, save, load, verify frames are identical."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "replay.wgr.gz"
        recorder = WargameRecorder(path)
        recorder.start("roundtrip", 0.0)
        original_frames = [_make_frame(i) for i in range(1, 11)]
        for frame in original_frames:
            recorder.record_frame(frame)
        recorder.stop(1.0)
        recorder.save()

        metadata, loaded_frames = WargameRecorder.load(path)

        assert loaded_frames == original_frames


def test_metadata_preserved():
    """Scenario name, timestamps, and frame count survive a save/load cycle."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "replay.wgr.gz"
        recorder = WargameRecorder(path)
        recorder.start("alpha_raid", 5.0)
        for i in range(1, 11):
            recorder.record_frame(_make_frame(i))
        recorder.stop(15.0)
        recorder.save()

        metadata, frames = WargameRecorder.load(path)

        assert metadata.scenario_name == "alpha_raid"
        assert metadata.start_time == 5.0
        assert metadata.end_time == 15.0
        assert metadata.total_frames == 10
        assert len(frames) == 10


def test_gzip_compression():
    """Saved file must be a valid gzip archive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "replay.wgr.gz"
        _record_n_frames(5, path)

        # gzip magic bytes: 1f 8b
        with open(path, "rb") as fh:
            magic = fh.read(2)

        assert magic == b"\x1f\x8b"

        # Must decompress cleanly.
        with gzip.open(path, "rt") as fh:
            payload = json.load(fh)

        assert "metadata" in payload
        assert "frames" in payload


# ---------------------------------------------------------------------------
# WargamePlayer tests
# ---------------------------------------------------------------------------

def _make_player(n: int = 10) -> WargamePlayer:
    frames = [_make_frame(i) for i in range(1, n + 1)]
    metadata = RecordingMetadata(
        scenario_name="player_test",
        start_time=0.0,
        end_time=n * 0.1,
        total_frames=n,
    )
    return WargamePlayer(frames, metadata)


def test_player_next_frame():
    """Sequential playback returns frames in order."""
    player = _make_player(10)
    results = []
    while True:
        frame = player.next_frame()
        if frame is None:
            break
        results.append(frame)

    assert len(results) == 10
    for i, frame in enumerate(results):
        assert frame["metrics"]["tick"] == i + 1


def test_player_seek():
    """Seeking to frame index 5 returns the 6th frame (0-based)."""
    player = _make_player(10)
    frame = player.seek(5)

    assert frame is not None
    assert frame["metrics"]["tick"] == 6
    assert player.current_index == 6


def test_player_seek_time():
    """Seek to a timestamp lands on the closest frame."""
    player = _make_player(10)
    # Frames have sim_time_s = tick * 0.1, so tick=7 -> 0.7s
    frame = player.seek_time(0.72)

    assert frame is not None
    # Closest to 0.72 is tick 7 (0.70) or tick 8 (0.80); 0.72 is closer to 7
    assert frame["metrics"]["tick"] == 7


def test_player_progress():
    """Progress increases from 0 to 1 as frames are consumed."""
    player = _make_player(10)

    assert player.progress == 0.0

    for _ in range(5):
        player.next_frame()
    assert abs(player.progress - 0.5) < 1e-9

    for _ in range(5):
        player.next_frame()
    assert player.progress == 1.0


def test_player_seek_out_of_range():
    """Seek beyond bounds returns None without raising."""
    player = _make_player(5)
    assert player.seek(99) is None
    assert player.seek(-1) is None


def test_player_next_frame_exhausted():
    """next_frame returns None after all frames are consumed."""
    player = _make_player(3)
    player.seek(2)  # moves cursor to 3 (end)
    player.next_frame()  # this is index 2
    assert player.next_frame() is None


# ---------------------------------------------------------------------------
# AARExporter tests
# ---------------------------------------------------------------------------

def _make_exporter(n: int = 10) -> AARExporter:
    frames = [_make_frame(i) for i in range(1, n + 1)]
    metadata = RecordingMetadata(
        scenario_name="aar_test",
        start_time=0.0,
        end_time=n * 0.1,
        total_frames=n,
    )
    return AARExporter(metadata, frames)


def test_aar_summary_has_metrics():
    """Summary dict includes all expected engagement and cost-exchange keys."""
    exporter = _make_exporter(10)
    summary = exporter.generate_summary()

    required_keys = [
        "scenario_name",
        "duration_s",
        "total_frames",
        "total_engagements",
        "hits",
        "misses",
        "leakers",
        "cost_exchange_ratio",
        "defender_spent",
        "attacker_destroyed",
        "peak_threat_count",
        "peak_threat_time_s",
        "cascade_outcomes",
        "engagement_orders_made",
        "timeline",
    ]
    for key in required_keys:
        assert key in summary, f"Missing key in summary: {key}"

    assert summary["total_engagements"] >= 0
    assert summary["hits"] >= 0
    assert summary["misses"] >= 0
    assert isinstance(summary["timeline"], list)


def test_aar_text_report_format():
    """Text report contains all required section headers."""
    exporter = _make_exporter(10)
    report = exporter.generate_text_report()

    assert "OVERWATCH/BULWARK AFTER-ACTION REPORT" in report
    assert "Scenario:" in report
    assert "Duration:" in report
    assert "EXECUTIVE SUMMARY" in report
    assert "ENGAGEMENT TIMELINE" in report
    assert "CASCADE ANALYSIS" in report
    assert "ROE AUDIT" in report


def test_aar_csv_export():
    """CSV export produces a file with the correct headers and one row per frame."""
    exporter = _make_exporter(10)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "timeline.csv"
        exporter.export_csv_timeline(path)

        assert path.exists()

        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        expected_headers = {
            "frame_index",
            "sim_time_s",
            "active_hostiles",
            "tracks_held",
            "leakers",
            "engagements_made",
            "intercepts",
            "intercept_rate",
            "defender_spent",
            "attacker_destroyed",
            "cost_exchange_ratio",
        }
        assert expected_headers == set(reader.fieldnames)
        assert len(rows) == 10


def test_aar_json_export():
    """JSON export writes a valid JSON file with expected keys."""
    exporter = _make_exporter(10)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "summary.json"
        exporter.export_json(path)

        assert path.exists()
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        assert data["scenario_name"] == "aar_test"
        assert "total_engagements" in data


def test_aar_empty_frames():
    """AARExporter handles an empty frame list without raising."""
    metadata = RecordingMetadata(
        scenario_name="empty",
        start_time=0.0,
        end_time=0.0,
        total_frames=0,
    )
    exporter = AARExporter(metadata, [])
    summary = exporter.generate_summary()
    assert summary["total_frames"] == 0
    report = exporter.generate_text_report()
    assert "OVERWATCH/BULWARK AFTER-ACTION REPORT" in report
