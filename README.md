# OVERWATCH -- ISR Asset Coordination Platform

Multi-asset ISR coordination and telemetry platform. Hardware-agnostic, plug-and-play, designed for 2-50 assets.

## BULWARK -- Counter-Swarm Defense Engine

BULWARK is an autonomous counter-swarm defense engine built on OVERWATCH. One
decision engine consumes any sensor source and fuses it into one real-time
picture, classifies swarm threats, allocates finite effectors, and resolves
engagements. The headline metric is the cost-exchange ratio, defender dollars
spent per attacker dollar destroyed, with the goal of driving it below one.

Full design and module map: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project Structure

```
OVERWATCH/
├── .github/workflows/ci.yml           # GitHub Actions CI pipeline
├── .env.example                        # Environment variable template
├── Dockerfile                          # Multi-stage production build
├── docker-compose.yml                  # Full stack (backend + nginx)
├── nginx/                              # TLS reverse proxy config
│   ├── nginx.conf
│   ├── Dockerfile
│   └── generate-dev-certs.sh
├── src/
│   ├── hud/
│   │   ├── index.html                  # Single-file tactical HUD (HTML/CSS/JS)
│   │   ├── bulwark.html                # BULWARK C2 + defense HUD
│   │   └── 3d-view.html                # 3D asset visualization
│   ├── docs/
│   │   └── generate-doc.js             # Architecture document generator (docx)
│   ├── shared/
│   │   └── protocol.js                 # Wire protocol constants and utilities
│   └── simulation/
│       └── drone-simulator.js          # Standalone WebSocket asset simulator
├── backend/                            # Python FastAPI backend
│   ├── api/
│   │   ├── auth.py                     # JWT authentication + RBAC
│   │   ├── rate_limiter.py             # WebSocket per-topic rate limiting
│   │   ├── metrics.py                  # Prometheus metrics endpoint
│   │   ├── routes.py                   # REST API routes
│   │   └── websocket.py               # WebSocket server + CoT wiring
│   ├── decision/
│   │   ├── engine.py                   # Decision engine
│   │   └── roe.py                      # Rules of engagement gate
│   ├── extensions/                     # Plugin system (load/run/unload lifecycle)
│   │   ├── base.py                     # Extension ABC
│   │   ├── manager.py                  # Lifecycle + dependency resolution
│   │   ├── collision_ext.py            # Inter-asset separation enforcement
│   │   ├── geofence_ext.py            # Boundary violation detection
│   │   └── alerts_ext.py              # Event notification dispatch
│   ├── registries/                     # Typed registries with event callbacks
│   │   ├── base.py                     # Generic Registry[T]
│   │   ├── drone_registry.py           # Asset state registry
│   │   └── connection_registry.py      # Client connection registry
│   ├── sensors/                        # SensorSource interface + adapters
│   ├── fusion/                         # Multi-sensor track fusion (Kalman)
│   ├── threat/                         # Swarm classification + scoring
│   ├── defense/                        # Effector allocation + cost ledger
│   ├── cot/                            # Bidirectional TAK/CoT integration
│   ├── wargame/                        # Runner, scenarios, RL gym, replay
│   ├── attacker/                       # Red force AI
│   └── scripts/                        # CLI tools (benchmark, replay, RL training)
├── drone-sim/ros2_ws/                  # ROS2 simulation workspace
├── config/
│   └── overwatch.yaml                  # Companion computer config
├── dist/                               # Build output
└── tests/
    └── protocol.test.js                # Protocol unit tests
```

## Quick Start

```bash
# View the HUD (no server needed)
open src/hud/index.html

# Run the backend
cd backend
pip install -r requirements.txt
python3 main.py

# Run a wargame
python -m wargame.run --scenario saturation_1000 --fast

# Run tests
python -m pytest -m "not slow"       # 1004+ fast tests, about 20s
python -m pytest                     # full suite including end-to-end wargames
```

## Operator C2 Interface

The BULWARK HUD (`src/hud/bulwark.html`) provides full command-and-control over taskforce assets.

**Taskforce Controls.** Six buttons for fleet-wide commands: ARM, DISARM, LAUNCH, RECOVER, RTL, and ABORT. Each command targets all assets or a selected subset.

**Formation Selector.** Six formation presets: V, LINE, COLUMN, DIAMOND, ORBIT, SCATTER. Click to apply. The active formation is highlighted.

**Speed and Altitude Sliders.** Speed range: 0-30 m/s. Altitude range: 10-120 m AGL. Changes are sent to assets on slider release.

**Click-to-GOTO.** Toggle the crosshair mode, click any point on the map, and all selected assets navigate to that location. A pulsing ring confirms the target point.

**Mission Planner.** Click the map to add waypoints. Drag rows to reorder. Cycle waypoint type (GOTO, ORBIT, LOITER, RTL) by clicking the type badge. Hit Execute to push the plan to assets.

**ROE Status Dashboard.** Displays the current weapons posture (WEAPONS FREE or WEAPONS HOLD) with a color-coded indicator. Lists each ROE condition (Positive ID, Within Corridor, Threat Imminent, Authorized Weapon, Altitude Floor, Civilian Clear) with met/unmet status. Shows a live authorization and denial feed for engagement decisions.

