"""
Engagement replay analyzer with synthetic vision detection.

Replays saved wargame recordings through a virtual camera model and the
DroneClassifier to evaluate what the vision system would have seen. Compares
vision-based detections against the ground truth tracks from the wargame.

Usage:
    python3 -m scripts.replay_with_detection \
        --recording path/to/recording.json.gz \
        --camera-fov 60 \
        --camera-range 2000 \
        --model models/drone_seraphim_best.pt
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from vision.drone_detector import (
    CameraParams,
    DroneClass,
    DroneClassifier,
    DroneDetection,
)
from vision.tensorrt_detector import Detection
from wargame.recorder import RecordingMetadata, WargameRecorder

logger = logging.getLogger("overwatch.replay_analysis")

# Typical drone body size in pixels at 100m range with a 60-degree FOV
_REF_RANGE_M = 100.0
_REF_BBOX_PX = 80.0
# Minimum apparent pixel size for a detection to be viable
_MIN_BBOX_PX = 4.0
# Default image resolution for the virtual camera
_DEFAULT_WIDTH = 1920
_DEFAULT_HEIGHT = 1080


# ---------------------------------------------------------------------------
# ReplayAnalysis result
# ---------------------------------------------------------------------------


@dataclass
class FrameAnalysis:
    """Per-frame detection breakdown."""

    tick: int
    sim_time_s: float
    total_tracks: int
    in_fov: int
    detected: int
    missed: int
    detections: List[dict] = field(default_factory=list)


@dataclass
class ReplayAnalysis:
    """Aggregated detection analysis across all frames."""

    total_frames: int
    total_tracks: int
    tracks_detected: int
    tracks_missed: int
    detection_rate: float
    avg_range_at_detection: float
    max_detection_range: float
    classification_accuracy: float
    false_positive_rate: float
    frames_with_detections: int
    per_frame: List[FrameAnalysis] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Virtual camera model
# ---------------------------------------------------------------------------


@dataclass
class VirtualCamera:
    """Pinhole camera placed at the defended site looking outward."""

    position: Tuple[float, float, float]
    bearing_deg: float
    fov_deg: float
    max_range_m: float
    width_px: int = _DEFAULT_WIDTH
    height_px: int = _DEFAULT_HEIGHT

    @property
    def fov_rad(self) -> float:
        return math.radians(self.fov_deg)

    @property
    def bearing_rad(self) -> float:
        return math.radians(self.bearing_deg)


def is_in_fov(
    camera: VirtualCamera,
    track_enu: Tuple[float, float, float],
) -> bool:
    """Check whether a track falls within the camera frustum.

    Uses a 2D horizontal bearing check against the camera FOV cone and a
    range check against max_range_m.
    """
    dx = track_enu[0] - camera.position[0]
    dy = track_enu[1] - camera.position[1]
    distance = math.sqrt(dx * dx + dy * dy)
    if distance > camera.max_range_m:
        return False
    if distance < 0.01:
        return True
    bearing_to_track = math.atan2(dx, dy)
    angle_diff = _wrap_angle(bearing_to_track - camera.bearing_rad)
    half_fov = camera.fov_rad / 2.0
    return abs(angle_diff) <= half_fov


def compute_range(
    camera: VirtualCamera,
    track_enu: Tuple[float, float, float],
) -> float:
    """Compute 3D slant range from camera to track in meters."""
    dx = track_enu[0] - camera.position[0]
    dy = track_enu[1] - camera.position[1]
    dz = track_enu[2] - camera.position[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_apparent_size(
    range_m: float,
    ref_range: float = _REF_RANGE_M,
    ref_size: float = _REF_BBOX_PX,
) -> float:
    """Compute apparent bbox size in pixels using inverse-range scaling.

    At ref_range meters the object spans ref_size pixels. Farther objects
    shrink linearly with range.
    """
    if range_m <= 0:
        return ref_size
    return ref_size * (ref_range / range_m)


def project_to_pixel(
    camera: VirtualCamera,
    track_enu: Tuple[float, float, float],
) -> Tuple[float, float]:
    """Project a 3D ENU position into virtual camera pixel coordinates.

    Returns (px, py) in image space. Tracks outside the FOV may return
    coordinates outside [0, width) x [0, height).
    """
    dx = track_enu[0] - camera.position[0]
    dy = track_enu[1] - camera.position[1]
    dz = track_enu[2] - camera.position[2]
    bearing_to_track = math.atan2(dx, dy)
    angle_h = _wrap_angle(bearing_to_track - camera.bearing_rad)
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    angle_v = math.atan2(dz, max(rng, 0.01))
    px = camera.width_px / 2.0 + (angle_h / (camera.fov_rad / 2.0)) * (camera.width_px / 2.0)
    vfov = camera.fov_rad * (camera.height_px / camera.width_px)
    py = camera.height_px / 2.0 - (angle_v / (vfov / 2.0)) * (camera.height_px / 2.0)
    return px, py


def synthesize_bbox(
    camera: VirtualCamera,
    track_enu: Tuple[float, float, float],
) -> Optional[Tuple[float, float, float, float]]:
    """Generate a synthetic bounding box for a track visible in the camera.

    Returns (x1, y1, x2, y2) in pixel coordinates or None if the track
    is out of range or too small to detect.
    """
    if not is_in_fov(camera, track_enu):
        return None
    rng = compute_range(camera, track_enu)
    if rng > camera.max_range_m:
        return None
    apparent = compute_apparent_size(rng)
    if apparent < _MIN_BBOX_PX:
        return None
    px, py = project_to_pixel(camera, track_enu)
    half = apparent / 2.0
    x1 = max(0.0, px - half)
    y1 = max(0.0, py - half)
    x2 = min(float(camera.width_px), px + half)
    y2 = min(float(camera.height_px), py + half)
    return (x1, y1, x2, y2)


def _wrap_angle(angle: float) -> float:
    """Wrap an angle to [-pi, pi]."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def auto_bearing(
    camera_pos: Tuple[float, float, float],
    tracks: List[dict],
) -> float:
    """Compute bearing from camera toward the centroid of all tracks.

    Returns bearing in degrees. Falls back to 0 if no tracks.
    """
    if not tracks:
        return 0.0
    xs, ys = [], []
    for t in tracks:
        enu = t.get("enu", [0, 0, 0])
        xs.append(enu[0])
        ys.append(enu[1])
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    dx = cx - camera_pos[0]
    dy = cy - camera_pos[1]
    return math.degrees(math.atan2(dx, dy))


