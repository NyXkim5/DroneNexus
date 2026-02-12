/**
 * NEXUS Protocol Unit Tests
 * Run: node tests/protocol.test.js
 */

const assert = require('assert');
const {
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
} = require('../src/shared/protocol');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
    passed++;
  } catch (e) {
    console.log(`  FAIL  ${name}`);
    console.log(`        ${e.message}`);
    failed++;
  }
}

console.log('NEXUS Protocol Tests');
console.log('====================\n');

// --- Message Types ---
test('MessageType has all required types', () => {
  const expected = ['TELEM', 'HEARTBEAT', 'CMD', 'FORMATION', 'WAYPOINT', 'PEER', 'ALERT', 'ACK'];
  for (const t of expected) {
    assert.strictEqual(MessageType[t], t);
  }
});

// --- Drone Roles ---
test('DroneRole has all roles', () => {
  const expected = ['LEADER', 'WINGMAN', 'RECON', 'SUPPORT', 'TAIL'];
  for (const r of expected) {
    assert.strictEqual(DroneRole[r], r);
  }
});

// --- Drone Status ---
test('DroneStatus has all statuses', () => {
  const expected = ['ACTIVE', 'LOW_BATT', 'WEAK_SIGNAL', 'RTL', 'LANDED', 'LOST'];
  for (const s of expected) {
    assert.strictEqual(DroneStatus[s], s);
  }
});

// --- Formation Types ---
test('FormationType has all formations', () => {
  const expected = ['V_FORMATION', 'LINE_ABREAST', 'COLUMN', 'DIAMOND', 'ORBIT', 'SCATTER'];
  for (const f of expected) {
    assert.strictEqual(FormationType[f], f);
  }
});

// --- V-Formation Offsets ---
test('V-Formation has offsets for all 6 drones', () => {
  const droneIds = ['ALPHA-1', 'BRAVO-2', 'CHARLIE-3', 'DELTA-4', 'ECHO-5', 'FOXTROT-6'];
  for (const id of droneIds) {
    assert.ok(V_FORMATION_OFFSETS[id], `Missing offset for ${id}`);
    assert.ok('dx' in V_FORMATION_OFFSETS[id], `Missing dx for ${id}`);
    assert.ok('dy' in V_FORMATION_OFFSETS[id], `Missing dy for ${id}`);
  }
});

test('Leader offset is at origin', () => {
  assert.strictEqual(V_FORMATION_OFFSETS['ALPHA-1'].dx, 0);
  assert.strictEqual(V_FORMATION_OFFSETS['ALPHA-1'].dy, 0);
});

test('V-Formation is symmetric on x-axis', () => {
  assert.strictEqual(V_FORMATION_OFFSETS['BRAVO-2'].dx, -V_FORMATION_OFFSETS['CHARLIE-3'].dx);
  assert.strictEqual(V_FORMATION_OFFSETS['BRAVO-2'].dy, V_FORMATION_OFFSETS['CHARLIE-3'].dy);
  assert.strictEqual(V_FORMATION_OFFSETS['DELTA-4'].dx, -V_FORMATION_OFFSETS['ECHO-5'].dx);
  assert.strictEqual(V_FORMATION_OFFSETS['DELTA-4'].dy, V_FORMATION_OFFSETS['ECHO-5'].dy);
});

