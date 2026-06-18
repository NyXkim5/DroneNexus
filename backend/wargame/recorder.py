"""
Wargame replay recorder and player.

WargameRecorder captures one serialized frame dict per tick and writes a
gzip-compressed JSON file on completion. WargamePlayer reads that file back
and supports sequential playback, frame seeks, and timestamp seeks.

File format on disk:
    gzip( json( {"metadata": {...}, "frames": [...]} ) )

All times are simulation seconds (tick * dt), not wall-clock.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class RecordingMetadata:
    scenario_name: str
    start_time: float
    end_time: float = 0.0
    total_frames: int = 0
    version: str = "1.0"


class WargameRecorder:
    """Records wargame frames for later playback."""

    def __init__(self, output_path: Path) -> None:
        self._path = output_path
        self._frames: List[dict] = []
        self._metadata: Optional[RecordingMetadata] = None

    def start(self, scenario_name: str, timestamp: float) -> None:
        """Begin recording a new scenario."""
        self._frames = []
        self._metadata = RecordingMetadata(
            scenario_name=scenario_name,
            start_time=timestamp,
        )

    def record_frame(self, frame_dict: dict) -> None:
        """Append one serialized frame."""
        if self._metadata is None:
            raise RuntimeError("Call start() before record_frame().")
        self._frames.append(frame_dict)

    def stop(self, timestamp: float) -> None:
        """Finalize recording metadata."""
        if self._metadata is None:
            raise RuntimeError("Call start() before stop().")
        self._metadata.end_time = timestamp
        self._metadata.total_frames = len(self._frames)

    def save(self) -> Path:
        """Write gzipped JSON to disk and return the output path."""
        if self._metadata is None:
            raise RuntimeError("Call start() and stop() before save().")
        payload = {
            "metadata": asdict(self._metadata),
            "frames": self._frames,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(self._path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return self._path

    @classmethod
    def load(cls, path: Path) -> Tuple[RecordingMetadata, List[dict]]:
        """Load a recording from disk and return (metadata, frames)."""
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        raw = payload["metadata"]
        metadata = RecordingMetadata(
            scenario_name=raw["scenario_name"],
            start_time=raw["start_time"],
            end_time=raw["end_time"],
            total_frames=raw["total_frames"],
            version=raw.get("version", "1.0"),
        )
        return metadata, payload["frames"]


class WargamePlayer:
    """Plays back recorded wargame frames."""

    def __init__(self, frames: List[dict], metadata: RecordingMetadata) -> None:
        self._frames = frames
        self._metadata = metadata
        self._index = 0

    def next_frame(self) -> Optional[dict]:
        """Return the next frame and advance the cursor, or None if done."""
        if self._index >= len(self._frames):
            return None
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def seek(self, frame_index: int) -> Optional[dict]:
        """Jump to a specific frame index and return it, or None if out of range."""
        if frame_index < 0 or frame_index >= len(self._frames):
            return None
        self._index = frame_index + 1
        return self._frames[frame_index]

    def seek_time(self, timestamp: float) -> Optional[dict]:
        """Jump to the frame whose sim_time_s is closest to timestamp."""
        if not self._frames:
            return None
        best_index = 0
        best_delta = float("inf")
        for i, frame in enumerate(self._frames):
            metrics = frame.get("metrics") or {}
            t = metrics.get("sim_time_s", 0.0)
            delta = abs(t - timestamp)
            if delta < best_delta:
                best_delta = delta
                best_index = i
        return self.seek(best_index)

    @property
    def progress(self) -> float:
        """Fraction of recording consumed, in [0, 1]."""
        total = len(self._frames)
        if total == 0:
            return 1.0
        return min(self._index / total, 1.0)

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def current_index(self) -> int:
        return self._index
