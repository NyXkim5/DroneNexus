/**
 * NEXUS Wire Protocol — Shared Constants & Definitions
 *
 * Reference implementation of the NEXUS communication protocol.
 * Used by both ground station server and companion computer clients.
 */

// ============================================================
// Message Types
// ============================================================
const MessageType = {
  TELEM:      'TELEM',       // Telemetry data (downlink, 10Hz)
  HEARTBEAT:  'HEARTBEAT',   // Liveness check (bidirectional, 1Hz)
  CMD:        'CMD',         // Operator command (uplink, on-demand)
  FORMATION:  'FORMATION',   // Formation update (downlink, on-change)
  WAYPOINT:   'WAYPOINT',    // Mission waypoint (uplink, on-demand)
  PEER:       'PEER',        // Peer state exchange (lateral, 5Hz)
  ALERT:      'ALERT',       // Fault notification (downlink, on-event)
  ACK:        'ACK',         // Command acknowledgment (bidirectional)
  VIDEO_CTRL:  'VIDEO_CTRL',  // Video stream control (start/stop/switch)
  CAMERA_CTRL: 'CAMERA_CTRL', // Camera/gimbal command (tilt/record/photo)
  MSP_TELEM:   'MSP_TELEM',   // MSP-sourced telemetry (translated)
  GOGGLES:     'GOGGLES',     // Goggles telemetry bridge data
  DVR_CTRL:    'DVR_CTRL',    // DVR recording control
  DEVICE_SCAN: 'DEVICE_SCAN', // USB device detection results
};

// ============================================================
// Drone Roles
// ============================================================
const DroneRole = {
  LEADER:   'LEADER',
  WINGMAN:  'WINGMAN',
  RECON:    'RECON',
  SUPPORT:  'SUPPORT',
  TAIL:     'TAIL',
};

// ============================================================
// Drone Status
// ============================================================
const DroneStatus = {
  ACTIVE:      'ACTIVE',
  LOW_BATT:    'LOW_BATT',
  WEAK_SIGNAL: 'WEAK_SIGNAL',
  RTL:         'RTL',
  LANDED:      'LANDED',
  LOST:        'LOST',
  FPV_SOLO:    'FPV_SOLO',
};

// ============================================================
// Formation Types
// ============================================================
const FormationType = {
  V_FORMATION:   'V_FORMATION',
  LINE_ABREAST:  'LINE_ABREAST',
  COLUMN:        'COLUMN',
  DIAMOND:       'DIAMOND',
  ORBIT:         'ORBIT',
  SCATTER:       'SCATTER',
};

// ============================================================
// V-Formation Offset Vectors (meters, relative to leader)
// ============================================================
const V_FORMATION_OFFSETS = {
  'ALPHA-1':   { dx:  0,   dy:  0   },  // Leader (apex)
  'BRAVO-2':   { dx: -12,  dy: -10  },  // Left wing 1
  'CHARLIE-3': { dx:  12,  dy: -10  },  // Right wing 1
  'DELTA-4':   { dx: -24,  dy: -20  },  // Left wing 2
  'ECHO-5':    { dx:  24,  dy: -20  },  // Right wing 2
  'FOXTROT-6': { dx:  0,   dy: -30  },  // Tail
};

// ============================================================
// Alert Severity Levels
// ============================================================
const AlertSeverity = {
  INFO:     'INFO',
  WARNING:  'WARNING',
  CRITICAL: 'CRITICAL',
};

// ============================================================
// Command Types
// ============================================================
const CommandType = {
  ARM:              'ARM',
  DISARM:           'DISARM',
  TAKEOFF:          'TAKEOFF',
  LAND:             'LAND',
  RTL:              'RTL',
  GOTO:             'GOTO',
  SET_MODE:         'SET_MODE',
  SET_FORMATION:    'SET_FORMATION',
  SET_SPEED:        'SET_SPEED',
  SET_ALTITUDE:     'SET_ALTITUDE',
  EMERGENCY_STOP:   'EMERGENCY_STOP',
  CAMERA_TILT:      'CAMERA_TILT',
  CAMERA_RECORD:    'CAMERA_RECORD',
  CAMERA_PHOTO:     'CAMERA_PHOTO',
  GIMBAL_CONTROL:   'GIMBAL_CONTROL',
  MSP_ARM:          'MSP_ARM',
  MSP_DISARM:       'MSP_DISARM',
  MSP_SET_MODE:     'MSP_SET_MODE',
};