// --- Telemetry Packet ---
test('createTelemetryPacket returns valid packet', () => {
  const packet = createTelemetryPacket('ALPHA-1', {
    seq: 1,
    lat: 33.6405,
    lon: -117.8443,
    alt_msl: 135,
    alt_agl: 120,
    roll: 2.5,
    pitch: -1.2,
    yaw: 180,
    ground_speed: 12,
    vertical_speed: 0.5,
    heading: 180,
    voltage: 22.5,
    current: 10.2,
    remaining_pct: 85,
    satellites: 15,
    hdop: 0.8,
    rssi: 90,
    quality: 95,
    latency_ms: 25,
    status: 'ACTIVE',
    role: 'LEADER',
    cohesion: 0.95,
  });

  assert.strictEqual(packet.type, 'TELEM');
  assert.strictEqual(packet.drone_id, 'ALPHA-1');
  assert.ok(packet.timestamp);
  assert.strictEqual(packet.position.lat, 33.6405);
  assert.strictEqual(packet.attitude.roll, 2.5);
  assert.strictEqual(packet.velocity.ground_speed, 12);
  assert.strictEqual(packet.battery.voltage, 22.5);
  assert.strictEqual(packet.gps.satellites, 15);
  assert.strictEqual(packet.link.rssi, 90);
  assert.strictEqual(packet.status, 'ACTIVE');
  assert.strictEqual(packet.formation.role, 'LEADER');
});

// --- Leader Election Score ---
test('calculateLeaderScore returns 0-1 range', () => {
  const drone = {
    battery: { remaining_pct: 80 },
    gps: { satellites: 15, hdop: 0.8 },
    link: { rssi: 90, quality: 95 },
    formation: { cohesion: 0.9 },
  };
  const score = calculateLeaderScore(drone);
  assert.ok(score >= 0 && score <= 1, `Score ${score} not in [0,1]`);
});

test('Higher battery gives higher score', () => {
  const makeDrone = (batt) => ({
    battery: { remaining_pct: batt },
    gps: { satellites: 15, hdop: 0.8 },
    link: { rssi: 90, quality: 95 },
    formation: { cohesion: 0.9 },
  });
  const highBatt = calculateLeaderScore(makeDrone(100));
  const lowBatt = calculateLeaderScore(makeDrone(30));
  assert.ok(highBatt > lowBatt, `High batt ${highBatt} should > low batt ${lowBatt}`);
});

// --- Cohesion Calculator ---
test('Perfect cohesion when at expected position', () => {
  const cohesion = calculateCohesion({ dx: 10, dy: -15 }, { dx: 10, dy: -15 });
  assert.strictEqual(cohesion, 1);
});

test('Zero cohesion when far from expected position', () => {
  const cohesion = calculateCohesion({ dx: 100, dy: 100 }, { dx: 0, dy: 0 }, 15);
  assert.strictEqual(cohesion, 0);
});

test('Partial cohesion for moderate error', () => {
  const cohesion = calculateCohesion({ dx: 12, dy: -15 }, { dx: 10, dy: -15 }, 15);
  assert.ok(cohesion > 0 && cohesion < 1, `Cohesion ${cohesion} should be between 0 and 1`);
});

// --- FPV: Message Types ---
test('MessageType has FPV types', () => {
  const fpvTypes = ['VIDEO_CTRL', 'CAMERA_CTRL', 'MSP_TELEM', 'GOGGLES', 'DVR_CTRL', 'DEVICE_SCAN'];
  for (const t of fpvTypes) {
    assert.strictEqual(MessageType[t], t, `Missing MessageType.${t}`);
  }
});

// --- FPV: Command Types ---
test('CommandType has FPV commands', () => {
  const fpvCmds = ['CAMERA_TILT', 'CAMERA_RECORD', 'CAMERA_PHOTO', 'GIMBAL_CONTROL', 'MSP_ARM', 'MSP_DISARM', 'MSP_SET_MODE'];
  for (const c of fpvCmds) {
    assert.strictEqual(CommandType[c], c, `Missing CommandType.${c}`);
  }
});

// --- FPV: DroneStatus ---
test('DroneStatus has FPV_SOLO', () => {
  assert.strictEqual(DroneStatus.FPV_SOLO, 'FPV_SOLO');
});

// --- FPV: FlightMode ---
test('FlightMode has all modes', () => {
  const modes = ['ANGLE', 'HORIZON', 'ACRO', 'AIR', 'TURTLE', 'GPS_RESCUE', 'STABILIZE', 'ALT_HOLD', 'LOITER', 'AUTO', 'GUIDED', 'RTL'];
  for (const m of modes) {
    assert.strictEqual(FlightMode[m], m, `Missing FlightMode.${m}`);
  }
});

