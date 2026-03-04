# OVERWATCH Backend -- Ground Control Station

Python backend for the OVERWATCH ISR asset coordination platform. Handles real-time MAVLink communication with assets, serves telemetry over WebSocket, and provides REST API for mission planning and taskforce management.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run in simulation mode (default -- no hardware needed)
python3 main.py

# Run in live mode (requires PX4 SITL or real assets)
OVERWATCH_SIMULATION_MODE=false python3 main.py
```

The backend starts on port **8765** by default:
- WebSocket telemetry: `ws://localhost:8765/telemetry/stream`
- REST API: `http://localhost:8765/api/v1`
- API docs: `http://localhost:8765/docs`

## Connect the HUD

1. Open `src/hud/index.html` in a browser
2. Click the **SIM MODE** badge in the top bar
3. Enter `ws://localhost:8765/telemetry/stream`
4. Click **CONNECT**

## Architecture

```
main.py (FastAPI + uvicorn)
 ├── simulation/mock_drone.py    -> MockSwarm (10Hz physics tick)
 ├── telemetry/aggregator.py     -> SwarmAggregator (10Hz WebSocket broadcast)
 ├── swarm/coordinator.py        -> SwarmCoordinator (formation + collision)
 ├── api/websocket.py            -> WebSocket command handler
 ├── api/routes.py               -> REST endpoints
 └── db/models.py                -> SQLite persistence
```

## API Endpoints

All endpoints are prefixed with `/api/v1`.

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/ontology/assets | List all assets |
| POST | /api/v1/actions/assets/{id}/launch-prep | Arm asset |
| POST | /api/v1/actions/assets/{id}/stand-down | Disarm asset |
| POST | /api/v1/actions/taskforce/launch | Taskforce launch |
| POST | /api/v1/actions/taskforce/recover | Taskforce land |
| POST | /api/v1/actions/taskforce/abort | Emergency stop |
| POST | /api/v1/overlays/formation | Set formation |
| POST | /api/v1/actions/taskforce/set-speed | Set speed |
| POST | /api/v1/actions/taskforce/set-altitude | Set altitude |
| GET | /api/v1/ontology/taskforce/health | Taskforce health score |
| POST | /api/v1/operations/create | Create operation |
| POST | /api/v1/operations/execute | Execute operation |
| POST | /api/v1/operations/abort | Abort operation |
| GET | /api/v1/activity/directives | Directive history |
| GET | /api/v1/activity/stream | Activity stream |
| GET | /api/v1/platform/status | Platform status |
| GET | /api/v1/platform/connections | WebSocket connections |
| POST | /api/v1/debrief/start | Start recording |
| POST | /api/v1/debrief/stop | Stop recording |
| GET | /api/v1/debrief/sessions | List sessions |
| POST | /api/v1/debrief/play | Replay session |
| POST | /api/v1/debrief/pause | Pause replay |
| GET | /api/v1/platform/devices/scan | Scan USB devices |
| POST | /api/v1/platform/devices/connect | Connect device |
| GET | /api/v1/platform/devices/connected | List connected devices |
| GET | /api/v1/isr/feeds/sources | List video sources |
| POST | /api/v1/isr/feeds/sources | Add video source |
| DELETE | /api/v1/isr/feeds/sources/{id} | Remove video source |

## Environment Variables

All variables use the `OVERWATCH_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| OVERWATCH_SIMULATION_MODE | true | Use mock assets (true) or MAVSDK (false) |
| OVERWATCH_WS_PORT | 8765 | WebSocket/HTTP server port |
| OVERWATCH_TELEMETRY_RATE_HZ | 10 | Telemetry publish rate |
| OVERWATCH_SITL_DRONE_COUNT | 6 | Number of assets |
| OVERWATCH_SITL_BASE_PORT | 14540 | Base UDP port for PX4 SITL |
| OVERWATCH_DB_PATH | overwatch.db | SQLite database path |
| OVERWATCH_SAFETY_BUBBLE_M | 5.0 | Collision avoidance radius |
| OVERWATCH_MAX_ALTITUDE_M | 120 | Maximum altitude |
| OVERWATCH_MAX_SPEED_MS | 20 | Maximum speed |
| OVERWATCH_AUTH_ENABLED | false | Enable API authentication |

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
│   ├── collision.py         # Collision avoidance
│   ├── geofence.py          # Geofence enforcement
│   └── alerts.py            # Alert management
├── api/
│   ├── routes.py            # REST endpoints
│   ├── websocket.py         # WebSocket handler
│   ├── auth.py              # Authentication
│   ├── export.py            # Data export
│   └── middleware.py        # Request middleware
├── telemetry/
│   ├── collector.py         # DroneState dataclass
│   ├── aggregator.py        # 10Hz telemetry broadcast
│   └── replay.py            # Telemetry recording & replay
├── missions/
│   ├── planner.py           # Mission planning
│   └── state_machine.py     # Mission state machine
├── db/
│   └── models.py            # SQLite persistence
├── simulation/
│   ├── mock_drone.py        # In-process asset simulator
│   └── sitl_launcher.py     # PX4 SITL launcher
├── video/                   # Video feed management
├── usb/                     # USB auto-detection
├── msp/                     # MSP protocol support
├── goggles/                 # FPV goggles integration
└── tests/
```
