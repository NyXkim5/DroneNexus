# OVERWATCH Gotham Transformation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the NEXUS drone telemetry system into OVERWATCH, a Palantir Gotham-style ISR platform — full-stack rebrand covering protocol, backend, frontend, config, and docs.

**Architecture:** Bottom-up transformation. Start with the shared protocol layer (foundation), then backend (config, models, routes), then the HUD (complete visual redesign to Gotham aesthetic), then config/scripts/docs. Each layer builds on the previous.

**Tech Stack:** Python/FastAPI (backend), single-file HTML/CSS/JS with Leaflet (HUD), WebSocket telemetry, Node.js simulator

**Design doc:** `docs/plans/2026-03-03-overwatch-gotham-transformation-design.md`

---

## Task 1: Protocol Layer — Shared Constants (JS + Python)

Rename all protocol identifiers across both the JS and Python protocol files. This is the foundation everything else depends on.

**Files:**
- Modify: `src/shared/protocol.js`
- Modify: `backend/protocol.py`
- Modify: `tests/protocol.test.js`

**Step 1: Update `src/shared/protocol.js`**

Rename branding in comments/docstrings from NEXUS to OVERWATCH. Update MessageType enum values:

```javascript
const MessageType = {
  ASSET_STATE:  'ASSET_STATE',     // was TELEM
  HEARTBEAT:    'HEARTBEAT',       // unchanged
  DIRECTIVE:    'DIRECTIVE',       // was CMD
  OVERLAY_UPDATE: 'OVERLAY_UPDATE', // was FORMATION
  OBJECTIVE:    'OBJECTIVE',       // was WAYPOINT
  PEER_STATE:   'PEER_STATE',      // was PEER
  ACTIVITY:     'ACTIVITY',        // was ALERT
  ACK:          'ACK',             // unchanged
  ISR_CTRL:     'ISR_CTRL',        // was VIDEO_CTRL
  SENSOR_CTRL:  'SENSOR_CTRL',     // was CAMERA_CTRL
  MSP_STATE:    'MSP_STATE',       // was MSP_TELEM
  HMD_STATE:    'HMD_STATE',       // was GOGGLES
  RECORD_CTRL:  'RECORD_CTRL',     // was DVR_CTRL
  DEVICE_SCAN:  'DEVICE_SCAN',     // unchanged
};
```

Update DroneRole to AssetClassification:

```javascript
const AssetClassification = {
  PRIMARY:   'PRIMARY',     // was LEADER
  ESCORT:    'ESCORT',      // was WINGMAN
  ISR:       'ISR',         // was RECON
  LOGISTICS: 'LOGISTICS',   // was SUPPORT
  OVERWATCH: 'OVERWATCH',   // was TAIL
};
```

Update DroneStatus to OperationalStatus:

```javascript
const OperationalStatus = {
  NOMINAL:     'NOMINAL',      // was ACTIVE
  DEGRADED:    'DEGRADED',     // was LOW_BATT
  COMMS_DEGRADED: 'COMMS_DEGRADED', // was WEAK_SIGNAL
  RTB:         'RTB',          // was RTL
  GROUNDED:    'GROUNDED',     // was LANDED
  OFFLINE:     'OFFLINE',      // was LOST
  ISR_SOLO:    'ISR_SOLO',     // was FPV_SOLO
};
```

Update FormationType to OverlayType:

```javascript
const OverlayType = {
  V_FORMATION:   'V_FORMATION',
  LINE_ABREAST:  'LINE_ABREAST',
  COLUMN:        'COLUMN',
  DIAMOND:       'DIAMOND',
  ORBIT:         'ORBIT',
  SCATTER:       'SCATTER',
};
```

Update CommandType to DirectiveType:

```javascript
const DirectiveType = {
  LAUNCH_PREP:      'LAUNCH_PREP',      // was ARM
  STAND_DOWN:       'STAND_DOWN',       // was DISARM
  LAUNCH:           'LAUNCH',           // was TAKEOFF
  RECOVER:          'RECOVER',          // was LAND
  RTB:              'RTB',              // was RTL
  GOTO:             'GOTO',
  SET_MODE:         'SET_MODE',
  SET_OVERLAY:      'SET_OVERLAY',      // was SET_FORMATION
  SET_SPEED:        'SET_SPEED',
  SET_ALTITUDE:     'SET_ALTITUDE',
  ABORT:            'ABORT',            // was EMERGENCY_STOP
  SENSOR_TILT:      'SENSOR_TILT',      // was CAMERA_TILT
  SENSOR_RECORD:    'SENSOR_RECORD',    // was CAMERA_RECORD
  SENSOR_CAPTURE:   'SENSOR_CAPTURE',   // was CAMERA_PHOTO
  GIMBAL_CONTROL:   'GIMBAL_CONTROL',
  MSP_LAUNCH_PREP:  'MSP_LAUNCH_PREP',  // was MSP_ARM
  MSP_STAND_DOWN:   'MSP_STAND_DOWN',   // was MSP_DISARM
  MSP_SET_MODE:     'MSP_SET_MODE',
};
```

