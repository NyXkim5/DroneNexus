# OVERWATCH — Palantir Gotham ISR Platform Transformation

## Product Vision

OVERWATCH is a real-time ISR (Intelligence, Surveillance, Reconnaissance) asset coordination platform built on Palantir Gotham's ontology framework. It enables operators to monitor, task, and debrief multi-asset aerial operations through a unified geospatial intelligence interface.

Portfolio narrative: "I took an open-source drone telemetry system and redesigned it from the ground up as a Palantir Gotham application — ontology-driven, operator-focused, and fully functional with live streaming data."

## Target Audience

Palantir Gotham design internship reviewers. The portfolio piece must demonstrate:

1. Understanding of Gotham's mental model — ontology, objects, relationships, actions
2. Designing for high-stakes operators — information density without cognitive overload
3. Shipping real software — not Figma mockups, a fully functional running application
4. Systems thinking — data flows, state management, protocol design

## Design Principles (Gotham-aligned)

1. Ontology-first — Every entity is an object with typed properties and relationships
2. Operator trust — High-information-density displays that reward expertise
3. Action at the point of need — Context menus, inline actions, keyboard shortcuts
4. Temporal awareness — Everything has a timeline, every change is versioned
5. Classification discipline — Banners, markings, and access controls are first-class

## Ontology Model

```
OVERWATCH Ontology
├── Asset (drone)
│   ├── Properties: designator, classification, status, position, power, comms
│   ├── Relationships:
│   │   ├── MEMBER_OF → Task Force
│   │   ├── ASSIGNED_TO → Operation
│   │   ├── REPORTS_TO → Asset (leader)
│   │   └── OBSERVES → Area of Interest
│   └── Events: launched, recovered, waypoint_reached, status_changed
├── Task Force (swarm)
│   ├── Properties: callsign, formation, health, asset_count
│   └── Relationships:
│       ├── CONTAINS → Asset[]
│       └── EXECUTING → Operation
├── Operation (mission)
│   ├── Properties: name, status, objectives, timeline
│   └── Relationships:
│       ├── HAS_OBJECTIVE → Objective[]
│       └── ASSIGNED → Task Force
├── Objective (waypoint)
│   ├── Properties: position, type, radius, priority
│   └── Relationships:
│       └── PART_OF → Operation
└── Area of Interest (geofence/AOI)
    ├── Properties: geometry, threat_level, classification
    └── Relationships:
        └── OBSERVED_BY → Asset[]
```

## Terminology Mapping

| Current (NEXUS)       | OVERWATCH (Gotham)                          |
|-----------------------|---------------------------------------------|
| Drone                 | Asset                                       |
| Drone ID (ALPHA-1)    | Asset Designator                            |
| Role (LEADER, etc.)   | Asset Classification (PRIMARY, ESCORT, ISR, LOGISTICS, OVERWATCH) |
| Status (ACTIVE, etc.) | Operational Status (NOMINAL, DEGRADED, RTB, OFFLINE, COMPROMISED) |
| Formation             | Operational Overlay                         |
| Telemetry             | Asset Properties                            |
| Swarm                 | Task Force                                  |
| Waypoint              | Objective                                   |
| Mission               | Operation                                   |
| Event Log             | Activity Stream                             |
| Command               | Directive                                   |
| Battery               | Power State                                 |
| GPS                   | Positioning                                 |
| Link Quality          | COMMLINK                                    |
| Takeoff / Land / RTL  | LAUNCH / RECOVER / RTB                      |
| Emergency Stop        | ABORT                                       |
| Cohesion              | Formation Integrity                         |
| MONITOR mode          | OBSERVE                                     |
| COMMAND mode          | TASK                                        |
| REPLAY mode           | DEBRIEF                                     |
| SOLO FPV mode         | ISR FEED                                    |