// ============================================================
// Telemetry Packet Factory
// ============================================================
function createTelemetryPacket(droneId, data) {
  return {
    type: MessageType.TELEM,
    drone_id: droneId,
    timestamp: new Date().toISOString(),
    seq: data.seq || 0,
    position: {
      lat: data.lat,
      lon: data.lon,
      alt_msl: data.alt_msl,
      alt_agl: data.alt_agl,
    },
    attitude: {
      roll: data.roll,
      pitch: data.pitch,
      yaw: data.yaw,
    },
    velocity: {
      ground_speed: data.ground_speed,
      vertical_speed: data.vertical_speed,
      heading: data.heading,
    },
    battery: {
      voltage: data.voltage,
      current: data.current,
      remaining_pct: data.remaining_pct,
    },
    gps: {
      fix_type: data.fix_type || '3D-RTK',
      satellites: data.satellites,
      hdop: data.hdop,
    },
    link: {
      rssi: data.rssi,
      quality: data.quality,
      latency_ms: data.latency_ms,
    },
    status: data.status || DroneStatus.ACTIVE,
    formation: {
      role: data.role,
      offset_vector: data.offset_vector || { dx: 0, dy: 0 },
      cohesion: data.cohesion,
    },
  };
}

// ============================================================
// Leader Election Score Calculator
// ============================================================
function calculateLeaderScore(drone, weights = {}) {
  const w = {
    battery: weights.battery || 0.30,
    gps:     weights.gps     || 0.25,
    link:    weights.link    || 0.25,
    position: weights.position || 0.20,
  };

  const batteryScore = drone.battery.remaining_pct / 100;
  const gpsScore = Math.min(1.0, drone.gps.satellites / 20) * (1 - Math.min(1.0, drone.gps.hdop / 5));
  const linkScore = (drone.link.rssi / 100) * (drone.link.quality / 100);
  const positionScore = drone.formation.cohesion || 0.5;

  return (
    w.battery  * batteryScore +
    w.gps      * gpsScore +
    w.link     * linkScore +
    w.position * positionScore
  );
}

// ============================================================
// Cohesion Calculator
// ============================================================
function calculateCohesion(actualOffset, expectedOffset, spacingM = 15) {
  const dx = actualOffset.dx - expectedOffset.dx;
  const dy = actualOffset.dy - expectedOffset.dy;
  const error = Math.sqrt(dx * dx + dy * dy);
  return Math.max(0, 1 - error / spacingM);
}

// ============================================================
// Flight Modes (Betaflight + ArduPilot unified)
// ============================================================
const FlightMode = {
  ANGLE:      'ANGLE',
  HORIZON:    'HORIZON',
  ACRO:       'ACRO',
  AIR:        'AIR',
  TURTLE:     'TURTLE',
  GPS_RESCUE: 'GPS_RESCUE',
  STABILIZE:  'STABILIZE',
  ALT_HOLD:   'ALT_HOLD',
  LOITER:     'LOITER',
  AUTO:       'AUTO',
  GUIDED:     'GUIDED',
  RTL:        'RTL',
};

// ============================================================
// Connection Protocol Type
// ============================================================
const ProtocolType = {
  MAVLINK: 'MAVLINK',
  MSP:     'MSP',
  UNKNOWN: 'UNKNOWN',
};

// ============================================================
// FPV Telemetry Packet Factory
// ============================================================
function createFPVTelemetryPacket(droneId, data) {
  const base = createTelemetryPacket(droneId, data);
  base.fpv = {
    flight_mode:        data.flight_mode || FlightMode.ANGLE,
    camera_tilt:        data.camera_tilt || 0,
    mah_consumed:       data.mah_consumed || 0,
    cell_voltage:       data.cell_voltage || 0,
    flight_timer_s:     data.flight_timer_s || 0,
    arm_timer_s:        data.arm_timer_s || 0,
    home_distance_m:    data.home_distance_m || 0,
    home_direction_deg: data.home_direction_deg || 0,
    video_link: data.video_link || null,
    protocol:           data.protocol || ProtocolType.MAVLINK,
  };
  return base;
}

// ============================================================
// Video Control Packet Factory
// ============================================================
function createVideoControlPacket(droneId, action, params) {
  return {
    type: MessageType.VIDEO_CTRL,
    drone_id: droneId,
    timestamp: new Date().toISOString(),
    action: action,
    params: params || {},
  };
}

// ============================================================
// Exports (CommonJS)
// ============================================================
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    MessageType,
    DroneRole,
    DroneStatus,
    FormationType,
    V_FORMATION_OFFSETS,
    AlertSeverity,
    CommandType,
    FlightMode,
    ProtocolType,
    createTelemetryPacket,
    createFPVTelemetryPacket,
    createVideoControlPacket,
    calculateLeaderScore,
    calculateCohesion,
  };
}