Rename factory functions:
- `createTelemetryPacket` → `createAssetStatePacket`
- `createFPVTelemetryPacket` → `createISRStatePacket`
- `createVideoControlPacket` → `createISRControlPacket`

Update all internal references (field names in packets stay the same for wire compatibility, but variable names and exports change).

Update exports block to use new names.

**Step 2: Update `backend/protocol.py`**

Mirror all enum renames from Step 1 into the Python Pydantic models. Update:
- `MessageType` enum values
- `DroneRole` → `AssetClassification`
- `DroneStatus` → `OperationalStatus`
- `FormationType` → `OverlayType`
- `CommandType` → `DirectiveType`
- Class names: `TelemetryPacket` → `AssetStatePacket`, `CommandPacket` → `DirectivePacket`
- Docstring references from NEXUS to OVERWATCH

Keep field names in models identical (position.lat, position.lon, battery.remaining_pct, etc.) so the wire format doesn't break existing HUD parsing during incremental migration.

**Step 3: Update `tests/protocol.test.js`**

Update all test imports and assertions to use new constant names. Update test descriptions.

**Step 4: Run protocol tests**

Run: `cd /Users/jay/DroneNexus && node tests/protocol.test.js`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/shared/protocol.js backend/protocol.py tests/protocol.test.js
git commit -m "refactor: rename protocol layer from NEXUS to OVERWATCH ontology"
```

---

## Task 2: Backend Config & Settings

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/db/models.py`
- Modify: `backend/Dockerfile`
- Modify: `backend/docker-compose.yml`

**Step 1: Rename config classes in `backend/config.py`**

- `NexusSettings` → `OverwatchSettings`
- `env_prefix: "NEXUS_"` → `"OVERWATCH_"`
- `db_path: str = "nexus.db"` → `"overwatch.db"`
- `DroneConfig` → `AssetConfig` (with fields: id → designator, role → classification)
- `DRONE_FLEET` → `ASSET_ROSTER`
- Update role values to match new AssetClassification: LEADER→PRIMARY, WINGMAN→ESCORT, RECON→ISR, SUPPORT→LOGISTICS, TAIL→OVERWATCH
- Docstrings: NEXUS → OVERWATCH

**Step 2: Update `backend/db/models.py`**

- `NexusDB` → `OverwatchDB`
- Database table names: keep functional (commands, events, telemetry) — no NEXUS references in table names
- Docstrings: NEXUS → OVERWATCH

**Step 3: Update `backend/Dockerfile`**

- `ENV NEXUS_SIMULATION_MODE=true` → `ENV OVERWATCH_SIMULATION_MODE=true`
- `ENV NEXUS_DB_PATH=...` → `ENV OVERWATCH_DB_PATH=...`

**Step 4: Update `backend/docker-compose.yml`**

- Service name and environment variables: NEXUS_ → OVERWATCH_

**Step 5: Update all imports of NexusSettings/NexusDB across backend**

Search all Python files importing `NexusSettings` or `NexusDB` and update to new names. Key files:
- `backend/main.py` (NexusSettings, NexusDB, DRONE_FLEET imports)
- `backend/telemetry/aggregator.py`
- `backend/swarm/coordinator.py`
- `backend/simulation/mock_drone.py`
- `backend/tests/test_api.py`
- Any other file importing from config or db.models

**Step 6: Update `backend/main.py` class names and branding**

- `NexusApp` → `OverwatchApp`
- `nexus_app` → `overwatch_app`
- FastAPI title: "NEXUS Ground Control Station" → "OVERWATCH ISR Platform"
- All logger messages: "NEXUS" → "OVERWATCH"
- All docstrings

**Step 7: Verify backend starts**

Run: `cd /Users/jay/DroneNexus/backend && python3 -c "from config import OverwatchSettings; print(OverwatchSettings())"`
Expected: Settings object prints without error

**Step 8: Commit**

```bash
git add backend/config.py backend/db/models.py backend/main.py backend/Dockerfile backend/docker-compose.yml
git add -u backend/  # catch all import updates
git commit -m "refactor: rename backend config and core classes to OVERWATCH"
```

