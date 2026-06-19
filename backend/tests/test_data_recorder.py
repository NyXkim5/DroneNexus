"""
Tests for the detection data recording pipeline.

Covers YOLO annotation format, session lifecycle, auto-labeling modes,
summary statistics, and multi-session dataset merging.
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np
import pytest

from vision.data_recorder import (
    DEFAULT_CLASS_NAMES,
    DetectionRecorder,
    LabelingMode,
    SessionSummary,
    _bbox_to_yolo,
    _perceptual_hash,
    merge_sessions,
)
from vision.tensorrt_detector import Detection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(w: int = 640, h: int = 480) -> np.ndarray:
    """Create a synthetic BGR frame."""
    return np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)


def _make_detection(
    class_name: str = "uas",
    confidence: float = 0.9,
    bbox: tuple = (100.0, 100.0, 200.0, 200.0),
    class_id: int = 0,
) -> Detection:
    return Detection(
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        class_id=class_id,
    )


# ---------------------------------------------------------------------------
# YOLO annotation format
# ---------------------------------------------------------------------------


class TestBboxToYolo:
    def test_center_of_frame(self) -> None:
        cx, cy, w, h = _bbox_to_yolo((0.0, 0.0, 640.0, 480.0), 640, 480)
        assert abs(cx - 0.5) < 1e-6
        assert abs(cy - 0.5) < 1e-6
        assert abs(w - 1.0) < 1e-6
        assert abs(h - 1.0) < 1e-6

    def test_quarter_box(self) -> None:
        cx, cy, w, h = _bbox_to_yolo((0.0, 0.0, 320.0, 240.0), 640, 480)
        assert abs(cx - 0.25) < 1e-6
        assert abs(cy - 0.25) < 1e-6
        assert abs(w - 0.5) < 1e-6
        assert abs(h - 0.5) < 1e-6

    def test_small_box_normalized(self) -> None:
        cx, cy, w, h = _bbox_to_yolo((100.0, 100.0, 200.0, 200.0), 640, 480)
        assert 0.0 <= cx <= 1.0
        assert 0.0 <= cy <= 1.0
        assert 0.0 <= w <= 1.0
        assert 0.0 <= h <= 1.0
        expected_cx = 150.0 / 640.0
        expected_cy = 150.0 / 480.0
        assert abs(cx - expected_cx) < 1e-6
        assert abs(cy - expected_cy) < 1e-6

    def test_values_clamped(self) -> None:
        cx, cy, w, h = _bbox_to_yolo((-10.0, -10.0, 700.0, 500.0), 640, 480)
        assert 0.0 <= cx <= 1.0
        assert 0.0 <= cy <= 1.0
        assert 0.0 <= w <= 1.0
        assert 0.0 <= h <= 1.0


# ---------------------------------------------------------------------------
# Session directory creation
# ---------------------------------------------------------------------------


class TestSessionDirectoryCreation:
    def test_creates_directory_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "test_session")
            assert os.path.isdir(os.path.join(session_dir, "images"))
            assert os.path.isdir(os.path.join(session_dir, "labels"))
            assert os.path.isdir(os.path.join(session_dir, "metadata"))
            assert os.path.isfile(os.path.join(session_dir, "dataset.yaml"))
            recorder.stop_session()

    def test_auto_generates_session_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir)
            basename = os.path.basename(session_dir)
            assert basename.startswith("session_")
            recorder.stop_session()

    def test_dataset_yaml_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "yaml_test")
            yaml_path = os.path.join(session_dir, "dataset.yaml")
            with open(yaml_path) as f:
                content = f.read()
            assert "nc: 5" in content
            assert "uas" in content
            assert "train: images" in content
            recorder.stop_session()


# ---------------------------------------------------------------------------
# Frame recording
# ---------------------------------------------------------------------------


class TestFrameRecording:
    def test_saves_image_label_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "rec_test")
            frame = _make_frame()
            dets = [_make_detection()]
            recorded = recorder.record_frame(frame, dets)
            assert recorded is True
            img_files = os.listdir(os.path.join(session_dir, "images"))
            lbl_files = os.listdir(os.path.join(session_dir, "labels"))
            meta_files = os.listdir(os.path.join(session_dir, "metadata"))
            assert len(img_files) == 1
            assert len(lbl_files) == 1
            assert len(meta_files) == 1
            recorder.stop_session()

    def test_label_format_is_yolo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "fmt_test")
            frame = _make_frame(640, 480)
            dets = [
                _make_detection(class_id=0, bbox=(100.0, 100.0, 200.0, 200.0)),
                _make_detection(class_name="bird", class_id=4, bbox=(300.0, 300.0, 400.0, 400.0)),
            ]
            recorder.record_frame(frame, dets)
            lbl_path = os.path.join(session_dir, "labels", "frame_000001.txt")
            with open(lbl_path) as f:
                lines = f.read().strip().split("\n")
            assert len(lines) == 2
            parts = lines[0].split()
            assert len(parts) == 5
            assert parts[0] == "0"
            for val in parts[1:]:
                fv = float(val)
                assert 0.0 <= fv <= 1.0
            recorder.stop_session()

    def test_metadata_json_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "meta_test")
            frame = _make_frame()
            extra = {"source": "test_camera", "track_ids": ["t1"]}
            recorder.record_frame(frame, [_make_detection()], metadata=extra)
            meta_path = os.path.join(session_dir, "metadata", "frame_000001.json")
            with open(meta_path) as f:
                doc = json.load(f)
            assert "detections" in doc
            assert len(doc["detections"]) == 1
            assert doc["detections"][0]["class_name"] == "uas"
            assert "confidence" in doc["detections"][0]
            assert "bbox_xyxy" in doc["detections"][0]
            assert doc["extra"]["source"] == "test_camera"
            recorder.stop_session()

    def test_skips_empty_detections_in_auto_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(labeling_mode=LabelingMode.AUTO_ALL)
            recorder.start_session(tmpdir, "skip_test")
            frame = _make_frame()
            recorded = recorder.record_frame(frame, [])
            assert recorded is False
            assert recorder.frame_count == 0
            recorder.stop_session()

    def test_raises_without_active_session(self) -> None:
        recorder = DetectionRecorder()
        with pytest.raises(RuntimeError):
            recorder.record_frame(_make_frame(), [_make_detection()])

    def test_stop_raises_without_active_session(self) -> None:
        recorder = DetectionRecorder()
        with pytest.raises(RuntimeError):
            recorder.stop_session()


# ---------------------------------------------------------------------------
# SessionSummary statistics
# ---------------------------------------------------------------------------


class TestSessionSummary:
    def test_summary_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            recorder.start_session(tmpdir, "summary_test")
            for _ in range(3):
                recorder.record_frame(
                    _make_frame(),
                    [_make_detection(), _make_detection(class_name="bird", class_id=4)],
                )
            recorder.record_frame(
                _make_frame(),
                [_make_detection()],
            )
            summary = recorder.stop_session()
            assert summary.session_id == "summary_test"
            assert summary.total_frames == 4
            assert summary.total_detections == 7
            assert summary.class_distribution["uas"] == 4
            assert summary.class_distribution["bird"] == 3
            assert abs(summary.avg_detections_per_frame - 1.75) < 0.01

    def test_summary_written_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            session_dir = recorder.start_session(tmpdir, "file_test")
            recorder.record_frame(_make_frame(), [_make_detection()])
            recorder.stop_session()
            session_path = os.path.join(session_dir, "session.json")
            assert os.path.isfile(session_path)
            with open(session_path) as f:
                data = json.load(f)
            assert data["total_frames"] == 1
            assert data["total_detections"] == 1

    def test_empty_session_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder()
            recorder.start_session(tmpdir, "empty_test")
            summary = recorder.stop_session()
            assert summary.total_frames == 0
            assert summary.total_detections == 0
            assert summary.avg_detections_per_frame == 0.0


# ---------------------------------------------------------------------------
# Auto-labeling mode filtering
# ---------------------------------------------------------------------------


class TestLabelingModes:
    def test_auto_all_records_when_detections_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(labeling_mode=LabelingMode.AUTO_ALL)
            recorder.start_session(tmpdir, "aa_test")
            assert recorder.record_frame(_make_frame(), [_make_detection()])
            assert not recorder.record_frame(_make_frame(), [])
            recorder.stop_session()

    def test_auto_confident_filters_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(
                labeling_mode=LabelingMode.AUTO_CONFIDENT,
                confidence_threshold=0.7,
            )
            recorder.start_session(tmpdir, "ac_test")
            low = _make_detection(confidence=0.5)
            high = _make_detection(confidence=0.85)
            assert not recorder.record_frame(_make_frame(), [low])
            assert recorder.record_frame(_make_frame(), [high])
            assert recorder.record_frame(_make_frame(), [low, high])
            recorder.stop_session()

    def test_auto_novel_records_new_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(
                labeling_mode=LabelingMode.AUTO_NOVEL,
                novelty_window=5,
            )
            recorder.start_session(tmpdir, "an_test")
            det = _make_detection()
            assert recorder.record_frame(
                _make_frame(), [det], metadata={"track_ids": ["t1"]},
            )
            assert not recorder.record_frame(
                _make_frame(), [det], metadata={"track_ids": ["t1"]},
            )
            assert recorder.record_frame(
                _make_frame(), [det], metadata={"track_ids": ["t2"]},
            )
            recorder.stop_session()

    def test_auto_novel_skips_without_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(
                labeling_mode=LabelingMode.AUTO_NOVEL,
            )
            recorder.start_session(tmpdir, "an_nometa")
            assert not recorder.record_frame(_make_frame(), [_make_detection()])
            recorder.stop_session()

    def test_manual_requires_record_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DetectionRecorder(labeling_mode=LabelingMode.MANUAL)
            recorder.start_session(tmpdir, "man_test")
            det = _make_detection()
            assert not recorder.record_frame(_make_frame(), [det])
            assert not recorder.record_frame(
                _make_frame(), [det], metadata={"source": "cam"},
            )
            assert recorder.record_frame(
                _make_frame(), [det], metadata={"record": True},
            )
            recorder.stop_session()


# ---------------------------------------------------------------------------
# Dataset merger with train/val split
# ---------------------------------------------------------------------------


class TestDatasetMerger:
    def _create_session(
        self,
        base_dir: str,
        name: str,
        n_frames: int = 5,
    ) -> str:
        """Helper to create a populated session directory."""
        recorder = DetectionRecorder()
        session_dir = recorder.start_session(base_dir, name)
        for i in range(n_frames):
            frame = _make_frame()
            dets = [_make_detection()]
            recorder.record_frame(frame, dets)
        recorder.stop_session()
        return session_dir

    def test_merge_creates_train_val_splits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._create_session(tmpdir, "s1", n_frames=8)
            s2 = self._create_session(tmpdir, "s2", n_frames=2)
            merged_dir = os.path.join(tmpdir, "merged")
            result = merge_sessions([s1, s2], merged_dir)
            assert result["total_frames"] == 10
            assert result["train_frames"] == 8
            assert result["val_frames"] == 2
            assert os.path.isdir(os.path.join(merged_dir, "train", "images"))
            assert os.path.isdir(os.path.join(merged_dir, "train", "labels"))
            assert os.path.isdir(os.path.join(merged_dir, "val", "images"))
            assert os.path.isdir(os.path.join(merged_dir, "val", "labels"))

    def test_merge_dataset_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._create_session(tmpdir, "s1", n_frames=3)
            merged_dir = os.path.join(tmpdir, "merged")
            merge_sessions([s1], merged_dir)
            yaml_path = os.path.join(merged_dir, "dataset.yaml")
            assert os.path.isfile(yaml_path)
            with open(yaml_path) as f:
                content = f.read()
            assert "train: train/images" in content
            assert "val: val/images" in content
            assert "nc: 5" in content

    def test_merge_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._create_session(tmpdir, "s1", n_frames=4)
            merged_dir = os.path.join(tmpdir, "merged")
            merge_sessions([s1], merged_dir)
            summary_path = os.path.join(merged_dir, "merge_summary.json")
            assert os.path.isfile(summary_path)
            with open(summary_path) as f:
                data = json.load(f)
            assert data["source_sessions"] == 1

    def test_deduplication_removes_identical_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._create_session(tmpdir, "s1", n_frames=3)
            img_dir = os.path.join(s1, "images")
            files = sorted(os.listdir(img_dir))
            if len(files) >= 2:
                src = os.path.join(img_dir, files[0])
                dst = os.path.join(img_dir, files[1])
                with open(src, "rb") as f:
                    data = f.read()
                with open(dst, "wb") as f:
                    f.write(data)
            merged_dir = os.path.join(tmpdir, "merged")
            result = merge_sessions([s1], merged_dir, deduplicate=True)
            assert result["total_frames"] == 2

    def test_merge_without_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            s1 = self._create_session(tmpdir, "s1", n_frames=3)
            img_dir = os.path.join(s1, "images")
            files = sorted(os.listdir(img_dir))
            if len(files) >= 2:
                src = os.path.join(img_dir, files[0])
                dst = os.path.join(img_dir, files[1])
                with open(src, "rb") as f:
                    data = f.read()
                with open(dst, "wb") as f:
                    f.write(data)
            merged_dir = os.path.join(tmpdir, "merged")
            result = merge_sessions([s1], merged_dir, deduplicate=False)
            assert result["total_frames"] == 3


# ---------------------------------------------------------------------------
# Perceptual hash
# ---------------------------------------------------------------------------


class TestPerceptualHash:
    def test_identical_frames_same_hash(self) -> None:
        frame = _make_frame()
        h1 = _perceptual_hash(frame)
        h2 = _perceptual_hash(frame)
        assert h1 == h2

    def test_different_frames_different_hash(self) -> None:
        f1 = np.zeros((480, 640, 3), dtype=np.uint8)
        f1[:240, :, :] = 255  # top half white, bottom half black
        f2 = np.zeros((480, 640, 3), dtype=np.uint8)
        f2[:, :320, :] = 255  # left half white, right half black
        h1 = _perceptual_hash(f1)
        h2 = _perceptual_hash(f2)
        assert h1 != h2

    def test_grayscale_input(self) -> None:
        frame = np.random.randint(0, 255, (480, 640), dtype=np.uint8)
        h = _perceptual_hash(frame)
        assert isinstance(h, str)
        assert len(h) > 0
