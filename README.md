# OVERWATCH -- ISR Asset Coordination Platform

Multi-asset ISR coordination and telemetry platform. Hardware-agnostic, plug-and-play, designed for 2-50 assets.

## BULWARK -- Counter-Swarm Defense Engine

BULWARK is an autonomous counter-swarm defense engine built on OVERWATCH. One
decision engine consumes any sensor source and fuses it into one real-time
picture, classifies swarm threats, allocates finite effectors, and resolves
engagements. The headline metric is the cost-exchange ratio, defender dollars
spent per attacker dollar destroyed, with the goal of driving it below one.

Full design and module map: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The pipeline lives in `backend/`: `sensors/` (a SensorSource interface with a
simulated and a real-data adapter), `fusion/` (multi-sensor track fusion),
`threat/` (swarm classification and prioritization), `defense/` (layered
allocation with a cost ledger), `attacker/` (the red force), and `wargame/` (the
runner, scenarios, calibration, audit, and reports).

### Run a wargame

```bash
cd backend
python -m wargame.run --list                                   # list scenarios
python -m wargame.run --scenario saturation_1000 --fast        # fast batch run
python -m wargame.run --scenario contested_500 --audit run.db  # write an audit log
```

The HUD is `src/hud/bulwark.html`. Start the backend (`python3 main.py`) and open
it to watch a run stream over the wargame websocket.

### Inspect and tune

```bash
python -m wargame.report --db run.db                           # after-action report
python -m wargame.report --db run.db --engagement <id>         # reconstruct a kill chain
python -m wargame.sweep --scenario saturation_1000             # cost-exchange sensitivity
```

Effector performance lives in `backend/config/effectors.yaml` with a provenance
and uncertainty band on every value, so measured kill curves replace the current
estimates without code changes.

### Tests

```bash
cd backend
python -m pytest -m "not slow"     # fast unit and component tests, about 20s
python -m pytest                   # full suite including end-to-end wargames
```

## Project Structure

```
OVERWATCH/
├── src/
│   ├── hud/
│   │   ├── index.html              # Single-file tactical HUD (HTML/CSS/JS)
│   │   └── 3d-view.html            # 3D asset visualization
│   ├── docs/
│   │   └── generate-doc.js         # Architecture document generator (docx)
│   ├── shared/
│   │   └── protocol.js             # Wire protocol constants & utilities
│   └── simulation/
│       └── drone-simulator.js      # Standalone WebSocket asset simulator
├── backend/                        # Python FastAPI backend
├── dist/                           # Build output
│   ├── overwatch-hud.html          # Packaged HUD
│   └── overwatch-architecture.docx # Generated architecture document
├── config/
│   └── overwatch.yaml              # Example companion computer config
├── scripts/
│   ├── build.sh                    # Full build (doc + HUD packaging)
│   └── dev-server.sh               # Local dev server for HUD
├── tests/
│   └── protocol.test.js            # Protocol unit tests
└── docs/                           # Supplementary documentation
```

## Quick Start

### View the HUD

Open `src/hud/index.html` directly in a browser. The simulation runs client-side -- no server needed.

### Build All Deliverables

```bash
npm install
npm run build
```

Output:
- `dist/overwatch-hud.html` -- Tactical HUD
- `dist/overwatch-architecture.docx` -- Architecture document

### Run Tests

```bash
node tests/protocol.test.js
```

### Run Backend

```bash
cd backend
pip install -r requirements.txt
python3 main.py
```

## Deliverables

| Deliverable | Description | Location |
|---|---|---|
| Tactical HUD | Browser-based ISR monitoring interface | `src/hud/index.html` |
| Architecture Doc | System architecture & protocol specification | `dist/overwatch-architecture.docx` |
| Protocol Reference | Wire protocol constants and utilities | `src/shared/protocol.js` |
| Asset Simulator | WebSocket-based telemetry simulator | `src/simulation/drone-simulator.js` |
| Example Config | Companion computer YAML configuration | `config/overwatch.yaml` |
| Backend API | FastAPI ground control station | `backend/` |

