"""
Multi-camera management system for OVERWATCH site defense.

Provides 360-degree coverage through multiple camera feeds with sector
assignment, gap detection, health monitoring, and panorama stitching.

Each camera is described by a CameraConfig (position, bearing, FOV).
CameraFeed wraps OpenCV capture in a threaded async-compatible interface.
CameraManager orchestrates multiple feeds and tracks angular coverage.
PanoramaStitcher concatenates frames by bearing into a cylindrical strip.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_FOV_DEG = 60.0
_DEFAULT_RESOLUTION = (1280, 720)
_RECONNECT_BASE_S = 1.0
_RECONNECT_MAX_S = 30.0
_HEALTH_WINDOW_S = 5.0
_SECTOR_DIVIDER_WIDTH = 2
_SECTOR_DIVIDER_COLOR = (0, 255, 0)


def _normalize_angle(deg: float) -> float:
    """Normalize angle to [0, 360)."""
    return deg % 360.0


def _angular_range(bearing_deg: float, fov_deg: float) -> tuple[float, float]:
    """Return (start, end) angles in [0, 360) for a camera sector."""
    half = fov_deg / 2.0
    start = _normalize_angle(bearing_deg - half)
    end = _normalize_angle(bearing_deg + half)
    return start, end


def _angle_in_sector(
    angle: float, start: float, end: float,
) -> bool:
    """Check if an angle falls within sector [start, end), handling wraparound."""
    angle = _normalize_angle(angle)
    if start <= end:
        return start <= angle < end
    return angle >= start or angle < end


@dataclass
class CameraConfig:
    """Configuration for a single camera."""

    camera_id: str
    source: str
    position: tuple[float, float, float]
    bearing_deg: float
    fov_deg: float = _DEFAULT_FOV_DEG
    resolution: tuple[int, int] = field(default_factory=lambda: _DEFAULT_RESOLUTION)
    enabled: bool = True


@dataclass
class FrameHealth:
    """Health metrics for a camera feed."""

    fps: float = 0.0
    dropped_frames: int = 0
    last_frame_time: float = 0.0
    total_frames: int = 0


class CameraFeed:
    """Threaded OpenCV capture wrapper with health monitoring and reconnection.

    Runs a background thread that continuously grabs frames from the source.
    Only the latest frame is kept (no queue buildup). Reconnects on failure
    with exponential backoff.
    """

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._health = FrameHealth()
        self._frame_times: list[float] = []
        self._cv2: Optional[object] = None

    @property
    def config(self) -> CameraConfig:
        return self._config

    @property
    def health(self) -> FrameHealth:
        with self._lock:
            return FrameHealth(
                fps=self._health.fps,
                dropped_frames=self._health.dropped_frames,
                last_frame_time=self._health.last_frame_time,
                total_frames=self._health.total_frames,
            )

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_frame(self) -> Optional[np.ndarray]:
        """Return the latest frame or None if no frame is available."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def start(self) -> None:
        """Start the background frame grabbing thread."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"cam-{self._config.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Camera feed started: %s", self._config.camera_id)

    def stop(self) -> None:
        """Stop the background thread and release resources."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        with self._lock:
            self._frame = None
        logger.info("Camera feed stopped: %s", self._config.camera_id)

    def _capture_loop(self) -> None:
        """Background loop that grabs frames with reconnection on failure."""
        import cv2  # noqa: F811 -- late import to avoid hard dependency in tests

        self._cv2 = cv2
        backoff = _RECONNECT_BASE_S

        while not self._stop_event.is_set():
            cap = self._open_capture(cv2)
            if cap is None:
                logger.warning(
                    "Camera %s: failed to open source %s, retrying in %.1fs",
                    self._config.camera_id, self._config.source, backoff,
                )
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_S)
                continue

            backoff = _RECONNECT_BASE_S
            logger.info(
                "Camera %s: capture opened on %s",
                self._config.camera_id, self._config.source,
            )

            try:
                self._read_frames(cap)
            except Exception:
                logger.exception(
                    "Camera %s: error in capture loop",
                    self._config.camera_id,
                )
            finally:
                cap.release()

    def _open_capture(self, cv2_mod: object) -> object | None:
        """Open a cv2.VideoCapture from the configured source."""
        source = self._config.source
        if source.isdigit():
            source_arg = int(source)
        else:
            source_arg = source
        cap = cv2_mod.VideoCapture(source_arg)  # type: ignore[attr-defined]
        if not cap.isOpened():
            cap.release()
            return None
        w, h = self._config.resolution
        cap.set(cv2_mod.CAP_PROP_FRAME_WIDTH, w)  # type: ignore[attr-defined]
        cap.set(cv2_mod.CAP_PROP_FRAME_HEIGHT, h)  # type: ignore[attr-defined]
        return cap

    def _read_frames(self, cap: object) -> None:
        """Read frames from an open capture until stopped or error."""
        while not self._stop_event.is_set():
            ret, frame = cap.read()  # type: ignore[attr-defined]
            now = time.monotonic()
            if not ret or frame is None:
                with self._lock:
                    self._health.dropped_frames += 1
                logger.debug(
                    "Camera %s: dropped frame", self._config.camera_id,
                )
                break

            with self._lock:
                self._frame = frame
                self._health.last_frame_time = now
                self._health.total_frames += 1
                self._frame_times.append(now)
                self._update_fps(now)

    def _update_fps(self, now: float) -> None:
        """Recalculate FPS over a sliding window. Must hold self._lock."""
        cutoff = now - _HEALTH_WINDOW_S
        self._frame_times = [
            t for t in self._frame_times if t > cutoff
        ]
        elapsed = now - self._frame_times[0] if len(self._frame_times) > 1 else 1.0
        self._health.fps = (
            (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0.0
        )


class CameraManager:
    """Manages multiple CameraFeed instances and tracks angular coverage.

    Provides sector assignment, gap detection, and aggregate frame access.
    """

    def __init__(self) -> None:
        self._feeds: dict[str, CameraFeed] = {}
        self._configs: dict[str, CameraConfig] = {}

    @property
    def camera_ids(self) -> list[str]:
        return list(self._configs.keys())

    def add_camera(self, config: CameraConfig) -> None:
        """Register and optionally start a camera feed."""
        if config.camera_id in self._configs:
            raise KeyError(
                f"Camera {config.camera_id} already exists",
            )
        self._configs[config.camera_id] = config
        feed = CameraFeed(config)
        self._feeds[config.camera_id] = feed
        if config.enabled:
            feed.start()
        logger.info("Camera added: %s (bearing=%.0f, fov=%.0f)",
                     config.camera_id, config.bearing_deg, config.fov_deg)

    def remove_camera(self, camera_id: str) -> None:
        """Stop and remove a camera feed."""
        if camera_id not in self._feeds:
            raise KeyError(f"Camera {camera_id} not found")
        self._feeds[camera_id].stop()
        del self._feeds[camera_id]
        del self._configs[camera_id]
        logger.info("Camera removed: %s", camera_id)

    def get_frame(self, camera_id: str) -> Optional[np.ndarray]:
        """Get the latest frame from a specific camera."""
        feed = self._feeds.get(camera_id)
        if feed is None:
            return None
        return feed.get_frame()

    def get_all_frames(self) -> dict[str, np.ndarray]:
        """Get the latest frame from every camera that has one."""
        frames: dict[str, np.ndarray] = {}
        for cam_id, feed in self._feeds.items():
            frame = feed.get_frame()
            if frame is not None:
                frames[cam_id] = frame
        return frames

    def get_health(self, camera_id: str) -> Optional[FrameHealth]:
        """Get health metrics for a specific camera."""
        feed = self._feeds.get(camera_id)
        if feed is None:
            return None
        return feed.health

    def get_all_health(self) -> dict[str, FrameHealth]:
        """Get health metrics for all cameras."""
        return {
            cam_id: feed.health
            for cam_id, feed in self._feeds.items()
        }

    def get_coverage_map(self) -> list[dict]:
        """Return bearing/fov/position for each camera, suitable for HUD overlay."""
        coverage: list[dict] = []
        for config in self._configs.values():
            if not config.enabled:
                continue
            start, end = _angular_range(config.bearing_deg, config.fov_deg)
            coverage.append({
                "camera_id": config.camera_id,
                "bearing_deg": config.bearing_deg,
                "fov_deg": config.fov_deg,
                "sector_start": start,
                "sector_end": end,
                "position": config.position,
            })
        return coverage

    def get_sector_assignments(
        self, sector_size_deg: float = 10.0,
    ) -> dict[int, list[str]]:
        """Divide 360 degrees into sectors and assign cameras to each.

        Returns a dict mapping sector index to list of camera IDs covering
        that sector. Sector 0 is [0, sector_size_deg), etc.
        """
        num_sectors = int(360.0 / sector_size_deg)
        assignments: dict[int, list[str]] = {
            i: [] for i in range(num_sectors)
        }
        for config in self._configs.values():
            if not config.enabled:
                continue
            start, end = _angular_range(config.bearing_deg, config.fov_deg)
            for i in range(num_sectors):
                sector_center = i * sector_size_deg + sector_size_deg / 2.0
                if _angle_in_sector(sector_center, start, end):
                    assignments[i].append(config.camera_id)
        return assignments

    def detect_gaps(
        self, sector_size_deg: float = 10.0,
    ) -> list[tuple[float, float]]:
        """Identify angular sectors with no camera coverage.

        Returns a list of (start_deg, end_deg) tuples for uncovered sectors.
        """
        assignments = self.get_sector_assignments(sector_size_deg)
        gaps: list[tuple[float, float]] = []
        in_gap = False
        gap_start = 0.0

        for i in sorted(assignments.keys()):
            sector_start = i * sector_size_deg
            sector_end = sector_start + sector_size_deg
            if not assignments[i]:
                if not in_gap:
                    gap_start = sector_start
                    in_gap = True
            else:
                if in_gap:
                    gaps.append((gap_start, sector_start))
                    in_gap = False

        if in_gap:
            gaps.append((gap_start, 360.0))

        return gaps

    def stop_all(self) -> None:
        """Stop all camera feeds."""
        for feed in self._feeds.values():
            feed.stop()
        logger.info("All camera feeds stopped")


class PanoramaStitcher:
    """Simple cylindrical panorama stitcher.

    Concatenates camera frames side-by-side sorted by bearing with sector
    divider lines between them. This is not full homography-based stitching
    but a fast operational view for the HUD.
    """

    def __init__(
        self,
        target_height: int = 360,
        divider_width: int = _SECTOR_DIVIDER_WIDTH,
        divider_color: tuple[int, int, int] = _SECTOR_DIVIDER_COLOR,
    ) -> None:
        self._target_height = target_height
        self._divider_width = divider_width
        self._divider_color = divider_color

    def stitch(
        self,
        frames: dict[str, np.ndarray],
        configs: dict[str, CameraConfig],
    ) -> np.ndarray:
        """Stitch frames into a panorama strip sorted by bearing.

        Each frame is resized to a common height, then concatenated
        left-to-right in bearing order with green divider lines.

        Returns an empty black image if no frames are provided.
        """
        if not frames:
            return np.zeros(
                (self._target_height, 1, 3), dtype=np.uint8,
            )

        sorted_ids = sorted(
            frames.keys(),
            key=lambda cid: configs[cid].bearing_deg
            if cid in configs else 0.0,
        )

        resized: list[np.ndarray] = []
        for cam_id in sorted_ids:
            frame = frames[cam_id]
            scaled = self._resize_to_height(frame, self._target_height)
            resized.append(scaled)

        strips: list[np.ndarray] = []
        for i, img in enumerate(resized):
            if i > 0:
                divider = np.zeros(
                    (self._target_height, self._divider_width, 3),
                    dtype=np.uint8,
                )
                divider[:, :] = self._divider_color
                strips.append(divider)
            strips.append(img)

        return np.concatenate(strips, axis=1)

    def _resize_to_height(
        self, frame: np.ndarray, target_h: int,
    ) -> np.ndarray:
        """Resize a frame to target height, preserving aspect ratio."""
        h, w = frame.shape[:2]
        if h == target_h:
            return frame
        scale = target_h / h
        new_w = max(1, int(w * scale))
        try:
            import cv2
            return cv2.resize(frame, (new_w, target_h))
        except ImportError:
            out = np.zeros((target_h, new_w, 3), dtype=np.uint8)
            for row in range(target_h):
                src_row = min(int(row / scale), h - 1)
                for col in range(new_w):
                    src_col = min(int(col / scale), w - 1)
                    out[row, col] = frame[src_row, src_col]
            return out


def default_site_defense_configs(
    origin: tuple[float, float, float] = (0.0, 0.0, 3.0),
) -> list[CameraConfig]:
    """Return a default 4-camera config for 360-degree site defense.

    Cameras are placed at origin facing N, E, S, W with 90-degree FOV each
    for complete coverage with no gaps.
    """
    directions = [
        ("cam-north", 0.0),
        ("cam-east", 90.0),
        ("cam-south", 180.0),
        ("cam-west", 270.0),
    ]
    return [
        CameraConfig(
            camera_id=cam_id,
            source=str(i),
            position=origin,
            bearing_deg=bearing,
            fov_deg=90.0,
            resolution=(1280, 720),
            enabled=True,
        )
        for i, (cam_id, bearing) in enumerate(directions)
    ]