# ---------------------------------------------------------------------------
# ReplayAnalyzer
# ---------------------------------------------------------------------------


class ReplayAnalyzer:
    """Replays wargame recordings through a virtual camera and classifier."""

    def __init__(
        self,
        camera: VirtualCamera,
        classifier: Optional[DroneClassifier] = None,
    ) -> None:
        self._camera = camera
        self._classifier = classifier or DroneClassifier(
            camera=CameraParams(
                fov_horizontal_deg=camera.fov_deg,
                sensor_width_px=camera.width_px,
                sensor_height_px=camera.height_px,
            ),
            size_filter_enabled=False,
        )

    def analyze_recording(self, recording_path: Path) -> ReplayAnalysis:
        """Load and analyze every frame in a recording."""
        metadata, frames = WargameRecorder.load(recording_path)
        return self._analyze_frames(frames)

    def analyze_frames(self, frames: List[dict]) -> ReplayAnalysis:
        """Analyze a list of pre-loaded frame dicts."""
        return self._analyze_frames(frames)

    def _analyze_frames(self, frames: List[dict]) -> ReplayAnalysis:
        """Core analysis loop across all frames."""
        per_frame: List[FrameAnalysis] = []
        total_tracks = 0
        total_detected = 0
        total_missed = 0
        frames_with_det = 0
        detection_ranges: List[float] = []
        correct_classifications = 0
        total_classifications = 0

        for frame_dict in frames:
            fa = self._analyze_single_frame(frame_dict)
            per_frame.append(fa)
            total_tracks += fa.total_tracks
            total_detected += fa.detected
            total_missed += fa.missed
            if fa.detected > 0:
                frames_with_det += 1
            for det_info in fa.detections:
                detection_ranges.append(det_info["range_m"])
                total_classifications += 1
                if det_info["classified_as_drone"]:
                    correct_classifications += 1

        return _build_analysis(
            per_frame, total_tracks, total_detected, total_missed,
            frames_with_det, detection_ranges,
            correct_classifications, total_classifications,
        )

    def _analyze_single_frame(self, frame_dict: dict) -> FrameAnalysis:
        """Run virtual camera and classifier on one frame."""
        metrics = frame_dict.get("metrics") or {}
        tick = metrics.get("tick", 0)
        sim_time = metrics.get("sim_time_s", 0.0)
        tracks = frame_dict.get("tracks", [])

        in_fov = 0
        detected = 0
        det_infos: List[dict] = []

        for track in tracks:
            enu = tuple(track.get("enu", [0, 0, 0]))
            result = self._evaluate_track(track, enu)
            if result is None:
                continue
            in_fov += 1
            if result["bbox"] is not None:
                detected += 1
                det_infos.append(result)

        missed = in_fov - detected
        return FrameAnalysis(
            tick=tick,
            sim_time_s=sim_time,
            total_tracks=len(tracks),
            in_fov=in_fov,
            detected=detected,
            missed=missed,
            detections=det_infos,
        )

    def _evaluate_track(
        self,
        track: dict,
        enu: tuple,
    ) -> Optional[dict]:
        """Evaluate a single track against the virtual camera."""
        enu3 = (float(enu[0]), float(enu[1]), float(enu[2]) if len(enu) > 2 else 0.0)
        if not is_in_fov(self._camera, enu3):
            return None
        bbox = synthesize_bbox(self._camera, enu3)
        if bbox is None:
            return {"bbox": None, "range_m": compute_range(self._camera, enu3)}
        rng = compute_range(self._camera, enu3)
        drone_det = self._classify_synthetic(bbox, track)
        is_drone = drone_det is not None
        return {
            "track_id": track.get("id", "unknown"),
            "range_m": rng,
            "bbox": list(bbox),
            "apparent_size_px": compute_apparent_size(rng),
            "classified_as_drone": is_drone,
            "drone_class": drone_det.drone_class.value if drone_det else None,
            "confidence": drone_det.confidence if drone_det else 0.0,
        }

    def _classify_synthetic(
        self,
        bbox: Tuple[float, float, float, float],
        track: dict,
    ) -> Optional[DroneDetection]:
        """Pass a synthetic detection through DroneClassifier."""
        raw = Detection(
            class_name="uas",
            confidence=track.get("confidence", 0.8),
            bbox=bbox,
            class_id=0,
        )
        results = self._classifier.classify([raw])
        return results[0] if results else None