## Ontology Overview

OVERWATCH models ISR operations using the following Gotham ontology objects:

- **Asset** -- An ISR platform (drone, sensor, vehicle) with identity, telemetry, and status
- **Taskforce** -- A coordinated group of assets operating under shared objectives
- **Mission** -- A planned operation with waypoints, objectives, and asset assignments
- **Observation** -- Sensor data or intelligence collected by an asset during a mission

## Tech Stack

- **HUD**: Single-file HTML, Leaflet.js, Canvas API, vanilla JS
- **Backend**: Python, FastAPI, WebSocket, SQLite
- **Document**: Generated via `docx` npm package
- **Simulation**: Client-side JS (HUD) / Node.js WebSocket (standalone)
- **Fonts**: JetBrains Mono, Outfit, IBM Plex Mono (Google Fonts)
- **Map**: CARTO dark basemap tiles

---

## New Capabilities (Sprints 1-4)

### Sensor Integration

Four new sensor sources feed the fusion pipeline.

- **ODID Decoder** (`backend/sensors/odid_decoder.py`) -- Decodes ASTM F3411 Remote ID binary frames. Handles BasicID, Location, System, SelfID, OperatorID, and packed message containers.
- **DJI DroneID Decoder** (`backend/sensors/dji_decoder.py`) -- Parses DJI OcuSync/AeroScope frames from AntSDR binary TCP (port 41030) and AntSDR CSV text (port 52002). Extracts serial, UAS/operator/home positions, speed, RSSI.
- **AntSDR TCP Source** (`backend/sensors/antsdr_source.py`) -- Async TCP adapter connecting to an AntSDR receiver. Reads binary DJI DroneID frames, decodes them, and yields Detection objects through the SensorSource interface.
- **UDP RID Source** (`backend/sensors/udp_rid_source.py`) -- Async UDP listener on port 9999 for pre-decoded Remote ID JSON lines. Each datagram is normalized and converted to a Detection.

To connect an AntSDR receiver, point the TCP source at the receiver's IP on port 41030 (binary) or 52002 (CSV). The source handles reconnection with configurable retries and backoff.

### Perception

The YOLOv11x detector node (`drone-sim/ros2_ws/src/drone_perception/drone_perception/detector_node.py`) subscribes to camera images and publishes `Detection2DArray` messages.

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `model_path` | `''` | Path to YOLOv11x weights file |
| `confidence_threshold` | `0.5` | Minimum confidence for detections |
| `nms_threshold` | `0.4` | Non-max suppression IoU threshold |
| `input_size` | `640` | Model input resolution |
| `device` | `cpu` | Inference device (`cpu`, `cuda:0`) |
| `max_detections` | `100` | Cap on detections per frame |

Detections flow into the threat pipeline: detector -> clutter filter -> track fusion (Kalman) -> swarm classification -> threat scoring -> effector allocation.

### RL Adversary Training

The `BulwarkEnv` (`backend/wargame/gym_env.py`) is a Gymnasium-compatible environment wrapping the WargameRunner. The RL agent plays as red force, choosing one of six `AdaptiveTactic` actions per tick. The observation is an 8-dimensional normalized feature vector from frame metrics.

```bash
cd backend
python -m scripts.train_rl_adversary --scenario saturation_1000 --timesteps 50000
python -m scripts.train_rl_adversary --scenario saturation_1000 --timesteps 90000 --curriculum
python -m scripts.train_rl_adversary --scenario saturation_1000 --timesteps 50000 --eval
```

- `--curriculum` enables three-phase curriculum learning (easy -> medium -> hard), splitting timesteps evenly across phases.
- `--eval` runs post-training evaluation against the `AdaptiveAttackerAI` baseline and prints a comparison.
- Supports PPO and DQN via `--algorithm`. Tensorboard logging when tensorboard is installed.

### Extension System

