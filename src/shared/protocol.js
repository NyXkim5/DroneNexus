/**
 * OVERWATCH Wire Protocol — Shared Constants & Definitions
 *
 * Reference implementation of the OVERWATCH communication protocol.
 * Used by both ground station server and companion computer clients.
 */

// ============================================================
// Message Types
// ============================================================
const MessageType = {
  ASSET_STATE:   'ASSET_STATE',     // Asset state data (downlink, 10Hz)
  HEARTBEAT:     'HEARTBEAT',       // Liveness check (bidirectional, 1Hz)
  DIRECTIVE:     'DIRECTIVE',       // Operator directive (uplink, on-demand)
  OVERLAY_UPDATE:'OVERLAY_UPDATE',  // Overlay update (downlink, on-change)
  OBJECTIVE:     'OBJECTIVE',       // Mission objective (uplink, on-demand)
  PEER_STATE:    'PEER_STATE',      // Peer state exchange (lateral, 5Hz)
  ACTIVITY:      'ACTIVITY',        // Activity notification (downlink, on-event)
  ACK:           'ACK',             // Command acknowledgment (bidirectional)
  ISR_CTRL:      'ISR_CTRL',        // ISR stream control (start/stop/switch)
  SENSOR_CTRL:   'SENSOR_CTRL',     // Sensor/gimbal command (tilt/record/capture)
  MSP_STATE:     'MSP_STATE',       // MSP-sourced state (translated)
  HMD_STATE:     'HMD_STATE',       // HMD telemetry bridge data
  RECORD_CTRL:   'RECORD_CTRL',     // Recording control
  DEVICE_SCAN:   'DEVICE_SCAN',     // USB device detection results
};

// ============================================================
// Asset Classifications
// ============================================================
const AssetClassification = {
  PRIMARY:    'PRIMARY',
  ESCORT:     'ESCORT',
  ISR:        'ISR',
  LOGISTICS:  'LOGISTICS',
  OVERWATCH:  'OVERWATCH',
};

// ============================================================
// Operational Status
// ============================================================
const OperationalStatus = {
  NOMINAL:        'NOMINAL',
  DEGRADED:       'DEGRADED',
  COMMS_DEGRADED: 'COMMS_DEGRADED',
  RTB:            'RTB',
  GROUNDED:       'GROUNDED',
  OFFLINE:        'OFFLINE',
  ISR_SOLO:       'ISR_SOLO',
};

// ============================================================
// Overlay Types
// ============================================================
const OverlayType = {
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
// Directive Types
// ============================================================
const DirectiveType = {
  LAUNCH_PREP:      'LAUNCH_PREP',
  STAND_DOWN:       'STAND_DOWN',
  LAUNCH:           'LAUNCH',
  RECOVER:          'RECOVER',
  RTB:              'RTB',
  GOTO:             'GOTO',
  SET_MODE:         'SET_MODE',
  SET_OVERLAY:      'SET_OVERLAY',
  SET_SPEED:        'SET_SPEED',
  SET_ALTITUDE:     'SET_ALTITUDE',
  ABORT:            'ABORT',
  SENSOR_TILT:      'SENSOR_TILT',
  SENSOR_RECORD:    'SENSOR_RECORD',
  SENSOR_CAPTURE:   'SENSOR_CAPTURE',
  GIMBAL_CONTROL:   'GIMBAL_CONTROL',
  MSP_LAUNCH_PREP:  'MSP_LAUNCH_PREP',
  MSP_STAND_DOWN:   'MSP_STAND_DOWN',
  MSP_SET_MODE:     'MSP_SET_MODE',
};

// ============================================================
// Asset State Packet Factory
// ============================================================
function createAssetStatePacket(droneId, data) {
  return {
    type: MessageType.ASSET_STATE,
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
    status: data.status || OperationalStatus.NOMINAL,
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
// ISR State Packet Factory
// ============================================================
function createISRStatePacket(droneId, data) {
  const base = createAssetStatePacket(droneId, data);
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
// ISR Control Packet Factory
// ============================================================
function createISRControlPacket(droneId, action, params) {
  return {
    type: MessageType.ISR_CTRL,
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
    AssetClassification,
    OperationalStatus,
    OverlayType,
    V_FORMATION_OFFSETS,
    AlertSeverity,
    DirectiveType,
    FlightMode,
    ProtocolType,
    createAssetStatePacket,
    createISRStatePacket,
    createISRControlPacket,
    calculateLeaderScore,
    calculateCohesion,
  };
}