---

## Task 3: Backend API Routes Transformation

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `backend/api/websocket.py`
- Modify: `backend/api/auth.py`
- Modify: `backend/api/export.py`
- Modify: `backend/api/middleware.py`
- Modify: `backend/main.py` (router mount paths)

**Step 1: Update route paths in `backend/api/routes.py`**

Remap all routes per the design doc:
- `GET /drones` → `GET /ontology/assets`
- `POST /drones/{drone_id}/arm` → `POST /actions/assets/{asset_id}/launch-prep`
- `POST /drones/{drone_id}/disarm` → `POST /actions/assets/{asset_id}/stand-down`
- `POST /swarm/takeoff` → `POST /actions/taskforce/launch`
- `POST /swarm/land` → `POST /actions/taskforce/recover`
- `POST /swarm/emergency-stop` → `POST /actions/taskforce/abort`
- `POST /swarm/formation` → `POST /overlays/formation`
- `POST /swarm/speed` → `POST /actions/taskforce/set-speed`
- `POST /swarm/altitude` → `POST /actions/taskforce/set-altitude`
- `GET /swarm/health` → `GET /ontology/taskforce/health`
- `POST /mission/create` → `POST /operations/create`
- `POST /mission/execute` → `POST /operations/execute`
- `POST /mission/abort` → `POST /operations/abort`
- `GET /logs/commands` → `GET /activity/directives`
- `GET /logs/events` → `GET /activity/stream`
- `GET /connections` → `GET /platform/connections`
- `GET /status` → `GET /platform/status`
- Replay routes: `/replay/*` → `/debrief/*`
- Device routes: `/devices/*` → `/platform/devices/*`
- Video routes: `/video/*` → `/isr/feeds/*`

Update function names, docstrings, parameter names (drone_id → asset_id where applicable in path params).

**Step 2: Update route prefixes in `backend/main.py`**

```python
app.include_router(api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(export_router, prefix="/api/v1/products")
```

**Step 3: Update WebSocket paths in `backend/main.py`**

```python
@app.websocket("/ws/v1/stream")
async def telemetry_websocket(websocket: WebSocket):
    await overwatch_app.ws_handler.handle(websocket)

@app.websocket("/ws/v1/compat")
async def ws_compat(websocket: WebSocket):
    await overwatch_app.ws_handler.handle(websocket)
```

**Step 4: Update `backend/api/websocket.py`**

- Update message type strings to match new protocol enums
- `CMD` → `DIRECTIVE`, command dispatch mapping updated
- STATE_SYNC payload key: `drones` → `assets`
- Docstrings: NEXUS → OVERWATCH

**Step 5: Update `backend/api/auth.py`**

- Docstrings: NEXUS → OVERWATCH
- JWT secret env var: `NEXUS_JWT_SECRET` → `OVERWATCH_JWT_SECRET`
- Default secret: `"nexus-dev-secret-change-in-prod"` → `"overwatch-dev-secret-change-in-prod"`

**Step 6: Update `backend/api/export.py`**

- Route paths: already handled by prefix change
- KML/CSV content: rename references in generated output
- Docstrings

**Step 7: Verify backend starts and serves API docs**

Run:
```bash
cd /Users/jay/DroneNexus/backend
# Kill existing server
lsof -ti:8765 | xargs kill -9 2>/dev/null
python3 main.py &
sleep 3
curl -s http://localhost:8765/docs | head -3
curl -s http://localhost:8765/api/v1/platform/status
kill %1
```
Expected: Swagger docs load, status endpoint returns JSON

**Step 8: Commit**

```bash
git add -u backend/
git commit -m "refactor: transform API routes to Gotham ontology structure"
```

---

## Task 4: Backend Supporting Modules

Rename NEXUS references across all remaining backend modules. These are mostly docstring/logging/comment changes.

**Files:**
- Modify: `backend/telemetry/aggregator.py`
- Modify: `backend/telemetry/collector.py`
- Modify: `backend/telemetry/replay.py`
- Modify: `backend/swarm/coordinator.py`
- Modify: `backend/swarm/formations.py`
- Modify: `backend/swarm/collision.py`
- Modify: `backend/swarm/geofence.py`
- Modify: `backend/swarm/alerts.py`
- Modify: `backend/missions/planner.py`
- Modify: `backend/missions/state_machine.py`
- Modify: `backend/mavlink/connection.py`
- Modify: `backend/mavlink/commands.py`
- Modify: `backend/msp/protocol.py`
- Modify: `backend/msp/translator.py`
- Modify: `backend/msp/connection.py`
- Modify: `backend/msp/commands.py`
- Modify: `backend/simulation/mock_drone.py`
- Modify: `backend/simulation/sitl_launcher.py`
- Modify: `backend/video/stream_proxy.py`
- Modify: `backend/video/webrtc_signaling.py`
- Modify: `backend/goggles/bridge.py`
- Modify: `backend/usb/scanner.py`
- Modify: `backend/usb/auto_connect.py`

