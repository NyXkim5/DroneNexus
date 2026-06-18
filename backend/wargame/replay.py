"""
Async replay player for recorded wargame sessions.

Loads gzip-compressed JSON recordings produced by WargameRecorder and yields
frame dicts through an async iterator that matches the WargameRunner.run()
contract. Consumers (websocket handler, CLI) work unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from wargame.recorder import RecordingMetadata, WargameRecorder

logger = logging.getLogger("overwatch.replay")


class ReplayPlayer:
    """Plays back a saved wargame recording with speed control and seeking."""

    def __init__(self, recording_path: Path) -> None:
        self._path = recording_path
        self._metadata: RecordingMetadata | None = None
        self._frames: list[dict] = []
        self._speed: float = 1.0
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Parse the recording file. Returns metadata as a plain dict."""
        if not self._path.exists():
            raise FileNotFoundError(f"Recording not found: {self._path}")
        self._metadata, self._frames = WargameRecorder.load(self._path)
        self._loaded = True
        logger.info(
            "Loaded recording %s: %d frames, scenario=%s",
            self._path.name,
            len(self._frames),
            self._metadata.scenario_name,
        )
        return self._metadata_dict()

    def _ensure_loaded(self) -> None:
        """Raise if load() has not been called."""
        if not self._loaded or self._metadata is None:
            raise RuntimeError("Call load() before using the player.")

    def _metadata_dict(self) -> dict:
        """Return metadata as a plain dict."""
        assert self._metadata is not None
        return {
            "scenario_name": self._metadata.scenario_name,
            "start_time": self._metadata.start_time,
            "end_time": self._metadata.end_time,
            "total_frames": self._metadata.total_frames,
            "version": self._metadata.version,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def frame_count(self) -> int:
        """Total number of frames in the recording."""
        self._ensure_loaded()
        return len(self._frames)

    @property
    def duration_s(self) -> float:
        """Total duration of the recording in simulation seconds."""
        self._ensure_loaded()
        assert self._metadata is not None
        return self._metadata.end_time - self._metadata.start_time

    @property
    def metadata(self) -> dict:
        """Scenario name, start/end time, and frame count."""
        self._ensure_loaded()
        return self._metadata_dict()

    @property
    def speed(self) -> float:
        """Current playback speed multiplier."""
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        if value <= 0:
            raise ValueError("Speed multiplier must be positive.")
        self._speed = value

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    async def play(
        self, pace: bool = True,
    ) -> AsyncIterator[dict]:
        """Yield frame dicts, optionally paced at the original tick rate.

        When pace is True, sleeps between frames adjusted by the speed
        multiplier. When False, yields as fast as possible.
        """
        self._ensure_loaded()
        async for frame in self._iter_frames(
            0, len(self._frames), pace,
        ):
            yield frame

    async def play_range(
        self,
        start_tick: int,
        end_tick: int,
        pace: bool = True,
    ) -> AsyncIterator[dict]:
        """Yield a subset of frames between start_tick and end_tick."""
        self._ensure_loaded()
        start = max(0, start_tick)
        end = min(end_tick, len(self._frames))
        if start >= end:
            return
        async for frame in self._iter_frames(start, end, pace):
            yield frame

    def seek(self, tick: int) -> dict:
        """Jump to a specific tick and return that frame dict."""
        self._ensure_loaded()
        if tick < 0 or tick >= len(self._frames):
            raise IndexError(
                f"Tick {tick} out of range [0, {len(self._frames) - 1}]."
            )
        return self._frames[tick]

    # ------------------------------------------------------------------
    # Internal iterator
    # ------------------------------------------------------------------

    async def _iter_frames(
        self, start: int, end: int, pace: bool,
    ) -> AsyncIterator[dict]:
        """Core frame iterator with optional pacing."""
        tick_interval = self._compute_tick_interval()
        for i in range(start, end):
            yield self._frames[i]
            if pace and i < end - 1:
                await asyncio.sleep(tick_interval / self._speed)

    def _compute_tick_interval(self) -> float:
        """Derive the tick interval from recording metadata."""
        assert self._metadata is not None
        total = len(self._frames)
        if total <= 1:
            return 0.5
        duration = self._metadata.end_time - self._metadata.start_time
        if duration <= 0:
            return 0.5
        return duration / (total - 1)
