"""
Model export helper for OVERWATCH edge vision pipeline.

Exports a YOLO .pt model to ONNX and optionally to a TensorRT engine.
Falls back to ONNX-only export if TensorRT is unavailable.

Run from cwd=backend:
  python -m scripts.export_model --input model.pt --output model.onnx
  python -m scripts.export_model --input model.pt --output model.onnx --tensorrt
  python -m scripts.export_model --input model.pt --output model.onnx --fp16
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger("overwatch.export_model")


def _check_onnx_available() -> bool:
    """Return True if the onnx package is importable."""
    try:
        importlib.import_module("onnx")
        return True
    except ImportError:
        return False


def _check_tensorrt_available() -> bool:
    """Return True if tensorrt is importable."""
    try:
        importlib.import_module("tensorrt")
        return True
    except ImportError:
        return False


def export_to_onnx(
    input_path: str,
    output_path: str,
    opset: int = 17,
    fp16: bool = False,
) -> str:
    """Export a YOLO .pt model to ONNX format.

    Returns the path to the exported ONNX file.
    """
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise RuntimeError("torch is required for ONNX export") from exc

    in_path = Path(input_path)
    if not in_path.exists():
        raise FileNotFoundError(f"Input model not found: {input_path}")

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading model from %s", input_path)
    model = torch.load(input_path, map_location="cpu", weights_only=False)

    if hasattr(model, "model"):
        model = model.model
    if hasattr(model, "eval"):
        model.eval()
    if fp16 and hasattr(model, "half"):
        model = model.half()

    dtype = torch.float16 if fp16 else torch.float32
    dummy_input = torch.zeros(1, 3, 640, 640, dtype=dtype)

    logger.info(
        "Exporting to ONNX: %s (opset=%d, fp16=%s)",
        output_path, opset, fp16,
    )
    torch.onnx.export(
        model,
        dummy_input,
        str(out_path),
        opset_version=opset,
        input_names=["images"],
        output_names=["output"],
        dynamic_axes={
            "images": {0: "batch"},
            "output": {0: "batch"},
        },
    )
    logger.info("ONNX export complete: %s", out_path)
    return str(out_path)


def compile_to_tensorrt(
    onnx_path: str,
    engine_path: str,
    fp16: bool = False,
    workspace_gb: int = 1,
) -> str:
    """Compile an ONNX model to a TensorRT engine file.

    Returns the path to the compiled engine.
    """
    try:
        trt = importlib.import_module("tensorrt")
    except ImportError as exc:
        raise RuntimeError(
            "tensorrt is required for engine compilation"
        ) from exc

    onnx_file = Path(onnx_path)
    if not onnx_file.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path}")

    trt_logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(trt_logger)
    flags = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    network = builder.create_network(flags)
    parser = trt.OnnxParser(network, trt_logger)

    _parse_onnx_file(parser, onnx_path)

    config = builder.create_builder_config()
    workspace_bytes = workspace_gb * (1 << 30)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_bytes)

    if fp16 and builder.platform_has_fast_fp16:
        config.set_flag(trt.BuilderFlag.FP16)
        logger.info("FP16 mode enabled for TensorRT build")

    logger.info("Building TensorRT engine from %s", onnx_path)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TensorRT engine build failed")

    out = Path(engine_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(serialized)
    logger.info("TensorRT engine saved: %s", engine_path)
    return str(out)


def _parse_onnx_file(parser: object, onnx_path: str) -> None:
    """Parse an ONNX file into a TensorRT network. Raises on failure."""
    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):  # type: ignore[union-attr]
            errors = []
            for i in range(parser.num_errors):  # type: ignore[union-attr]
                errors.append(str(parser.get_error(i)))  # type: ignore[union-attr]
            raise RuntimeError(
                f"Failed to parse ONNX model: {', '.join(errors)}"
            )


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Export YOLO model to ONNX and optionally TensorRT"
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to input YOLO .pt model",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Path for output ONNX file",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX opset version (default: 17)",
    )
    parser.add_argument(
        "--tensorrt",
        action="store_true",
        help="Also compile to TensorRT engine",
    )
    parser.add_argument(
        "--fp16",
        action="store_true",
        help="Use half-precision (FP16) for export",
    )
    parser.add_argument(
        "--workspace",
        type=int,
        default=1,
        help="TensorRT workspace size in GB (default: 1)",
    )
    return parser


def main() -> int:
    """CLI entry point for model export."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()

    onnx_path = export_to_onnx(
        args.input, args.output, opset=args.opset, fp16=args.fp16,
    )
    print(f"ONNX exported: {onnx_path}")

    if args.tensorrt:
        if not _check_tensorrt_available():
            logger.warning(
                "TensorRT not available. ONNX-only export complete."
            )
            print("TensorRT not installed. Skipping engine compilation.")
            return 0
        engine_path = Path(args.output).with_suffix(".engine")
        result = compile_to_tensorrt(
            onnx_path,
            str(engine_path),
            fp16=args.fp16,
            workspace_gb=args.workspace,
        )
        print(f"TensorRT engine: {result}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
