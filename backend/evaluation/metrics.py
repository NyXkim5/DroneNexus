"""Standard detection and tracking metrics for OVERWATCH/BULWARK benchmarking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class DetectionResult:
    """One detection in one frame."""

    frame_id: int
    target_id: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    confidence: float


@dataclass
class GroundTruth:
    """One ground truth annotation in one frame."""

    frame_id: int
    target_id: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h


def iou(box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]) -> float:
    """Compute intersection over union of two bboxes in x, y, w, h format."""
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b

    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax + aw, bx + bw)
    inter_y2 = min(ay + ah, by + bh)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = aw * ah
    area_b = bw * bh
    union_area = area_a + area_b - inter_area

    if union_area <= 0:
        return 0.0

    return float(inter_area / union_area)


def compute_ap(
    detections: List[DetectionResult],
    ground_truths: List[GroundTruth],
    iou_threshold: float = 0.5,
) -> float:
    """Compute Average Precision at a given IoU threshold.

    Steps:
    1. Sort detections by confidence descending.
    2. For each detection, find best matching GT (highest IoU above threshold).
    3. Mark as TP if match found and GT not already matched, else FP.
    4. Compute precision-recall curve.
    5. Compute AP as area under PR curve (11-point interpolation).
    """
    if not ground_truths:
        return 0.0

    if not detections:
        return 0.0

    # Group GTs by frame for efficient lookup.
    gts_by_frame: Dict[int, List[GroundTruth]] = {}
    for gt in ground_truths:
        gts_by_frame.setdefault(gt.frame_id, []).append(gt)

    # Track which GTs have been matched to prevent double-counting.
    matched_gts: set[Tuple[int, str]] = set()

    sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)

    tp_list: List[int] = []
    fp_list: List[int] = []

    for det in sorted_dets:
        frame_gts = gts_by_frame.get(det.frame_id, [])
        best_iou = 0.0
        best_gt: Optional[GroundTruth] = None

        for gt in frame_gts:
            score = iou(det.bbox, gt.bbox)
            if score > best_iou:
                best_iou = score
                best_gt = gt

        if best_iou >= iou_threshold and best_gt is not None:
            key = (best_gt.frame_id, best_gt.target_id)
            if key not in matched_gts:
                matched_gts.add(key)
                tp_list.append(1)
                fp_list.append(0)
            else:
                tp_list.append(0)
                fp_list.append(1)
        else:
            tp_list.append(0)
            fp_list.append(1)

    tp_cumsum = np.cumsum(tp_list).astype(float)
    fp_cumsum = np.cumsum(fp_list).astype(float)

    total_gt = float(len(ground_truths))
    recalls = tp_cumsum / total_gt
    precisions = tp_cumsum / (tp_cumsum + fp_cumsum)

    # 11-point interpolation.
    ap = 0.0
    for recall_threshold in np.linspace(0.0, 1.0, 11):
        mask = recalls >= recall_threshold
        if mask.any():
            ap += float(np.max(precisions[mask]))

    return ap / 11.0


def compute_ap_at_thresholds(
    detections: List[DetectionResult],
    ground_truths: List[GroundTruth],
    thresholds: Optional[List[float]] = None,
) -> Dict[str, float]:
    """Compute AP@50, AP@75, and mAP (AP@50:95:5)."""
    ap50 = compute_ap(detections, ground_truths, iou_threshold=0.5)
    ap75 = compute_ap(detections, ground_truths, iou_threshold=0.75)

    coco_thresholds = thresholds if thresholds is not None else list(np.arange(0.5, 1.0, 0.05))
    map_val = float(
        np.mean([compute_ap(detections, ground_truths, iou_threshold=t) for t in coco_thresholds])
    )

    return {
        "AP@50": ap50,
        "AP@75": ap75,
        "mAP": map_val,
    }


def compute_mota(
    detections: List[DetectionResult],
    ground_truths: List[GroundTruth],
    iou_threshold: float = 0.5,
) -> Dict[str, float]:
    """Multi-Object Tracking Accuracy.

    Returns: mota, misses, false_positives, mismatches, total_gt.

    MOTA = 1 - (misses + false_positives + mismatches) / total_gt.
    Per-frame: match detections to GT by IoU, count misses/FP/ID switches.
    """
    # Collect all frame IDs.
    frame_ids: set[int] = set()
    for gt in ground_truths:
        frame_ids.add(gt.frame_id)
    for det in detections:
        frame_ids.add(det.frame_id)

    gts_by_frame: Dict[int, List[GroundTruth]] = {}
    for gt in ground_truths:
        gts_by_frame.setdefault(gt.frame_id, []).append(gt)

    dets_by_frame: Dict[int, List[DetectionResult]] = {}
    for det in detections:
        dets_by_frame.setdefault(det.frame_id, []).append(det)

    total_gt = len(ground_truths)
    misses = 0
    false_positives = 0
    mismatches = 0

    # Track last known assignment: gt_id -> det_id.
    prev_assignment: Dict[str, str] = {}

    for frame_id in sorted(frame_ids):
        frame_gts = gts_by_frame.get(frame_id, [])
        frame_dets = dets_by_frame.get(frame_id, [])

        # Build IoU matrix: rows = GTs, cols = detections.
        n_gt = len(frame_gts)
        n_det = len(frame_dets)

        matched_gt_indices: set[int] = set()
        matched_det_indices: set[int] = set()
        current_assignment: Dict[str, str] = {}

        if n_gt > 0 and n_det > 0:
            iou_matrix = np.zeros((n_gt, n_det), dtype=float)
            for gi, gt in enumerate(frame_gts):
                for di, det in enumerate(frame_dets):
                    iou_matrix[gi, di] = iou(gt.bbox, det.bbox)

            # Greedy matching: repeatedly pick the highest IoU pair.
            iou_work = iou_matrix.copy()
            while True:
                max_val = iou_work.max()
                if max_val < iou_threshold:
                    break
                gi, di = np.unravel_index(iou_work.argmax(), iou_work.shape)
                gi = int(gi)
                di = int(di)
                matched_gt_indices.add(gi)
                matched_det_indices.add(di)
                current_assignment[frame_gts[gi].target_id] = frame_dets[di].target_id
                iou_work[gi, :] = -1.0
                iou_work[:, di] = -1.0

        # Misses: GTs with no matching detection.
        misses += n_gt - len(matched_gt_indices)

        # False positives: detections with no matching GT.
        false_positives += n_det - len(matched_det_indices)

        # Mismatches: GT matched to a different detection ID than last frame.
        for gt_id, det_id in current_assignment.items():
            prev_det_id = prev_assignment.get(gt_id)
            if prev_det_id is not None and prev_det_id != det_id:
                mismatches += 1

        prev_assignment = current_assignment

    if total_gt == 0:
        mota = 0.0
    else:
        mota = 1.0 - (misses + false_positives + mismatches) / total_gt

    return {
        "mota": mota,
        "misses": float(misses),
        "false_positives": float(false_positives),
        "mismatches": float(mismatches),
        "total_gt": float(total_gt),
    }


def compute_motp(
    detections: List[DetectionResult],
    ground_truths: List[GroundTruth],
    iou_threshold: float = 0.5,
) -> float:
    """Multi-Object Tracking Precision: average IoU of matched pairs."""
    gts_by_frame: Dict[int, List[GroundTruth]] = {}
    for gt in ground_truths:
        gts_by_frame.setdefault(gt.frame_id, []).append(gt)

    dets_by_frame: Dict[int, List[DetectionResult]] = {}
    for det in detections:
        dets_by_frame.setdefault(det.frame_id, []).append(det)

    total_iou = 0.0
    total_matches = 0

    all_frame_ids = set(gts_by_frame.keys()) | set(dets_by_frame.keys())

    for frame_id in all_frame_ids:
        frame_gts = gts_by_frame.get(frame_id, [])
        frame_dets = dets_by_frame.get(frame_id, [])

        n_gt = len(frame_gts)
        n_det = len(frame_dets)

        if n_gt == 0 or n_det == 0:
            continue

        iou_matrix = np.zeros((n_gt, n_det), dtype=float)
        for gi, gt in enumerate(frame_gts):
            for di, det in enumerate(frame_dets):
                iou_matrix[gi, di] = iou(gt.bbox, det.bbox)

        iou_work = iou_matrix.copy()
        while True:
            max_val = iou_work.max()
            if max_val < iou_threshold:
                break
            gi, di = np.unravel_index(iou_work.argmax(), iou_work.shape)
            total_iou += iou_work[gi, di]
            total_matches += 1
            iou_work[int(gi), :] = -1.0
            iou_work[:, int(di)] = -1.0

    if total_matches == 0:
        return 0.0

    return total_iou / total_matches
