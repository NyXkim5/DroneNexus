#!/usr/bin/env python3
"""
CLI script to fine-tune YOLOv11n on a drone detection dataset.

Usage:
    python -m scripts.train_drone_detector \
        --data backend/vision/drone_classes.yaml \
        --epochs 100 \
        --weights yolo11n.pt \
        --imgsz 640

Saves best weights to backend/models/drone_detector_best.pt and prints
mAP and training summary on completion.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

logger = logging.getLogger("overwatch.scripts.train_drone_detector")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for drone detector training."""
    parser = argparse.ArgumentParser(
        description="Fine-tune YOLOv11n on a drone detection dataset",
    )
    parser.add_argument(
        "--data",
        type=str,
        required=True,
        help="Path to YOLO dataset.yaml with class definitions and data paths",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=100,
        help="Number of training epochs (default: 100)",
    )
    parser.add_argument(
        "--weights",
        type=str,
        default="yolo11n.pt",
        help="Pretrained weights to start from (default: yolo11n.pt)",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Training image size in pixels (default: 640)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=16,
        help="Batch size (default: 16)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Device: 'cpu', '0', '0,1', etc. (default: auto)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for weights (default: backend/models/)",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="runs/drone_detect",
        help="Project directory for training artifacts",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="train",
        help="Experiment name within the project directory",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=20,
        help="Early stopping patience in epochs (default: 20)",
    )
    parser.add_argument(
        "--lr0",
        type=float,
        default=0.01,
        help="Initial learning rate (default: 0.01)",
    )
    parser.add_argument(
        "--freeze",
        type=int,
        default=0,
        help="Number of backbone layers to freeze (default: 0)",
    )
    return parser.parse_args(argv)


def resolve_output_dir(output_dir: str | None) -> Path:
    """Resolve and create the output directory for best weights."""
    if output_dir:
        path = Path(output_dir)
    else:
        path = Path(__file__).resolve().parent.parent / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def train(args: argparse.Namespace) -> dict:
    """Run YOLO training and return metrics summary.

    Returns a dict with keys: mAP50, mAP50_95, precision, recall,
    best_weights_path, total_time_s.
    """
    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
    except ImportError:
        logger.error("ultralytics package is required. Install with: pip install ultralytics")
        sys.exit(1)

    data_path = Path(args.data).resolve()
    if not data_path.exists():
        logger.error("Dataset config not found: %s", data_path)
        sys.exit(1)

    output_dir = resolve_output_dir(args.output_dir)

    logger.info("Loading pretrained weights: %s", args.weights)
    model = YOLO(args.weights)

    logger.info(
        "Starting training: data=%s epochs=%d imgsz=%d batch=%d",
        data_path, args.epochs, args.imgsz, args.batch,
    )

    start = time.monotonic()

    train_kwargs: dict = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "patience": args.patience,
        "lr0": args.lr0,
        "project": args.project,
        "name": args.name,
        "exist_ok": True,
        "verbose": True,
        "save": True,
        "plots": True,
    }

    if args.device is not None:
        train_kwargs["device"] = args.device

    if args.freeze > 0:
        train_kwargs["freeze"] = args.freeze

    results = model.train(**train_kwargs)

    elapsed = time.monotonic() - start

    # Copy best weights to output directory
    best_src = Path(args.project) / args.name / "weights" / "best.pt"
    best_dst = output_dir / "drone_detector_best.pt"

    if best_src.exists():
        shutil.copy2(best_src, best_dst)
        logger.info("Best weights saved to: %s", best_dst)
    else:
        logger.warning("best.pt not found at %s, checking alternative paths", best_src)
        # Try the results object for the save directory
        alt_path = Path(str(results.save_dir)) / "weights" / "best.pt" if hasattr(results, "save_dir") else None
        if alt_path and alt_path.exists():
            shutil.copy2(alt_path, best_dst)
            logger.info("Best weights saved to: %s", best_dst)
        else:
            logger.error("Could not locate best.pt after training")
            best_dst = Path("NOT_FOUND")

    # Extract metrics from the results object
    metrics = extract_metrics(results, best_dst, elapsed)
    return metrics


def extract_metrics(
    results: object,
    best_weights_path: Path,
    elapsed: float,
) -> dict:
    """Pull mAP and other metrics from the ultralytics results object."""
    metric_keys = {
        "mAP50": ["metrics/mAP50(B)", "mAP50"],
        "mAP50_95": ["metrics/mAP50-95(B)", "mAP50-95"],
        "precision": ["metrics/precision(B)", "precision"],
        "recall": ["metrics/recall(B)", "recall"],
    }

    summary: dict = {
        "best_weights_path": str(best_weights_path),
        "total_time_s": round(elapsed, 1),
    }

    results_dict: dict = {}
    if hasattr(results, "results_dict"):
        results_dict = results.results_dict

    for key, candidates in metric_keys.items():
        value = None
        for candidate in candidates:
            if candidate in results_dict:
                value = results_dict[candidate]
                break
        if value is None and hasattr(results, key.lower()):
            value = getattr(results, key.lower(), None)
        summary[key] = round(float(value), 4) if value is not None else None

    return summary


def print_summary(metrics: dict) -> None:
    """Print a formatted training summary to stdout."""
    print("\n" + "=" * 60)
    print("  OVERWATCH Drone Detector Training Summary")
    print("=" * 60)
    print(f"  Best weights : {metrics.get('best_weights_path', 'N/A')}")
    print(f"  Training time: {metrics.get('total_time_s', 0):.1f}s")
    print()
    print(f"  mAP@50       : {metrics.get('mAP50', 'N/A')}")
    print(f"  mAP@50:95    : {metrics.get('mAP50_95', 'N/A')}")
    print(f"  Precision    : {metrics.get('precision', 'N/A')}")
    print(f"  Recall       : {metrics.get('recall', 'N/A')}")
    print("=" * 60 + "\n")


def main(argv: list[str] | None = None) -> None:
    """Entry point for drone detector training CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    args = parse_args(argv)
    metrics = train(args)
    print_summary(metrics)


if __name__ == "__main__":
    main()
