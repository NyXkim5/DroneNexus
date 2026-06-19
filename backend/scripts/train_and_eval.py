#!/usr/bin/env python3
"""
End-to-end drone detector training and evaluation pipeline.

Prepares a dataset (if needed), trains YOLOv11n, evaluates on the validation
split, saves results, and compares against any previous run.

Run from cwd=backend:
    python -m scripts.train_and_eval \
        --dataset-dir /data/unified_drone_dataset \
        --epochs 50 \
        --batch 16 \
        --imgsz 640

For a full pipeline including dataset preparation:
    python -m scripts.train_and_eval \
        --sources seraphim:/data/seraphim,anti_uav:/data/anti_uav \
        --dataset-dir /tmp/drone_dataset \
        --epochs 50
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("overwatch.scripts.train_and_eval")

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
BEST_WEIGHTS_PATH = MODELS_DIR / "drone_detector_best.pt"
EVAL_RESULTS_PATH = MODELS_DIR / "drone_eval_results.json"

METRIC_KEYS = ("mAP50", "mAP50_95", "precision", "recall")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all training/eval flags."""
    p = argparse.ArgumentParser(
        description="End-to-end drone detector training and evaluation",
    )
    p.add_argument("--dataset-dir", type=str, required=True,
                   help="Path to unified YOLO dataset (or output for --sources)")
    p.add_argument("--sources", type=str, default=None,
                   help="Optional: prepare dataset first. Format: name:/path,name2:/path2")
    p.add_argument("--epochs", type=int, default=50,
                   help="Number of training epochs (default: 50)")
    p.add_argument("--batch", type=int, default=16,
                   help="Batch size (default: 16)")
    p.add_argument("--imgsz", type=int, default=640,
                   help="Training image size in pixels (default: 640)")
    p.add_argument("--weights", type=str, default="yolo11n.pt",
                   help="Pretrained weights (default: yolo11n.pt)")
    p.add_argument("--device", type=str, default=None,
                   help="Device: 'cpu', '0', 'mps', etc. (default: auto)")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for dataset preparation (default: 42)")
    p.add_argument("--skip-train", action="store_true",
                   help="Skip training, only run evaluation on existing weights")
    p.add_argument("--project", type=str, default="runs/drone_detect",
                   help="Project directory for training artifacts")
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for end-to-end training pipeline."""
    return _build_arg_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Dataset preparation
# ---------------------------------------------------------------------------


def prepare_dataset_if_needed(args: argparse.Namespace) -> Path:
    """Run dataset preparation if --sources is provided. Returns dataset.yaml path."""
    dataset_dir = Path(args.dataset_dir)
    yaml_path = dataset_dir / "dataset.yaml"

    if args.sources:
        logger.info("Preparing dataset from sources: %s", args.sources)
        from scripts.prepare_drone_dataset import main as prepare_main
        prepare_main([
            "--sources", args.sources,
            "--output", str(dataset_dir),
            "--seed", str(args.seed),
        ])

    if not yaml_path.exists():
        logger.error("dataset.yaml not found at %s", yaml_path)
        sys.exit(1)

    return yaml_path


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def run_training(
    yaml_path: Path,
    args: argparse.Namespace,
) -> Path:
    """Train YOLOv11n and return path to best weights."""
    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
    except ImportError:
        logger.error("ultralytics is required. Install: pip install ultralytics")
        sys.exit(1)

    logger.info("Loading pretrained weights: %s", args.weights)
    model = YOLO(args.weights)

    train_kwargs = _build_train_kwargs(yaml_path, args)
    logger.info("Starting training for %d epochs", args.epochs)
    start = time.monotonic()
    results = model.train(**train_kwargs)
    elapsed = time.monotonic() - start
    logger.info("Training complete in %.1fs", elapsed)

    best_src = _find_best_weights(args.project, results)
    if best_src is None:
        logger.error("Could not locate best.pt after training")
        sys.exit(1)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_src, BEST_WEIGHTS_PATH)
    logger.info("Best weights saved to %s", BEST_WEIGHTS_PATH)
    return BEST_WEIGHTS_PATH


def _build_train_kwargs(
    yaml_path: Path,
    args: argparse.Namespace,
) -> dict:
    """Build the kwargs dict for model.train()."""
    kwargs: dict = {
        "data": str(yaml_path.resolve()),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "patience": 20,
        "project": args.project,
        "name": "train",
        "exist_ok": True,
        "verbose": True,
        "save": True,
        "plots": True,
        "seed": args.seed,
    }
    if args.device is not None:
        kwargs["device"] = args.device
    return kwargs


def _find_best_weights(
    project: str,
    results: object,
) -> Optional[Path]:
    """Locate best.pt from training results."""
    best = Path(project) / "train" / "weights" / "best.pt"
    if best.exists():
        return best
    if hasattr(results, "save_dir"):
        alt = Path(str(results.save_dir)) / "weights" / "best.pt"
        if alt.exists():
            return alt
    return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def run_evaluation(
    weights_path: Path,
    yaml_path: Path,
    args: argparse.Namespace,
) -> dict:
    """Run validation on the val split and return metrics dict."""
    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
    except ImportError:
        logger.error("ultralytics is required for evaluation")
        sys.exit(1)

    logger.info("Evaluating model: %s", weights_path)
    model = YOLO(str(weights_path))

    val_kwargs: dict = {
        "data": str(yaml_path.resolve()),
        "split": "val",
        "imgsz": args.imgsz,
        "batch": args.batch,
        "verbose": False,
    }
    if args.device is not None:
        val_kwargs["device"] = args.device

    results = model.val(**val_kwargs)
    metrics = _extract_val_metrics(results)
    return metrics


def _extract_val_metrics(results: object) -> dict:
    """Pull mAP and per-class AP from validation results."""
    metrics: dict = {}

    box = getattr(results, "box", None)
    if box is not None:
        metrics["mAP50"] = _safe_float(getattr(box, "map50", None))
        metrics["mAP50_95"] = _safe_float(getattr(box, "map", None))
        metrics["precision"] = _safe_float(_mean_attr(box, "p"))
        metrics["recall"] = _safe_float(_mean_attr(box, "r"))
        metrics["per_class_ap50"] = _per_class_ap(box, results)

    if hasattr(results, "speed"):
        metrics["inference_ms"] = _safe_float(
            results.speed.get("inference", None)
            if isinstance(results.speed, dict)
            else None,
        )

    return metrics


def _safe_float(val: object) -> Optional[float]:
    """Convert to float rounded to 4 decimals, or None."""
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def _mean_attr(box: object, attr: str) -> Optional[float]:
    """Get mean of a per-class array attribute."""
    arr = getattr(box, attr, None)
    if arr is None:
        return None
    try:
        import numpy as np
        return float(np.mean(arr))
    except Exception:
        return None


def _per_class_ap(box: object, results: object) -> Dict[str, float]:
    """Extract per-class AP50 values."""
    per_class: Dict[str, float] = {}
    ap50_arr = getattr(box, "ap50", None)
    names = getattr(results, "names", {})

    if ap50_arr is None or not names:
        return per_class

    try:
        for i, val in enumerate(ap50_arr):
            class_name = names.get(i, f"class_{i}")
            per_class[class_name] = round(float(val), 4)
    except Exception:
        pass

    return per_class


# ---------------------------------------------------------------------------
# Comparison and reporting
# ---------------------------------------------------------------------------


def load_previous_results() -> Optional[dict]:
    """Load previous eval results if they exist."""
    if not EVAL_RESULTS_PATH.exists():
        return None
    try:
        with open(EVAL_RESULTS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_results(metrics: dict) -> None:
    """Save evaluation results to JSON."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVAL_RESULTS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)
    logger.info("Eval results saved to %s", EVAL_RESULTS_PATH)


