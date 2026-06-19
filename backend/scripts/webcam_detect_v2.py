"""
Live webcam detection server v2 for OVERWATCH.

GPU-accelerated preprocessing, multi-backend detection, object tracking,
async frame capture, and recording support.

Usage:
    python3 -m scripts.webcam_detect_v2 [--port 8766] [--model yolo11n.pt]
    python3 -m scripts.webcam_detect_v2 --backend tensorrt --model engine.trt
    python3 -m scripts.webcam_detect_v2 --rtsp rtsp://192.168.1.10/stream
    python3 -m scripts.webcam_detect_v2 --camera 1 --skip 2 --record out.mp4
    python3 -m scripts.webcam_detect_v2 --classes person,car,drone
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

import cv2

logger = logging.getLogger("overwatch.webcam_v2")

# ---------------------------------------------------------------------------
# Multi-backend detection
# ---------------------------------------------------------------------------

_TENSORRT_AVAILABLE = False
_create_detector = None

try:
    from vision.tensorrt_detector import create_detector as _create_detector_fn

    _TENSORRT_AVAILABLE = True
    _create_detector = _create_detector_fn
    logger.info("TensorRT detector module available")
except ImportError:
    logger.debug("vision.tensorrt_detector not available, will use Ultralytics fallback")


class DetectorProtocol(Protocol):
    """Minimal protocol that any detection backend must satisfy."""

    def detect(self, frame: Any, conf: float = 0.4) -> List[dict]:
        ...


class UltralyticsDetector:
    """Wraps ultralytics YOLO for the DetectorProtocol interface."""

    def __init__(self, model_path: str) -> None:
        from ultralytics import YOLO

        self._model = YOLO(model_path)
        logger.info("Loaded Ultralytics YOLO model: %s", model_path)

    def detect(self, frame: Any, conf: float = 0.4) -> List[dict]:
        results = self._model(frame, verbose=False, conf=conf)
        detections: List[dict] = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                cls_name = r.names[cls_id]
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    "class": cls_name,
                    "confidence": round(confidence, 3),
                    "bbox": [round(x1), round(y1), round(x2), round(y2)],
                    "center_x": round((x1 + x2) / 2),
                    "center_y": round((y1 + y2) / 2),
                })
        return detections


def build_detector(
    backend: str,
    model_path: str,
) -> DetectorProtocol:
    """Factory: build a detector for the requested backend."""
    if backend in ("tensorrt", "jetson"):
        if _TENSORRT_AVAILABLE and _create_detector is not None:
            logger.info("Creating %s detector via vision.tensorrt_detector", backend)
            return _create_detector(model_path, backend=backend)
        logger.warning(
            "Backend '%s' requested but vision.tensorrt_detector unavailable. "
            "Falling back to Ultralytics.",
            backend,
        )
    return UltralyticsDetector(model_path)


# ---------------------------------------------------------------------------
# Centroid tracker
# ---------------------------------------------------------------------------

@dataclass
class _TrackedObject:
    track_id: int
    cx: float
    cy: float
    cls: str
    disappeared: int = 0


class CentroidTracker:
    """Assign persistent IDs by matching detection centroids across frames."""

    def __init__(
        self,
        max_disappeared: int = 15,
        distance_threshold: float = 50.0,
    ) -> None:
        self._next_id = 0
        self._objects: Dict[int, _TrackedObject] = {}
        self._max_disappeared = max_disappeared
        self._distance_threshold = distance_threshold

    def update(self, detections: List[dict]) -> List[dict]:
        """Match detections to existing tracks. Returns detections with track_id."""
        if not detections:
            self._mark_all_disappeared()
            return []

        input_centroids = [
            (d["center_x"], d["center_y"], d["class"]) for d in detections
        ]

        if not self._objects:
            return self._register_all(detections, input_centroids)

        return self._match(detections, input_centroids)

    # -- internal helpers --------------------------------------------------

    def _mark_all_disappeared(self) -> None:
        to_remove: List[int] = []
        for tid, obj in self._objects.items():
            obj.disappeared += 1
            if obj.disappeared > self._max_disappeared:
                to_remove.append(tid)
        for tid in to_remove:
            del self._objects[tid]

    def _register(self, cx: float, cy: float, cls: str) -> int:
        tid = self._next_id
        self._objects[tid] = _TrackedObject(
            track_id=tid, cx=cx, cy=cy, cls=cls,
        )
        self._next_id += 1
        return tid

    def _register_all(
        self,
        detections: List[dict],
        centroids: List[tuple],
    ) -> List[dict]:
        result: List[dict] = []
        for det, (cx, cy, cls) in zip(detections, centroids):
            tid = self._register(cx, cy, cls)
            result.append({**det, "track_id": tid})
        return result

    def _match(
        self,
        detections: List[dict],
        centroids: List[tuple],
    ) -> List[dict]:
        object_ids = list(self._objects.keys())
        object_list = [self._objects[oid] for oid in object_ids]

        # Build distance matrix
        num_objects = len(object_list)
        num_inputs = len(centroids)
        dist_matrix: List[List[float]] = []
        for obj in object_list:
            row: List[float] = []
            for cx, cy, _ in centroids:
                d = math.hypot(obj.cx - cx, obj.cy - cy)
                row.append(d)
            dist_matrix.append(row)

        # Greedy assignment: pick smallest distance first
        used_rows: set = set()
        used_cols: set = set()
        assignments: Dict[int, int] = {}  # col_idx -> object_id

        flat = []
        for r in range(num_objects):
            for c in range(num_inputs):
                flat.append((dist_matrix[r][c], r, c))
        flat.sort(key=lambda x: x[0])

        for dist, r, c in flat:
            if r in used_rows or c in used_cols:
                continue
            if dist > self._distance_threshold:
                break
            used_rows.add(r)
            used_cols.add(c)
            assignments[c] = object_ids[r]

        # Update matched objects
        for c, oid in assignments.items():
            cx, cy, cls = centroids[c]
            self._objects[oid].cx = cx
            self._objects[oid].cy = cy
            self._objects[oid].cls = cls
            self._objects[oid].disappeared = 0

        # Mark unmatched objects as disappeared
        for r in range(num_objects):
            if r not in used_rows:
                oid = object_ids[r]
                self._objects[oid].disappeared += 1
                if self._objects[oid].disappeared > self._max_disappeared:
                    del self._objects[oid]

        # Register unmatched detections
        result: List[dict] = []
        for c in range(num_inputs):
            if c in assignments:
                tid = assignments[c]
            else:
                cx, cy, cls = centroids[c]
                tid = self._register(cx, cy, cls)
            result.append({**detections[c], "track_id": tid})

        return result


# ---------------------------------------------------------------------------
# Async threaded camera capture
# ---------------------------------------------------------------------------

class AsyncCamera:
    """Read camera frames in a background thread to decouple from inference."""

    def __init__(
        self,
        source: int | str,
        width: int = 1280,
        height: int = 720,
    ) -> None:
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera/stream: {source}")

        if isinstance(source, int):
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        self._frame: Optional[Any] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def fps(self) -> float:
        return self._cap.get(cv2.CAP_PROP_FPS) or 30.0

    @property
    def frame_size(self) -> tuple[int, int]:
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return w, h

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="cam-capture",
        )
        self._thread.start()
        logger.info("Camera capture thread started")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._cap.release()
        logger.info("Camera released")

    def read(self) -> Optional[Any]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def _capture_loop(self) -> None:
        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame = frame


# ---------------------------------------------------------------------------
# Video recorder
# ---------------------------------------------------------------------------

class VideoRecorder:
    """Write annotated frames to an MP4 file."""

    def __init__(self, path: str, fps: float, width: int, height: int) -> None:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
        if not self._writer.isOpened():
            raise RuntimeError(f"Cannot open video writer: {path}")
        self._path = path
        logger.info("Recording to %s (%dx%d @ %.1f fps)", path, width, height, fps)

    def write(self, frame: Any) -> None:
        self._writer.write(frame)

    def release(self) -> None:
        self._writer.release()
        logger.info("Recording saved: %s", self._path)


# ---------------------------------------------------------------------------
# Frame annotation (for recording)
# ---------------------------------------------------------------------------

def annotate_frame(frame: Any, detections: List[dict]) -> Any:
    """Draw bounding boxes and labels onto the frame for recording."""
    annotated = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        tid = det.get("track_id", "?")
        label = f"[{tid}] {det['class']} {det['confidence']:.2f}"
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            annotated, label, (x1, y1 - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
        )
    return annotated


# ---------------------------------------------------------------------------
# FPS counter
# ---------------------------------------------------------------------------

class FPSCounter:
    """Track rolling FPS over a configurable window."""

    def __init__(self, window: int = 30) -> None:
        self._timestamps: deque[float] = deque(maxlen=window)

    def tick(self) -> float:
        now = time.monotonic()
        self._timestamps.append(now)
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / elapsed


# ---------------------------------------------------------------------------
# Main server
# ---------------------------------------------------------------------------

async def webcam_ws_server(args: argparse.Namespace) -> None:
    """Start WebSocket server streaming webcam detections with tracking."""
    import websockets

    # Build detector
    detector = build_detector(args.backend, args.model)

    # Resolve camera source
    source: int | str = args.camera
    if args.rtsp:
        source = args.rtsp

    # Start async camera
    camera = AsyncCamera(source)
    camera.start()

    # Wait for the first frame
    for _ in range(50):
        if camera.read() is not None:
            break
        await asyncio.sleep(0.05)
    else:
        logger.error("Timed out waiting for first frame")
        camera.stop()
        return

    cam_w, cam_h = camera.frame_size

    # Class filter
    allowed_classes: Optional[set] = None
    if args.classes:
        allowed_classes = {c.strip().lower() for c in args.classes.split(",")}
        logger.info("Filtering to classes: %s", allowed_classes)

    # Optional recorder
    recorder: Optional[VideoRecorder] = None
    if args.record:
        recorder = VideoRecorder(args.record, camera.fps, cam_w, cam_h)

    tracker = CentroidTracker(
        max_disappeared=15, distance_threshold=50.0,
    )
    fps_counter = FPSCounter(window=30)

    clients: set = set()
    frame_count = 0

    async def handler(ws: Any) -> None:
        clients.add(ws)
        logger.info("Client connected (%d total)", len(clients))
        try:
            await ws.wait_closed()
        finally:
            clients.discard(ws)
            logger.info("Client disconnected (%d total)", len(clients))

    async def broadcast_loop() -> None:
        nonlocal frame_count
        loop = asyncio.get_running_loop()

        while True:
            frame = camera.read()
            if frame is None:
                await asyncio.sleep(0.01)
                continue

            frame_count += 1

            # Frame skip: only run inference every Nth frame
            if frame_count % args.skip != 0:
                await asyncio.sleep(0.005)
                continue

            # Downscale for inference if requested
            infer_frame = frame
            if args.infer_size:
                h, w = frame.shape[:2]
                scale = args.infer_size / max(h, w)
                if scale < 1.0:
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    infer_frame = cv2.resize(
                        frame, (new_w, new_h),
                        interpolation=cv2.INTER_LINEAR,
                    )

            # Run inference in thread pool to avoid blocking the event loop
            t0 = time.monotonic()
            detections = await loop.run_in_executor(
                None, detector.detect, infer_frame, args.conf,
            )
            elapsed = time.monotonic() - t0

            # Rescale bboxes back to original resolution if we downscaled
            if args.infer_size:
                h, w = frame.shape[:2]
                scale = args.infer_size / max(h, w)
                if scale < 1.0:
                    inv_scale = 1.0 / scale
                    for det in detections:
                        det["bbox"] = [
                            round(det["bbox"][0] * inv_scale),
                            round(det["bbox"][1] * inv_scale),
                            round(det["bbox"][2] * inv_scale),
                            round(det["bbox"][3] * inv_scale),
                        ]
                        det["center_x"] = round(
                            (det["bbox"][0] + det["bbox"][2]) / 2,
                        )
                        det["center_y"] = round(
                            (det["bbox"][1] + det["bbox"][3]) / 2,
                        )

            # Filter by class
            if allowed_classes:
                detections = [
                    d for d in detections
                    if d["class"].lower() in allowed_classes
                ]

            # Apply centroid tracker
            tracked = tracker.update(detections)

            # FPS
            current_fps = fps_counter.tick()

            # Recording with annotations
            if recorder is not None:
                annotated = annotate_frame(frame, tracked)
                recorder.write(annotated)

            # Encode frame for WebSocket transmission
            _, jpeg = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70],
            )
            frame_b64 = base64.b64encode(jpeg.tobytes()).decode("ascii")

            msg = json.dumps({
                "type": "WEBCAM_FRAME",
                "detections": tracked,
                "detection_count": len(tracked),
                "inference_ms": round(elapsed * 1000, 1),
                "fps": round(current_fps, 1),
                "timestamp": time.time(),
                "frame": frame_b64,
                "width": frame.shape[1],
                "height": frame.shape[0],
            })

            # Broadcast to all connected clients
            dead: set = set()
            for ws in clients:
                try:
                    await ws.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    dead.add(ws)
                except Exception as exc:
                    logger.warning("Send error: %s", exc)
                    dead.add(ws)
            clients.difference_update(dead)

            # Small yield to keep the event loop responsive
            await asyncio.sleep(0.005)

    server = await websockets.serve(handler, "0.0.0.0", args.port)
    logger.info(
        "Webcam detection server v2 on ws://localhost:%d "
        "(backend=%s, skip=%d, infer_size=%s)",
        args.port, args.backend, args.skip, args.infer_size,
    )

    try:
        await broadcast_loop()
    finally:
        camera.stop()
        if recorder is not None:
            recorder.release()
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OVERWATCH Webcam Detection Server v2",
    )

    # Server
    parser.add_argument(
        "--port", type=int, default=8766,
        help="WebSocket server port (default: 8766)",
    )

    # Model / backend
    parser.add_argument(
        "--model", default="yolo11n.pt",
        help="Model path (default: yolo11n.pt)",
    )
    parser.add_argument(
        "--backend", choices=["ultralytics", "tensorrt", "jetson"],
        default="ultralytics",
        help="Detection backend (default: ultralytics)",
    )
    parser.add_argument(
        "--conf", type=float, default=0.4,
        help="Detection confidence threshold (default: 0.4)",
    )

    # Performance
    parser.add_argument(
        "--skip", type=int, default=1,
        help="Run inference every Nth frame (default: 1, no skip)",
    )
    parser.add_argument(
        "--infer-size", type=int, default=640,
        help="Downscale longest edge to this for inference (default: 640)",
    )

    # Camera
    parser.add_argument(
        "--camera", type=int, default=0,
        help="Camera device index (default: 0)",
    )
    parser.add_argument(
        "--rtsp", type=str, default=None,
        help="RTSP stream URL (overrides --camera)",
    )

    # Filtering
    parser.add_argument(
        "--classes", type=str, default=None,
        help="Comma-separated class filter (e.g., person,car,drone)",
    )

    # Recording
    parser.add_argument(
        "--record", type=str, default=None,
        help="Save annotated video to this path (e.g., output.mp4)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    asyncio.run(webcam_ws_server(args))


if __name__ == "__main__":
    main()
