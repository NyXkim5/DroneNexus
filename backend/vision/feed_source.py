from __future__ import annotations

import io
import time
from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw

from vision.detector import SimTargetPlacement
from vision.models import TargetType


_TARGET_COLORS = {
    TargetType.VEHICLE_CAR: (0, 180, 0),
    TargetType.VEHICLE_TRUCK: (0, 200, 50),
    TargetType.VEHICLE_APC: (0, 160, 80),
    TargetType.VEHICLE_FUEL_TANKER: (255, 100, 0),
    TargetType.PERSONNEL_INDIVIDUAL: (0, 100, 255),
    TargetType.PERSONNEL_GROUP: (0, 80, 200),
    TargetType.INFRA_GENERATOR: (200, 200, 0),
    TargetType.INFRA_ANTENNA: (180, 180, 0),
    TargetType.INFRA_BRIDGE: (160, 160, 160),
    TargetType.INFRA_BUILDING: (120, 120, 120),
    TargetType.ORDNANCE_AMMO_CACHE: (255, 0, 0),
    TargetType.ORDNANCE_FUEL_DEPOT: (255, 50, 50),
}

_TARGET_SIZES = {
    TargetType.VEHICLE_CAR: (20, 12),
    TargetType.VEHICLE_TRUCK: (28, 14),
    TargetType.VEHICLE_APC: (26, 16),
    TargetType.VEHICLE_FUEL_TANKER: (32, 14),
    TargetType.PERSONNEL_INDIVIDUAL: (4, 4),
    TargetType.PERSONNEL_GROUP: (16, 16),
    TargetType.INFRA_GENERATOR: (18, 18),
    TargetType.INFRA_ANTENNA: (8, 20),
    TargetType.INFRA_BRIDGE: (60, 16),
    TargetType.INFRA_BUILDING: (36, 28),
    TargetType.ORDNANCE_AMMO_CACHE: (24, 18),
    TargetType.ORDNANCE_FUEL_DEPOT: (40, 30),
}


class FeedSource(ABC):
    @abstractmethod
    def next_frame(self) -> Tuple[np.ndarray, float]:
        ...


class SimFeedSource(FeedSource):
    def __init__(
        self,
        placements: List[SimTargetPlacement],
        resolution: Tuple[int, int] = (1280, 720),
        fps: float = 5.0,
    ) -> None:
        self._placements = placements
        self._width, self._height = resolution
        self._fps = fps
        self._frame_count = 0
        self._start_time = time.monotonic()
        self._scene = self._render_scene()

    def _render_scene(self) -> np.ndarray:
        img = Image.new("RGB", (self._width, self._height), (30, 35, 30))
        draw = ImageDraw.Draw(img)

        for i in range(0, self._width, 40):
            draw.line([(i, 0), (i, self._height)], fill=(40, 45, 40))
        for i in range(0, self._height, 40):
            draw.line([(0, i), (self._width, i)], fill=(40, 45, 40))

        cx, cy = self._width // 2, self._height // 2
        scale = min(self._width, self._height) / 2000.0

        for p in self._placements:
            color = _TARGET_COLORS.get(p.target_type, (128, 128, 128))
            tw, th = _TARGET_SIZES.get(p.target_type, (16, 16))
            px = int(cx + p.position[0] * scale)
            py = int(cy - p.position[1] * scale)
            draw.rectangle(
                [px - tw // 2, py - th // 2, px + tw // 2, py + th // 2],
                fill=color,
                outline=(255, 255, 255),
            )

        return np.array(img)

    def next_frame(self) -> Tuple[np.ndarray, float]:
        ts = self._start_time + self._frame_count / self._fps
        self._frame_count += 1
        return self._scene.copy(), ts


class StreamFeedSource(FeedSource):
    def __init__(self, video_manager: object, drone_id: str) -> None:
        self._vm = video_manager
        self._drone_id = drone_id
        self._frame_count = 0

    def next_frame(self) -> Tuple[np.ndarray, float]:
        frame_bytes = self._vm.get_latest_frame(self._drone_id)  # type: ignore[attr-defined]
        if frame_bytes is None:
            arr = np.zeros((720, 1280, 3), dtype=np.uint8)
        else:
            img = Image.open(io.BytesIO(frame_bytes))
            arr = np.array(img)
        self._frame_count += 1
        return arr, time.monotonic()
