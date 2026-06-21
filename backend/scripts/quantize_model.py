"""Model quantization pipeline for OVERWATCH edge deployment.

Exports a trained YOLO .pt model to optimized formats (ONNX, CoreML)
and benchmarks inference latency across all available formats.

Run from cwd=backend:
  python -m scripts.quantize_model
  python -m scripts.quantize_model --skip-coreml
  python -m scripts.quantize_model --input models/custom.pt --benchmark-only
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import ssl
import sys
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger("overwatch.quantize_model")

# Allow downloads without SSL verification issues on macOS
ssl._create_default_https_context = ssl._create_unverified_context

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_INPUT = MODELS_DIR / "drone_seraphim_best.pt"
ONNX_OUTPUT = MODELS_DIR / "drone_seraphim.onnx"
COREML_OUTPUT = MODELS_DIR / "drone_seraphim.mlpackage"
BENCHMARK_OUTPUT = MODELS_DIR / "quantization_benchmark.json"

WARMUP_RUNS = 3
BENCHMARK_RUNS = 10
INPUT_SHAPE = (1, 3, 640, 640)


def load_yolo_model(input_path: Path) -> object:
    """Load a YOLO model from a .pt file."""
    from ultralytics import YOLO

    if not input_path.exists():
        raise FileNotFoundError(f"Model not found: {input_path}")

    model = YOLO(str(input_path))
    logger.info("Loaded model: %s (%s)", input_path.name, model.task)
    return model


def export_onnx(model: object, output_path: Path, opset: int = 17) -> Path:
    """Export YOLO model to ONNX format and validate the result."""
    logger.info("Exporting to ONNX (opset=%d)...", opset)

    result_path = model.export(format="onnx", opset=opset, simplify=True)
    result = Path(result_path)

    _move_export_output(result, output_path)
    _validate_onnx(output_path)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("ONNX export complete: %s (%.2f MB)", output_path.name, size_mb)
    return output_path


def _validate_onnx(onnx_path: Path) -> None:
    """Load and validate an ONNX model to confirm it is well-formed."""
    import onnx

    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)
    logger.info("ONNX validation passed")


def export_coreml(model: object, output_path: Path) -> Path:
    """Export YOLO model to CoreML format for Apple Neural Engine.

    NMS is disabled because coremltools 9.x has a known incompatibility
    with the YOLO11 attention layer int cast op. Post-processing NMS
    should be applied in the inference pipeline instead.
    """
    logger.info("Exporting to CoreML (nms=False)...")

    result_path = model.export(format="coreml", nms=False)
    result = Path(result_path)

    _move_export_output(result, output_path)
    logger.info("CoreML export complete: %s", output_path.name)
    return output_path


def _move_export_output(src: Path, dst: Path) -> None:
    """Move an exported file or directory to the target path."""
    if src == dst:
        return
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _generate_test_images(count: int = 10) -> list[np.ndarray]:
    """Generate random test images for benchmarking."""
    rng = np.random.default_rng(42)
    return [
        rng.integers(0, 255, (640, 640, 3), dtype=np.uint8)
        for _ in range(count)
    ]


def benchmark_pytorch(model: object, images: list[np.ndarray]) -> dict:
    """Benchmark inference with the PyTorch YOLO model."""
    logger.info("Benchmarking PyTorch inference...")

    for img in images[:WARMUP_RUNS]:
        model.predict(img, verbose=False)

    latencies = []
    for img in images:
        start = time.perf_counter()
        model.predict(img, verbose=False)
        latencies.append((time.perf_counter() - start) * 1000)

    return _compute_stats("PyTorch (.pt)", latencies, model.ckpt_path)


def benchmark_onnx(onnx_path: Path, images: list[np.ndarray]) -> dict:
    """Benchmark inference with ONNX Runtime on CPU."""
    import onnxruntime as ort

    logger.info("Benchmarking ONNX Runtime inference...")
    session = ort.InferenceSession(
        str(onnx_path),
        providers=["CPUExecutionProvider"],
    )
    input_name = session.get_inputs()[0].name

    preprocessed = [_preprocess_image(img) for img in images]

    for inp in preprocessed[:WARMUP_RUNS]:
        session.run(None, {input_name: inp})

    latencies = []
    for inp in preprocessed:
        start = time.perf_counter()
        session.run(None, {input_name: inp})
        latencies.append((time.perf_counter() - start) * 1000)

    return _compute_stats("ONNX (.onnx)", latencies, str(onnx_path))


def _preprocess_image(img: np.ndarray) -> np.ndarray:
    """Convert HWC uint8 image to NCHW float32 normalized tensor."""
    tensor = img.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))
    return np.expand_dims(tensor, axis=0)


def _compute_stats(
    format_name: str,
    latencies: list[float],
    file_path: str,
) -> dict:
    """Compute benchmark statistics from a list of latency measurements."""
    path = Path(file_path)
    if path.is_dir():
        size_mb = sum(f.stat().st_size for f in path.rglob("*")) / (1024 * 1024)
    elif path.exists():
        size_mb = path.stat().st_size / (1024 * 1024)
    else:
        size_mb = 0.0

    avg_ms = np.mean(latencies)
    return {
        "format": format_name,
        "size_mb": round(size_mb, 2),
        "latency_mean_ms": round(float(avg_ms), 2),
        "latency_p50_ms": round(float(np.median(latencies)), 2),
        "latency_p95_ms": round(float(np.percentile(latencies, 95)), 2),
        "throughput_fps": round(1000.0 / avg_ms, 1) if avg_ms > 0 else 0.0,
    }


def print_benchmark_table(results: list[dict]) -> None:
    """Print a formatted comparison table of benchmark results."""
    header = f"{'Format':<20} {'Size (MB)':>10} {'Lat mean':>10} {'Lat p50':>10} {'Lat p95':>10} {'FPS':>8}"
    print("\n" + "=" * len(header))
    print("OVERWATCH Model Quantization Benchmark")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['format']:<20} {r['size_mb']:>10.2f} "
            f"{r['latency_mean_ms']:>8.2f}ms "
            f"{r['latency_p50_ms']:>8.2f}ms "
            f"{r['latency_p95_ms']:>8.2f}ms "
            f"{r['throughput_fps']:>7.1f}"
        )
    print("=" * len(header) + "\n")


def save_benchmark_results(results: list[dict], output: Path) -> None:
    """Save benchmark results to JSON."""
    payload = {
        "model": "drone_seraphim (YOLOv11n)",
        "input_shape": list(INPUT_SHAPE),
        "warmup_runs": WARMUP_RUNS,
        "benchmark_runs": BENCHMARK_RUNS,
        "results": results,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n")
    logger.info("Benchmark results saved: %s", output)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Quantize and benchmark YOLO model for edge deployment",
    )
    parser.add_argument(
        "--input", type=str, default=str(DEFAULT_INPUT),
        help="Path to input .pt model",
    )
    parser.add_argument(
        "--skip-coreml", action="store_true",
        help="Skip CoreML export",
    )
    parser.add_argument(
        "--skip-onnx", action="store_true",
        help="Skip ONNX export",
    )
    parser.add_argument(
        "--benchmark-only", action="store_true",
        help="Only run benchmarks on existing exported models",
    )
    parser.add_argument(
        "--opset", type=int, default=17,
        help="ONNX opset version (default: 17)",
    )
    return parser


def run_exports(args: argparse.Namespace) -> object:
    """Run model exports based on CLI arguments. Returns loaded model."""
    input_path = Path(args.input)
    model = load_yolo_model(input_path)

    if not args.benchmark_only:
        if not args.skip_onnx:
            export_onnx(model, ONNX_OUTPUT, opset=args.opset)
        if not args.skip_coreml:
            export_coreml(model, COREML_OUTPUT)

    return model


def run_benchmarks(model: object) -> list[dict]:
    """Run benchmarks across all available exported formats."""
    images = _generate_test_images(BENCHMARK_RUNS)
    results = []

    results.append(benchmark_pytorch(model, images))

    if ONNX_OUTPUT.exists():
        results.append(benchmark_onnx(ONNX_OUTPUT, images))

    if COREML_OUTPUT.exists():
        coreml_size = sum(
            f.stat().st_size for f in COREML_OUTPUT.rglob("*")
        ) / (1024 * 1024)
        results.append({
            "format": "CoreML (.mlpackage)",
            "size_mb": round(coreml_size, 2),
            "latency_mean_ms": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
            "throughput_fps": 0.0,
            "note": "CoreML benchmarking requires macOS predict API",
        })

    return results


def main() -> int:
    """CLI entry point for model quantization pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _build_parser().parse_args()

    model = run_exports(args)
    results = run_benchmarks(model)

    print_benchmark_table(results)
    save_benchmark_results(results, BENCHMARK_OUTPUT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
