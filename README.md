# NEXUS — Swarm Telemetry & Coordination System

Multi-drone swarm telemetry monitoring and coordination platform. Hardware-agnostic, plug-and-play, designed for 2-50 UAVs.

## Project Structure

```
NEXUS/
├── src/
│   ├── hud/
│   │   └── index.html              # Single-file tactical HUD (HTML/CSS/JS)
│   ├── docs/
│   │   └── generate-doc.js         # Architecture document generator (docx)
│   ├── shared/
│   │   └── protocol.js             # Wire protocol constants & utilities
│   └── simulation/
│       └── drone-simulator.js      # Standalone WebSocket drone simulator
├── dist/                           # Build output
│   ├── nexus-hud.html              # Packaged HUD
│   └── nexus-architecture.docx     # Generated architecture document
├── config/
│   └── nexus.yaml                  # Example companion computer config
├── scripts/
│   ├── build.sh                    # Full build (doc + HUD packaging)
│   └── dev-server.sh               # Local dev server for HUD
├── tests/
│   └── protocol.test.js            # Protocol unit tests
├── docs/                           # Supplementary documentation
│   ├── architecture/
│   ├── protocol/
│   ├── formations/
│   └── safety/
└── assets/
    ├── icons/
    └── screenshots/
```

## Quick Start

### View the HUD

Open `src/hud/index.html` directly in a browser. The simulation runs client-side — no server needed.

### Build All Deliverables

```bash
npm install
npm run build
```

Output:
- `dist/nexus-hud.html` — Tactical HUD
- `dist/nexus-architecture.docx` — Architecture document

### Run Tests

```bash
node tests/protocol.test.js
```

### Run Standalone Simulator

```bash
npm install ws
node src/simulation/drone-simulator.js --drones 6 --port 8765
```

## Deliverables

| Deliverable | Description | Location |
|---|---|---|
| Tactical HUD | Browser-based swarm monitoring interface | `src/hud/index.html` |
| Architecture Doc | System architecture & protocol specification | `dist/nexus-architecture.docx` |
| Protocol Reference | Wire protocol constants and utilities | `src/shared/protocol.js` |
| Drone Simulator | WebSocket-based telemetry simulator | `src/simulation/drone-simulator.js` |
| Example Config | Companion computer YAML configuration | `config/nexus.yaml` |

## Tech Stack

- **HUD**: Single-file HTML, Leaflet.js, Canvas API, vanilla JS
- **Document**: Generated via `docx` npm package
- **Simulation**: Client-side JS (HUD) / Node.js WebSocket (standalone)
- **Fonts**: JetBrains Mono, Outfit, IBM Plex Mono (Google Fonts)
- **Map**: CARTO dark basemap tiles

## Swarm Protocol

- V-formation with real offset vectors
- Modified Raft consensus for leader election
- Market-based task allocation
- Collision avoidance with 5m safety bubbles
- Dual-transport redundancy (WiFi + RFD900x)