**Step 1: Batch rename NEXUS → OVERWATCH in all docstrings, comments, and logger names**

For each file:
- Replace `"nexus` logger names with `"overwatch`
- Replace `NEXUS` in docstrings/comments with `OVERWATCH`
- Update any class/function references that imported old names (NexusSettings, NexusApp, etc.)

Key functional renames:
- `backend/swarm/coordinator.py`: `SwarmCoordinator` stays (functional name, not branded). Update `NexusSettings` import.
- `backend/simulation/mock_drone.py`: `MockSwarm` stays. Update config imports. Update role references to new AssetClassification values.
- `backend/telemetry/collector.py`: `DroneState` → keep as-is internally (it's a data class, not user-facing). Update imports.
- `backend/swarm/alerts.py`: Update alert source strings from "NEXUS" to "OVERWATCH". Update event templates to use Gotham language.

**Step 2: Update alert/event message templates in `backend/swarm/alerts.py`**

Transform event messages to Gotham operational language:
- "Drone {id} battery low" → "Asset {id} power state degraded"
- "Formation cohesion degraded" → "Overlay integrity below threshold"
- "Link quality poor" → "COMMLINK degraded"
- etc.

**Step 3: Run backend tests**

Run: `cd /Users/jay/DroneNexus/backend && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -30`
Expected: Tests pass (some may need updates — fix in next step)

**Step 4: Fix any failing backend tests**

Update test files:
- `backend/tests/test_api.py` — route paths, response field names
- `backend/tests/test_protocol.py` — enum values
- `backend/tests/test_formations.py` — role references
- `backend/tests/test_collision.py` — status references
- `backend/tests/test_geofence.py` — config references
- `backend/tests/test_msp.py` — protocol references
- `backend/tests/test_usb_scanner.py` — config references
- `backend/tests/test_stress.py` — endpoint paths

**Step 5: Run tests again**

Run: `cd /Users/jay/DroneNexus/backend && python3 -m pytest tests/ -v --tb=short`
Expected: All pass

**Step 6: Commit**

```bash
git add -u backend/
git commit -m "refactor: complete backend OVERWATCH transformation"
```

---

## Task 5: Frontend HUD — Visual Design System

This is the most critical task for the portfolio. Complete visual transformation of `src/hud/index.html` from neon-tactical to Palantir Gotham institutional aesthetic.

**Files:**
- Modify: `src/hud/index.html`

**Step 1: Replace CSS color palette**

Replace all CSS custom properties and hardcoded color values:

```css
:root {
  /* OVERWATCH — Palantir Gotham Design System */
  --bg:           #0d1117;
  --panel:        #161b22;
  --panel-elevated: #1c2128;
  --border:       #21262d;
  --border-light: #30363d;
  --text:         #e6edf3;
  --text-secondary: #7d8590;
  --text-tertiary: #484f58;
  --accent:       #4493f8;    /* Palantir blue */
  --green:        #3fb950;
  --amber:        #d29922;
  --red:          #f85149;
  --cyan:         #4493f8;    /* unified with accent */
  --purple:       #a371f7;
  --classification-bg: #d29922;
  --classification-text: #000000;

  --font-ui:    'Inter', system-ui, -apple-system, sans-serif;
  --font-data:  'JetBrains Mono', 'IBM Plex Mono', monospace;
  --font-label: 'Inter', system-ui, sans-serif;
}
```

**Step 2: Replace typography**

- Remove Google Fonts link for Outfit
- Add Inter font (Google Fonts or system-ui fallback)
- All UI text: Inter, 13px base
- All labels: Inter, 11px, uppercase, letter-spacing: 0.05em
- All data/telemetry: JetBrains Mono, 12px
- Remove all `text-shadow` glow effects
- Remove all `box-shadow` glow effects

**Step 3: Replace panel styling**

- Remove `backdrop-filter: blur()` glassmorphism
- Panels: flat `background: var(--panel)`, `border: 1px solid var(--border)`
- No border-radius on panels (0 or 2px max)
- Cards inside panels: 4px border-radius, `var(--panel-elevated)` background
- Hover: background transition to `var(--border-light)`
- Selected: left `3px solid var(--accent)` border

**Step 4: Replace animations**

- Remove `pulse-dot` neon pulse — replace with subtle opacity pulse (0.6 → 1.0)
- Remove `emergency-flash` — replace with steady red background
- Keep `toast-in` slide animation but simplify
- No glowing anything

**Step 5: Update scrollbar styling**

- Track: `var(--bg)`
- Thumb: `var(--border)`, hover `var(--border-light)`
- 4px width (thinner, more subtle)

**Step 6: Verify visual changes by opening in browser**

Run: `open /Users/jay/DroneNexus/src/hud/index.html`
Expected: Dark, flat, institutional look. No neon, no glow.

**Step 7: Commit**

```bash
git add src/hud/index.html
git commit -m "design: replace visual design system with Palantir Gotham aesthetic"
```

---

## Task 6: Frontend HUD — Layout & Structure

Transform the HUD layout from tactical gaming to Gotham intelligence platform.

**Files:**
- Modify: `src/hud/index.html`

**Step 1: Add classification banners**

Add top and bottom classification banners:

```html
<div class="classification-banner">UNCLASSIFIED // FOR OFFICIAL USE ONLY</div>
<!-- ... entire app ... -->
<div class="classification-banner">UNCLASSIFIED // FOR OFFICIAL USE ONLY</div>
```

```css
.classification-banner {
  background: var(--classification-bg);
  color: var(--classification-text);
  text-align: center;
  font: bold 11px var(--font-ui);
  letter-spacing: 0.1em;
  padding: 3px 0;
  text-transform: uppercase;
  user-select: none;
  z-index: 9999;
}
```

**Step 2: Redesign top bar**

Replace neon NEXUS brand with OVERWATCH Gotham header:

- Left: Diamond icon + "OVERWATCH" in clean sans-serif (Inter 600, 16px, letter-spacing 0.15em, white)
- Below brand: "GOTHAM // ISR PLATFORM" in `var(--text-secondary)`, 10px, uppercase
- Center: Mode tabs — OBSERVE | TASK | DEBRIEF | ISR FEED (styled as underline tabs, not pill buttons)
- Right: Operational status badge ("OPERATIONAL" green dot + text), Task Force callsign "TF-OVERWATCH-01", UTC clock in military format "03MAR2026 1641Z"

**Step 3: Redesign left panel as Object Explorer**

- Panel header: "OBJECT EXPLORER" (11px, uppercase, letter-spacing, `var(--text-secondary)`)
- Collapsible tree sections:
  - `▸ ASSETS (6)` — expandable, shows all drones
  - `▸ OPERATIONS (0)` — expandable, shows active missions
  - `▸ AREAS OF INTEREST (1)` — expandable, shows geofences
- Each asset item in tree:
  - Small colored status dot (4px, no glow)
  - Designator: "ALPHA-1" in mono font
  - Classification badge: "PRIMARY" in small pill (accent blue bg for primary, gray for others)
  - Status: "NOMINAL" in `var(--green)`, small text
  - On hover: background `var(--border)` transition
  - On select: left `3px solid var(--accent)`, background `var(--panel-elevated)`
- Remove the old drone card stats grid (alt/spd/batt) from the list — that data moves to the Inspector

**Step 4: Redesign right panel as Inspector**

Replace drone detail panel with tabbed Inspector:

- Header: "{DESIGNATOR} // {CLASSIFICATION}" (e.g., "ALPHA-1 // PRIMARY")
- Tab bar: PROPERTIES | RELATIONSHIPS | TIMELINE (underline tabs)

**Properties tab** (default):
- Clean key-value table layout, no cards
- Sections with thin dividers:
  - POSITION: Lat, Lon, Alt AGL, Alt MSL
  - VELOCITY: Ground Speed, Vertical Speed, Heading
  - POWER STATE: Battery %, Voltage, Current (battery bar: thin 2px, accent-colored)
  - POSITIONING: Fix Type, Satellites, HDOP
  - COMMLINK: RSSI, Quality, Latency
  - OPERATIONAL STATUS: Status badge
- Each row: label (11px, uppercase, `var(--text-secondary)`) | value (12px mono, `var(--text)`)

**Relationships tab:**
- Linked object cards:
  - "MEMBER_OF → TF-OVERWATCH-01" (with clickable link)
  - "REPORTS_TO → ALPHA-1" (if not leader)
  - "CLASSIFICATION → PRIMARY"
- Each relationship: small card with left accent border colored by relationship type

**Timeline tab:**
- Chronological event list for this specific asset
- Each event: timestamp (mono) + description
- Scrollable, most recent on top

**Step 5: Redesign bottom panel**

- Left section: "ACTIVITY STREAM" — scrollable event feed
  - Each item: `[HHMMz] [SOURCE] message` in mono font
  - No severity colors on background — use small colored dot (4px) before each entry
- Right section: Three metric cards
  - "TF HEALTH" — percentage + thin sparkline
  - "COMMLINK" — latency + status badge (NOMINAL / DEGRADED)
  - "POWER STATE" — avg battery % + range
- Sparklines: thin 1px line, no gradient fill, subtle

**Step 6: Update attitude indicator**

Keep the attitude indicator ball but restyle:
- Remove bright colors — use muted blue/brown
- Crosshair: thin 1px white lines, no glow
- Roll/pitch/yaw readouts: mono font, `var(--text-secondary)` labels, `var(--text)` values

**Step 7: Verify layout**

Run: `open /Users/jay/DroneNexus/src/hud/index.html`
Expected: Clean Gotham layout with classification banners, Object Explorer, Inspector, Activity Stream

**Step 8: Commit**

```bash
git add src/hud/index.html
git commit -m "design: transform HUD layout to Gotham Object Explorer + Inspector"
```

---

## Task 7: Frontend HUD — Mode System & Interactions

Transform mode behaviors and command interfaces.

**Files:**
- Modify: `src/hud/index.html`

**Step 1: Rename modes in JS**

- `MONITOR` → `OBSERVE`
- `COMMAND` → `TASK`
- `REPLAY` → `DEBRIEF`
- `SOLO` → `ISR`

Update `data-mode` attribute values, all mode switch logic, button text, CSS selectors.

**Step 2: Redesign TASK mode (was COMMAND)**

Replace "COMMAND CENTER" right panel with "DIRECTIVE CENTER":
- Target selector: "TARGET: ALL ASSETS" or "TARGET: ALPHA-1"
- Safety section: "LAUNCH PREP" / "STAND DOWN" buttons (flat, outlined style, no neon)
- "ABORT" button: red background, full width, but no animated flash — steady, prominent
- Flight section: "LAUNCH" / "RECOVER" / "RTB" / "GOTO (MAP)" buttons
- Overlay control: dropdown for overlay type (V_FORMATION, LINE_ABREAST, etc.)
- Speed/altitude sliders: thin track, small accent-blue thumb, mono value display
- Operation planning: "OBJECTIVES" section with objective count + "EXECUTE OPERATION" button + "CLEAR"
- Directive log: last 5 directives with timestamps

**Step 3: Rename waypoint system to objectives**

- "waypoint" → "objective" in all JS variables, functions, UI text
- WaypointManager → ObjectiveManager
- Waypoint markers: change from cyan circles to numbered diamonds with accent-blue stroke
- "+ WAYPOINT" → "+ OBJECTIVE"
- "EXECUTE MISSION" → "EXECUTE OPERATION"
- "LOITER" → "HOLD"
- Waypoint toolbar styled flat (no bright colors, use border-only buttons)

**Step 4: Redesign DEBRIEF mode (was REPLAY)**

- Right panel: "DEBRIEF" header
- Session info card: frame count, duration, status
- Playback controls: clean, flat transport buttons
- Timeline scrubber on map: thin accent-blue progress bar

**Step 5: Redesign ISR FEED mode (was SOLO FPV)**

- Rebrand all FPV text: "FPV PILOT" → "ISR FEED"
- "Flight Mode" buttons stay (ANGLE, HORIZON, ACRO, GPS_RESCUE)
- Video panel: remove test pattern grid colors — use dark gray grid on black
- OSD overlay: mono font, muted colors, institutional feel
- DVR: "REC" badge in red, clean font
- Keyboard shortcut hints: small, muted, bottom of panel

**Step 6: Update map markers**

- Drone triangles → chevron/arrow icons, slightly larger (30x30)
- Remove colored glow from markers
- Formation lines: thin 1px dashed, `var(--border-light)` color (not white)
- Trails: thin 1px, same color as asset, 3 opacity levels (0.15, 0.3, 0.5)
- Tooltips: dark background, mono font, no colored backgrounds

**Step 7: Update event generation**

Replace event templates with Gotham-style operational messages:
- "Waypoint Alpha reached" → "OBJ-1 reached by ALPHA-1"
- "Obstacle detected, adjusting course" → "Deconfliction maneuver — BRAVO-2"
- "Wind compensation active" → "Environmental compensation active"
- "Mesh network update" → "COMMLINK mesh topology updated"
- Source labels: use "OVERWATCH", "TASKFORCE", "COMMS" instead of "NEXUS", "MESH", "NAV"

**Step 8: Update connection manager**

- "SIM MODE" badge → "EXERCISE" badge (amber text)
- "LIVE" badge → "OPERATIONAL" badge (green text)
- "Connect to NEXUS ground station" → "Connect to OVERWATCH platform"
- WebSocket URL: update to `ws://v1/stream` path

**Step 9: Verify all modes work**

Open in browser, click through each mode: OBSERVE, TASK, DEBRIEF, ISR FEED. Verify:
- Mode switching works
- Correct panels show/hide
- Map interactivity works
- Commands dispatch correctly

**Step 10: Commit**

```bash
git add src/hud/index.html
git commit -m "feat: implement Gotham mode system, directives, and operational UI"
```

---

## Task 8: Frontend HUD — JavaScript Domain Rename

Rename all JS variables, classes, and functions from drone/swarm/telemetry to asset/taskforce/ontology language.

**Files:**
- Modify: `src/hud/index.html`

**Step 1: Rename simulation classes**

- `DroneSimulator` → `AssetSimulator`
- `CommandEngine` → `DirectiveEngine`
- `WaypointManager` → `ObjectiveManager`
- `ConnectionManager` → `PlatformLink`
- `ReplaySystem` → `DebriefSystem`
- `VideoFeedManager` → `ISRFeedManager`
- `OSDRenderer` → `ISROverlayRenderer`
- `DVRManager` → `RecordingManager`

**Step 2: Rename data variables**

- `drones` array/map → `assets`
- `drone.id` → `asset.designator` (internal JS only, wire format stays `drone_id`)
- `selectedDrone` → `selectedAsset`
- `swarmHealth` → `taskforceHealth`
- `commandLog` → `directiveLog`
- `eventLog` → `activityStream`
- `waypoints` → `objectives`

**Step 3: Rename functions**

- `updateDroneList()` → `updateAssetExplorer()`
- `updateDroneDetail()` → `updateInspector()`
- `sendCommand()` → `issueDirective()`
- `addWaypoint()` → `addObjective()`
- `executeMission()` → `executeOperation()`
- `updateSwarmMetrics()` → `updateTaskforceMetrics()`
- `generateEvent()` → `generateActivity()`

**Step 4: Update all internal string references**

- Role display text: "LEADER" → "PRIMARY", "WINGMAN" → "ESCORT", etc.
- Status display text: "ACTIVE" → "NOMINAL", "LOW_BATT" → "DEGRADED", etc.
- Formation display text stays the same (V_FORMATION is still V_FORMATION)

**Step 5: Verify everything still works**

Open in browser, confirm:
- Assets appear in Object Explorer
- Clicking an asset shows Inspector
- Telemetry updates in real-time
- Mode switching works
- Console shows no JS errors

**Step 6: Commit**

```bash
git add src/hud/index.html
git commit -m "refactor: rename all HUD JS domain language to Gotham ontology"
```

---

## Task 9: Standalone Simulator & Doc Generator

**Files:**
- Modify: `src/simulation/drone-simulator.js`
- Modify: `src/docs/generate-doc.js`
- Modify: `src/hud/3d-view.html`

**Step 1: Update `src/simulation/drone-simulator.js`**

- Import names: use new protocol exports (AssetClassification, OperationalStatus, etc.)
- Console output: "NEXUS Simulator" → "OVERWATCH Simulator"
- Class name: `SimulatedDrone` → `SimulatedAsset`
- Role references: use new AssetClassification values
- Status references: use new OperationalStatus values

**Step 2: Update `src/docs/generate-doc.js`**

- All document title references: NEXUS → OVERWATCH
- Architecture doc content: update terminology throughout
- Output filename: `nexus-architecture.docx` → `overwatch-architecture.docx`

**Step 3: Update `src/hud/3d-view.html`**

- Title: "NEXUS 3D Swarm View" → "OVERWATCH 3D Asset View"
- Overlay title text
- Any NEXUS branding

**Step 4: Commit**

```bash
git add src/
git commit -m "refactor: update simulator, doc generator, and 3D view to OVERWATCH"
```

---

## Task 10: Config, Scripts, Package, README

**Files:**
- Rename: `config/nexus.yaml` → `config/overwatch.yaml`
- Modify: `config/overwatch.yaml` (content)
- Modify: `scripts/build.sh`
- Modify: `scripts/dev-server.sh`
- Modify: `package.json`
- Modify: `README.md`
- Modify: `backend/README.md`

**Step 1: Rename and update config file**

```bash
cd /Users/jay/DroneNexus
git mv config/nexus.yaml config/overwatch.yaml
```

Update content:
- Title comment: NEXUS → OVERWATCH
- Config path references: /etc/nexus → /etc/overwatch
- `drone:` section → `asset:` with classification instead of role
- `swarm:` → `taskforce:`
- `network:` → `comms:`
- SSID: "NEXUS-MESH" → "OVERWATCH-MESH"
- WiFi passphrase var: `${NEXUS_WIFI_PASSPHRASE}` → `${OVERWATCH_WIFI_PASSPHRASE}`
- Service name: `_nexus._tcp.local.` → `_overwatch._tcp.local.`
- `telemetry:` → `asset_telemetry:`
- `failover:` → `contingency:`
- `safety:` → `safety_envelope:`
- Logging paths: /var/log/nexus/ → /var/log/overwatch/

**Step 2: Update `scripts/build.sh`**

- Title echo: NEXUS → OVERWATCH
- Output paths: nexus-hud.html → overwatch-hud.html, nexus-architecture.docx → overwatch-architecture.docx

**Step 3: Update `scripts/dev-server.sh`**

- Title echo: NEXUS → OVERWATCH

**Step 4: Update `package.json`**

```json
{
  "name": "overwatch-isr-platform",
  "description": "OVERWATCH — ISR Asset Coordination Platform (Palantir Gotham)",
  "scripts": {
    "generate-doc": "node src/docs/generate-doc.js",
    "build": "node src/docs/generate-doc.js && cp src/hud/index.html dist/overwatch-hud.html"
  }
}
```

**Step 5: Update `README.md`**

Full rewrite with OVERWATCH branding:
- Title: "OVERWATCH — ISR Asset Coordination Platform"
- Subtitle: "Built on Palantir Gotham"
- Updated project structure showing new file names
- Updated quick start with new config paths
- Updated deliverables table
- Tech stack section
- Ontology overview section

**Step 6: Update `backend/README.md`**

- Title: OVERWATCH Backend
- Environment variables: OVERWATCH_ prefix
- API endpoint docs: new /api/v1/ paths

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor: complete config, scripts, and documentation transformation to OVERWATCH"
```

---

## Task 11: Integration Test & Polish

**Files:**
- All modified files (verification pass)

**Step 1: Kill any running server, start fresh**

```bash
lsof -ti:8765 | xargs kill -9 2>/dev/null
cd /Users/jay/DroneNexus/backend && python3 main.py &
sleep 3
```

Expected: Server starts with "OVERWATCH" branding in logs

**Step 2: Verify API endpoints**

```bash
curl -s http://localhost:8765/api/v1/platform/status | python3 -m json.tool
curl -s http://localhost:8765/api/v1/ontology/assets | python3 -m json.tool
curl -s http://localhost:8765/docs | head -3
```

Expected: All return valid responses

**Step 3: Verify HUD connects and renders**

```bash
open /Users/jay/DroneNexus/src/hud/index.html
```

Check in browser:
- Classification banners visible top and bottom
- OVERWATCH branding in header
- Object Explorer shows 6 assets with classifications
- Map shows assets with chevron markers
- Inspector shows properties when clicking an asset
- Activity Stream shows events
- All 4 modes work (OBSERVE, TASK, DEBRIEF, ISR FEED)
- No console errors

**Step 4: Run all tests**

```bash
cd /Users/jay/DroneNexus && node tests/protocol.test.js
cd /Users/jay/DroneNexus/backend && python3 -m pytest tests/ -v --tb=short
```

Expected: All tests pass

**Step 5: Fix any remaining issues**

Address any broken tests, missing renames, or visual glitches found during integration.

**Step 6: Final commit**

```bash
git add -A
git commit -m "test: verify full OVERWATCH integration — all tests passing"
```

---

## Task Summary

| Task | Description | Estimated Complexity |
|------|-------------|---------------------|
| 1 | Protocol layer (JS + Python enums) | Medium |
| 2 | Backend config & settings | Medium |
| 3 | Backend API routes | Large |
| 4 | Backend supporting modules | Medium (bulk rename) |
| 5 | HUD visual design system (CSS) | Large |
| 6 | HUD layout & structure (HTML) | Very Large |
| 7 | HUD mode system & interactions | Large |
| 8 | HUD JavaScript domain rename | Large |
| 9 | Simulator, doc gen, 3D view | Small |
| 10 | Config, scripts, package, README | Medium |
| 11 | Integration test & polish | Medium |
