# NEXUS Backend — Ground Control Station

Python backend for the NEXUS drone swarm ground control station. Handles real-time MAVLink communication with PX4 drones, serves telemetry over WebSocket, and provides REST API for mission planning and swarm management.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run in simulation mode (default — no hardware needed)
python3 main.py

# Run in live mode (requires PX4 SITL or real drones)
NEXUS_SIMULATION_MODE=false python3 main.py
```

The backend starts on port **8765** by default:
- WebSocket telemetry: `ws://localhost:8765/telemetry/stream`
- REST API: `http://localhost:8765/api`
- API docs: `http://localhost:8765/docs`

## Connect the HUD

1. Open `src/hud/index.html` in a browser
2. Click the **SIM MODE** badge in the top bar
3. Enter `ws://localhost:8765/telemetry/stream`
4. Click **CONNECT**

## Architecture

```
main.py (FastAPI + uvicorn)
 ├── simulation/mock_drone.py    → MockSwarm (10Hz physics tick)
 ├── telemetry/aggregator.py     → SwarmAggregator (10Hz WebSocket broadcast)
 ├── swarm/coordinator.py        → SwarmCoordinator (formation + collision)
 ├── api/websocket.py            → WebSocket command handler
 ├── api/routes.py               → REST endpoints
 └── db/models.py                → SQLite persistence
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/drones | List all drones |
| POST | /api/drones/{id}/arm | Arm drone |
| POST | /api/drones/{id}/disarm | Disarm drone |
| POST | /api/swarm/takeoff | Swarm takeoff |
| POST | /api/swarm/land | Swarm land |
| POST | /api/swarm/emergency-stop | Emergency stop |
| POST | /api/swarm/formation | Set formation |
| POST | /api/swarm/speed | Set speed |
| POST | /api/swarm/altitude | Set altitude |
| POST | /api/mission/create | Create mission |
| POST | /api/mission/execute | Execute mission |
| POST | /api/mission/abort | Abort mission |
| GET | /api/swarm/health | Swarm health score |
| GET | /api/logs/commands | Command history |
| GET | /api/logs/events | Event history |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| NEXUS_SIMULATION_MODE | true | Use mock drones (true) or MAVSDK (false) |
| NEXUS_WS_PORT | 8765 | WebSocket/HTTP server port |
| NEXUS_TELEMETRY_RATE_HZ | 10 | Telemetry publish rate |
| NEXUS_SITL_DRONE_COUNT | 6 | Number of drones |
| NEXUS_SITL_BASE_PORT | 14540 | Base UDP port for PX4 SITL |
| NEXUS_DB_PATH | nexus.db | SQLite database path |
| NEXUS_SAFETY_BUBBLE_M | 5.0 | Collision avoidance radius |
| NEXUS_MAX_ALTITUDE_M | 120 | Maximum altitude |
| NEXUS_MAX_SPEED_MS | 20 | Maximum speed |

## Docker

```bash
docker-compose up
```

## Tests

```bash
cd backend
python3 tests/test_protocol.py
python3 tests/test_formations.py
python3 tests/test_collision.py
```

## Project Structure

```
backend/
├── main.py                  # FastAPI app entry point
├── config.py                # Settings/configuration
├── protocol.py              # Wire protocol Pydantic models
├── mavlink/
│   ├── connection.py        # MAVSDK connection manager
│   └── commands.py          # MAVLink command wrappers
├── swarm/
│   ├── coordinator.py       # Leader-follower coordination
│   ├── formations.py        # Formation geometry
│   └── collision.py         # Collision avoidance
├── api/
│   ├── routes.py            # REST endpoints
│   └── websocket.py         # WebSocket handler
├── telemetry/
│   ├── collector.py         # DroneState dataclass
│   └── aggregator.py        # 10Hz telemetry broadcast
├── missions/
│   ├── planner.py           # Mission planning
│   └── state_machine.py     # Mission state machine
├── db/
│   └── models.py            # SQLite persistence
├── simulation/
│   ├── mock_drone.py        # In-process drone simulator
│   └── sitl_launcher.py     # PX4 SITL launcher
└── tests/
```