// --- FPV: ProtocolType ---
test('ProtocolType has all protocols', () => {
  const protocols = ['MAVLINK', 'MSP', 'UNKNOWN'];
  for (const p of protocols) {
    assert.strictEqual(ProtocolType[p], p, `Missing ProtocolType.${p}`);
  }
});

// --- FPV: Telemetry Packet ---
test('createFPVTelemetryPacket includes fpv extension', () => {
  const packet = createFPVTelemetryPacket('ALPHA-1', {
    seq: 1, lat: 33.6405, lon: -117.8443, alt_msl: 135, alt_agl: 120,
    roll: 0, pitch: 0, yaw: 0, ground_speed: 10, vertical_speed: 0, heading: 0,
    voltage: 22.5, current: 10, remaining_pct: 85,
    satellites: 15, hdop: 0.8, rssi: 90, quality: 95, latency_ms: 25,
    status: 'ACTIVE', role: 'LEADER', cohesion: 0.95,
    flight_mode: 'ACRO', camera_tilt: -15, mah_consumed: 450,
    cell_voltage: 3.75, flight_timer_s: 120, arm_timer_s: 90,
    home_distance_m: 55.3, home_direction_deg: 180,
    protocol: 'MSP',
  });

  assert.strictEqual(packet.type, 'TELEM');
  assert.ok(packet.fpv, 'Missing fpv extension');
  assert.strictEqual(packet.fpv.flight_mode, 'ACRO');
  assert.strictEqual(packet.fpv.camera_tilt, -15);
  assert.strictEqual(packet.fpv.mah_consumed, 450);
  assert.strictEqual(packet.fpv.cell_voltage, 3.75);
  assert.strictEqual(packet.fpv.flight_timer_s, 120);
  assert.strictEqual(packet.fpv.arm_timer_s, 90);
  assert.strictEqual(packet.fpv.home_distance_m, 55.3);
  assert.strictEqual(packet.fpv.home_direction_deg, 180);
  assert.strictEqual(packet.fpv.protocol, 'MSP');
});

// --- FPV: Video Control Packet ---
test('createVideoControlPacket is valid', () => {
  const packet = createVideoControlPacket('ALPHA-1', 'START', { url: 'rtsp://localhost:8554/fpv' });
  assert.strictEqual(packet.type, 'VIDEO_CTRL');
  assert.strictEqual(packet.drone_id, 'ALPHA-1');
  assert.strictEqual(packet.action, 'START');
  assert.strictEqual(packet.params.url, 'rtsp://localhost:8554/fpv');
  assert.ok(packet.timestamp);
});

// --- FPV: Base packet still works ---
test('createFPVTelemetryPacket preserves base fields', () => {
  const packet = createFPVTelemetryPacket('BRAVO-2', {
    seq: 5, lat: 34.0, lon: -118.0, alt_msl: 100, alt_agl: 85,
    roll: 3, pitch: -2, yaw: 90, ground_speed: 8, vertical_speed: 1, heading: 90,
    voltage: 23, current: 12, remaining_pct: 70,
    satellites: 14, hdop: 1.0, rssi: 85, quality: 90, latency_ms: 30,
    status: 'ACTIVE', role: 'WINGMAN', cohesion: 0.88,
  });

  assert.strictEqual(packet.position.lat, 34.0);
  assert.strictEqual(packet.attitude.roll, 3);
  assert.strictEqual(packet.battery.remaining_pct, 70);
  assert.ok(packet.fpv, 'fpv block should exist even with defaults');
  assert.strictEqual(packet.fpv.flight_mode, 'ANGLE');  // default
  assert.strictEqual(packet.fpv.protocol, 'MAVLINK');    // default
});

// --- Summary ---
console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
