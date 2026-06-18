"""
Tests for the async ReplayPlayer.

Uses a small synthetic recording created in a pytest fixture to validate
loading, playback, seeking, speed control, and error handling.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import tempfile
import time
from pathlib import Path

import pytest

from wargame.replay import ReplayPlayer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_frame(tick: int, sim_time: float) -> dict:
    """Build a minimal frame dict matching recorder output shape."""
    return {
        "type": "WARGAME_FRAME",
        "scenario": "test_scenario",
        "done": False,
        "metrics": {
            "tick": tick,
            "sim_time_s": sim_time,
            "active_hostiles": max(10 - tick, 0),
            "tracks_held": 8,
            "leakers": tick // 3,
            "engagements_made": tick,
            "intercepts": tick,
            "intercept_rate": round(tick / max(tick + 1, 1), 4),
            "defender_spent": tick * 500.0,
            "attacker_destroyed": tick * 800.0,
            "cost_exchange_ratio": 0.625 if tick > 0 else None,
        },
        "tracks": [],
        "defenders": [],
        "assignments": [],
    }


FRAME_COUNT = 20
TICK_DT = 0.5


@pytest.fixture()
def recording_path(tmp_path: Path) -> Path:
    """Write a synthetic gzipped recording and return its path."""
    frames = [_make_frame(i, i * TICK_DT) for i in range(FRAME_COUNT)]
    payload = {
        "metadata": {
            "scenario_name": "test_scenario",
            "start_time": 0.0,
            "end_time": (FRAME_COUNT - 1) * TICK_DT,
            "total_frames": FRAME_COUNT,
            "version": "1.0",
        },
        "frames": frames,
    }
    path = tmp_path / "test_recording.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadRecording:
    def test_load_valid(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        meta = player.load()
        assert meta["scenario_name"] == "test_scenario"
        assert meta["total_frames"] == FRAME_COUNT

    def test_invalid_path_raises(self, tmp_path: Path) -> None:
        player = ReplayPlayer(tmp_path / "nonexistent.json.gz")
        with pytest.raises(FileNotFoundError):
            player.load()


class TestProperties:
    def test_frame_count(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        assert player.frame_count == FRAME_COUNT

    def test_duration(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        expected = (FRAME_COUNT - 1) * TICK_DT
        assert abs(player.duration_s - expected) < 1e-6

    def test_metadata(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        meta = player.metadata
        assert meta["scenario_name"] == "test_scenario"
        assert meta["start_time"] == 0.0

    def test_properties_before_load_raise(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        with pytest.raises(RuntimeError):
            _ = player.frame_count


class TestPlay:
    @pytest.mark.asyncio()
    async def test_play_yields_all_frames(
        self, recording_path: Path,
    ) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        frames: list[dict] = []
        async for frame in player.play(pace=False):
            frames.append(frame)
        assert len(frames) == FRAME_COUNT

    @pytest.mark.asyncio()
    async def test_play_frame_order(
        self, recording_path: Path,
    ) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        ticks: list[int] = []
        async for frame in player.play(pace=False):
            ticks.append(frame["metrics"]["tick"])
        assert ticks == list(range(FRAME_COUNT))


class TestPlayRange:
    @pytest.mark.asyncio()
    async def test_play_range_subset(
        self, recording_path: Path,
    ) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        frames: list[dict] = []
        async for frame in player.play_range(5, 10, pace=False):
            frames.append(frame)
        assert len(frames) == 5
        assert frames[0]["metrics"]["tick"] == 5
        assert frames[-1]["metrics"]["tick"] == 9

    @pytest.mark.asyncio()
    async def test_play_range_empty(
        self, recording_path: Path,
    ) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        frames: list[dict] = []
        async for frame in player.play_range(10, 5, pace=False):
            frames.append(frame)
        assert len(frames) == 0

    @pytest.mark.asyncio()
    async def test_play_range_clamps(
        self, recording_path: Path,
    ) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        frames: list[dict] = []
        async for frame in player.play_range(-5, 99999, pace=False):
            frames.append(frame)
        assert len(frames) == FRAME_COUNT


class TestSeek:
    def test_seek_valid(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        frame = player.seek(7)
        assert frame["metrics"]["tick"] == 7

    def test_seek_out_of_range(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        with pytest.raises(IndexError):
            player.seek(999)

    def test_seek_negative(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.load()
        with pytest.raises(IndexError):
            player.seek(-1)


class TestSpeed:
    def test_speed_default(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        assert player.speed == 1.0

    def test_speed_setter(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        player.speed = 4.0
        assert player.speed == 4.0

    def test_speed_invalid(self, recording_path: Path) -> None:
        player = ReplayPlayer(recording_path)
        with pytest.raises(ValueError):
            player.speed = 0.0

    @pytest.mark.asyncio()
    async def test_speed_affects_pacing(
        self, recording_path: Path,
    ) -> None:
        """Faster speed means shorter wall-clock playback."""
        player = ReplayPlayer(recording_path)
        player.load()

        # Play 4 frames at 1x, then 4 frames at 4x
        async def _timed_play(spd: float) -> float:
            player.speed = spd
            t0 = time.monotonic()
            async for _ in player.play_range(0, 4, pace=True):
                pass
            return time.monotonic() - t0

        slow = await _timed_play(1.0)
        fast = await _timed_play(4.0)
        # 4x speed should be roughly 4x faster (allow generous margin)
        assert fast < slow * 0.6
