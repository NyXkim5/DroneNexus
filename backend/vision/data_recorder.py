"""
Data recording pipeline for capturing and labeling detection data.

Records frames with YOLO-format annotations and JSON metadata sidecars
for continuous model improvement. Supports auto-labeling modes and
multi-session dataset merging with train/val splitting.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from vision.tensorrt_detector import Detection

logger = logging.getLogger("overwatch.vision.data_recorder")

# Default YOLO class names for the drone detection dataset
DEFAULT_CLASS_NAMES: Dict[int, str] = {
    0: "uas",
    1: "quadrotor",
    2: "fixed_wing",
    3: "helicopter",
    4: "bird",
}


class LabelingMode(Enum):
    """Auto-labeling modes controlling which frames get recorded."""

    AUTO_ALL = "auto_all"
    AUTO_CONFIDENT = "auto_confident"
    AUTO_NOVEL = "auto_novel"
    MANUAL = "manual"


@dataclass
class SessionSummary:
    """Summary statistics for a completed recording session."""

    session_id: str
    start_time: str
    end_time: str
    total_frames: int
    total_detections: int
    class_distribution: Dict[str, int]
    avg_detections_per_frame: float


# ---------------------------------------------------------------------------
# YOLO annotation helpers
# ---------------------------------------------------------------------------


def _bbox_to_yolo(
    bbox: Tuple[float, float, float, float],
    img_width: int,
    img_height: int,
) -> Tuple[float, float, float, float]:
    """Convert (x1, y1, x2, y2) pixel bbox to YOLO normalized format.

    Returns (center_x, center_y, width, height) each in [0, 1].
    """
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0 / img_width
    cy = (y1 + y2) / 2.0 / img_height
    w = abs(x2 - x1) / img_width
    h = abs(y2 - y1) / img_height
    cx = max(0.0, min(1.0, cx))
    cy = max(0.0, min(1.0, cy))
    w = max(0.0, min(1.0, w))
    h = max(0.0, min(1.0, h))
    return cx, cy, w, h


def _perceptual_hash(frame: np.ndarray, hash_size: int = 8) -> str:
    """Compute a simple average-hash for near-duplicate detection.

    Resizes to hash_size x hash_size grayscale, thresholds against mean,
    and returns a hex digest. No opencv resize needed -- uses numpy stride
    tricks via block averaging.
    """
    if frame.ndim == 3:
        gray = np.mean(frame, axis=2)
    else:
        gray = frame.astype(np.float64)
    h, w = gray.shape
    bh = max(1, h // hash_size)
    bw = max(1, w // hash_size)
    cropped = gray[: bh * hash_size, : bw * hash_size]
    blocks = cropped.reshape(hash_size, bh, hash_size, bw).mean(axis=(1, 3))
    mean_val = blocks.mean()
    bits = (blocks > mean_val).flatten()
    byte_arr = np.packbits(bits)
    return byte_arr.tobytes().hex()


# ---------------------------------------------------------------------------
# DetectionRecorder
# ---------------------------------------------------------------------------


class DetectionRecorder:
    """Records frames and detection annotations for YOLO training."""

    def __init__(
        self,
        labeling_mode: LabelingMode = LabelingMode.AUTO_ALL,
        confidence_threshold: float = 0.7,
        novelty_window: int = 30,
        jpeg_quality: int = 95,
        class_names: Optional[Dict[int, str]] = None,
    ) -> None:
        self._mode = labeling_mode
        self._confidence_threshold = confidence_threshold
        self._novelty_window = novelty_window
        self._jpeg_quality = jpeg_quality
        self._class_names = class_names or dict(DEFAULT_CLASS_NAMES)
        self._session_dir: Optional[str] = None
        self._session_id: Optional[str] = None
        self._start_time: Optional[str] = None
        self._frame_count = 0
        self._detection_count = 0
        self._class_dist: Dict[str, int] = {}
        self._recent_track_ids: List[Set[str]] = []
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def start_session(
        self,
        output_dir: str,
        session_name: Optional[str] = None,
    ) -> str:
        """Create output directory structure and begin recording.

        Returns the session directory path.
        """
        if session_name is None:
            ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            session_name = f"session_{ts}"
        self._session_id = session_name
        self._session_dir = os.path.join(output_dir, session_name)
        _create_session_dirs(self._session_dir)
        self._start_time = datetime.now(tz=timezone.utc).isoformat()
        self._frame_count = 0
        self._detection_count = 0
        self._class_dist = {}
        self._recent_track_ids = []
        self._active = True
        _write_dataset_yaml(self._session_dir, self._class_names)
        logger.info("Recording session started: %s", self._session_dir)
        return self._session_dir

    def record_frame(
        self,
        frame: np.ndarray,
        detections: List[Detection],
        metadata: Optional[dict] = None,
    ) -> bool:
        """Save frame, annotation, and metadata if labeling mode allows.

        Returns True if the frame was recorded, False if skipped.
        """
        if not self._active:
            raise RuntimeError("No active recording session")
        if not self._should_record(detections, metadata):
            return False
        self._frame_count += 1
        frame_name = f"frame_{self._frame_count:06d}"
        self._save_image(frame, frame_name)
        self._save_label(frame, detections, frame_name)
        self._save_metadata(detections, metadata, frame_name)
        self._update_stats(detections)
        self._update_novelty_window(metadata)
        return True

    def stop_session(self) -> SessionSummary:
        """Close the session and return summary statistics."""
        if not self._active:
            raise RuntimeError("No active recording session")
        self._active = False
        end_time = datetime.now(tz=timezone.utc).isoformat()
        avg = (
            self._detection_count / self._frame_count
            if self._frame_count > 0
            else 0.0
        )
        summary = SessionSummary(
            session_id=self._session_id or "",
            start_time=self._start_time or "",
            end_time=end_time,
            total_frames=self._frame_count,
            total_detections=self._detection_count,
            class_distribution=dict(self._class_dist),
            avg_detections_per_frame=round(avg, 2),
        )
        _write_session_json(self._session_dir, summary)
        logger.info(
            "Session stopped: %d frames, %d detections",
            summary.total_frames,
            summary.total_detections,
        )
        return summary

    # -- private helpers -----------------------------------------------------

    def _should_record(
        self,
        detections: List[Detection],
        metadata: Optional[dict],
    ) -> bool:
        """Decide whether to record based on labeling mode."""
        if self._mode == LabelingMode.MANUAL:
            return metadata is not None and metadata.get("record", False)
        if self._mode == LabelingMode.AUTO_ALL:
            return len(detections) > 0
        if self._mode == LabelingMode.AUTO_CONFIDENT:
            if not detections:
                return False
            max_conf = max(d.confidence for d in detections)
            return max_conf > self._confidence_threshold
        if self._mode == LabelingMode.AUTO_NOVEL:
            return self._has_novel_tracks(metadata)
        return False

    def _has_novel_tracks(self, metadata: Optional[dict]) -> bool:
        """Check if any track IDs are new relative to recent history."""
        if metadata is None:
            return False
        track_ids = metadata.get("track_ids", [])
        if not track_ids:
            return False
        seen: Set[str] = set()
        for window_set in self._recent_track_ids:
            seen.update(window_set)
        current = set(track_ids)
        return bool(current - seen)

    def _save_image(self, frame: np.ndarray, frame_name: str) -> None:
        """Save frame as JPEG to the images directory."""
        path = os.path.join(self._session_dir, "images", f"{frame_name}.jpg")  # type: ignore[arg-type]
        try:
            import cv2
            params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
            cv2.imwrite(path, frame, params)
        except ImportError:
            _save_ppm_fallback(path, frame)

    def _save_label(
        self,
        frame: np.ndarray,
        detections: List[Detection],
        frame_name: str,
    ) -> None:
        """Save YOLO-format annotation to the labels directory."""
        h, w = frame.shape[:2]
        path = os.path.join(self._session_dir, "labels", f"{frame_name}.txt")  # type: ignore[arg-type]
        lines = []
        for det in detections:
            cx, cy, bw, bh = _bbox_to_yolo(det.bbox, w, h)
            lines.append(f"{det.class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        with open(path, "w") as f:
            f.write("\n".join(lines))

    def _save_metadata(
        self,
        detections: List[Detection],
        metadata: Optional[dict],
        frame_name: str,
    ) -> None:
        """Save JSON sidecar with full detection metadata."""
        path = os.path.join(self._session_dir, "metadata", f"{frame_name}.json")  # type: ignore[arg-type]
        entries = []
        for det in detections:
            entry: Dict = {
                "class_name": det.class_name,
                "class_id": det.class_id,
                "confidence": det.confidence,
                "bbox_xyxy": list(det.bbox),
            }
            entries.append(entry)
        doc = {
            "frame": frame_name,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "detections": entries,
        }
        if metadata:
            doc["extra"] = metadata
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)

    def _update_stats(self, detections: List[Detection]) -> None:
        """Accumulate per-class counts."""
        self._detection_count += len(detections)
        for det in detections:
            name = det.class_name
            self._class_dist[name] = self._class_dist.get(name, 0) + 1

    def _update_novelty_window(self, metadata: Optional[dict]) -> None:
        """Maintain sliding window of recent track IDs."""
        track_ids: List[str] = []
        if metadata:
            track_ids = metadata.get("track_ids", [])
        self._recent_track_ids.append(set(track_ids))
        if len(self._recent_track_ids) > self._novelty_window:
            self._recent_track_ids.pop(0)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _create_session_dirs(session_dir: str) -> None:
    """Create the images/, labels/, metadata/ subdirectories."""
    for sub in ("images", "labels", "metadata"):
        os.makedirs(os.path.join(session_dir, sub), exist_ok=True)


def _write_dataset_yaml(
    session_dir: str,
    class_names: Dict[int, str],
) -> None:
    """Write a YOLO dataset.yaml config file."""
    lines = [
        f"path: {session_dir}",
        "train: images",
        "val: images",
        "",
        f"nc: {len(class_names)}",
        "names:",
    ]
    for idx in sorted(class_names.keys()):
        lines.append(f"  {idx}: {class_names[idx]}")
    path = os.path.join(session_dir, "dataset.yaml")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _write_session_json(
    session_dir: Optional[str],
    summary: SessionSummary,
) -> None:
    """Write session.json summary file."""
    if session_dir is None:
        return
    path = os.path.join(session_dir, "session.json")
    with open(path, "w") as f:
        json.dump(asdict(summary), f, indent=2)


def _save_ppm_fallback(path: str, frame: np.ndarray) -> None:
    """Save frame as PPM when cv2 is not available (test fallback)."""
    if frame.ndim == 2:
        h, w = frame.shape
        header = f"P5\n{w} {h}\n255\n".encode()
        data = frame.astype(np.uint8).tobytes()
    else:
        h, w = frame.shape[:2]
        header = f"P6\n{w} {h}\n255\n".encode()
        rgb = frame[:, :, ::-1] if frame.shape[2] == 3 else frame
        data = rgb.astype(np.uint8).tobytes()
    ppm_path = path.replace(".jpg", ".ppm")
    with open(ppm_path, "wb") as f:
        f.write(header + data)


# ---------------------------------------------------------------------------
# Dataset merger
# ---------------------------------------------------------------------------


def merge_sessions(
    session_dirs: List[str],
    output_dir: str,
    train_ratio: float = 0.8,
    class_names: Optional[Dict[int, str]] = None,
    deduplicate: bool = True,
) -> Dict:
    """Combine multiple recording sessions into one YOLO dataset.

    Creates train/ and val/ splits. Returns merge summary dict.
    """
    class_names = class_names or dict(DEFAULT_CLASS_NAMES)
    _create_merged_dirs(output_dir)
    frames = _collect_frames(session_dirs)
    if deduplicate:
        frames = _deduplicate_frames(frames)
    train_frames, val_frames = _split_train_val(frames, train_ratio)
    _copy_frames_to_split(train_frames, output_dir, "train")
    _copy_frames_to_split(val_frames, output_dir, "val")
    _write_merged_dataset_yaml(output_dir, class_names)
    summary = {
        "total_frames": len(frames),
        "train_frames": len(train_frames),
        "val_frames": len(val_frames),
        "deduplicated": deduplicate,
        "source_sessions": len(session_dirs),
    }
    summary_path = os.path.join(output_dir, "merge_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary


@dataclass
class _FrameEntry:
    """Internal representation of a collected frame for merging."""
    image_path: str
    label_path: str
    metadata_path: Optional[str]
    phash: str = ""


def _collect_frames(session_dirs: List[str]) -> List[_FrameEntry]:
    """Gather all frame entries from multiple session directories."""
    entries: List[_FrameEntry] = []
    for sdir in session_dirs:
        img_dir = os.path.join(sdir, "images")
        lbl_dir = os.path.join(sdir, "labels")
        meta_dir = os.path.join(sdir, "metadata")
        if not os.path.isdir(img_dir):
            continue
        for fname in sorted(os.listdir(img_dir)):
            stem, ext = os.path.splitext(fname)
            img_path = os.path.join(img_dir, fname)
            lbl_path = os.path.join(lbl_dir, f"{stem}.txt")
            meta_path = os.path.join(meta_dir, f"{stem}.json")
            if not os.path.isfile(lbl_path):
                lbl_path = ""
            if not os.path.isfile(meta_path):
                meta_path = None
            entries.append(_FrameEntry(
                image_path=img_path,
                label_path=lbl_path,
                metadata_path=meta_path,
            ))
    return entries


def _deduplicate_frames(frames: List[_FrameEntry]) -> List[_FrameEntry]:
    """Remove near-duplicate frames using perceptual hashing."""
    seen_hashes: Set[str] = set()
    unique: List[_FrameEntry] = []
    for entry in frames:
        phash = _compute_file_hash(entry.image_path)
        if phash in seen_hashes:
            continue
        seen_hashes.add(phash)
        entry.phash = phash
        unique.append(entry)
    return unique


def _compute_file_hash(path: str) -> str:
    """Compute a content hash of a file for deduplication."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _split_train_val(
    frames: List[_FrameEntry],
    train_ratio: float,
) -> Tuple[List[_FrameEntry], List[_FrameEntry]]:
    """Split frames into train and val sets deterministically."""
    n_train = int(len(frames) * train_ratio)
    return frames[:n_train], frames[n_train:]


