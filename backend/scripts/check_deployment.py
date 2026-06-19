"""
Deployment readiness checker for OVERWATCH edge vision pipeline.

Checks the current system for CUDA, TensorRT, GPU memory, OpenCV GPU
support, and camera accessibility. Prints a PASS/FAIL/SKIP table.

Run from cwd=backend:
  python -m scripts.check_deployment
"""
from __future__ import annotations

import importlib
import logging
import platform
import sys
from typing import List, Tuple

logger = logging.getLogger("overwatch.deploy_check")

CheckResult = Tuple[str, str, str]  # (check_name, status, detail)

MIN_GPU_MEMORY_MB = 1024


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_cuda() -> Tuple[str, str]:
    """Check if CUDA is available via PyTorch."""
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return ("SKIP", "torch not installed")
    if torch is None:
        return ("SKIP", "torch not installed")
    try:
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return ("PASS", f"GPU: {name}")
        return ("FAIL", "No CUDA device found")
    except Exception as exc:
        return ("FAIL", f"Error checking CUDA: {exc}")


def check_tensorrt() -> Tuple[str, str]:
    """Check if TensorRT is installed and importable."""
    try:
        trt = importlib.import_module("tensorrt")
    except ImportError:
        return ("FAIL", "tensorrt not installed")
    if trt is None:
        return ("FAIL", "tensorrt not installed")
    try:
        version = getattr(trt, "__version__", "unknown")
        return ("PASS", f"TensorRT {version}")
    except Exception as exc:
        return ("FAIL", f"Error reading TensorRT version: {exc}")


def check_gpu_memory() -> Tuple[str, str]:
    """Check available GPU memory via PyTorch CUDA."""
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return ("SKIP", "torch not installed")
    if torch is None:
        return ("SKIP", "torch not installed")
    try:
        if not torch.cuda.is_available():
            return ("SKIP", "No CUDA device")
        free, total = torch.cuda.mem_get_info(0)
        total_mb = total / (1024 * 1024)
        free_mb = free / (1024 * 1024)
        if total_mb >= MIN_GPU_MEMORY_MB:
            return ("PASS", f"{total_mb:.0f} MB total, {free_mb:.0f} MB free")
        return ("FAIL", f"{total_mb:.0f} MB total (need {MIN_GPU_MEMORY_MB} MB)")
    except Exception as exc:
        return ("SKIP", f"Error checking GPU memory: {exc}")


def check_opencv_gpu() -> Tuple[str, str]:
    """Check if OpenCV was built with CUDA support."""
    try:
        cv2 = importlib.import_module("cv2")
    except ImportError:
        return ("SKIP", "cv2 not installed")
    if cv2 is None:
        return ("SKIP", "cv2 not installed")
    try:
        build_info = cv2.getBuildInformation()
        has_cuda = "CUDA" in build_info and "YES" in build_info.split("CUDA")[1][:50]
        if has_cuda:
            return ("PASS", "OpenCV built with CUDA")
        return ("FAIL", "OpenCV built without CUDA support")
    except Exception as exc:
        return ("SKIP", f"Error checking OpenCV GPU: {exc}")


def check_camera() -> Tuple[str, str]:
    """Check if a camera device is accessible via OpenCV."""
    try:
        cv2 = importlib.import_module("cv2")
    except ImportError:
        return ("SKIP", "cv2 not installed")
    if cv2 is None:
        return ("SKIP", "cv2 not installed")
    try:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.release()
            return ("PASS", "Camera 0 accessible")
        cap.release()
        return ("FAIL", "No camera found at index 0")
    except Exception as exc:
        return ("SKIP", f"Error checking camera: {exc}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    ("CUDA", check_cuda),
    ("TensorRT", check_tensorrt),
    ("GPU Memory", check_gpu_memory),
    ("OpenCV GPU", check_opencv_gpu),
    ("Camera", check_camera),
]


def run_all_checks() -> List[CheckResult]:
    """Execute every check and return a list of (name, status, detail)."""
    results: List[CheckResult] = []
    for name, fn in ALL_CHECKS:
        status, detail = fn()
        results.append((name, status, detail))
    return results


def meets_minimum_requirements(
    results: List[CheckResult],
) -> bool:
    """Return True if CUDA and TensorRT both pass."""
    status_map = {name: status for name, status, _ in results}
    cuda_ok = status_map.get("CUDA") == "PASS"
    trt_ok = status_map.get("TensorRT") == "PASS"
    return cuda_ok and trt_ok


def _format_table(results: List[CheckResult]) -> str:
    """Format check results as an aligned ASCII table."""
    name_width = max(len(r[0]) for r in results)
    status_width = 4
    lines = [
        f"{'Check'.ljust(name_width)}   {'Status'.ljust(status_width)}   Detail",
        "-" * (name_width + status_width + 30),
    ]
    for name, status, detail in results:
        lines.append(
            f"{name.ljust(name_width)}   {status.ljust(status_width)}   {detail}"
        )
    return "\n".join(lines)


def _print_macos_guidance() -> None:
    """Print what would be needed for deployment on macOS."""
    print("\n--- macOS detected ---")
    print("Edge deployment requires an NVIDIA Jetson or GPU server.")
    print("Needed for deployment:")
    print("  - NVIDIA GPU with CUDA 11.8+")
    print("  - TensorRT 8.6+")
    print("  - pycuda")
    print("  - OpenCV built with CUDA support")
    print("  - USB or CSI camera")
    print("For Jetson: install jetson-inference and JetPack SDK.")


def main() -> int:
    """CLI entry point. Returns 0 if minimum requirements met."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    print("OVERWATCH Edge Deployment Readiness Check")
    print(f"Platform: {platform.system()} {platform.machine()}\n")

    results = run_all_checks()
    print(_format_table(results))

    if platform.system() == "Darwin":
        _print_macos_guidance()

    ready = meets_minimum_requirements(results)
    print(f"\nDeployment ready: {'YES' if ready else 'NO'}")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
