"""Tests for the evaluation module: metrics and BenchmarkRunner."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.metrics import (
    DetectionResult,
    GroundTruth,
    iou,
    compute_ap,
    compute_mota,
    compute_motp,
)
from evaluation.benchmark import BenchmarkRunner


# ---------------------------------------------------------------------------
# IoU tests
# ---------------------------------------------------------------------------


def test_iou_perfect_overlap() -> None:
    box = (10, 10, 50, 50)
    assert iou(box, box) == pytest.approx(1.0)


def test_iou_no_overlap() -> None:
    box_a = (0, 0, 10, 10)
    box_b = (100, 100, 10, 10)
    assert iou(box_a, box_b) == pytest.approx(0.0)


def test_iou_partial() -> None:
    # box_a: x=0, y=0, w=10, h=10 -> area 100, region [0..10, 0..10]
    # box_b: x=5, y=5, w=10, h=10 -> area 100, region [5..15, 5..15]
    # intersection: [5..10, 5..10] -> 5x5 = 25
    # union: 100 + 100 - 25 = 175
    box_a = (0, 0, 10, 10)
    box_b = (5, 5, 10, 10)
    expected = 25.0 / 175.0
    assert iou(box_a, box_b) == pytest.approx(expected, rel=1e-5)


# ---------------------------------------------------------------------------
# AP tests
# ---------------------------------------------------------------------------


def _make_dets(frame_id: int, target_ids: list[str], bbox: tuple, conf: float = 1.0) -> list[DetectionResult]:
    return [
        DetectionResult(frame_id=frame_id, target_id=tid, bbox=bbox, confidence=conf)
        for tid in target_ids
    ]


def _make_gts(frame_id: int, target_ids: list[str], bbox: tuple) -> list[GroundTruth]:
    return [
        GroundTruth(frame_id=frame_id, target_id=tid, bbox=bbox)
        for tid in target_ids
    ]


def test_ap_perfect_detections() -> None:
    # Two GTs with distinct bboxes, each matched by exactly one detection.
    dets = [
        DetectionResult(frame_id=0, target_id="t1", bbox=(0, 0, 50, 50), confidence=1.0),
        DetectionResult(frame_id=0, target_id="t2", bbox=(100, 100, 50, 50), confidence=0.9),
    ]
    gts = [
        GroundTruth(frame_id=0, target_id="t1", bbox=(0, 0, 50, 50)),
        GroundTruth(frame_id=0, target_id="t2", bbox=(100, 100, 50, 50)),
    ]
    result = compute_ap(dets, gts)
    assert result == pytest.approx(1.0, rel=1e-5)


def test_ap_no_detections() -> None:
    gts = _make_gts(0, ["t1", "t2"], (0, 0, 50, 50))
    result = compute_ap([], gts)
    assert result == pytest.approx(0.0)


def test_ap_with_false_positives() -> None:
    bbox = (0, 0, 50, 50)
    far_bbox = (500, 500, 50, 50)
    # FP at higher confidence is processed first, reducing precision before the
    # TP is seen. This guarantees AP < 1.0.
    dets = [
        DetectionResult(frame_id=0, target_id="fp", bbox=far_bbox, confidence=0.95),
        DetectionResult(frame_id=0, target_id="t1", bbox=bbox, confidence=0.8),
    ]
    gts = _make_gts(0, ["t1"], bbox)
    result = compute_ap(dets, gts)
    assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# MOTA tests
# ---------------------------------------------------------------------------


def test_mota_perfect() -> None:
    bbox = (0, 0, 50, 50)
    dets = [
        DetectionResult(frame_id=f, target_id="t1", bbox=bbox, confidence=1.0)
        for f in range(5)
    ]
    gts = [
        GroundTruth(frame_id=f, target_id="t1", bbox=bbox)
        for f in range(5)
    ]
    result = compute_mota(dets, gts)
    assert result["mota"] == pytest.approx(1.0, rel=1e-5)
    assert result["misses"] == 0
    assert result["false_positives"] == 0
    assert result["mismatches"] == 0


def test_mota_with_misses() -> None:
    bbox = (0, 0, 50, 50)
    # GT exists for 5 frames, but only 2 frames have detections.
    dets = [
        DetectionResult(frame_id=f, target_id="t1", bbox=bbox, confidence=1.0)
        for f in range(2)
    ]
    gts = [
        GroundTruth(frame_id=f, target_id="t1", bbox=bbox)
        for f in range(5)
    ]
    result = compute_mota(dets, gts)
    assert result["mota"] < 1.0
    assert result["misses"] == 3


# ---------------------------------------------------------------------------
# MOTP tests
# ---------------------------------------------------------------------------


def test_motp_perfect() -> None:
    bbox = (0, 0, 50, 50)
    dets = [
        DetectionResult(frame_id=f, target_id="t1", bbox=bbox, confidence=1.0)
        for f in range(5)
    ]
    gts = [
        GroundTruth(frame_id=f, target_id="t1", bbox=bbox)
        for f in range(5)
    ]
    result = compute_motp(dets, gts)
    assert result == pytest.approx(1.0, rel=1e-5)


# ---------------------------------------------------------------------------
# BenchmarkRunner integration test
# ---------------------------------------------------------------------------


def test_benchmark_runner_produces_metrics() -> None:
    runner = BenchmarkRunner(
        scenario_name="ground_strike_convoy",
        n_frames=20,
        noise_sigma_m=0.0,
        false_positive_rate=0.0,
        seed=42,
    )
    metrics = runner.run()

    assert "AP@50" in metrics
    assert "AP@75" in metrics
    assert "mAP" in metrics
    assert "MOTA" in metrics
    assert "MOTP" in metrics

    # With no noise and no FPs every metric should be near-perfect.
    assert metrics["AP@50"] > 0.0
    assert metrics["MOTA"] > 0.0
    assert metrics["MOTP"] > 0.0
