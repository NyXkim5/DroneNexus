# OVERWATCH Edge Deployment Guide

Model export and deployment procedures for the drone detection pipeline on edge hardware.

## Model Summary

- Architecture: YOLOv11n (2.59M params, 6.4 GFLOPs)
- Task: Single-class detection (drone)
- Training: 75K images, mAP50 0.949, mAP50-95 0.647
- Base checkpoint: `models/drone_seraphim_best.pt` (5.2 MB)

## Export Formats

| Format | File | Use Case |
|--------|------|----------|
| PyTorch (.pt) | `drone_seraphim_best.pt` | Training, fine-tuning |
| ONNX (.onnx) | `drone_seraphim.onnx` | Cross-platform CPU/GPU inference |
| CoreML (.mlpackage) | `drone_seraphim.mlpackage` | macOS/iOS Apple Neural Engine |
| TensorRT FP16 (.engine) | Build on target | NVIDIA Jetson deployment |
| TensorRT INT8 (.engine) | Build on target | Maximum Jetson throughput |

## Running the Quantization Pipeline

From the `backend/` directory:

```bash
# Full pipeline (ONNX + CoreML + benchmark)
python -m scripts.quantize_model

# ONNX only
python -m scripts.quantize_model --skip-coreml

# Benchmark existing exports
python -m scripts.quantize_model --benchmark-only
```

## TensorRT Export (Requires NVIDIA GPU)

TensorRT engines must be built on the target device. An engine built on AGX Orin will not run on Orin Nano. Always build on the deployment hardware.

### FP16 Export

```bash
# On the Jetson device (or x86 with NVIDIA GPU)
pip install ultralytics

# Export using Ultralytics built-in TensorRT exporter
yolo export model=models/drone_seraphim_best.pt format=engine half=True device=0

# Or from Python
from ultralytics import YOLO
model = YOLO("models/drone_seraphim_best.pt")
model.export(format="engine", half=True, device=0)
```

### INT8 Export (Requires Calibration Dataset)

INT8 quantization needs a representative calibration dataset (100-500 images) to determine optimal quantization ranges.

```bash
# Prepare calibration images in a flat directory
# Use images from the training/validation set that cover typical scenes
mkdir -p calibration_data/
cp /path/to/representative/images/*.jpg calibration_data/

# Export with INT8 quantization
yolo export model=models/drone_seraphim_best.pt format=engine \
    int8=True device=0 data=datasets/seraphim.yaml

# From Python with explicit calibration
from ultralytics import YOLO
model = YOLO("models/drone_seraphim_best.pt")
model.export(
    format="engine",
    int8=True,
    data="datasets/seraphim.yaml",  # dataset yaml for calibration
    device=0,
)
```

The calibration dataset should include:
- Clear sky backgrounds with drones at various distances
- Cluttered backgrounds (trees, buildings)
- Various lighting conditions (dawn, midday, dusk)
- Different drone types if available

### Using trtexec Directly

For more control over engine build parameters:

```bash
# FP16
/usr/src/tensorrt/bin/trtexec \
    --onnx=models/drone_seraphim.onnx \
    --saveEngine=models/drone_seraphim_fp16.engine \
    --fp16 \
    --workspace=2048 \
    --minShapes=images:1x3x640x640 \
    --optShapes=images:1x3x640x640 \
    --maxShapes=images:4x3x640x640

# INT8 with calibration cache
/usr/src/tensorrt/bin/trtexec \
    --onnx=models/drone_seraphim.onnx \
    --saveEngine=models/drone_seraphim_int8.engine \
    --int8 \
    --calib=calibration_cache.bin \
    --workspace=2048
```

## Jetson Performance Expectations

Estimates for YOLOv11n (640x640 input, batch=1) based on published benchmarks and architectural specs.

| Device | GPU Cores | FP16 TOPS | FP16 Latency | FP16 FPS | INT8 FPS |
|--------|-----------|-----------|-------------|----------|----------|
| Orin Nano (8GB) | 1024 | 40 | ~8 ms | ~120 | ~180 |
| Orin NX (16GB) | 1024 | 100 | ~5 ms | ~200 | ~300 |
| AGX Orin (64GB) | 2048 | 275 | ~2.5 ms | ~400 | ~550 |

