/**
 * NEXUS System Architecture Document Generator
 *
 * Generates a professional Word document (.docx) containing the complete
 * NEXUS system architecture and protocol specification.
 *
 * Usage: node src/docs/generate-doc.js
 * Output: dist/nexus-architecture.docx
 */

const fs = require("fs");
const path = require("path");
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  Table,
  TableRow,
  TableCell,
  WidthType,
  AlignmentType,
  HeadingLevel,
  BorderStyle,
  PageNumber,
  NumberFormat,
  Header,
  Footer,
  ShadingType,
  VerticalAlign,
  PageBreak,
  TabStopPosition,
  TabStopType,
  TableLayoutType,
  convertInchesToTwip,
} = require("docx");

// ---------------------------------------------------------------------------
// Color & Style Constants
// ---------------------------------------------------------------------------
const COLORS = {
  DARK_BLUE: "0F1A2E",
  MEDIUM_BLUE: "1B3A5C",
  WHITE: "FFFFFF",
  BLACK: "000000",
  LIGHT_GRAY: "CCCCCC",
  TABLE_HEADER_BG: "0F1A2E",
  TABLE_ALT_ROW: "F2F4F7",
  BODY_TEXT: "222222",
  CLASSIFICATION: "CC0000",
};

const FONT = "Arial";

// ---------------------------------------------------------------------------
// Helper: create a styled TextRun
// ---------------------------------------------------------------------------
function text(content, opts = {}) {
  return new TextRun({
    text: content,
    font: FONT,
    size: opts.size || 22, // 11pt default
    bold: opts.bold || false,
    italics: opts.italics || false,
    color: opts.color || COLORS.BODY_TEXT,
    break: opts.break,
  });
}

// ---------------------------------------------------------------------------
// Helper: body paragraph
// ---------------------------------------------------------------------------
function bodyParagraph(content, opts = {}) {
  const runs = Array.isArray(content) ? content : [text(content)];
  return new Paragraph({
    children: runs,
    spacing: { after: 200, line: 276 },
    alignment: opts.alignment || AlignmentType.LEFT,
  });
}

// ---------------------------------------------------------------------------
// Helper: heading paragraphs
// ---------------------------------------------------------------------------
function heading1(title) {
  return new Paragraph({
    children: [
      new TextRun({
        text: title,
        font: FONT,
        size: 48, // 24pt
        bold: true,
        color: COLORS.DARK_BLUE,
      }),
    ],
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 400, after: 200 },
  });
}

function heading2(title) {
  return new Paragraph({
    children: [
      new TextRun({
        text: title,
        font: FONT,
        size: 36, // 18pt
        bold: true,
        color: COLORS.DARK_BLUE,
      }),
    ],
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 150 },
  });
}

function heading3(title) {
  return new Paragraph({
    children: [
      new TextRun({
        text: title,
        font: FONT,
        size: 28, // 14pt
        bold: true,
        color: COLORS.MEDIUM_BLUE,
      }),
    ],
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 240, after: 120 },
  });
}

// ---------------------------------------------------------------------------
// Helper: table construction
// ---------------------------------------------------------------------------
const THIN_BORDER = {
  style: BorderStyle.SINGLE,
  size: 1,
  color: COLORS.LIGHT_GRAY,
};

const TABLE_BORDERS = {
  top: THIN_BORDER,
  bottom: THIN_BORDER,
  left: THIN_BORDER,
  right: THIN_BORDER,
};

function headerCell(content, widthPct) {
  return new TableCell({
    children: [
      new Paragraph({
        children: [
          new TextRun({
            text: content,
            font: FONT,
            size: 20,
            bold: true,
            color: COLORS.WHITE,
          }),
        ],
        spacing: { before: 40, after: 40 },
      }),
    ],
    width: { size: widthPct, type: WidthType.PERCENTAGE },
    shading: {
      type: ShadingType.CLEAR,
      fill: COLORS.TABLE_HEADER_BG,
    },
    verticalAlign: VerticalAlign.CENTER,
    borders: TABLE_BORDERS,
  });
}

function dataCell(content, widthPct, isAlt = false) {
  const runs = Array.isArray(content)
    ? content
    : [
        new TextRun({
          text: content,
          font: FONT,
          size: 19,
          color: COLORS.BODY_TEXT,
        }),
      ];
  return new TableCell({
    children: [
      new Paragraph({
        children: runs,
        spacing: { before: 30, after: 30 },
      }),
    ],
    width: { size: widthPct, type: WidthType.PERCENTAGE },
    shading: isAlt
      ? { type: ShadingType.CLEAR, fill: COLORS.TABLE_ALT_ROW }
      : undefined,
    verticalAlign: VerticalAlign.CENTER,
    borders: TABLE_BORDERS,
  });
}

function makeTable(headers, rows, widths) {
  const headerRow = new TableRow({
    children: headers.map((h, i) => headerCell(h, widths[i])),
    tableHeader: true,
  });
  const dataRows = rows.map(
    (row, rIdx) =>
      new TableRow({
        children: row.map((cell, cIdx) =>
          dataCell(cell, widths[cIdx], rIdx % 2 === 1)
        ),
      })
  );
  return new Table({
    rows: [headerRow, ...dataRows],
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
  });
}

// ---------------------------------------------------------------------------
// Helper: code block style paragraph
// ---------------------------------------------------------------------------
function codeParagraph(line) {
  return new Paragraph({
    children: [
      new TextRun({
        text: line,
        font: "Courier New",
        size: 18,
        color: COLORS.BODY_TEXT,
      }),
    ],
    spacing: { after: 40, line: 240 },
    indent: { left: convertInchesToTwip(0.3) },
  });
}

function codeBlock(lines) {
  return lines.map((l) => codeParagraph(l));
}

// ---------------------------------------------------------------------------
// Helper: bullet paragraph
// ---------------------------------------------------------------------------
function bullet(content, level = 0) {
  const runs = Array.isArray(content) ? content : [text(content)];
  return new Paragraph({
    children: runs,
    bullet: { level },
    spacing: { after: 80, line: 264 },
  });
}

// ---------------------------------------------------------------------------
// Helper: numbered list paragraph
// ---------------------------------------------------------------------------
function numberedItem(content) {
  const runs = Array.isArray(content) ? content : [text(content)];
  return new Paragraph({
    children: runs,
    numbering: { reference: "numbered-list", level: 0 },
    spacing: { after: 100, line: 276 },
  });
}