def _copy_frames_to_split(
    frames: List[_FrameEntry],
    output_dir: str,
    split: str,
) -> None:
    """Copy frame images and labels into a split directory."""
    img_out = os.path.join(output_dir, split, "images")
    lbl_out = os.path.join(output_dir, split, "labels")
    for i, entry in enumerate(frames):
        new_name = f"frame_{i:06d}"
        _copy_file(entry.image_path, img_out, new_name)
        if entry.label_path:
            _copy_label(entry.label_path, lbl_out, new_name)


def _copy_file(src: str, dst_dir: str, new_stem: str) -> None:
    """Copy a file preserving extension."""
    _, ext = os.path.splitext(src)
    dst = os.path.join(dst_dir, f"{new_stem}{ext}")
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(fin.read())


def _copy_label(src: str, dst_dir: str, new_stem: str) -> None:
    """Copy a label file with .txt extension."""
    dst = os.path.join(dst_dir, f"{new_stem}.txt")
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(fin.read())


def _create_merged_dirs(output_dir: str) -> None:
    """Create train/val directory structure for merged dataset."""
    for split in ("train", "val"):
        for sub in ("images", "labels"):
            os.makedirs(os.path.join(output_dir, split, sub), exist_ok=True)


def _write_merged_dataset_yaml(
    output_dir: str,
    class_names: Dict[int, str],
) -> None:
    """Write dataset.yaml for the merged dataset with train/val paths."""
    lines = [
        f"path: {output_dir}",
        "train: train/images",
        "val: val/images",
        "",
        f"nc: {len(class_names)}",
        "names:",
    ]
    for idx in sorted(class_names.keys()):
        lines.append(f"  {idx}: {class_names[idx]}")
    path = os.path.join(output_dir, "dataset.yaml")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
