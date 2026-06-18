"""Benchmark runner for OVERWATCH/BULWARK detection and tracking evaluation."""

from __future__ import annotations

import sys
import os

# Ensure the backend root is on sys.path so vision imports resolve.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from typing import Dict

import numpy as np

from vision.detector import SimDetector
from vision.feed_source import SimFeedSource
from vision.scenarios import load_target_scenario

from evaluation.metrics import (
    DetectionResult,
    GroundTruth,
    compute_ap_at_thresholds,
    compute_mota,
    compute_motp,
)


class BenchmarkRunner:
    """Run SimDetector against SimFeedSource scenarios and compute metrics."""

    def __init__(
        self,
        scenario_name: str,
        n_frames: int = 100,
        noise_sigma_m: float = 2.0,
        false_positive_rate: float = 0.02,
        seed: int = 0,
    ) -> None:
        """Load scenario, create detector and feed source."""
        self._scenario = load_target_scenario(scenario_name)
        self._n_frames = n_frames
        self._feed = SimFeedSource(placements=self._scenario.placements)
        self._detector = SimDetector(
            placements=self._scenario.placements,
            noise_sigma_m=noise_sigma_m,
            false_positive_rate=false_positive_rate,
            seed=seed,
        )
        self._metrics: Dict[str, float] = {}

    def run(self) -> Dict[str, float]:
        """Run the benchmark and return all metrics.

        For each frame:
        1. Get ground truth positions from scenario placements.
        2. Run SimDetector.detect().
        3. Collect DetectionResults and GroundTruths.
        Then compute: AP@50, AP@75, mAP, MOTA, MOTP.
        """
        all_detections: list[DetectionResult] = []
        all_ground_truths: list[GroundTruth] = []

        # Pre-build GT bboxes once per placement. The SimDetector uses fixed
        # placements with a deterministic bounding-box mapping, so we can derive
        # GT bboxes from the same _BB_SIZES table used by the detector.
        from vision.detector import _BB_SIZES

        # Default resolution matches SimFeedSource default (1280x720).
        frame_w, frame_h = 1280, 720
        scene_scale = min(frame_w, frame_h) / 2000.0
        cx, cy = frame_w // 2, frame_h // 2

        gt_bboxes: Dict[str, tuple] = {}
        for placement in self._scenario.placements:
            bb_w, bb_h = _BB_SIZES.get(placement.target_type, (48, 32))
            px = int(cx + placement.position[0] * scene_scale)
            py = int(cy - placement.position[1] * scene_scale)
            bb_x = max(0, min(px - bb_w // 2, frame_w - bb_w))
            bb_y = max(0, min(py - bb_h // 2, frame_h - bb_h))
            gt_bboxes[placement.id] = (bb_x, bb_y, bb_w, bb_h)

        for frame_id in range(self._n_frames):
            frame, timestamp = self._feed.next_frame()

            # Build ground truths for this frame.
            for placement in self._scenario.placements:
                all_ground_truths.append(
                    GroundTruth(
                        frame_id=frame_id,
                        target_id=placement.id,
                        bbox=gt_bboxes[placement.id],
                    )
                )

            # Run detector and collect results.
            visual_targets = self._detector.detect(frame, timestamp=timestamp)
            for target in visual_targets:
                bb = target.bounding_box
                all_detections.append(
                    DetectionResult(
                        frame_id=frame_id,
                        target_id=target.id,
                        bbox=(bb.x, bb.y, bb.width, bb.height),
                        confidence=target.confidence,
                    )
                )

        ap_metrics = compute_ap_at_thresholds(all_detections, all_ground_truths)
        mota_metrics = compute_mota(all_detections, all_ground_truths)
        motp = compute_motp(all_detections, all_ground_truths)

        self._metrics = {
            **ap_metrics,
            "MOTA": mota_metrics["mota"],
            "misses": mota_metrics["misses"],
            "false_positives": mota_metrics["false_positives"],
            "mismatches": mota_metrics["mismatches"],
            "total_gt": mota_metrics["total_gt"],
            "MOTP": motp,
        }

        return self._metrics

    def report(self) -> str:
        """Pretty-print metrics table."""
        if not self._metrics:
            self.run()

        lines = [
            "",
            f"  Benchmark: {self._scenario.name}",
            f"  Frames:    {self._n_frames}",
            "",
            "  +-----------------+----------+",
            "  | Metric          |    Value |",
            "  +-----------------+----------+",
        ]

        display_keys = ["AP@50", "AP@75", "mAP", "MOTA", "MOTP",
                        "misses", "false_positives", "mismatches", "total_gt"]
        for key in display_keys:
            val = self._metrics.get(key, float("nan"))
            if key in ("misses", "false_positives", "mismatches", "total_gt"):
                lines.append(f"  | {key:<15} | {int(val):>8} |")
            else:
                lines.append(f"  | {key:<15} | {val:>8.4f} |")

        lines.append("  +-----------------+----------+")
        lines.append("")

        return "\n".join(lines)