Notes:
- FPS numbers assume DLA is not used. DLA can offload conv layers and improve throughput by 20-30% on AGX Orin.
- INT8 numbers assume proper calibration. Poor calibration degrades mAP by 1-3%.
- Power mode affects clock speeds. Use `sudo nvpmodel -m 0` for max performance.

## Docker Deployment on Jetson

### Build Container

```dockerfile
# Dockerfile.jetson
FROM nvcr.io/nvidia/l4t-ml:r36.4.0-py3

RUN pip install ultralytics onnxruntime-gpu

WORKDIR /app
COPY models/drone_seraphim_best.pt models/
COPY models/drone_seraphim.onnx models/
COPY backend/ backend/

# Build TensorRT engine on first run
CMD ["python3", "-c", \
    "from ultralytics import YOLO; \
     m = YOLO('models/drone_seraphim_best.pt'); \
     m.export(format='engine', half=True)"]
```

### Run Container

```bash
# Build
docker build -t overwatch-detector -f Dockerfile.jetson .

# Run with GPU access
docker run --runtime nvidia \
    --network host \
    -v /tmp/argus_socket:/tmp/argus_socket \
    -v $(pwd)/models:/app/models \
    overwatch-detector

# Run inference server
docker run --runtime nvidia \
    --network host \
    -p 8765:8765 \
    -v $(pwd)/models:/app/models \
    overwatch-detector \
    python3 -m backend.api.websocket
```

### Docker Compose (with camera)

```yaml
version: "3.8"
services:
  detector:
    image: overwatch-detector
    runtime: nvidia
    network_mode: host
    volumes:
      - ./models:/app/models
      - /tmp/argus_socket:/tmp/argus_socket
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    devices:
      - /dev/video0:/dev/video0
    restart: unless-stopped
```

## DeepStream Integration

For multi-stream processing on Jetson, use NVIDIA DeepStream with the ONNX or TensorRT engine.

```ini
# config_detector.txt (DeepStream nvinfer config)
[property]
gpu-id=0
net-scale-factor=0.00392157
model-engine-file=models/drone_seraphim_fp16.engine
onnx-file=models/drone_seraphim.onnx
labelfile-path=labels.txt
batch-size=1
network-mode=2  # 0=FP32, 1=INT8, 2=FP16
num-detected-classes=1
interval=0
gie-unique-id=1
process-mode=1  # primary detector
network-type=0  # detector

[class-attrs-all]
threshold=0.25
nms-iou-threshold=0.45
```

## Validation After Export

Always validate that export did not degrade detection quality.

```bash
# Validate ONNX model on test set
yolo val model=models/drone_seraphim.onnx data=datasets/seraphim.yaml

# Validate TensorRT engine
yolo val model=models/drone_seraphim_fp16.engine data=datasets/seraphim.yaml

# Compare mAP across formats
python3 -c "
from ultralytics import YOLO
for fmt in ['drone_seraphim_best.pt', 'drone_seraphim.onnx']:
    m = YOLO(f'models/{fmt}')
    r = m.val(data='datasets/seraphim.yaml')
    print(f'{fmt}: mAP50={r.box.map50:.4f} mAP50-95={r.box.map:.4f}')
"
```

Expected mAP degradation by format:
- ONNX FP32: < 0.1% (lossless)
- TensorRT FP16: < 0.5%
- TensorRT INT8 (calibrated): 1-3%
- TensorRT INT8 (uncalibrated): 5-15% (do not deploy)

## Latency Monitoring in Production

The detector node reports latency via the `/diagnostics` topic. Set alert thresholds based on mission requirements.

```python
# Example: Alert if inference exceeds 15ms (drops below 66 FPS)
MAX_INFERENCE_MS = 15.0
```

For the ISR coordination pipeline, the end-to-end budget from frame capture to CoT publish is 50ms. The detector gets 15ms of that budget.
