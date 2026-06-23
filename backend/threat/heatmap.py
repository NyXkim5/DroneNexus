"""Geospatial threat heatmap -- accumulates density over time, identifies
high-risk approach corridors, and suggests effector placement."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from csontology import Track, TrackClass, Vec3

logger = logging.getLogger("overwatch.heatmap")

DEFAULT_CELL_SIZE_M = 50.0
DEFAULT_GRID_RADIUS_M = 2000.0


@dataclass
class Hotspot:
    """A high-density grid cell worth defending."""
    grid_x: int
    grid_y: int
    center_position: Vec3
    threat_density: float
    peak_count: int
    total_transits: int


@dataclass
class _Cell:
    """Internal accumulator for one grid square."""
    total_transits: int = 0
    dwell_time_s: float = 0.0
    peak_simultaneous: int = 0
    _prev_ids: Set[str] = field(default_factory=set)


class ThreatHeatmap:
    """Grid-based threat density accumulator."""

    def __init__(
        self,
        cell_size_m: float = DEFAULT_CELL_SIZE_M,
        grid_radius_m: float = DEFAULT_GRID_RADIUS_M,
    ) -> None:
        self.cell_size = cell_size_m
        self.grid_radius = grid_radius_m
        self._cells: Dict[Tuple[int, int], _Cell] = {}
        self._last_ts: Optional[float] = None

    def update(self, tracks: List[Track], timestamp: float) -> None:
        """Accumulate threat density from hostile tracks for one tick."""
        dt = 0.0
        if self._last_ts is not None and timestamp > self._last_ts:
            dt = timestamp - self._last_ts
        self._last_ts = timestamp
        occupied: Dict[Tuple[int, int], Set[str]] = {}
        for track in tracks:
            if track.classification == TrackClass.FRIENDLY:
                continue
            gx, gy = self._to_grid(track.position)
            if not self._in_bounds(gx, gy):
                continue
            occupied.setdefault((gx, gy), set()).add(track.id)
        for key, ids in occupied.items():
            cell = self._cells.setdefault(key, _Cell())
            cell.total_transits += len(ids - cell._prev_ids)
            cell.dwell_time_s += dt * len(ids)
            cell.peak_simultaneous = max(cell.peak_simultaneous, len(ids))
            cell._prev_ids = ids
        for key, cell in self._cells.items():
            if key not in occupied:
                cell._prev_ids = set()

    def decay(self, factor: float = 0.995) -> None:
        """Apply time decay so stale data fades."""
        dead: List[Tuple[int, int]] = []
        for key, cell in self._cells.items():
            cell.dwell_time_s *= factor
            if cell.dwell_time_s < 0.01 and cell.total_transits == 0:
                dead.append(key)
        for key in dead:
            del self._cells[key]

    def get_hotspots(self, top_n: int = 5) -> List[Hotspot]:
        """Return the N highest-density cells."""
        scored = [(self._density(c), k) for k, c in self._cells.items()
                  if self._density(c) > 0]
        scored.sort(reverse=True)
        return [self._to_hotspot(k, d) for d, k in scored[:top_n]]

    def get_approach_corridors(self, min_density: float = 0.0) -> List[List[Hotspot]]:
        """Identify connected high-density cell clusters via flood-fill."""
        if not self._cells:
            return []
        threshold = min_density
        if threshold <= 0:
            densities = sorted(self._density(c) for c in self._cells.values()
                               if self._density(c) > 0)
            if not densities:
                return []
            threshold = densities[len(densities) // 2]
        hot_keys: Set[Tuple[int, int]] = {
            k for k, c in self._cells.items() if self._density(c) >= threshold
        }
        visited: Set[Tuple[int, int]] = set()
        corridors: List[List[Hotspot]] = []
        for key in hot_keys:
            if key in visited:
                continue
            cluster = self._flood(key, hot_keys, visited)
            if len(cluster) >= 2:
                spots = sorted(
                    [self._to_hotspot(k, self._density(self._cells[k])) for k in cluster],
                    key=lambda h: h.threat_density, reverse=True,
                )
                corridors.append(spots)
        corridors.sort(key=lambda c: sum(h.threat_density for h in c), reverse=True)
        return corridors

    def suggest_effector_position(
        self, covered_positions: Optional[List[Vec3]] = None,
        cover_radius_m: float = 200.0,
    ) -> Optional[Hotspot]:
        """Return the highest-density cell not already covered."""
        for hotspot in self.get_hotspots(top_n=20):
            if covered_positions is None:
                return hotspot
            if not self._is_covered(hotspot.center_position, covered_positions, cover_radius_m):
                return hotspot
        return None

    def to_dict(self) -> Dict[str, object]:
        """Serialize for visualization over the websocket."""
        cells = []
        for (gx, gy), cell in self._cells.items():
            d = self._density(cell)
            if d <= 0:
                continue
            cx, cy = self._to_enu(gx, gy)
            cells.append({"gx": gx, "gy": gy, "center": [round(cx, 1), round(cy, 1), 0.0],
                          "density": round(d, 4), "peak": cell.peak_simultaneous,
                          "transits": cell.total_transits, "dwell_s": round(cell.dwell_time_s, 2)})
        cells.sort(key=lambda c: c["density"], reverse=True)
        hs = [{"gx": h.grid_x, "gy": h.grid_y, "center": list(h.center_position),
               "density": round(h.threat_density, 4)} for h in self.get_hotspots()]
        return {"cell_size_m": self.cell_size, "total_cells": len(cells),
                "cells": cells, "hotspots": hs,
                "corridors": len(self.get_approach_corridors())}

    # -- internals --

    def _to_grid(self, pos: Vec3) -> Tuple[int, int]:
        return int(math.floor(pos[0] / self.cell_size)), int(math.floor(pos[1] / self.cell_size))

    def _to_enu(self, gx: int, gy: int) -> Tuple[float, float]:
        return (gx + 0.5) * self.cell_size, (gy + 0.5) * self.cell_size

    def _in_bounds(self, gx: int, gy: int) -> bool:
        half = self.grid_radius / self.cell_size
        return abs(gx) <= half and abs(gy) <= half

    def _density(self, cell: _Cell) -> float:
        return cell.total_transits + cell.dwell_time_s * 0.1 + cell.peak_simultaneous * 2.0

    def _to_hotspot(self, key: Tuple[int, int], density: float) -> Hotspot:
        cell = self._cells[key]
        cx, cy = self._to_enu(*key)
        return Hotspot(
            grid_x=key[0], grid_y=key[1],
            center_position=(round(cx, 1), round(cy, 1), 0.0),
            threat_density=density, peak_count=cell.peak_simultaneous,
            total_transits=cell.total_transits,
        )

    @staticmethod
    def _flood(start: Tuple[int, int], keys: Set[Tuple[int, int]],
               visited: Set[Tuple[int, int]]) -> List[Tuple[int, int]]:
        stack, cluster = [start], []
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (-1, 1), (1, -1), (1, 1)):
                nb = (node[0] + dx, node[1] + dy)
                if nb in keys and nb not in visited:
                    stack.append(nb)
        return cluster

    @staticmethod
    def _is_covered(pos: Vec3, covered: List[Vec3], radius: float) -> bool:
        return any(math.hypot(pos[0] - cp[0], pos[1] - cp[1]) <= radius for cp in covered)