// ---------------------------------------------------------------------------
// Section: Title Page
// ---------------------------------------------------------------------------
function buildTitlePage() {
  const today = new Date();
  const dateStr = today.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return [
    new Paragraph({ spacing: { before: 2000 } }),
    new Paragraph({
      children: [
        new TextRun({
          text: "NEXUS",
          font: FONT,
          size: 72, // 36pt
          bold: true,
          color: COLORS.DARK_BLUE,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: "System Architecture & Protocol Specification",
          font: FONT,
          size: 36,
          color: COLORS.MEDIUM_BLUE,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 100 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: "Multi-UAV Swarm Telemetry & Coordination Platform",
          font: FONT,
          size: 26,
          italics: true,
          color: COLORS.BODY_TEXT,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 600 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: "Version 1.0",
          font: FONT,
          size: 28,
          bold: true,
          color: COLORS.DARK_BLUE,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 100 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: dateStr,
          font: FONT,
          size: 24,
          color: COLORS.BODY_TEXT,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 800 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: "CLASSIFICATION: UNCLASSIFIED // FOR OFFICIAL USE ONLY",
          font: FONT,
          size: 22,
          bold: true,
          color: COLORS.CLASSIFICATION,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
    }),
    new Paragraph({
      children: [new PageBreak()],
    }),
  ];
}

// ---------------------------------------------------------------------------
// Section 1: Executive Summary
// ---------------------------------------------------------------------------
function buildExecutiveSummary() {
  return [
    heading1("1. Executive Summary"),

    bodyParagraph(
      "NEXUS is a modular, hardware-agnostic multi-UAV swarm management platform designed to provide real-time telemetry visualization, autonomous coordination, and mission-critical command and control for teams of 2 to 50 unmanned aerial vehicles operating in contested or communications-degraded environments. The system bridges the gap between individual drone autopilot firmware and fleet-scale operational awareness by introducing a layered software architecture that runs on inexpensive companion computers mounted aboard each airframe. NEXUS aggregates MAVLink telemetry from each vehicle, normalizes it into a unified JSON wire protocol, distributes state over a self-healing mesh network, and presents the consolidated picture to operators through a browser-based heads-up display (HUD) that supports map visualization, attitude indicators, battery sparklines, and formation cohesion overlays."
    ),

    bodyParagraph(
      "The platform is built around four core engineering principles. First, hardware agnosticism: NEXUS communicates through the MAVLink v2 standard and therefore supports any flight controller running ArduPilot or PX4, on any airframe from micro-quads to fixed-wing survey aircraft. Second, plug-and-play deployment: each companion computer ships a pre-built SD card image; the operator edits a single YAML configuration file, connects the companion to the flight controller over UART, and the drone joins the swarm automatically via mDNS peer discovery. Third, graceful degradation: when communication links deteriorate, the system falls back from high-bandwidth WiFi JSON streams to low-bandwidth binary MessagePack packets over RFD900x telemetry radios, and if all links are lost a drone autonomously executes a return-to-launch (RTL) procedure with staggered altitude separation to avoid mid-air collisions. Fourth, operator-centric design: every automated behavior can be overridden by the human operator, every critical event triggers both visual and audible alerts, and the HUD is designed for single-operator management of the entire swarm under high-workload conditions."
    ),

    bodyParagraph(
      "This document defines the complete technical architecture of NEXUS version 1.0. It covers the five-layer system model, the communication protocol stack including both MAVLink integration and the NEXUS Wire Protocol, swarm coordination algorithms for formation flight and leader election, the companion computer software suite, ground station server deployment options, the operator HUD interface, safety and fault-handling procedures, and the phased development roadmap from single-drone telemetry through computer-vision-assisted autonomous targeting. All specifications described herein are intended to serve as the authoritative reference for development, integration testing, and operational deployment of the NEXUS platform."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 2: System Architecture
// ---------------------------------------------------------------------------
function buildSystemArchitecture() {
  const layerTable = makeTable(
    ["Layer", "Name", "Function", "Technologies"],
    [
      [
        "1",
        "Vehicle Layer",
        "Flight control, sensors, actuators, low-level stabilization and navigation loops",
        "ArduPilot / PX4, IMU, GPS, ESC, barometer, magnetometer",
      ],
      [
        "2",
        "Interface Layer",
        "MAVLink bridge between autopilot and companion computer, telemetry aggregation and normalization",
        "mavlink_bridge.py, pymavlink, companion computer UART/USB",
      ],
      [
        "3",
        "Mesh Layer",
        "Peer-to-peer communications, message routing, transport redundancy, automatic failover",
        "WiFi 6 mesh, RFD900x, NEXUS Wire Protocol, mDNS/zeroconf",
      ],
      [
        "4",
        "Coordination Layer",
        "Formation management, task allocation, leader election, consensus, collision avoidance",
        "Modified Raft consensus, market-based task allocation, safety bubble deconfliction",
      ],
      [
        "5",
        "Operator Layer",
        "Real-time visualization, command dispatch, mission planning, event logging",
        "WebSocket, browser-based HUD, REST API, Express.js, Redis",
      ],
    ],
    [8, 16, 40, 36]
  );

  const hardwareTable = makeTable(
    ["Component", "Recommended", "Alternatives", "Notes"],
    [
      [
        "Flight Controller",
        "Pixhawk 6X",
        "CubePilot Orange+, Holybro Kakute H7",
        "Must support MAVLink v2 over UART at 921600 baud; Pixhawk 6X preferred for redundant IMUs",
      ],
      [
        "Companion Computer",
        "Raspberry Pi 5 (8 GB)",
        "Jetson Orin Nano, Intel NUC 13",
        "Pi 5 balances cost/weight/performance; Jetson required for CV pipeline in Phase 7",
      ],
      [
        "Primary Comms",
        "WiFi 6 mesh (802.11ax)",
        "Ubiquiti LiteBeam 5AC, Rajant Peregrine",
        "WiFi 6 provides 1+ km range line-of-sight with MIMO; Rajant for MANET self-healing",
      ],
      [
        "Backup Comms",
        "RFD900x (900 MHz)",
        "Microhard pDDL, Silvus StreamCaster",
        "Low-bandwidth fallback (64 kbps); RFD900x chosen for cost, range up to 40 km",
      ],
      [
        "GPS",
        "u-blox F9P (RTK)",
        "Here3+, Septentrio Mosaic-X5",
        "F9P enables centimeter-level RTK positioning for tight formation flight",
      ],
      [
        "Airframe",
        "Holybro X500 V2",
        "Custom quad/hex, fixed-wing (Believer/Talon)",
        "X500 V2 carries 500 g payload, 25 min endurance; hex for redundancy, fixed-wing for range",
      ],
    ],
    [16, 22, 28, 34]
  );

  return [
    heading1("2. System Architecture"),

    bodyParagraph(
      "The NEXUS platform is organized into five discrete layers, each encapsulating a well-defined set of responsibilities and communicating with adjacent layers through stable, documented interfaces. This layered design allows any single layer to be replaced or upgraded without affecting the others: a new flight controller can be swapped in at Layer 1 provided it speaks MAVLink v2; a different mesh radio can replace the WiFi link at Layer 3 provided it delivers IP packets; and the operator HUD at Layer 5 can be reskinned or replaced entirely without touching the coordination engine beneath it."
    ),

    heading2("2.1 Five-Layer Architecture Model"),
    layerTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("2.2 Data Flow Architecture"),

    heading3("Uplink (Operator to Drone)"),
    bodyParagraph(
      "Operator commands originate in the HUD as user interactions (button clicks, map gestures, typed commands) and are translated into NEXUS Wire Protocol CMD messages by the front-end JavaScript client. These messages traverse a WebSocket connection to the ground station server, which validates the command against the current mission state, resolves the target drone or drone group, and forwards the message over the primary WiFi mesh link to the addressed companion computer. The companion computer's cmd_executor module deserializes the command, performs a secondary safety validation (checking that the requested action does not violate geofence boundaries or minimum battery thresholds), and translates the command into one or more MAVLink COMMAND_LONG messages sent to the flight controller over UART. An ACK message flows back up the chain to confirm receipt and execution status."
    ),

    heading3("Downlink (Drone to Operator)"),
    bodyParagraph(
      "Each drone's companion computer continuously reads MAVLink telemetry from the flight controller at the native MAVLink rate (typically 10-50 Hz depending on message type). The mavlink_bridge module parses HEARTBEAT, GLOBAL_POSITION_INT, ATTITUDE, SYS_STATUS, GPS_RAW_INT, and BATTERY_STATUS messages, normalizes units (radians to degrees, cm/s to m/s), and passes the consolidated state to the telem_publisher module. The telem_publisher assembles a NEXUS TELEM packet at 10 Hz and transmits it over the WiFi mesh to the ground station server. The server updates the in-memory Redis state store for that drone and pushes the packet to all connected HUD clients over WebSocket. If the WiFi link degrades below a configurable RSSI threshold, the failover_mgr switches the downlink to the RFD900x backup radio using a reduced-field binary MessagePack encoding to fit within the 64 kbps bandwidth constraint."
    ),

    heading3("Lateral (Peer-to-Peer Mesh)"),
    bodyParagraph(
      "In addition to the vertical uplink/downlink channels, every drone in the swarm exchanges state information directly with its peers over the mesh network at 5 Hz. These PEER messages contain a compact subset of telemetry (position, velocity, battery, status) and are used by the coordination layer for formation maintenance, collision avoidance, and consensus operations. Peer messages are broadcast to all nodes within radio range and relayed by intermediate nodes to ensure full swarm connectivity even when not all drones have direct line-of-sight to each other. This lateral data flow enables the swarm to maintain coherence even if the ground station link is temporarily lost."
    ),

    heading2("2.3 Hardware Stack Per Drone"),
    hardwareTable,

    new Paragraph({ spacing: { after: 200 } }),

    bodyParagraph(
      "The total hardware cost per drone unit, using the recommended components and a Holybro X500 V2 frame, is approximately $1,200-$1,800 USD depending on supplier and quantity discounts. This positions NEXUS as a cost-effective solution compared to proprietary swarm platforms that typically start at $5,000-$10,000 per vehicle. The modular component selection allows operators to scale up to more capable (and expensive) hardware only where mission requirements demand it, such as adding a Jetson Orin Nano for onboard computer vision or upgrading to a Silvus StreamCaster radio for operations beyond 10 km range."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 3: Communication Protocol
// ---------------------------------------------------------------------------
function buildCommunicationProtocol() {
  const mavlinkTable = makeTable(
    ["Message", "ID", "Direction", "Rate", "Key Fields"],
    [
      [
        "HEARTBEAT",
        "0",
        "Bidirectional",
        "1 Hz",
        "type, autopilot, base_mode, custom_mode, system_status",
      ],
      [
        "GLOBAL_POSITION_INT",
        "33",
        "Downlink",
        "10 Hz",
        "lat, lon, alt, relative_alt, vx, vy, vz, hdg",
      ],
      [
        "ATTITUDE",
        "30",
        "Downlink",
        "10 Hz",
        "roll, pitch, yaw, rollspeed, pitchspeed, yawspeed",
      ],
      [
        "SYS_STATUS",
        "1",
        "Downlink",
        "1 Hz",
        "voltage_battery, current_battery, battery_remaining, errors_count",
      ],
      [
        "GPS_RAW_INT",
        "24",
        "Downlink",
        "5 Hz",
        "fix_type, lat, lon, alt, eph, epv, vel, satellites_visible",
      ],
      [
        "BATTERY_STATUS",
        "147",
        "Downlink",
        "0.2 Hz",
        "current_consumed, energy_consumed, voltages, current_battery, remaining",
      ],
      [
        "COMMAND_LONG",
        "76",
        "Uplink",
        "On demand",
        "target_system, target_component, command, confirmation, param1-7",
      ],
      [
        "SET_MODE",
        "11",
        "Uplink",
        "On demand",
        "target_system, base_mode, custom_mode",
      ],
      [
        "SET_POSITION_TARGET_GLOBAL_INT",
        "86",
        "Uplink",
        "Variable",
        "lat_int, lon_int, alt, vx, vy, vz, type_mask, coordinate_frame",
      ],
    ],
    [18, 6, 12, 10, 54]
  );

  const messageTypesTable = makeTable(
    ["Type", "Direction", "Rate", "Payload Description"],
    [
      [
        "TELEM",
        "Downlink",
        "10 Hz",
        "Full telemetry snapshot: position, attitude, velocity, battery, GPS, link quality, formation state",
      ],
      [
        "HEARTBEAT",
        "Bidirectional",
        "1 Hz",
        "Liveness indicator with system_id, uptime_ms, software version, and current mode",
      ],
      [
        "CMD",
        "Uplink",
        "On demand",
        "Operator commands: arm, disarm, takeoff, land, RTL, goto, set_mode, set_formation",
      ],
      [
        "FORMATION",
        "Downlink (coordinator)",
        "On change",
        "Formation type, role assignments, offset vectors, transition timing, cohesion target",
      ],
      [
        "WAYPOINT",
        "Uplink",
        "On demand",
        "Ordered list of waypoints with lat, lon, alt, speed, hold time, action at each point",
      ],
      [
        "PEER",
        "Lateral",
        "5 Hz",
        "Compact peer state: position, velocity, battery percentage, status, formation role",
      ],
      [
        "ALERT",
        "Downlink",
        "On event",
        "Fault notifications: severity (info/warning/critical), fault code, description, recommended action",
      ],
      [
        "ACK",
        "Bidirectional",
        "On receipt",
        "Command acknowledgment: original_msg_id, result (accepted/rejected/executed/failed), reason",
      ],
    ],
    [14, 18, 12, 56]
  );

  return [
    heading1("3. Communication Protocol"),

    heading2("3.1 MAVLink v2 Integration"),
    bodyParagraph(
      "NEXUS interfaces with each drone's flight controller through the MAVLink v2 protocol, the de facto standard for communication with ArduPilot and PX4 autopilot firmware. The mavlink_bridge module on each companion computer establishes a serial connection to the flight controller at 921600 baud over UART (or 57600 baud over USB as a fallback) and exchanges MAVLink messages using the pymavlink library. Incoming MAVLink messages are parsed, filtered for the subset relevant to NEXUS operations, and converted into internal Python data structures. Outgoing commands from the operator are translated from NEXUS Wire Protocol format into the appropriate MAVLink message type and sent to the flight controller with automatic retry and acknowledgment tracking."
    ),
    bodyParagraph(
      "The following table lists the MAVLink v2 messages that NEXUS actively consumes or generates. While the MAVLink protocol defines hundreds of message types, NEXUS focuses on the subset required for telemetry aggregation, command execution, and mode management. Additional message types can be added by extending the mavlink_bridge module's message handler map."
    ),

    mavlinkTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("3.2 NEXUS Wire Protocol"),
    bodyParagraph(
      "Above the MAVLink layer, NEXUS defines its own application-level wire protocol for all communication between companion computers, the ground station server, and HUD clients. The NEXUS Wire Protocol uses JSON-encoded messages transported over WebSocket connections (RFC 6455) on the primary WiFi link. Each message is a JSON object containing a \"type\" field that identifies the message class, a \"drone_id\" field that identifies the source or target vehicle, a \"timestamp\" field in ISO 8601 format, and a \"payload\" object whose structure varies by message type. The protocol is intentionally designed for human readability during development and debugging; production deployments can optionally enable per-message gzip compression to reduce bandwidth consumption by approximately 60-70%."
    ),

    heading3("Example TELEM Packet"),
    ...codeBlock([
      "{",
      '  "type": "TELEM",',
      '  "drone_id": "NEXUS-07",',
      '  "timestamp": "2026-02-10T14:32:07.482Z",',
      '  "seq": 48201,',
      '  "payload": {',
      '    "position": {',
      '      "lat": 34.052235,',
      '      "lon": -118.243683,',
      '      "alt_msl": 142.7,',
      '      "alt_agl": 38.2',
      "    },",
      '    "attitude": {',
      '      "roll": 2.3,',
      '      "pitch": -1.1,',
      '      "yaw": 247.8',
      "    },",
      '    "velocity": {',
      '      "ground_speed": 8.4,',
      '      "vertical_speed": -0.3,',
      '      "heading": 247.8',
      "    },",
      '    "battery": {',
      '      "voltage": 22.4,',
      '      "current": 12.7,',
      '      "remaining_pct": 68',
      "    },",
      '    "gps": {',
      '      "fix_type": "3D_FIX",',
      '      "satellites": 14,',
      '      "hdop": 0.8',
      "    },",
      '    "link": {',
      '      "rssi": -52,',
      '      "quality": 94,',
      '      "latency_ms": 23',
      "    },",
      '    "status": "ACTIVE",',
      '    "formation": {',
      '      "role": "follower",',
      '      "offset_vector": { "x": -8.0, "y": 5.5, "z": 0.0 },',
      '      "cohesion": 0.94',
      "    }",
      "  }",
      "}",
    ]),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("3.3 Message Types"),
    messageTypesTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("3.4 Transport Redundancy"),
    bodyParagraph(
      "NEXUS implements a dual-transport architecture to maintain communication continuity in degraded radio environments. The primary transport is WiFi 6 mesh operating in the 5 GHz band, carrying full JSON-encoded NEXUS Wire Protocol messages at their native rates. The companion computer's failover_mgr module continuously monitors the WiFi link quality by tracking RSSI, packet loss rate, and round-trip latency. When the WiFi link degrades below configurable thresholds (default: RSSI below -80 dBm, packet loss above 15%, or latency above 500 ms), the failover_mgr automatically switches the downlink telemetry and uplink command streams to the RFD900x backup radio operating in the 900 MHz ISM band."
    ),
    bodyParagraph(
      "Because the RFD900x provides only 64 kbps of usable bandwidth (compared to tens of megabits on WiFi), the fallback transport uses a compact binary encoding based on MessagePack rather than JSON. The telemetry payload is also reduced to a critical subset of fields: position (lat, lon, alt_msl), battery remaining percentage, GPS fix type, link RSSI, and status. Attitude, velocity, and formation data are omitted to fit within the bandwidth constraint. The telemetry rate is also reduced from 10 Hz to 2 Hz on the backup link. When the WiFi link recovers (RSSI above -70 dBm for 10 consecutive seconds), the failover_mgr seamlessly transitions back to the primary transport and restores full telemetry fidelity. All failover events are logged and reported to the operator via ALERT messages."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 4: Swarm Coordination Protocol
// ---------------------------------------------------------------------------
function buildSwarmCoordination() {
  const formationTable = makeTable(
    ["Formation", "Description", "Offset Pattern", "Use Case"],
    [
      [
        "V-Formation",
        "Classic V shape with the leader at the apex and followers staggered along two trailing arms at 30-degree angles",
        "Each follower offset +/-30 deg behind leader with increasing depth; lateral spacing 8-12 m, longitudinal 10-15 m",
        "Transit flights, photogrammetric survey where overlapping coverage is needed",
      ],
      [
        "Line Abreast",
        "All drones fly side by side at equal lateral spacing along a line perpendicular to the direction of travel",
        "Equal lateral spacing (10-20 m) from formation center; all at same altitude and longitudinal position",
        "Wide area search, border patrol, linear infrastructure inspection",
      ],
      [
        "Column",
        "Single-file formation with all drones directly behind the leader at equal longitudinal spacing",
        "Direct trail behind leader with 12-18 m longitudinal spacing; identical lateral position and altitude",
        "Narrow corridor transit, following roads or rivers, penetration through constrained airspace",
      ],
      [
        "Diamond",
        "Four drones arranged at the cardinal points around a center, forming a diamond when viewed from above",
        "Four positions at north/south/east/west offsets (typically 15 m) from the geometric center of the formation",
        "Defensive posture, 360-degree sensor coverage, communication relay with omni-directional reach",
      ],
      [
        "Orbit",
        "Drones fly in a circle around a fixed point of interest, maintaining equal angular spacing on the orbit radius",
        "Equal angular separation (360/N degrees) at configurable orbit radius (30-100 m) and altitude",
        "Persistent surveillance, overwatch of a static target, communications relay over a fixed position",
      ],
      [
        "Scatter",
        "Pseudo-random distribution of drones within a defined geographic zone, maintaining minimum separation",
        "Random positions generated within polygon bounds, subject to minimum 20 m inter-drone spacing constraint",
        "Area denial, decoy saturation, distributed sensor coverage where regular patterns are undesirable",
      ],
    ],
    [14, 24, 32, 30]
  );

  return [
    heading1("4. Swarm Coordination Protocol"),

    bodyParagraph(
      "The swarm coordination layer is the algorithmic heart of NEXUS, responsible for transforming a collection of independently flying drones into a coherent, collaborative unit. It runs on the ground station server's Coordination Engine with critical safety functions also executing locally on each companion computer to ensure continued safe operation during communication outages. The coordination layer implements four major subsystems: formation management, leader election, collision avoidance, and task allocation."
    ),

    heading2("4.1 Formation Types"),
    bodyParagraph(
      "NEXUS supports six predefined formation types, each optimized for different operational scenarios. The operator selects a formation type through the HUD, and the coordination engine computes the required offset vector for each drone relative to the formation center or leader position. Drones transition to their assigned positions using smooth trajectory interpolation over a configurable transition period (default: 10 seconds) to avoid abrupt maneuvers."
    ),

    formationTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("4.2 Leader Election (Modified Raft Consensus)"),
    bodyParagraph(
      "NEXUS employs a modified version of the Raft consensus algorithm to elect and maintain a swarm leader. The leader is responsible for broadcasting formation updates, serving as the reference point for offset calculations, and making authoritative decisions during split-brain scenarios. Unlike traditional Raft which assigns equal weight to all voters, NEXUS uses a weighted scoring system to ensure the most capable drone is elected leader."
    ),

    heading3("Leader Scoring Criteria"),
    bullet([
      text("Battery Remaining (30% weight): ", { bold: true }),
      text(
        "Drones with higher battery reserves score higher, ensuring the leader can sustain its role for the duration of the mission without requiring a mid-mission leader transition."
      ),
    ]),
    bullet([
      text("GPS Quality (25% weight): ", { bold: true }),
      text(
        "Measured by fix type (3D RTK > 3D Fix > 2D Fix) and HDOP value. A leader with poor GPS would cause the entire formation to drift, so positional accuracy is heavily weighted."
      ),
    ]),
    bullet([
      text("Link Quality (25% weight): ", { bold: true }),
      text(
        "Composite score of WiFi RSSI, packet loss rate, and latency to the ground station. The leader must maintain reliable communication with both the operator and all followers."
      ),
    ]),
    bullet([
      text("Position Centrality (20% weight): ", { bold: true }),
      text(
        "Computed as the inverse of the average distance to all other swarm members. A centrally located leader minimizes the maximum offset distance any follower must maintain."
      ),
    ]),

    new Paragraph({ spacing: { after: 100 } }),

    heading3("Election Triggers"),
    bodyParagraph(
      "A new leader election is triggered under any of the following conditions: the current leader's heartbeat is not received for 3 consecutive seconds (leader failure); the current leader's composite score drops below 40% of its initial value at election time (leader degradation); the operator manually requests a leader change via the HUD; or the swarm composition changes by more than 25% (drones joining or leaving). When an election is triggered, each drone computes its own candidate score, broadcasts it as a PEER message, and the drone with the highest score becomes the new leader. In case of a tie, the drone with the numerically lower drone_id wins. The new leader's term is assigned an incrementing term number to prevent stale leadership claims, consistent with Raft term semantics."
    ),

    heading2("4.3 Collision Avoidance"),
    bodyParagraph(
      "Every drone in the NEXUS swarm continuously executes a collision avoidance algorithm on its local companion computer at 10 Hz, independent of the ground station link. The algorithm maintains a virtual safety bubble of 5-meter radius around each drone. Using PEER messages received from neighboring drones (and the drone's own GPS position), the algorithm computes the distance to every known peer and triggers avoidance maneuvers when any peer enters the safety bubble."
    ),

    bullet([
      text("Safety Bubble Radius: ", { bold: true }),
      text(
        "5 meters per drone (configurable). Any peer closer than 5 m triggers an immediate avoidance response."
      ),
    ]),
    bullet([
      text("Deconfliction Loop Rate: ", { bold: true }),
      text(
        "10 Hz. Position checks run every 100 ms to ensure rapid detection of converging trajectories."
      ),
    ]),
    bullet([
      text("Minimum Vertical Separation: ", { bold: true }),
      text(
        "3 meters. When two drones are on a potential collision course, the first avoidance action is vertical separation of at least 3 meters."
      ),
    ]),
    bullet([
      text("Right-of-Way Rule: ", { bold: true }),
      text(
        "The drone at higher altitude has right-of-way and maintains course. The lower drone descends or maneuvers laterally. If both are at the same altitude, the drone with the lower drone_id holds course."
      ),
    ]),
    bullet([
      text("Avoidance Maneuver Sequence: ", { bold: true }),
      text(
        "(1) Immediate velocity reduction to 50%. (2) Vertical separation: lower drone descends 3 m. (3) If still converging, lateral offset perpendicular to closing vector. (4) Resume formation position once separation exceeds 8 m."
      ),
    ]),

    new Paragraph({ spacing: { after: 100 } }),

    heading2("4.4 Market-Based Task Allocation"),
    bodyParagraph(
      "When the operator assigns a mission task (such as surveying a waypoint, inspecting a target, or relaying communications from a specific position), the coordination engine distributes the task using a market-based allocation mechanism. This approach produces near-optimal task assignments without requiring centralized planning and adapts naturally as drones join, leave, or change state during a mission."
    ),

    bullet([
      text("Task Announcement: ", { bold: true }),
      text(
        "The coordination engine broadcasts the task definition (type, location, priority, estimated duration, required capabilities) to all active drones via the mesh network."
      ),
    ]),
    bullet([
      text("Bidding: ", { bold: true }),
      text(
        "Each drone computes a bid score based on its distance to the task location (closer is better, weighted 40%), remaining battery capacity (more is better, weighted 35%), and whether it possesses required capabilities such as a specific sensor payload (binary qualifier, weighted 25%). Drones below the minimum battery threshold for the task are excluded."
      ),
    ]),
    bullet([
      text("Assignment: ", { bold: true }),
      text(
        "The coordination engine collects bids within a 2-second bidding window, selects the highest bidder, and sends a FORMATION message assigning the task. If the winning drone fails to acknowledge within 3 seconds, the task is re-auctioned to the remaining bidders."
      ),
    ]),
    bullet([
      text("Execution and Reporting: ", { bold: true }),
      text(
        "The assigned drone navigates to the task location, executes the required behavior, and reports completion via an ACK message. The coordination engine updates the mission state and can reassign the drone to the next pending task."
      ),
    ]),
  ];
}

// ---------------------------------------------------------------------------
// Section 5: Companion Computer Software
// ---------------------------------------------------------------------------
function buildCompanionSoftware() {
  const modulesTable = makeTable(
    ["Module", "File", "Function", "Dependencies"],
    [
      [
        "MAVLink Bridge",
        "mavlink_bridge.py",
        "Establishes serial connection to flight controller, parses incoming MAVLink messages, translates outgoing commands from internal format to MAVLink, maintains heartbeat with autopilot",
        "pymavlink",
      ],
      [
        "Telemetry Publisher",
        "telem_publisher.py",
        "Aggregates normalized telemetry from mavlink_bridge, assembles NEXUS TELEM packets at 10 Hz, publishes to ground station and peers via WebSocket",
        "websockets, msgpack",
      ],
      [
        "Command Executor",
        "cmd_executor.py",
        "Receives operator CMD messages, validates against safety constraints (geofence, battery limits), translates to MAVLink COMMAND_LONG, tracks ACK status",
        "(none)",
      ],
      [
        "Peer Mesh",
        "peer_mesh.py",
        "Discovers peers via mDNS/zeroconf, maintains peer state table, broadcasts compact PEER messages at 5 Hz, relays messages for multi-hop routing",
        "zeroconf",
      ],
      [
        "Failover Manager",
        "failover_mgr.py",
        "Monitors WiFi and RFD900x link health metrics, triggers transport failover when thresholds are breached, manages fallback to binary MessagePack encoding, restores primary link",
        "(none)",
      ],
      [
        "Config Loader",
        "config_loader.py",
        "Reads and validates the nexus.yaml configuration file at startup, provides typed access to all config parameters, watches for live config updates",
        "pyyaml",
      ],
    ],
    [14, 16, 46, 24]
  );

  return [
    heading1("5. Companion Computer Software"),

    bodyParagraph(
      "Each drone in the NEXUS swarm runs a software suite on its companion computer (Raspberry Pi 5 by default) that handles all communication bridging, telemetry processing, peer networking, and local safety enforcement. The software is written in Python 3.11 for rapid development and broad hardware compatibility, with performance-critical paths (telemetry serialization, collision avoidance math) implemented using NumPy vectorized operations. The suite is managed by systemd and starts automatically on boot, establishing communication with the flight controller and joining the swarm within 15 seconds of power-on."
    ),

    heading2("5.1 Software Modules"),
    modulesTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("5.2 Plug-and-Play Installation"),
    bodyParagraph(
      "Deploying NEXUS on a new drone requires three steps and can be completed in under 10 minutes by a field technician with no software development experience."
    ),

    numberedItem([
      text("Flash SD Card: ", { bold: true }),
      text(
        "Download the pre-built NEXUS companion image (based on Raspberry Pi OS Lite 64-bit) and flash it to a 32 GB or larger microSD card using balenaEtcher or the Raspberry Pi Imager. The image includes Python 3.11, all NEXUS dependencies, pre-configured systemd services, and network management tools."
      ),
    ]),
    numberedItem([
      text("Edit nexus.yaml: ", { bold: true }),
      text(
        "Mount the SD card on any computer and open the /boot/nexus.yaml configuration file in a text editor. Set the drone_id to a unique identifier (e.g., NEXUS-07), assign the role (leader or follower), configure the WiFi mesh SSID and passphrase, and verify the MAVLink serial port path (default: /dev/ttyAMA0). All other settings have sensible defaults."
      ),
    ]),
    numberedItem([
      text("Connect and Power On: ", { bold: true }),
      text(
        "Insert the SD card into the Raspberry Pi 5, connect the Pi to the flight controller's TELEM2 port via a JST-GH to USB-C cable (or direct UART wiring), and power on the companion computer. The NEXUS services will start automatically, establish a MAVLink heartbeat with the flight controller, join the WiFi mesh, announce themselves via mDNS, and begin publishing telemetry within 15 seconds."
      ),
    ]),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("5.3 Configuration Reference (nexus.yaml)"),
    bodyParagraph(
      "The following is a complete example nexus.yaml configuration file showing all available parameters with their default or recommended values."
    ),

    ...codeBlock([
      "# =========================================================",
      "# NEXUS Companion Computer Configuration",
      "# File: /boot/nexus.yaml",
      "# =========================================================",
      "",
      "drone_id: NEXUS-07",
      "role: follower          # leader | follower",
      "swarm_id: ALPHA         # Swarm group identifier",
      "",
      "swarm:",
      "  max_drones: 50",
      "  heartbeat_interval_ms: 1000",
      "  heartbeat_timeout_ms: 3000",
      "  peer_broadcast_hz: 5",
      "  telem_publish_hz: 10",
      "",
      "network:",
      "  wifi:",
      "    ssid: NEXUS-MESH-ALPHA",
      "    passphrase: s3cur3-m3sh-k3y!",
      "    channel: 36",
      "    band: 5GHz",
      "    mode: mesh            # mesh | ap | client",
      "  rfd900:",
      "    port: /dev/ttyUSB0",
      "    baud: 57600",
      "    net_id: 25",
      "    tx_power: 27          # dBm (max 30)",
      "  ground_station:",
      "    host: 192.168.50.1",
      "    ws_port: 8765",
      "    api_port: 3000",
      "",
      "mavlink:",
      "  port: /dev/ttyAMA0",
      "  baud: 921600",
      "  system_id: 1",
      "  component_id: 191       # MAV_COMP_ID_ONBOARD_COMPUTER",
      "  dialect: ardupilot      # ardupilot | px4",
      "  stream_rates:",
      "    GLOBAL_POSITION_INT: 10",
      "    ATTITUDE: 10",
      "    SYS_STATUS: 1",
      "    GPS_RAW_INT: 5",
      "    BATTERY_STATUS: 0.2",
      "",
      "failover:",
      "  wifi_rssi_threshold: -80       # dBm",
      "  wifi_loss_threshold: 15        # percent",
      "  wifi_latency_threshold: 500    # ms",
      "  recovery_rssi: -70             # dBm",
      "  recovery_hold_s: 10            # seconds above threshold",
      "  rtl_timeout_s: 10              # seconds link-lost before RTL",
      "",
      "formation:",
      "  default_type: v-formation",
      "  default_spacing_m: 12",
      "  transition_time_s: 10",
      "  cohesion_target: 0.90",
      "  max_offset_error_m: 3.0",
      "",
      "safety:",
      "  geofence_enabled: true",
      "  geofence_polygon:              # WGS84 vertices",
      "    - [34.055, -118.250]",
      "    - [34.055, -118.235]",
      "    - [34.045, -118.235]",
      "    - [34.045, -118.250]",
      "  max_altitude_m: 120",
      "  min_altitude_m: 10",
      "  safety_bubble_m: 5",
      "  min_vertical_sep_m: 3",
      "  battery_rtl_pct: 25",
      "  battery_land_pct: 10",
      "",
      "logging:",
      "  level: INFO                    # DEBUG | INFO | WARNING | ERROR",
      "  file: /var/log/nexus/nexus.log",
      "  max_size_mb: 100",
      "  rotate_count: 5",
      "  telem_recording: true",
      "  telem_log: /var/log/nexus/telem.jsonl",
    ]),
  ];
}

// ---------------------------------------------------------------------------
// Section 6: Ground Station Server
// ---------------------------------------------------------------------------
function buildGroundStation() {
  const componentsTable = makeTable(
    ["Component", "Technology", "Function"],
    [
      [
        "WebSocket Hub",
        "Node.js 20 + ws library",
        "Maintains persistent bidirectional WebSocket connections to every drone companion computer and every connected HUD client. Routes TELEM messages from drones to HUD clients, and CMD messages from HUD clients to the addressed drone. Handles connection lifecycle, reconnection, and message queuing.",
      ],
      [
        "Coordination Engine",
        "Node.js + custom modules",
        "Implements swarm coordination logic: formation offset computation, modified Raft leader election, market-based task allocation, and collision avoidance monitoring. Publishes FORMATION messages to drones and status updates to HUD clients.",
      ],
      [
        "State Store",
        "Redis 7 + SQLite 3",
        "Redis provides an in-memory key-value cache of the latest state for each drone (updated at 10 Hz), enabling sub-millisecond reads for the HUD and coordination engine. SQLite stores persistent mission logs, telemetry history, event records, and configuration snapshots.",
      ],
      [
        "HTTP Server",
        "Express.js 4",
        "Serves the HUD static files (HTML, CSS, JavaScript), exposes a REST API for mission planning (CRUD waypoints, geofences, formation presets), provides configuration endpoints for companion computers to fetch over-the-air updates, and serves health check endpoints for monitoring.",
      ],
      [
        "Logger",
        "Winston + daily-rotate-file",
        "Structured JSON logging for all server events, command auditing, and error tracking. Telemetry data is logged to separate rotating files at configurable intervals. Log levels are configurable at runtime via the REST API.",
      ],
    ],
    [16, 20, 64]
  );

  const deploymentTable = makeTable(
    ["Option", "Configuration", "Drone Scale", "Description"],
    [
      [
        "Field Laptop",
        "Single machine, all-in-one",
        "2-6 drones",
        "A ruggedized laptop (e.g., Panasonic Toughbook) runs the complete NEXUS ground station stack: Node.js server, Redis, SQLite, and HUD browser. The laptop connects directly to the WiFi mesh network via an external USB WiFi 6 adapter with an omnidirectional antenna. Ideal for small-team operations, training exercises, and rapid deployment scenarios where setup time must be under 5 minutes. Limited by the laptop's single WiFi radio and processing power.",
      ],
      [
        "Edge Server",
        "Rack-mount at field operations center",
        "6-20 drones",
        "A compact rack-mount server (e.g., Intel NUC cluster or Supermicro Mini-ITX) deployed at a forward operating base with a dedicated network bridge to the drone mesh. Multiple WiFi radios and a cellular backhaul provide redundant connectivity. Redis runs with persistence enabled, and SQLite is replaced with PostgreSQL for concurrent access. Supports multiple simultaneous operator HUD sessions on different workstations.",
      ],
      [
        "Cloud Hybrid",
        "Cloud backend + field relay",
        "20-50 drones",
        "The coordination engine and state store run on AWS EC2 or Google Cloud Compute instances, providing virtually unlimited processing capacity and global accessibility. A field relay unit (edge server with a satellite or LTE uplink) bridges the local drone mesh to the cloud backend. HUD clients can connect from anywhere via the internet. Telemetry is stored in cloud-hosted TimescaleDB for long-term analytics. Adds 50-150 ms latency compared to local deployment.",
      ],
    ],
    [12, 18, 12, 58]
  );

  return [
    heading1("6. Ground Station Server"),

    bodyParagraph(
      "The NEXUS ground station server is the central hub that connects all drones in the swarm to the operator. It is implemented as a Node.js application that can run on hardware ranging from a single laptop to a multi-node cloud deployment, depending on the scale of the operation. The server maintains real-time bidirectional communication with every drone via WebSocket, processes and distributes telemetry data, executes swarm coordination algorithms, serves the operator HUD interface, and provides a REST API for mission planning and system configuration."
    ),

    heading2("6.1 Server Components"),
    componentsTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("6.2 Deployment Options"),
    bodyParagraph(
      "NEXUS supports three deployment configurations that scale from small field teams to large multi-site operations. The ground station software is identical across all configurations; only the underlying hardware and network topology change."
    ),

    deploymentTable,

    new Paragraph({ spacing: { after: 200 } }),
  ];
}

// ---------------------------------------------------------------------------
// Section 7: Operator Interface (HUD)
// ---------------------------------------------------------------------------
function buildOperatorHUD() {
  return [
    heading1("7. Operator Interface (HUD)"),

    bodyParagraph(
      "The NEXUS Heads-Up Display (HUD) is a browser-based single-page application that provides the operator with comprehensive real-time situational awareness and full command authority over the drone swarm. It is built with vanilla HTML5, CSS3, and JavaScript (no framework dependencies) to ensure maximum compatibility, minimal load times, and reliable operation on field-deployed hardware where installing Node.js build toolchains may not be practical. The HUD connects to the ground station server via a single WebSocket connection and renders updates at 60 fps using hardware-accelerated CSS transforms and HTML5 Canvas for the map and attitude indicators."
    ),

    heading2("7.1 HUD Components"),

    heading3("Map View"),
    bodyParagraph(
      "The central map occupies the majority of the screen and displays all active drones as color-coded icons on an OpenStreetMap tile layer. Each drone icon shows its current heading as a directional arrow, and a fading trail indicates its recent flight path. Formation offset lines are drawn between the leader and each follower, with color coding indicating cohesion quality (green for within tolerance, yellow for drifting, red for out of formation). The map supports pan, zoom, click-to-select, and drawing tools for geofence polygons and waypoint paths. A minimap in the corner provides a zoomed-out overview of the entire operational area."
    ),

    heading3("Drone List Panel"),
    bodyParagraph(
      "A scrollable panel on the left side of the HUD lists every drone in the swarm as a compact card. Each card displays the drone ID, current status (a color-coded badge: green for active, yellow for warning, red for critical, gray for offline), battery percentage with a visual bar, GPS fix indicator, link quality bar, and the drone's current formation role (leader or follower). Clicking a drone card selects that drone and centers the map on its position. The list auto-sorts by status severity so that drones requiring attention appear at the top."
    ),

    heading3("Detail Panel"),
    bodyParagraph(
      "When a drone is selected, the right side of the HUD expands to show a comprehensive detail panel. This panel includes numerical readouts for all telemetry fields (latitude, longitude, altitude MSL and AGL, ground speed, vertical speed, heading, roll, pitch, yaw), an attitude indicator (artificial horizon) rendered on Canvas that visually represents the drone's roll and pitch, and battery/GPS/link quality gauges. Command buttons allow the operator to arm, disarm, takeoff, land, RTL, or change the flight mode of the selected drone."
    ),

    heading3("Sparkline Charts"),
    bodyParagraph(
      "Below the detail panel, a row of sparkline mini-charts displays 60-second rolling histories for key metrics: altitude, ground speed, battery voltage, and link RSSI. These sparklines allow the operator to quickly identify trends (a steadily dropping battery, an oscillating altitude indicating turbulence, or a degrading link signal) without needing to switch to a full charting view. Each sparkline auto-scales to the data range and highlights threshold crossings with color changes."
    ),

    heading3("Event Log"),
    bodyParagraph(
      "A collapsible panel at the bottom of the HUD displays a chronological event log with color-coded entries: blue for informational (drone connected, mode changed), amber for warnings (low battery, GPS degradation), and red for critical alerts (link loss, collision risk, geofence breach). Each event includes a timestamp, the source drone ID, and a human-readable description. Critical events also trigger an audio chime and cause the relevant drone card in the list to flash. The log can be filtered by severity level or drone ID."
    ),

    heading2("7.2 Interaction Model"),
    bodyParagraph(
      "The HUD is designed for single-operator control of the entire swarm. Click a drone on the map or in the list to select it; the detail panel updates immediately. Hold Shift and click multiple drones to select a group, then issue commands that apply to all selected vehicles simultaneously. Mode switching is handled through a dropdown in the detail panel: each flight mode (Stabilize, AltHold, Loiter, Guided, Auto, RTL, Land) is represented with a clear label and icon. Formation commands are accessible from a floating toolbar: the operator selects a formation type, adjusts spacing with a slider, and clicks \"Apply\" to transition the swarm. All destructive commands (disarm, RTL, Land) require a confirmation click to prevent accidental activation."
    ),

    heading2("7.3 Design Philosophy"),
    bodyParagraph(
      "The HUD design prioritizes information density without clutter, leveraging the principles of ecological interface design to make critical state information immediately perceptible through spatial layout, color coding, and motion rather than requiring the operator to read numerical values. Status-at-a-glance is achieved through consistent use of the green/yellow/red color vocabulary across all components. Animations are used sparingly and purposefully: a flashing drone card demands attention, a smooth map pan maintains spatial orientation, and a pulsing ring around a selected drone confirms the selection without obscuring the map. The interface is optimized for a single 15-inch or larger display but also functions on tablets for field-portable operations."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 8: Safety & Fault Handling
// ---------------------------------------------------------------------------
function buildSafetyFaultHandling() {
  const faultTable = makeTable(
    ["Fault", "Detection Method", "Automated Response", "Operator Alert"],
    [
      [
        "Link Loss (Single Drone)",
        "3 consecutive missed heartbeats (3 seconds without HEARTBEAT message from one drone)",
        "Hold position for 10 seconds awaiting reconnection; if link not restored, execute autonomous RTL with pre-assigned altitude slot; if GPS degraded, hover on IMU dead-reckoning and descend at 0.5 m/s",
        "Amber alert in event log, drone card flashes amber, map icon changes to hollow outline with last-known position marker",
      ],
      [
        "Link Loss (Swarm-Wide)",
        "More than 50% of active drones simultaneously fail heartbeat check within a 5-second window",
        "All drones execute autonomous RTL with staggered altitude assignments (base altitude + drone_index * 5 m) to prevent mid-air collisions during return; ground station enters emergency mode",
        "Red alert banner across top of HUD, audio alarm (continuous tone), all drone cards flash red, map shows projected RTL paths",
      ],
      [
        "Low Battery",
        "Battery remaining percentage drops below 25% (configurable threshold) as reported in BATTERY_STATUS messages",
        "Automatic RTL command issued to the affected drone; priority landing slot assigned to prevent multiple drones landing simultaneously; formation position reassigned to remaining drones",
        "Amber alert with countdown timer showing estimated remaining flight time, battery bar turns red, sparkline highlights threshold crossing",
      ],
      [
        "GPS Loss",
        "GPS fix_type degrades below 3D Fix (value < 3) or HDOP exceeds 5.0 as reported in GPS_RAW_INT messages",
        "Drone holds current position using IMU dead-reckoning and barometric altitude hold; begins slow controlled descent at 0.5 m/s if GPS not recovered within 30 seconds; excluded from formation until GPS restored",
        "Amber alert with GPS icon crossed out on drone card, map icon shows uncertainty circle expanding over time, detail panel GPS gauge turns red",
      ],
      [
        "Leader Failure",
        "Swarm leader's heartbeat not received for 3 consecutive seconds by any follower or the ground station",
        "Raft election process triggers automatically; all followers compute candidate scores; highest-scoring follower promotes to leader within 2 seconds; formation offsets recalculated relative to new leader",
        "Alert notification identifying old and new leader, new leader's drone card highlighted with crown icon, formation lines on map update to reflect new reference point",
      ],
      [
        "Collision Risk",
        "Safety bubble overlap detected: two or more drones within 5 meters of each other as computed from PEER position reports at 10 Hz",
        "Both drones immediately reduce velocity to 50%; lower drone descends 3 m for vertical separation; if still converging, lateral offset applied perpendicular to closing vector; normal flight resumes when separation exceeds 8 m",
        "Red flash on both affected drone cards and map icons, red connecting line drawn between the two drones on map, audio warning chime, event log entry with separation distance",
      ],
      [
        "Geofence Breach",
        "Drone position (from GLOBAL_POSITION_INT) is outside the defined geofence polygon or exceeds maximum altitude limit, checked at 10 Hz",
        "Hard stop command at geofence boundary (drone halts forward motion); if drone has already crossed the boundary, immediate RTL command issued; fence violation logged with GPS coordinates and timestamp",
        "Red alert in event log, geofence boundary highlighted red on map, drone icon shows stop symbol, affected drone card displays GEOFENCE BREACH in red text",
      ],
      [
        "Motor Failure",
        "RPM or current draw anomaly detected by the flight controller (reported via SYS_STATUS error flags or STATUSTEXT messages indicating motor failure)",
        "Emergency landing initiated immediately on the affected drone; surrounding drones commanded to increase separation and clear the airspace below the failing drone to prevent secondary collisions",
        "Red critical alert with audio alarm, drone card shows MOTOR FAIL in flashing red, map icon changes to emergency symbol, event log records motor index and failure type",
      ],
    ],
    [14, 22, 34, 30]
  );

  return [
    heading1("8. Safety & Fault Handling"),

    bodyParagraph(
      "Safety is the paramount design consideration in NEXUS. Every automated behavior is designed to fail safe: when in doubt, a drone will hold position or return to launch rather than continue a potentially dangerous maneuver. The fault handling system operates at two levels: local safety enforcement on each companion computer (which continues to function even without a ground station link) and centralized monitoring on the ground station server (which provides the operator with comprehensive situational awareness and override capability). The following table defines the eight primary fault scenarios, their detection mechanisms, automated responses, and operator alerts."
    ),

    heading2("8.1 Fault Response Matrix"),
    faultTable,

    new Paragraph({ spacing: { after: 200 } }),

    heading2("8.2 Safety Architecture Principles"),
    bodyParagraph(
      "The NEXUS safety architecture is built on three foundational principles. First, defense in depth: every safety-critical function is implemented at multiple levels. Collision avoidance runs locally on each companion computer, is monitored by the coordination engine on the ground station, and can be manually overridden by the operator. Battery management is enforced by the companion computer's cmd_executor (which refuses to execute commands that would violate battery thresholds), by the flight controller's own failsafe parameters, and by the operator via the HUD's battery alerts. Second, fail-safe defaults: all timeouts, thresholds, and fallback behaviors are configured to produce the safest possible outcome. A drone that loses communication returns to launch. A drone that loses GPS descends slowly. A drone that detects a collision risk stops. Third, operator authority: every automated safety response can be overridden by the operator through the HUD. If the operator determines that an automated RTL is inappropriate (for example, because the launch point is now in a danger zone), they can cancel the RTL and manually command the drone to an alternate landing site."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 9: Development Roadmap
// ---------------------------------------------------------------------------
function buildDevelopmentRoadmap() {
  const roadmapTable = makeTable(
    ["Phase", "Name", "Description", "Timeline"],
    [
      [
        "1",
        "Foundation",
        "Establish core infrastructure: single-drone MAVLink bridge on companion computer, basic telemetry parsing and normalization, WebSocket server for real-time data streaming, initial HUD with map view and single-drone telemetry display. Deliverables include mavlink_bridge.py, telem_publisher.py, ground station WebSocket hub, and a minimal but functional HUD.",
        "Weeks 1-4",
      ],
      [
        "2",
        "Multi-Drone",
        "Extend the platform to support multiple simultaneous drones: state management for N vehicles in Redis, drone list panel in HUD with click-to-select interaction, detail panel with per-drone telemetry, drone switching, and multi-drone map visualization with individual icons and trails. Load testing with simulated multi-vehicle SITL instances.",
        "Weeks 5-8",
      ],
      [
        "3",
        "Mesh Network",
        "Implement the peer-to-peer mesh networking layer: peer_mesh.py with mDNS discovery and PEER message broadcasting, transport redundancy with RFD900x fallback, failover_mgr.py for automatic link switching, binary MessagePack encoding for low-bandwidth transport, and comprehensive link quality monitoring in the HUD.",
        "Weeks 9-12",
      ],
      [
        "4",
        "Formation",
        "Implement formation flight capabilities: V-formation as the initial formation type with configurable spacing, formation offset computation relative to leader position, cohesion tracking and visualization on the HUD map (colored lines between leader and followers), formation transition commands from the HUD toolbar, and smooth trajectory interpolation during transitions.",
        "Weeks 13-16",
      ],
      [
        "5",
        "Coordination",
        "Build the full swarm coordination layer: modified Raft consensus for leader election with weighted scoring, market-based task allocation with bidding, collision avoidance with 5 m safety bubbles and 10 Hz deconfliction, all six formation types (V, Line Abreast, Column, Diamond, Orbit, Scatter), and comprehensive fault handling as defined in Section 8.",
        "Weeks 17-22",
      ],
      [
        "6",
        "Mission Planning",
        "Add mission planning capabilities to the HUD: waypoint editor with drag-and-drop map interface, geofence polygon drawing tool with visual boundary enforcement, mission recording and replay for after-action review, mission templates for common operations, and a REST API for programmatic mission management by external systems.",
        "Weeks 23-28",
      ],
      [
        "7",
        "CV Integration",
        "Integrate onboard computer vision capabilities for autonomous target detection and tracking: Jetson Orin Nano as companion computer (replacing Pi 5 on CV-equipped drones), YOLO-based object detection pipeline, target handoff between drones during formation transitions, video streaming to the HUD with detection overlays, and autonomous orbit-and-track behavior for detected targets.",
        "Weeks 29-36",
      ],
    ],
    [7, 12, 62, 12]
  );

  return [
    heading1("9. Development Roadmap"),

    bodyParagraph(
      "The NEXUS platform is being developed in seven phases over a 36-week timeline, each phase building incrementally on the capabilities delivered by the previous phase. This phased approach allows for continuous integration testing, regular stakeholder demonstrations, and the flexibility to adjust priorities based on operational feedback. Each phase concludes with a milestone review that includes a live demonstration of new capabilities, updated documentation, and a go/no-go decision for the next phase."
    ),

    roadmapTable,

    new Paragraph({ spacing: { after: 200 } }),

    bodyParagraph(
      "The timeline assumes a core development team of 3-4 engineers working full-time, with access to at least 4 physical drones and a complete ArduPilot SITL simulation environment for testing at scale. Phases 1-4 can be developed and tested entirely in simulation; Phases 5-7 require progressively more physical flight testing. All phase timelines include a one-week buffer for integration testing and bug fixes. The Phase 7 CV integration timeline is the most variable, as it depends on the availability and performance of the Jetson Orin Nano hardware and the maturity of the YOLO detection models for the target classes relevant to the operational mission."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Section 10: Getting Started
// ---------------------------------------------------------------------------
function buildGettingStarted() {
  return [
    heading1("10. Getting Started"),

    bodyParagraph(
      "This section provides a step-by-step guide to setting up a NEXUS development environment and verifying basic telemetry connectivity with a simulated drone. The entire process can be completed on a single Ubuntu 22.04 workstation in approximately 30 minutes. No physical drone hardware is required; the ArduPilot Software-In-The-Loop (SITL) simulator provides a fully functional virtual drone that responds to MAVLink commands and generates realistic telemetry."
    ),

    heading2("Step 1: Install ArduPilot SITL"),
    bodyParagraph(
      "The ArduPilot SITL simulator allows you to run the full ArduPilot firmware on your development machine, simulating a Copter, Plane, or Rover vehicle with realistic physics. Install the prerequisites and clone the ArduPilot repository:"
    ),
    ...codeBlock([
      "sudo apt-get update",
      "sudo apt-get install -y git python3-pip python3-dev",
      "sudo apt-get install -y build-essential ccache g++ gawk",
      "sudo apt-get install -y libtool libxml2-dev libxslt1-dev",
      "sudo apt-get install -y python3-lxml python3-numpy python3-pyparsing",
      "",
      "git clone https://github.com/ArduPilot/ardupilot.git",
      "cd ardupilot",
      "git submodule update --init --recursive",
      "./Tools/environment_install/install-prereqs-ubuntu.sh -y",
      ". ~/.profile",
    ]),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("Step 2: Clone NEXUS Repository"),
    bodyParagraph(
      "Clone the NEXUS repository and install all Node.js and Python dependencies:"
    ),
    ...codeBlock([
      "git clone https://github.com/nexus-swarm/nexus.git",
      "cd nexus",
      "npm install                    # Install Node.js dependencies",
      "pip3 install pymavlink websockets pyyaml zeroconf msgpack",
    ]),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("Step 3: Launch Simulated Drone"),
    bodyParagraph(
      "Start an ArduPilot SITL Copter instance. This creates a virtual drone that listens for MAVLink connections on TCP port 5760:"
    ),
    ...codeBlock([
      "cd ~/ardupilot",
      "sim_vehicle.py -v ArduCopter --map --console \\",
      "    -l 34.052235,-118.243683,30,0 \\",
      "    --out=udp:127.0.0.1:14550",
    ]),
    bodyParagraph(
      "The --map flag opens a real-time map window showing the simulated drone's position, and --console opens a MAVProxy console for manual MAVLink interaction. The -l flag sets the simulated drone's home location to Los Angeles (latitude 34.052235, longitude -118.243683, altitude 30 m, heading 0 degrees). The --out flag forwards MAVLink messages to UDP port 14550, which the NEXUS mavlink_bridge will connect to."
    ),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("Step 4: Start NEXUS Ground Station"),
    bodyParagraph(
      "In a new terminal, start the NEXUS ground station server. The server will listen for drone connections on WebSocket port 8765 and serve the HUD on HTTP port 3000:"
    ),
    ...codeBlock([
      "cd ~/nexus",
      "node server/index.js",
      "",
      "# Expected output:",
      "# [INFO] NEXUS Ground Station v1.0.0 starting...",
      "# [INFO] WebSocket hub listening on ws://0.0.0.0:8765",
      "# [INFO] HTTP server listening on http://0.0.0.0:3000",
      "# [INFO] Redis connected (state store ready)",
      "# [INFO] Waiting for drone connections...",
    ]),

    new Paragraph({ spacing: { after: 200 } }),

    heading2("Step 5: Open HUD and Verify Telemetry"),
    bodyParagraph(
      "Open a web browser and navigate to http://localhost:3000. The NEXUS HUD should load and display an empty map centered on the default operational area. Within a few seconds of the companion computer bridge connecting to the SITL drone, you should see:"
    ),

    bullet(
      "A drone icon appearing on the map at the simulated home location (Los Angeles)"
    ),
    bullet(
      "The drone list panel showing one drone (NEXUS-01) with a green ACTIVE status badge"
    ),
    bullet(
      "Battery level at 100%, GPS showing 3D Fix with 10+ satellites, and link quality at maximum"
    ),
    bullet(
      "The attitude indicator showing level flight (0 degrees roll and pitch)"
    ),
    bullet(
      "Sparklines beginning to populate with altitude, speed, and battery voltage data"
    ),
    bullet("The event log showing a DRONE_CONNECTED event with a blue badge"),

    new Paragraph({ spacing: { after: 100 } }),

    bodyParagraph(
      "To test command functionality, click the drone icon on the map to select it, then use the detail panel to arm the vehicle (click ARM, confirm) and issue a takeoff command (click TAKEOFF, set altitude to 20 m, confirm). The simulated drone should arm, spool up its virtual motors, and climb to 20 meters AGL. The HUD should show the altitude increasing in both the numerical readout and the altitude sparkline, and the map icon's altitude label should update in real time. Congratulations: your NEXUS development environment is operational."
    ),
  ];
}

// ---------------------------------------------------------------------------
// Main: Assemble and generate document
// ---------------------------------------------------------------------------
async function main() {
  console.log("NEXUS Document Generator v1.0");
  console.log("Generating: NEXUS - System Architecture & Protocol Specification");
  console.log("");

  const doc = new Document({
    title: "NEXUS - System Architecture & Protocol Specification",
    description:
      "Complete technical architecture document for the NEXUS multi-UAV swarm management platform.",
    creator: "NEXUS Engineering Team",
    styles: {
      default: {
        document: {
          run: {
            font: FONT,
            size: 22,
            color: COLORS.BODY_TEXT,
          },
          paragraph: {
            spacing: { line: 276 },
          },
        },
        heading1: {
          run: {
            font: FONT,
            size: 48,
            bold: true,
            color: COLORS.DARK_BLUE,
          },
          paragraph: {
            spacing: { before: 400, after: 200 },
          },
        },
        heading2: {
          run: {
            font: FONT,
            size: 36,
            bold: true,
            color: COLORS.DARK_BLUE,
          },
          paragraph: {
            spacing: { before: 300, after: 150 },
          },
        },
        heading3: {
          run: {
            font: FONT,
            size: 28,
            bold: true,
            color: COLORS.MEDIUM_BLUE,
          },
          paragraph: {
            spacing: { before: 240, after: 120 },
          },
        },
      },
    },
    numbering: {
      config: [
        {
          reference: "numbered-list",
          levels: [
            {
              level: 0,
              format: NumberFormat.DECIMAL,
              text: "%1.",
              alignment: AlignmentType.LEFT,
              style: {
                paragraph: {
                  indent: {
                    left: convertInchesToTwip(0.5),
                    hanging: convertInchesToTwip(0.25),
                  },
                },
              },
            },
          ],
        },
      ],
    },
    sections: [
      {
        properties: {
          page: {
            size: {
              width: convertInchesToTwip(8.5),
              height: convertInchesToTwip(11),
            },
            margin: {
              top: convertInchesToTwip(1),
              right: convertInchesToTwip(1),
              bottom: convertInchesToTwip(1),
              left: convertInchesToTwip(1),
            },
          },
        },
        headers: {
          default: new Header({
            children: [
              new Paragraph({
                children: [
                  new TextRun({
                    text: "NEXUS \u2014 System Architecture",
                    font: FONT,
                    size: 16,
                    color: "888888",
                    italics: true,
                  }),
                ],
                alignment: AlignmentType.RIGHT,
              }),
            ],
          }),
        },
        footers: {
          default: new Footer({
            children: [
              new Paragraph({
                children: [
                  new TextRun({
                    text: "Page ",
                    font: FONT,
                    size: 16,
                    color: "888888",
                  }),
                  new TextRun({
                    children: [PageNumber.CURRENT],
                    font: FONT,
                    size: 16,
                    color: "888888",
                  }),
                ],
                alignment: AlignmentType.CENTER,
              }),
            ],
          }),
        },
        children: [
          // Title page
          ...buildTitlePage(),

          // Section 1
          ...buildExecutiveSummary(),

          // Section 2
          ...buildSystemArchitecture(),

          // Section 3
          ...buildCommunicationProtocol(),

          // Section 4
          ...buildSwarmCoordination(),

          // Section 5
          ...buildCompanionSoftware(),

          // Section 6
          ...buildGroundStation(),

          // Section 7
          ...buildOperatorHUD(),

          // Section 8
          ...buildSafetyFaultHandling(),

          // Section 9
          ...buildDevelopmentRoadmap(),

          // Section 10
          ...buildGettingStarted(),
        ],
      },
    ],
  });

  // Ensure the output directory exists
  const outputDir = path.resolve(__dirname, "../../dist");
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const outputPath = path.join(outputDir, "nexus-architecture.docx");

  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);

  const fileSizeKB = (buffer.length / 1024).toFixed(1);
  console.log(`Document generated successfully.`);
  console.log(`  Output: ${outputPath}`);
  console.log(`  Size:   ${fileSizeKB} KB`);
  console.log("");
  console.log("Done.");
}

main().catch((err) => {
  console.error("Error generating document:", err);
  process.exit(1);
});
