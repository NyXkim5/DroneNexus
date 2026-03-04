# OVERWATCH -- ISR Asset Coordination Platform

Built on Palantir Gotham. Multi-asset ISR coordination and telemetry platform. Hardware-agnostic, plug-and-play, designed for 2-50 assets.

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
