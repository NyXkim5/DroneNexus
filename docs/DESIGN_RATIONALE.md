# OVERWATCH ISR Platform -- Design Rationale

## Problem Statement

Multi-UAS ISR operations lack a unified command-and-control interface. Operators are forced to juggle separate tools for telemetry monitoring, mission tasking, sensor management, and post-mission review -- each with its own mental model and interaction paradigm. This context-switching degrades situational awareness precisely when it matters most. OVERWATCH provides a single-pane-of-glass interface for real-time drone fleet monitoring and command, consolidating all ISR workflow phases into one coherent HUD.

## User Persona

**SSgt. Martinez** -- JTAC/UAS operator running a 6-drone ISR surveillance mission from a forward operating base.

- Needs constant situational awareness of all assets simultaneously (position, health, link status)
- Must issue commands quickly without context-switching between applications
- Requires degraded-state warnings before they escalate to emergencies -- seconds matter
- Needs post-mission debrief capability to review flight paths, events, and asset performance
- Operates under stress with high cognitive load; the interface must reduce friction, not add it

## Information Hierarchy

The layout follows a three-panel architecture with a persistent bottom status strip. Each region has a distinct role in the operator's attention hierarchy.

- **Left panel (Object Explorer):** Reflects Gotham's ontology-first paradigm. Assets are presented as typed objects -- ALPHA-1 through FOXTROT-6 -- with roles (PRIMARY, ESCORT, ISR, LOGISTICS, OVERWATCH), not anonymous dots on a map. Operations and Areas of Interest are separate tree sections, establishing a clear organizational taxonomy. This panel is always visible, providing a persistent fleet roster regardless of mode.
- **Center (Map):** Primary spatial context using ESRI satellite imagery darkened to a near-NVG aesthetic. Supports formation lines between assets, FOV cones showing sensor coverage, and measurement/annotation tools. The map is the largest panel because spatial context is the operator's primary frame of reference during ISR operations.
- **Right panel (Inspector):** Deep-dive into the selected object. Six tabs -- PROPS, RELS, TIME, DIAG, HW, INTEL -- follow Gotham's multi-aspect object inspection pattern. Properties display typed values with data type annotations. Relationships show linked objects with cardinality. This panel reconfigures entirely based on mode: Inspector in OBSERVE, Directive Center in TASK, ISR telemetry in ISR FEED, session summary in DEBRIEF.
- **Bottom strip:** Fleet-level aggregate metrics -- TF Health, Commlink latency, Power State, Sensor Coverage -- each with sparkline trend canvases. These provide ambient awareness without requiring the operator to inspect individual assets. The activity stream on the left captures all system events in chronological order.

## Interaction Patterns

- **Four modes (OBSERVE / TASK / DEBRIEF / ISR FEED):** Each mode reconfigures the HUD for a distinct phase of the ISR workflow. Modes are mutually exclusive -- switching triggers a 150ms panel transition -- to reduce cognitive load. The operator is never presented with controls irrelevant to their current task. Mode switching is accessible via top-bar buttons or keyboard shortcuts (O/T/D/I).
- **Command Palette (Cmd+K):** Borrowed from Gotham's universal search. Searches across assets, commands, and modes from a single input. This is a power-user-first, mouse-second pattern. The palette uses combobox ARIA roles with arrow-key navigation and instant filtering.
- **State machine guards:** The simulation enforces a strict state machine (IDLE -> ARMED -> TAKING_OFF -> FLYING -> LANDING -> LANDED). Commands are validated against valid transitions. You cannot TAKEOFF unless ARMED. Invalid commands produce DENIED toast feedback immediately. This prevents operator error in high-stress environments where a misclick could lose an asset.
- **Double-click ABORT:** The emergency stop button requires confirmation within a 3-second timeout window. This prevents accidental activation of the most destructive command available, while still keeping it fast enough for genuine emergencies.
- **Classification banners:** Top and bottom UNCLASSIFIED banners follow the Astro UXDS standard, demonstrating awareness of DoD UI requirements and information security display protocols.

## Visual Design Decisions

- **Palantir Blueprint tokens:** Dark theme built on authentic Blueprint v5 color values -- #111418 background, #1C2127 panels, #2D72D2 primary accent. This demonstrates direct familiarity with the design system rather than approximating it.
- **Typography:** Inter for UI labels (optimized for readability at small sizes), JetBrains Mono for telemetry data (fixed-width ensures columnar alignment of numeric values). Weight hierarchy: 300 for ambient/secondary text, 400 for body, 600 for section headers, 700 for emphasis and critical values.
- **Border radii:** 2px maximum throughout the interface. This enforces a brutalist defense aesthetic -- no rounded consumer UI patterns that would look out of place in a C2 environment.
- **High-contrast accents:** Asset colors (rgba highlights for ALPHA-1 through FOXTROT-6) use brighter values than standard Blueprint to ensure visual pop against the dark background. This is a deliberate departure for high-contrast readability in ISR contexts where rapid asset identification is critical.

## Ontology Integration

The Inspector panel models Gotham's core data architecture:

- Object properties carry explicit data type annotations (DOUBLE, STRING, ENUM, GEO_POINT) alongside their values
- Relationships use Link[] notation with cardinality markers (1:1, 1:N, N:1), connecting assets to operations, areas of interest, and hardware manifests
- Data provenance chains display source system, confidence level, schema version, and last-updated timestamp
- This framing demonstrates understanding that Gotham is not a dashboard tool -- it is an ontology platform, and the UI must surface the data model, not hide it

## Alternatives Considered

- **Tab-based layout vs. mode switching:** Tabs would allow simultaneous access to all workflow views but increase cognitive load by presenting irrelevant controls. Modes force focus on one workflow phase at a time -- a deliberate constraint for high-stress operations.
- **React/Vue vs. vanilla JavaScript:** Chose vanilla ES modules to demonstrate raw DOM competency and keep the build toolchain at zero. Trade-off: no component reuse patterns or declarative rendering. For a portfolio piece, showing that you understand what frameworks abstract away is more valuable than using one.
- **WebSocket vs. REST polling:** WebSocket for real-time telemetry streaming. REST polling would introduce latency unacceptable for ISR operations where position updates must arrive within a single render frame.

## Technical Architecture

- ES modules with single-responsibility files: state.js, simulation.js, map.js, inspector.js, diagnostics.js, activity.js, engine.js, sparkline.js, utils.js
- Shared mutable state pattern via a centralized state.js export -- all modules read and write from one object, avoiding prop-drilling without a framework
- Callback injection (setModeProvider, setEventCallback, setDiagStateProvider) to resolve circular dependency chains between modules
- requestAnimationFrame loop for smooth rendering with a 1-second tick gate for heavy UI updates (telemetry panels, asset explorer)
- requestIdleCallback for non-critical rendering tasks (sparkline chart updates)
- Targeted DOM updates: innerHTML on first render for batch insertion, getElementById for subsequent surgical updates to avoid layout thrashing