**Auto-Reconnect with Command Queue.** If the WebSocket drops, the HUD enters reconnect mode with exponential backoff. Commands issued while disconnected are queued and flushed on reconnect. A status bar shows connection state.

## Sensor Integration

Four sensor sources feed the fusion pipeline.

- **ODID Decoder** (`backend/sensors/odid_decoder.py`) -- ASTM F3411 Remote ID binary parser.
- **DJI DroneID Decoder** (`backend/sensors/dji_decoder.py`) -- DJI OcuSync/AeroScope frame parser (binary TCP port 41030, CSV port 52002).
- **AntSDR TCP Source** (`backend/sensors/antsdr_source.py`) -- Async TCP adapter for AntSDR receivers with reconnection and backoff.
- **UDP RID Source** (`backend/sensors/udp_rid_source.py`) -- Async UDP listener (port 9999) for pre-decoded Remote ID JSON.

## TAK Integration

Bidirectional CoT bridge (`backend/cot/`) connects OVERWATCH to any TAK server.

**Outbound:** Tracks, threats, defenders, engagements, and swarm cluster area markers formatted as CoT XML.

**Inbound:** Hostile reports become Tracks. Friendly positions update IFF. Map markers become waypoints for mission planning.

## Wargame and RL Training

```bash
cd backend
python -m wargame.run --list                                   # list scenarios
python -m wargame.run --scenario saturation_1000 --fast        # fast batch run
python -m wargame.run --scenario contested_500 --audit run.db  # audit log

python -m scripts.benchmark --scenarios saturation_1000,contested_500 --runs 3
python -m scripts.replay_wargame --recording path/to/rec.json.gz --speed 2.0
python -m scripts.train_rl_adversary --scenario saturation_1000 --timesteps 50000
```

The `BulwarkEnv` (`backend/wargame/gym_env.py`) wraps the WargameRunner as a Gymnasium environment. Supports PPO and DQN via Stable-Baselines3. Curriculum learning splits training across easy, medium, and hard phases.

## ROS2 Navigation

- **Platform FSM** -- 7 states, 13 transitions. Pure Python, no ROS2 dependency for unit testing.
- **Preflight Checks** -- 7 checks (battery, GPS, connection, armed, mode, IMU, geofence). FAILURE blocks launch.
- **OpenVINS Bridge** -- VIO output to DroneNexus topics with IMU dead-reckoning fallback.
- **Motion Handlers** -- Hover, Position, Speed, and Trajectory reference modes with frame conversions.

## Extension System

Extensions decouple subsystems from the coordinator. Lifecycle: UNLOADED -> LOADED -> RUNNING. The manager resolves dependencies and handles ordered startup/shutdown.

Built-in: collision avoidance, geofence enforcement, alert dispatch. Create custom extensions by subclassing `Extension` from `extensions/base.py`.

## Security

- **Authentication.** JWT tokens for REST and WebSocket. SHA-256 password hashing. Auth is toggle-controlled via `OverwatchSettings.auth_enabled`.
- **Transport.** TLS termination via Nginx reverse proxy. Certificates mounted read-only into the container.
- **Secrets.** All secrets loaded from environment variables. `.env.example` documents required values. No secrets in code or git history.
- **Container hardening.** Read-only filesystem, non-root user, `no-new-privileges`, resource limits, tmpfs for ephemeral writes.

## CI/CD

**GitHub Actions** (`.github/workflows/ci.yml`):
- Pull requests: fast test suite (`pytest -m "not slow"`).
- Push to main: full suite with coverage report.
- Concurrency groups cancel superseded runs.

**Docker:**
- Multi-stage build. Builder stage compiles dependencies. Runtime stage runs as non-root `overwatch` user.
- Healthcheck on `/health` endpoint. JSON file logging with size rotation.

## Deployment

```bash
cp .env.example .env
# Edit .env with production secrets (JWT_SECRET, auth credentials, TAK endpoints)
docker compose up -d
```

The stack runs two containers: `overwatch` (FastAPI on port 8000) and `overwatch-nginx` (TLS proxy on ports 80/443). Nginx waits for the backend healthcheck before accepting traffic. Persistent data is stored in the `overwatch-data` volume.

## Tech Stack

- **HUD**: Single-file HTML, Leaflet.js, Canvas API, vanilla JS
- **Backend**: Python 3.12, FastAPI, WebSocket, SQLite, asyncio
- **Protocols**: MAVLink, MSP, CoT/TAK
- **Simulation**: ROS2 (drone-sim), client-side JS (HUD), Node.js WebSocket (standalone)
- **RL**: Gymnasium, Stable-Baselines3 (PPO/DQN)
- **Infra**: Docker, Nginx, GitHub Actions

## Ontology Overview

OVERWATCH models ISR operations using the following Gotham ontology objects:

- **Asset** -- An ISR platform (drone, sensor, vehicle) with identity, telemetry, and status
- **Taskforce** -- A coordinated group of assets operating under shared objectives
- **Mission** -- A planned operation with waypoints, objectives, and asset assignments
- **Observation** -- Sensor data or intelligence collected by an asset during a mission