## UI Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  ▌▌ UNCLASSIFIED // FOR OFFICIAL USE ONLY ▌▌                     │
├──────────────────────────────────────────────────────────────────┤
│  ◆ OVERWATCH     GOTHAM // ISR     OBSERVE  TASK  DEBRIEF  ISR  │
│  ● OPERATIONAL   TF-OVERWATCH-01          03 MAR 2026 1641Z     │
├────────────┬─────────────────────────────────┬───────────────────┤
│            │                                 │                   │
│  OBJECT    │      GEOSPATIAL VIEW            │  INSPECTOR        │
│  EXPLORER  │                                 │                   │
│            │   ┌─AOI─┐                       │  ┌─ALPHA-1──────┐│
│  ▸ Assets  │   │     │  △ ← asset markers    │  │ Asset        ││
│    ALPHA-1 │   │  △  │                       │  │ PRIMARY      ││
│    BRAVO-2 │   │    △│  --- formation lines   │  ├──────────────┤│
│    ...     │   └─────┘                       │  │ PROPERTIES   ││
│  ▸ Ops     │                                 │  │ RELATIONSHIPS││
│  ▸ AOIs    │   [Overlay Controls]            │  │ TIMELINE     ││
│            │                                 │  └──────────────┘│
├────────────┴─────────────────────────────────┴───────────────────┤
│  ACTIVITY STREAM                    TF HEALTH  COMMS   POWER    │
│  1641Z ALPHA-1 reached OBJ-2       ███ 96%    12ms    87%      │
├──────────────────────────────────────────────────────────────────┤
│  ▌▌ UNCLASSIFIED // FOR OFFICIAL USE ONLY ▌▌                     │
└──────────────────────────────────────────────────────────────────┘
```

### Panel Descriptions

**Classification Banners (top + bottom):** Amber background, black bold text. Standard IC UI pattern. Always visible.

**Top Bar:** OVERWATCH logo (geometric diamond + text), platform subtitle "GOTHAM // ISR PLATFORM", mode tabs (OBSERVE / TASK / DEBRIEF / ISR FEED), operational status indicator, Task Force callsign, UTC clock in military format (DDMonYYYY HHMMz).

**Object Explorer (left, 280px):** Ontology tree navigator. Collapsible sections for Assets, Operations, Areas of Interest. Each asset shows: colored status dot, designator, classification badge, mini status. Click to select and inspect. Grouped by classification (PRIMARY, ESCORT, ISR, etc.).

**Geospatial View (center):** Dark basemap (CartoDB dark). Asset markers as chevron/arrow icons (not triangles). Formation overlay lines (dashed, muted). AOI boundaries as semi-transparent polygons. Objective markers as numbered diamonds. Operational overlay controls (toggle layers). No decorative elements — clean cartographic style.

**Inspector (right, 320px):** Tabbed property sheet for selected object.
- Properties tab: Key-value pairs in a clean data table. Position, altitude, speed, heading, power, comms.
- Relationships tab: Linked objects shown as cards. "MEMBER_OF TF-OVERWATCH-01", "REPORTS_TO ALPHA-1", "ASSIGNED_TO OP-EAGLE".
- Timeline tab: Chronological event history for this specific object. Filterable by event type.

**Bottom Panel (140px):** Activity Stream (scrollable event feed with timestamps and object links) + metric cards for Task Force Health, COMMLINK status, Power State. Sparklines retained but styled flat (no gradients, thin lines).

### Mode Behaviors

**OBSERVE:** Passive monitoring. All panels visible. Inspector shows Properties tab by default. Map shows all overlays. Read-only.

**TASK:** Active command. Right panel switches to Directive Center. Shows target selector, action buttons (LAUNCH PREP / LAUNCH / RECOVER / RTB / ABORT), formation controls, operation planning with objective placement on map.

**DEBRIEF:** Post-operation review. Timeline scrubber appears on map. Playback controls (play/pause/speed). Inspector shows Timeline tab by default.

**ISR FEED:** Single-asset video intelligence. Left panel collapses. Video feed fills center. OSD overlay with telemetry. DVR controls.

## Visual Design System

| Element              | Value                                    |
|----------------------|------------------------------------------|
| Background           | #0d1117                                  |
| Panel surface        | #161b22                                  |
| Panel border         | 1px solid #21262d                        |
| Elevated surface     | #1c2128                                  |
| Primary text         | #e6edf3                                  |
| Secondary text       | #7d8590                                  |
| Tertiary text        | #484f58                                  |
| Accent (primary)     | #4493f8 (Palantir blue)                  |
| Accent (success)     | #3fb950                                  |
| Accent (warning)     | #d29922                                  |
| Accent (critical)    | #f85149                                  |
| Classification bg    | #d29922 (amber)                          |
| Classification text  | #000000                                  |
| Font UI              | Inter, system-ui, sans-serif             |
| Font data            | JetBrains Mono, monospace                |
| Font size (body)     | 13px                                     |
| Font size (labels)   | 11px, uppercase, letter-spacing 0.05em   |
| Font size (data)     | 12px mono                                |
| Border radius        | 4px (cards), 2px (badges)                |
| Hover state          | background #21262d → #30363d             |
| Selected state       | left 3px solid #4493f8                   |
| Active tab           | bottom 2px solid #4493f8, text #e6edf3   |
| Transitions          | 0.15s ease                               |

No neon. No glow. No gradients. No box-shadow glow effects. Clean, flat, institutional.

## Backend API Transformation

### Route Mapping

| Current                    | New                                  |
|----------------------------|--------------------------------------|
| GET /api/drones            | GET /api/v1/ontology/assets          |
| POST /api/drones/{id}/arm  | POST /api/v1/actions/assets/{id}/launch-prep |
| POST /api/drones/{id}/disarm | POST /api/v1/actions/assets/{id}/stand-down |
| POST /api/swarm/takeoff    | POST /api/v1/actions/taskforce/launch |
| POST /api/swarm/land       | POST /api/v1/actions/taskforce/recover |
| POST /api/swarm/emergency-stop | POST /api/v1/actions/taskforce/abort |
| POST /api/swarm/formation  | POST /api/v1/overlays/formation      |
| POST /api/swarm/speed      | POST /api/v1/actions/taskforce/set-speed |
| POST /api/swarm/altitude   | POST /api/v1/actions/taskforce/set-altitude |
| GET /api/swarm/health      | GET /api/v1/ontology/taskforce/health |
| POST /api/mission/create   | POST /api/v1/operations/create       |
| POST /api/mission/execute  | POST /api/v1/operations/execute      |
| POST /api/mission/abort    | POST /api/v1/operations/abort        |
| GET /api/logs/commands     | GET /api/v1/activity/directives      |
| GET /api/logs/events       | GET /api/v1/activity/stream          |
| GET /api/connections       | GET /api/v1/platform/connections     |
| GET /api/status            | GET /api/v1/platform/status          |
| POST /api/replay/start     | POST /api/v1/debrief/record          |
| POST /api/replay/stop      | POST /api/v1/debrief/stop            |
| GET /api/replay/sessions   | GET /api/v1/debrief/sessions         |
| POST /api/replay/play      | POST /api/v1/debrief/play            |
| POST /api/replay/pause     | POST /api/v1/debrief/pause           |
| GET /api/export/kml        | GET /api/v1/products/geospatial      |
| GET /api/export/csv        | GET /api/v1/products/telemetry       |
| GET /api/devices/scan      | GET /api/v1/platform/devices/scan    |
| POST /api/devices/connect  | POST /api/v1/platform/devices/connect |
| GET /api/devices/connected | GET /api/v1/platform/devices          |
| GET /api/video/sources     | GET /api/v1/isr/feeds                |
| POST /api/video/sources    | POST /api/v1/isr/feeds               |
| DELETE /api/video/sources/{id} | DELETE /api/v1/isr/feeds/{id}    |
| POST /api/auth/login       | POST /api/v1/auth/authenticate       |
| GET /api/auth/me           | GET /api/v1/auth/identity            |
| WS /telemetry/stream       | WS /ws/v1/stream                     |
| WS /ws                     | WS /ws/v1/compat                     |

### WebSocket Message Types

| Current      | New              |
|--------------|------------------|
| TELEM        | ASSET_STATE      |
| CMD          | DIRECTIVE        |
| HEARTBEAT    | HEARTBEAT        |
| FORMATION    | OVERLAY_UPDATE   |
| WAYPOINT     | OBJECTIVE        |
| PEER         | PEER_STATE       |
| ALERT        | ACTIVITY         |
| ACK          | ACK              |
| VIDEO_CTRL   | ISR_CTRL         |
| CAMERA_CTRL  | SENSOR_CTRL      |
| MSP_TELEM    | MSP_STATE        |
| GOGGLES      | HMD_STATE        |
| DVR_CTRL     | RECORD_CTRL      |
| DEVICE_SCAN  | DEVICE_SCAN      |

### Protocol File Transformation

`src/shared/protocol.js` and `backend/protocol.py` will be updated with all new enum values, message type names, and factory functions renamed accordingly.

## Config Transformation

`config/nexus.yaml` renamed to `config/overwatch.yaml` with Gotham-aligned section names:
- `drone:` → `asset:`
- `swarm:` → `taskforce:`
- `network:` → `comms:`
- `mavlink:` → `vehicle_link:`
- `telemetry:` → `asset_telemetry:`
- `failover:` → `contingency:`
- `safety:` → `safety_envelope:`
- `fpv:` → `isr:`

## Portfolio-Grade Quality Markers

1. Working software — live WebSocket telemetry, real-time map, actual API calls
2. Ontology thinking — data restructured around objects/relationships/actions
3. Design system — consistent, documented, intentional visual language
4. Domain fluency — ISR terminology, classification markings, operator workflows
5. Full-stack — frontend, backend API, protocol, config touched at every layer
6. Before/after narrative — "open-source project reimagined as Gotham application"
