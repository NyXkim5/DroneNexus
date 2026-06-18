from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from vision.models import (
    BoundingBox,
    TargetType,
    VisualTarget,
    TARGET_DEFAULTS,
)


@dataclass
class SimTargetPlacement:
    id: str
    target_type: TargetType
    position: tuple
    occupancy_override: Optional[int] = None
    properties: Optional[dict] = None


class Detector(ABC):
    @abstractmethod
    def detect(self, frame: np.ndarray, timestamp: float) -> List[VisualTarget]:
        ...


_BB_SIZES = {
    TargetType.VEHICLE_CAR: (48, 28),
    TargetType.VEHICLE_TRUCK: (64, 32),
    TargetType.VEHICLE_APC: (60, 36),
    TargetType.VEHICLE_FUEL_TANKER: (72, 32),
    TargetType.PERSONNEL_INDIVIDUAL: (16, 32),
    TargetType.PERSONNEL_GROUP: (64, 48),
    TargetType.INFRA_GENERATOR: (40, 40),
    TargetType.INFRA_ANTENNA: (24, 48),
    TargetType.INFRA_BRIDGE: (120, 40),
    TargetType.INFRA_BUILDING: (80, 60),
    TargetType.ORDNANCE_AMMO_CACHE: (56, 40),
    TargetType.ORDNANCE_FUEL_DEPOT: (96, 64),
}


def _enu_to_pixel(
    position_enu: tuple,
    frame_width: int,
    frame_height: int,
    scene_scale: float,
) -> tuple:
    """Convert ENU world coordinates to pixel coordinates using the same
    projection as SimFeedSource._render_scene."""
    cx = frame_width // 2
    cy = frame_height // 2
    px = int(cx + position_enu[0] * scene_scale)
    py = int(cy - position_enu[1] * scene_scale)
    return px, py


class SimDetector(Detector):
    def __init__(
        self,
        placements: List[SimTargetPlacement],
        noise_sigma_m: float = 2.0,
        false_positive_rate: float = 0.02,
        seed: Optional[int] = None,
        resolution: tuple = (1280, 720),
        scene_scale: Optional[float] = None,
    ) -> None:
        self._placements = placements
        self._noise_sigma = noise_sigma_m
        self._fp_rate = false_positive_rate
        self._rng = random.Random(seed)
        self._np_rng = np.random.default_rng(seed)
        self._fp_counter = 0
        w, h = resolution
        self._scene_scale = scene_scale if scene_scale is not None else min(w, h) / 2000.0

    def detect(self, frame: np.ndarray, timestamp: float) -> List[VisualTarget]:
        h, w = frame.shape[:2]
        results: List[VisualTarget] = []

        for p in self._placements:
            defaults = TARGET_DEFAULTS[p.target_type]
            noise_x = self._np_rng.normal(0, self._noise_sigma) if self._noise_sigma > 0 else 0.0
            noise_y = self._np_rng.normal(0, self._noise_sigma) if self._noise_sigma > 0 else 0.0
            pos = (
                p.position[0] + noise_x,
                p.position[1] + noise_y,
                p.position[2] if len(p.position) > 2 else 0.0,
            )

            confidence = max(0.5, 1.0 - (self._noise_sigma / 20.0)) if self._noise_sigma > 0 else 1.0

            bb_w, bb_h = _BB_SIZES.get(p.target_type, (48, 32))
            cx, cy = w // 2, h // 2
            scale = self._scene_scale
            px = int(cx + pos[0] * scale)
            py = int(cy - pos[1] * scale)
            bb_x = max(0, min(px - bb_w // 2, w - bb_w))
            bb_y = max(0, min(py - bb_h // 2, h - bb_h))

            occupancy = p.occupancy_override if p.occupancy_override is not None else defaults["default_occupancy"]

            results.append(VisualTarget(
                id=p.id,
                target_type=p.target_type,
                position=pos,
                bounding_box=BoundingBox(x=bb_x, y=bb_y, width=bb_w, height=bb_h),
                confidence=confidence,
                occupancy_estimate=occupancy,
                base_value=defaults["base_value"],
                blast_radius_m=defaults["blast_radius_m"],
                properties=p.properties or {},
            ))

        num_fp = sum(1 for _ in range(len(self._placements)) if self._rng.random() < self._fp_rate)
        if self._fp_rate >= 1.0:
            num_fp = max(1, len(self._placements))
        for _ in range(num_fp):
            self._fp_counter += 1
            fp_type = self._rng.choice(list(TargetType))
            fp_defaults = TARGET_DEFAULTS[fp_type]
            bb_w, bb_h = _BB_SIZES.get(fp_type, (48, 32))
            results.append(VisualTarget(
                id=f"fp-{self._fp_counter}",
                target_type=fp_type,
                position=(
                    self._rng.uniform(-1000, 1000),
                    self._rng.uniform(-1000, 1000),
                    0.0,
                ),
                bounding_box=BoundingBox(
                    x=self._rng.randint(0, max(0, w - bb_w)),
                    y=self._rng.randint(0, max(0, h - bb_h)),
                    width=bb_w,
                    height=bb_h,
                ),
                confidence=self._rng.uniform(0.3, 0.7),
                occupancy_estimate=0,
                base_value=fp_defaults["base_value"],
                blast_radius_m=fp_defaults["blast_radius_m"],
                properties={},
            ))

        return results
