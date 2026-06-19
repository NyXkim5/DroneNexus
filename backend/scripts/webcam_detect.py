"""
Live webcam detection server for OVERWATCH.

Captures frames from the MacBook camera, runs YOLOv11 object detection,
and streams results over WebSocket to the BULWARK HUD.

Usage:
    python3 -m scripts.webcam_detect [--port 8766] [--model yolo11n.pt]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import List

import cv2
from ultralytics import YOLO

logger = logging.getLogger("overwatch.webcam")


def detect_frame(model: YOLO, frame, conf: float = 0.4) -> List[dict]:
    """Run YOLO on one frame and return detection dicts."""
    results = model(frame, verbose=False, conf=conf)
    detections = []
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


async def webcam_ws_server(port: int, model_path: str) -> None:
    """Start WebSocket server streaming webcam detections."""
    import websockets

    logger.info("Loading YOLO model: %s", model_path)
    model = YOLO(model_path)

    logger.info("Opening camera...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Cannot open camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    clients: set = set()

    async def handler(ws) -> None:
        clients.add(ws)
        logger.info("Webcam client connected (%d total)", len(clients))
        try:
            await ws.wait_closed()
        finally:
            clients.discard(ws)
            logger.info("Webcam client disconnected (%d total)", len(clients))

    async def broadcast_loop() -> None:
        while True:
            ret, frame = cap.read()
            if not ret:
                await asyncio.sleep(0.1)
                continue

            t0 = time.time()
            detections = detect_frame(model, frame)
            elapsed = time.time() - t0

            _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            import base64
            frame_b64 = base64.b64encode(jpeg.tobytes()).decode("ascii")

            msg = json.dumps({
                "type": "WEBCAM_FRAME",
                "detections": detections,
                "detection_count": len(detections),
                "inference_ms": round(elapsed * 1000, 1),
                "timestamp": time.time(),
                "frame": frame_b64,
                "width": frame.shape[1],
                "height": frame.shape[0],
            })

            dead = set()
            for ws in clients:
                try:
                    await ws.send(msg)
                except Exception:
                    dead.add(ws)
            clients.difference_update(dead)

            await asyncio.sleep(0.1)

    server = await websockets.serve(handler, "0.0.0.0", port)
    logger.info("Webcam detection server on ws://localhost:%d", port)
    await broadcast_loop()


def main() -> None:
    parser = argparse.ArgumentParser(description="OVERWATCH Webcam Detection Server")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO model path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    asyncio.run(webcam_ws_server(args.port, args.model))


if __name__ == "__main__":
    main()