Extensions decouple subsystems from the monolithic coordinator so they can be loaded, started, and stopped independently. Based on Skybrush Server's ext_manager pattern.

**Lifecycle:** `UNLOADED -> LOADING -> LOADED -> RUNNING -> UNLOADED` (or `ERROR` on failure).

**Built-in extensions:**

| Extension | File | Purpose |
|---|---|---|
| Collision avoidance | `extensions/collision_ext.py` | Inter-asset separation enforcement |
| Geofence | `extensions/geofence_ext.py` | Boundary violation detection |
| Alerts | `extensions/alerts_ext.py` | Event notification dispatch |

**Creating a custom extension:**

Subclass `Extension` from `extensions/base.py`. Set `name` and optionally `dependencies`. Implement `load()`, `run()`, and `unload()`. The extension manager handles lifecycle ordering and dependency resolution.

### TAK Integration

The bi-directional CoT bridge (`backend/cot/bidirectional.py`) connects OVERWATCH to any TAK server (ATAK, WinTAK).

**Outbound (OVERWATCH -> TAK):**

- Tracks, threats, defenders, engagements, and swarm cluster area markers are formatted as CoT XML and sent to the TAK network.

**Inbound (TAK -> OVERWATCH):**

- `a-h-*` hostile reports become Tracks in the fusion pipeline.
- `a-f-*` friendly positions update the IFF deconfliction registry.
- `b-m-p-*` map markers become waypoint/objective dicts for mission planning.

**CoT type codes:**

| Code | Meaning |
|---|---|
| `a-h-A-M-H-Q` | Hostile air, quadrotor |
| `a-u-A-M-H-Q` | Unknown air, quadrotor |
| `a-f-A-M-H-Q` | Friendly air, quadrotor |
| `a-f-G-E-S` | Friendly ground sensor (OVERWATCH heartbeat) |
| `a-f-G-E-W` | Friendly ground weapon (defender effector) |
| `a-h-G` | Hostile ground (swarm cluster area) |

### Tools

Three CLI tools for benchmarking, replay, and training.

**Benchmark** -- Run scenarios N times and compare metrics:

```bash
cd backend
python -m scripts.benchmark --scenarios saturation_1000,contested_500 --runs 3
python -m scripts.benchmark --all --format csv
```

**Replay** -- Replay saved wargame recordings:

```bash
cd backend
python -m scripts.replay_wargame --recording path/to/recording.json.gz --speed 2.0
```

**RL Training** -- Train adversary agents (see RL Adversary Training section above):

```bash
cd backend
python -m scripts.train_rl_adversary --scenario saturation_1000 --timesteps 50000
```

### ROS2 Navigation

**Platform FSM** (`drone-sim/ros2_ws/src/drone_control/drone_control/platform_fsm.py`)

7 states: DISCONNECTED, DISARMED, ARMED, TAKING_OFF, FLYING, LANDING, EMERGENCY. 13 transitions driven by events (connect, disconnect, arm, disarm, takeoff, altitude_reached, land, ground_contact, fault, reset). Pure Python with zero ROS2 dependencies for unit testability.

**Preflight Check System** (`drone-sim/ros2_ws/src/drone_control/drone_control/preflight.py`)

7 checks run before every mission: battery level, GPS lock, connection state, armed state, flight mode, IMU health, and geofence clearance. Each check returns PASS, WARNING, SOFT_FAILURE, FAILURE, or SKIP. All thresholds are configurable. A FAILURE result blocks launch.

**OpenVINS Integration** (`drone-sim/ros2_ws/src/drone_navigation/drone_navigation/openvins_bridge.py`)

Bridges OpenVINS VIO output to DroneNexus topics (vio/pose, vio/odom, vio/active, vio/source). Drop-in replacement for vio_fallback.py. Falls back to IMU dead reckoning when OpenVINS data goes stale. Launch config and estimator YAML included.

**Motion Reference Handlers**

Unified motion command interface supporting Hover, Position, Speed, and Trajectory reference modes with automatic frame conversions.
