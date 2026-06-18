"""Evaluation module for OVERWATCH/BULWARK detection and tracking benchmarks."""

from evaluation.metrics import (
    DetectionResult,
    GroundTruth,
    iou,
    compute_ap,
    compute_ap_at_thresholds,
    compute_mota,
    compute_motp,
)
from evaluation.benchmark import BenchmarkRunner

__all__ = [
    "DetectionResult",
    "GroundTruth",
    "iou",
    "compute_ap",
    "compute_ap_at_thresholds",
    "compute_mota",
    "compute_motp",
    "BenchmarkRunner",
]
