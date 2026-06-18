"""Detection heatmap accumulator for OVERWATCH/BULWARK.

Grid-based spatial accumulator in ENU space. Records where drones are
detected most frequently, revealing patrol patterns and ingress corridors.
"""
from __future__ import annotations

import base64
import io
from typing import List, Tuple

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Colormap: manual "hot" stops — no matplotlib dependency
# 0.0 = transparent black
# 0.25 = blue
# 0.5 = yellow
# 0.75 = orange
# 1.0 = red
# ---------------------------------------------------------------------------
_STOPS: List[Tuple[float, Tuple[int, int, int, int]]] = [
    (0.00, (0,   0,   0,   0)),
    (0.25, (0,   0,   255, 200)),
    (0.50, (255, 255, 0,   220)),
    (0.75, (255, 128, 0,   235)),
    (1.00, (255, 0,   0,   255)),
]


def _apply_colormap(normalized: np.ndarray) -> np.ndarray:
    """Map a 2-D float32 array in [0, 1] to an RGBA uint8 array."""
    h, w = normalized.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    for i in range(len(_STOPS) - 1):
        t0, c0 = _STOPS[i]
        t1, c1 = _STOPS[i + 1]
        mask = (normalized >= t0) & (normalized <= t1)
        if not np.any(mask):
            continue
        t = (normalized[mask] - t0) / (t1 - t0)
        for ch in range(4):
            rgba[mask, ch] = np.clip(
                c0[ch] + t * (c1[ch] - c0[ch]), 0, 255
            ).astype(np.uint8)

    return rgba


class DetectionHeatmap:
    """Accumulates drone detection positions into a 2-D ENU grid."""

    def __init__(
        self,
        width: int = 200,
        height: int = 200,
        bounds_m: float = 5000.0,
        decay: float = 0.995,
    ) -> None:
        """
        Args:
            width:    Grid columns (x axis).
            height:   Grid rows (y axis).
            bounds_m: Half-extent of the grid in meters. Covers
                      -bounds_m to +bounds_m in both x and y.
            decay:    Per-tick multiplicative decay applied to every cell.
        """
        self._grid = np.zeros((height, width), dtype=np.float32)
        self._bounds = bounds_m
        self._width = width
        self._height = height
        self._decay = decay

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enu_to_cell(self, x_enu: float, y_enu: float) -> Tuple[int, int] | None:
        """Convert ENU (x, y) to (row, col). Returns None if out of bounds."""
        if abs(x_enu) > self._bounds or abs(y_enu) > self._bounds:
            return None
        col = int((x_enu + self._bounds) / (2.0 * self._bounds) * self._width)
        row = int((self._bounds - y_enu) / (2.0 * self._bounds) * self._height)
        col = min(max(col, 0), self._width - 1)
        row = min(max(row, 0), self._height - 1)
        return row, col

    def _cell_to_enu(self, row: int, col: int) -> Tuple[float, float]:
        """Convert (row, col) back to ENU center of that cell."""
        x = (col + 0.5) / self._width * (2.0 * self._bounds) - self._bounds
        y = self._bounds - (row + 0.5) / self._height * (2.0 * self._bounds)
        return x, y

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_detection(
        self, x_enu: float, y_enu: float, weight: float = 1.0
    ) -> None:
        """Increment the grid cell at ENU position (x, y) by weight.

        Detections outside the grid bounds are silently ignored.
        """
        cell = self._enu_to_cell(x_enu, y_enu)
        if cell is None:
            return
        row, col = cell
        self._grid[row, col] += weight

    def add_detections(self, positions: List[Tuple[float, float]]) -> None:
        """Batch-add detections. Each entry is (x_enu, y_enu)."""
        for x, y in positions:
            self.add_detection(x, y)

    def tick(self) -> None:
        """Apply exponential decay to every cell to fade old detections."""
        self._grid *= self._decay

    def to_image(self, colormap: str = "hot") -> np.ndarray:
        """Render the heatmap as an RGBA uint8 array (H x W x 4).

        The colormap parameter is accepted for API compatibility but the
        built-in hot colormap is always used to avoid a matplotlib dependency.
        """
        max_val = float(self._grid.max())
        if max_val > 0.0:
            normalized = (self._grid / max_val).astype(np.float32)
        else:
            normalized = self._grid.copy()
        return _apply_colormap(normalized)

    def to_dict(self) -> dict:
        """Serialize the heatmap for WebSocket transport.

        Returns:
            {
                "image_b64": "<base64-encoded PNG>",
                "bounds_m": float,
                "width": int,
                "height": int,
            }
        """
        rgba = self.to_image()
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return {
            "image_b64": encoded,
            "bounds_m": self._bounds,
            "width": self._width,
            "height": self._height,
        }

    def hotspots(
        self, threshold: float = 0.7
    ) -> List[Tuple[float, float, float]]:
        """Return cells whose normalized intensity exceeds threshold.

        Returns:
            List of (x_enu, y_enu, intensity) tuples, intensity in [0, 1].
        """
        max_val = float(self._grid.max())
        if max_val == 0.0:
            return []
        normalized = self._grid / max_val
        rows, cols = np.where(normalized >= threshold)
        result: List[Tuple[float, float, float]] = []
        for row, col in zip(rows.tolist(), cols.tolist()):
            x, y = self._cell_to_enu(int(row), int(col))
            result.append((x, y, float(normalized[row, col])))
        return result

    def reset(self) -> None:
        """Zero every cell in the grid."""
        self._grid[:] = 0.0