def _build_analysis(
    per_frame: List[FrameAnalysis],
    total_tracks: int,
    total_detected: int,
    total_missed: int,
    frames_with_det: int,
    detection_ranges: List[float],
    correct_classifications: int,
    total_classifications: int,
) -> ReplayAnalysis:
    """Assemble the final ReplayAnalysis from accumulated stats."""
    detection_rate = total_detected / max(total_tracks, 1)
    avg_range = sum(detection_ranges) / max(len(detection_ranges), 1)
    max_range = max(detection_ranges) if detection_ranges else 0.0
    class_acc = correct_classifications / max(total_classifications, 1)
    fp_rate = 1.0 - class_acc

    return ReplayAnalysis(
        total_frames=len(per_frame),
        total_tracks=total_tracks,
        tracks_detected=total_detected,
        tracks_missed=total_missed,
        detection_rate=round(detection_rate, 4),
        avg_range_at_detection=round(avg_range, 1),
        max_detection_range=round(max_range, 1),
        classification_accuracy=round(class_acc, 4),
        false_positive_rate=round(fp_rate, 4),
        frames_with_detections=frames_with_det,
        per_frame=per_frame,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay wargame recordings through the vision detection pipeline.",
    )
    parser.add_argument(
        "--recording", required=True, type=Path,
        help="Path to a wargame recording (.json.gz).",
    )
    parser.add_argument(
        "--camera-fov", type=float, default=60.0,
        help="Horizontal field of view in degrees (default: 60).",
    )
    parser.add_argument(
        "--camera-range", type=float, default=2000.0,
        help="Maximum detection range in meters (default: 2000).",
    )
    parser.add_argument(
        "--bearing", type=float, default=None,
        help="Camera bearing in degrees. Auto-computed if omitted.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to YOLO .pt weights (unused in synthetic mode).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("models"),
        help="Directory for JSON output (default: models/).",
    )
    return parser.parse_args()