def print_eval_report(
    metrics: dict,
    previous: Optional[dict] = None,
) -> None:
    """Print evaluation results and comparison to stdout."""
    print("\n" + "=" * 60)
    print("  OVERWATCH Drone Detector Evaluation Results")
    print("=" * 60)
    _print_metric_rows(metrics, previous)
    _print_per_class_ap(metrics)
    _print_inference_speed(metrics)
    print("=" * 60 + "\n")


def _print_metric_rows(
    metrics: dict,
    previous: Optional[dict],
) -> None:
    """Print main metric rows with optional comparison."""
    labels = {
        "mAP50": "mAP@50",
        "mAP50_95": "mAP@50:95",
        "precision": "Precision",
        "recall": "Recall",
    }
    for key, label in labels.items():
        val = metrics.get(key)
        val_str = f"{val:.4f}" if val is not None else "N/A"
        delta_str = ""
        if previous and key in previous and val is not None:
            prev_val = previous[key]
            if prev_val is not None:
                delta = val - prev_val
                arrow = "+" if delta >= 0 else ""
                tag = "improvement" if delta >= 0 else "regression"
                delta_str = f"  ({arrow}{delta:.4f} {tag})"
        print(f"  {label:15s}: {val_str}{delta_str}")


def _print_per_class_ap(metrics: dict) -> None:
    """Print per-class AP50 if available."""
    per_class = metrics.get("per_class_ap50", {})
    if not per_class:
        return
    print()
    print("  Per-class AP@50:")
    for name, ap in sorted(per_class.items()):
        print(f"    {name:20s}: {ap:.4f}")


def _print_inference_speed(metrics: dict) -> None:
    """Print inference speed if available."""
    speed = metrics.get("inference_ms")
    if speed is not None:
        print()
        print(f"  Inference speed  : {speed:.1f} ms/image")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point for end-to-end training and evaluation."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    args = parse_args(argv)
    yaml_path = prepare_dataset_if_needed(args)
    previous = load_previous_results()

    if not args.skip_train:
        run_training(yaml_path, args)

    if not BEST_WEIGHTS_PATH.exists():
        logger.error("No weights found at %s. Run training first.", BEST_WEIGHTS_PATH)
        sys.exit(1)

    metrics = run_evaluation(BEST_WEIGHTS_PATH, yaml_path, args)
    save_results(metrics)
    print_eval_report(metrics, previous)


if __name__ == "__main__":
    main()