def _print_summary(analysis: ReplayAnalysis) -> None:
    """Print a human-readable summary table to stdout."""
    print("\n=== Replay Detection Analysis ===")
    print(f"Total frames analyzed:     {analysis.total_frames}")
    print(f"Frames with detections:    {analysis.frames_with_detections}")
    print(f"Total track appearances:   {analysis.total_tracks}")
    print(f"Tracks detected:           {analysis.tracks_detected}")
    print(f"Tracks missed:             {analysis.tracks_missed}")
    print(f"Detection rate:            {analysis.detection_rate:.2%}")
    print(f"Avg range at detection:    {analysis.avg_range_at_detection:.1f} m")
    print(f"Max detection range:       {analysis.max_detection_range:.1f} m")
    print(f"Classification accuracy:   {analysis.classification_accuracy:.2%}")
    print(f"False positive rate:       {analysis.false_positive_rate:.2%}")


def _save_report(
    analysis: ReplayAnalysis,
    output_dir: Path,
) -> Path:
    """Save analysis JSON and return the output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"replay_analysis_{ts}.json"
    report = asdict(analysis)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Report saved to %s", out_path)
    return out_path


def _extract_site_enu(frames: List[dict]) -> Tuple[float, float, float]:
    """Pull the site ENU from the first frame, falling back to origin."""
    if not frames:
        return (0.0, 0.0, 0.0)
    site = frames[0].get("site", {})
    enu = site.get("enu", [0, 0, 0])
    return (float(enu[0]), float(enu[1]), float(enu[2]) if len(enu) > 2 else 0.0)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args()

    if not args.recording.exists():
        logger.error("Recording not found: %s", args.recording)
        sys.exit(1)

    metadata, frames = WargameRecorder.load(args.recording)
    logger.info(
        "Loaded %s: %d frames, scenario=%s",
        args.recording.name,
        len(frames),
        metadata.scenario_name,
    )

    site_enu = _extract_site_enu(frames)
    bearing = args.bearing
    if bearing is None:
        all_tracks = []
        for f in frames:
            all_tracks.extend(f.get("tracks", []))
        bearing = auto_bearing(site_enu, all_tracks)
        logger.info("Auto bearing: %.1f deg", bearing)

    camera = VirtualCamera(
        position=site_enu,
        bearing_deg=bearing,
        fov_deg=args.camera_fov,
        max_range_m=args.camera_range,
    )

    analyzer = ReplayAnalyzer(camera=camera)
    analysis = analyzer.analyze_frames(frames)
    _print_summary(analysis)
    out_path = _save_report(analysis, args.output_dir)
    print(f"\nFull report: {out_path}")


if __name__ == "__main__":
    main()
