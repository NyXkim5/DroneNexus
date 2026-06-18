/**
 * OVERWATCH — Standalone Asset Simulator
 *
 * Node.js-based simulator that generates realistic telemetry data
 * and publishes it over WebSocket. Used for testing the ground
 * station and HUD without hardware.
 *
 * Usage:
 *   node drone-simulator.js [--drones 6] [--port 8765]
 */

const WebSocket = require('ws');
const { V_FORMATION_OFFSETS, OperationalStatus, FlightMode, ProtocolType, createISRStatePacket } = require('../shared/protocol');

// ============================================================
// Configuration
// ============================================================
const CONFIG = {
  port: parseInt(process.argv.find((_, i, a) => a[i-1] === '--port') || '8765'),
  droneCount: parseInt(process.argv.find((_, i, a) => a[i-1] === '--drones') || '6'),
  updateRateHz: 10,
  centerLat: 33.6405,
  centerLon: -117.8443,
};

const DRONES = [
  { id: 'ALPHA-1',   role: 'PRIMARY',    color: '#00ff88' },
  { id: 'BRAVO-2',   role: 'ESCORT',     color: '#3388ff' },
  { id: 'CHARLIE-3', role: 'ISR',        color: '#ffaa00' },
  { id: 'DELTA-4',   role: 'ESCORT',     color: '#ff3355' },
  { id: 'ECHO-5',    role: 'LOGISTICS',  color: '#00ccff' },
  { id: 'FOXTROT-6', role: 'OVERWATCH',  color: '#aa55ff' },
];

// ============================================================
// Drone State
// ============================================================
class SimulatedDrone {
  constructor(config, index) {
    this.id = config.id;
    this.role = config.role;
    this.index = index;
    this.seq = 0;

    // Position
    this.lat = CONFIG.centerLat;
    this.lon = CONFIG.centerLon;
    this.alt = 120 + Math.random() * 10;
    this.heading = Math.random() * 360;

    // Attitude
    this.roll = 0;
    this.pitch = 0;
    this.yaw = this.heading;

    // Performance
    this.speed = 10 + Math.random() * 5;
    this.vspeed = 0;
    this.battery = 75 + Math.random() * 25;
    this.voltage = 22.2 + Math.random() * 2;
    this.current = 8 + Math.random() * 5;

    // Sensors
    this.satellites = 14 + Math.floor(Math.random() * 4);
    this.hdop = 0.7 + Math.random() * 0.3;
    this.rssi = 85 + Math.random() * 10;
    this.linkQuality = 92 + Math.random() * 8;
    this.latency = 20 + Math.random() * 10;
    this.temp = 42 + Math.random() * 8;

    // Orbit parameters
    this.orbitAngle = (index / CONFIG.droneCount) * Math.PI * 2;
    this.orbitRadius = 0.001; // ~111m in degrees
    this.orbitSpeed = 0.005;
    this.phaseOffset = index * 0.5;

    // FPV extensions
    this.flightMode = FlightMode.ANGLE;
    this.cameraTilt = 0;
    this.mahConsumed = 0;
    this.cellVoltage = this.voltage / 6;
    this.flightTimer = 0;
    this.armTimer = 0;
    this.homeDistance = 0;
    this.homeDirection = 0;
    this.videoLinkQuality = 90 + Math.random() * 10;
    this.videoChannel = 1 + index;
    this.videoFrequency = 5740 + index * 20;
    this.recording = false;
    this.protocol = ProtocolType.MAVLINK;

    // C2 command state
    this.armed = false;
    this.flying = false;
    this.commandDriven = false;
    this.targetAlt = 30;
    this.targetLat = CONFIG.centerLat;
    this.targetLon = CONFIG.centerLon;
    this.targetSpeed = 10;
    this.landing = false;
    this.rtlPhase = null; // null | 'transit' | 'land'
    this.missionWaypoints = [];
    this.missionIndex = 0;
    this.missionActive = false;
  }

  update(dt, leaderState) {
    this.seq++;
    const t = Date.now() / 1000;

    if (!this.commandDriven) {
      this._updateOrbitMode(dt, t, leaderState);
    } else {
      this._updateCommandMode(dt, t, leaderState);
    }

    // Battery drain (slower when grounded)
    const drainRate = this.flying ? 0.01 : 0.002;
    this.battery = Math.max(0, this.battery - drainRate * dt);
    this.voltage = 18 + (this.battery / 100) * 7;
    this.current = this.flying ? 8 + Math.random() * 5 : 1 + Math.random();

    // Sensor jitter
    this.satellites = Math.max(8, Math.min(20, this.satellites + (Math.random() - 0.5) * 2));
    this.hdop = Math.max(0.5, Math.min(3.0, this.hdop + (Math.random() - 0.5) * 0.1));
    this.rssi = Math.max(40, Math.min(99, this.rssi + (Math.random() - 0.5) * 3));
    this.linkQuality = Math.max(70, Math.min(100, this.linkQuality + (Math.random() - 0.5) * 2));
    this.latency = Math.max(5, Math.min(80, this.latency + (Math.random() - 0.5) * 5));
    this.temp = Math.max(35, Math.min(60, this.temp + (Math.random() - 0.5) * 0.5));

    // FPV data updates
    this.mahConsumed += Math.abs(this.current) * (dt / 3600) * 1000;
    this.cellVoltage = this.voltage / 6;
    this.flightTimer += dt;
    if (this.armed) this.armTimer += dt;
    const dLat = (this.lat - CONFIG.centerLat) * 111320;
    const dLon = (this.lon - CONFIG.centerLon) * 111320 * Math.cos(this.lat * Math.PI / 180);
    this.homeDistance = Math.sqrt(dLat * dLat + dLon * dLon);
    this.homeDirection = ((Math.atan2(-dLon, -dLat) * 180 / Math.PI) + 360) % 360;
    this.videoLinkQuality = Math.max(60, Math.min(100, this.videoLinkQuality + (Math.random() - 0.5) * 2));

    // Status derivation
    let status = OperationalStatus.NOMINAL;
    if (this.battery < 25) status = OperationalStatus.DEGRADED;
    else if (this.rssi < 60) status = OperationalStatus.COMMS_DEGRADED;

    return createISRStatePacket(this.id, {
      seq: this.seq,
      lat: this.lat,
      lon: this.lon,
      alt_msl: this.alt + 15,
      alt_agl: this.alt,
      roll: this.roll,
      pitch: this.pitch,
      yaw: this.yaw,
      ground_speed: this.speed,
      vertical_speed: this.vspeed,
      heading: this.heading,
      voltage: this.voltage,
      current: this.current,
      remaining_pct: this.battery,
      satellites: Math.round(this.satellites),
      hdop: parseFloat(this.hdop.toFixed(1)),
      rssi: Math.round(this.rssi),
      quality: Math.round(this.linkQuality),
      latency_ms: Math.round(this.latency),
      status: status,
      role: this.role,
      cohesion: 0.85 + Math.random() * 0.15,
      flight_mode: this.flightMode,
      camera_tilt: this.cameraTilt,
      mah_consumed: Math.round(this.mahConsumed),
      cell_voltage: parseFloat(this.cellVoltage.toFixed(2)),
      flight_timer_s: parseFloat(this.flightTimer.toFixed(1)),
      arm_timer_s: parseFloat(this.armTimer.toFixed(1)),
      home_distance_m: parseFloat(this.homeDistance.toFixed(1)),
      home_direction_deg: parseFloat(this.homeDirection.toFixed(1)),
      video_link: {
        quality: Math.round(this.videoLinkQuality),
        channel: this.videoChannel,
        frequency_mhz: this.videoFrequency,
        recording: this.recording,
        system: 'Simulation',
      },
      protocol: this.protocol,
    });
  }

  // Original orbit behavior (backward-compatible default)
  _updateOrbitMode(dt, t, leaderState) {
    if (this.role === 'PRIMARY') {
      this.orbitAngle += this.orbitSpeed * dt;
      this.lat = CONFIG.centerLat + Math.cos(this.orbitAngle) * this.orbitRadius;
      this.lon = CONFIG.centerLon + Math.sin(this.orbitAngle) * this.orbitRadius;
      this.heading = ((this.orbitAngle * 180 / Math.PI) + 90) % 360;
    } else if (leaderState) {
      const offset = V_FORMATION_OFFSETS[this.id] || { dx: 0, dy: 0 };
      const headingRad = leaderState.heading * Math.PI / 180;
      const metersToDeg = 1 / 111320;

      const rotatedDx = offset.dx * Math.cos(headingRad) - offset.dy * Math.sin(headingRad);
      const rotatedDy = offset.dx * Math.sin(headingRad) + offset.dy * Math.cos(headingRad);

      const targetLat = leaderState.lat + rotatedDy * metersToDeg;
      const targetLon = leaderState.lon + rotatedDx * metersToDeg / Math.cos(leaderState.lat * Math.PI / 180);

      this.lat += (targetLat - this.lat) * 0.1;
      this.lon += (targetLon - this.lon) * 0.1;
      this.heading = leaderState.heading + (Math.random() - 0.5) * 2;
    }

    this.alt = 120 + Math.sin(t * 0.1 + this.phaseOffset) * 5;
    this.vspeed = Math.cos(t * 0.1 + this.phaseOffset) * 0.5;
    this.roll = Math.sin(t * 0.3 + this.phaseOffset) * 8;
    this.pitch = Math.sin(t * 0.2 + this.phaseOffset * 1.5) * 3;
    this.yaw = this.heading;
    this.speed = 10 + Math.sin(t * 0.05 + this.index) * 3;
  }

  // Command-driven movement
  _updateCommandMode(dt, t, leaderState) {
    if (!this.armed) {
      this.alt = 0;
      this.speed = 0;
      this.vspeed = 0;
      this.roll = 0;
      this.pitch = 0;
      return;
    }

    if (this.armed && !this.flying) {
      this.alt = 0;
      this.speed = 0;
      this.vspeed = 0;
      this.roll = 0;
      this.pitch = 0;
      return;
    }

    // Handle landing descent
    if (this.landing) {
      this._updateLanding(dt);
      return;
    }

    // Handle RTL transit phase
    if (this.rtlPhase === 'transit') {
      const dist = this._distToTarget(CONFIG.centerLat, CONFIG.centerLon);
      if (dist < 5) {
        this.rtlPhase = 'land';
        this.landing = true;
      }
    }

    // Handle mission waypoint following (leader only)
    if (this.missionActive && this.role === 'PRIMARY') {
      this._updateMission();
    }

    // Smooth altitude transition (climb/descend at 2 m/s)
    const altDiff = this.targetAlt - this.alt;
    if (Math.abs(altDiff) > 0.1) {
      const climbRate = 2.0;
      const altStep = Math.sign(altDiff) * Math.min(Math.abs(altDiff), climbRate * dt);
      this.alt += altStep;
      this.vspeed = altStep / dt;
    } else {
      this.alt = this.targetAlt;
      this.vspeed = 0;
    }

    // Horizontal movement toward target
    if (this.role === 'PRIMARY') {
      this._moveTowardTarget(dt);
    } else if (leaderState) {
      this._followLeaderFormation(dt, leaderState);
    }

    // Attitude from movement
    this.roll = Math.sin(t * 0.3 + this.phaseOffset) * 3;
    this.pitch = this.speed > 0.5 ? -2 : 0;
    this.yaw = this.heading;
  }

  _moveTowardTarget(dt) {
    const dist = this._distToTarget(this.targetLat, this.targetLon);
    if (dist < 0.5) {
      this.speed = 0;
      return;
    }

    const metersToDeg = 1 / 111320;
    const dLatM = (this.targetLat - this.lat) / metersToDeg;
    const cosLat = Math.cos(this.lat * Math.PI / 180);
    const dLonM = (this.targetLon - this.lon) / (metersToDeg / cosLat);

    this.heading = ((Math.atan2(dLonM, dLatM) * 180 / Math.PI) + 360) % 360;

    const moveSpeed = Math.min(this.targetSpeed, dist);
    this.speed = moveSpeed;
    const moveM = moveSpeed * dt;

    if (moveM >= dist) {
      this.lat = this.targetLat;
      this.lon = this.targetLon;
    } else {
      const headingRad = this.heading * Math.PI / 180;
      this.lat += Math.cos(headingRad) * moveM * metersToDeg;
      this.lon += Math.sin(headingRad) * moveM * metersToDeg / cosLat;
    }
  }

  _followLeaderFormation(dt, leaderState) {
    const offset = V_FORMATION_OFFSETS[this.id] || { dx: 0, dy: 0 };
    const headingRad = leaderState.heading * Math.PI / 180;
    const metersToDeg = 1 / 111320;

    const rotatedDx = offset.dx * Math.cos(headingRad) - offset.dy * Math.sin(headingRad);
    const rotatedDy = offset.dx * Math.sin(headingRad) + offset.dy * Math.cos(headingRad);

    const formLat = leaderState.lat + rotatedDy * metersToDeg;
    const formLon = leaderState.lon + rotatedDx * metersToDeg / Math.cos(leaderState.lat * Math.PI / 180);

    const smoothing = Math.min(1.0, 3.0 * dt);
    this.lat += (formLat - this.lat) * smoothing;
    this.lon += (formLon - this.lon) * smoothing;
    this.heading = leaderState.heading + (Math.random() - 0.5) * 2;

    const dLatM = (formLat - this.lat) * 111320;
    const dLonM = (formLon - this.lon) * 111320 * Math.cos(this.lat * Math.PI / 180);
    this.speed = Math.sqrt(dLatM * dLatM + dLonM * dLonM) / Math.max(dt, 0.001);
  }

  _updateLanding(dt) {
    const descentRate = 1.0; // m/s
    this.alt -= descentRate * dt;
    this.vspeed = -descentRate;
    this.speed = 0;
    this.roll = 0;
    this.pitch = 0;

    if (this.alt <= 0) {
      this.alt = 0;
      this.vspeed = 0;
      this.flying = false;
      this.armed = false;
      this.landing = false;
      this.rtlPhase = null;
      this.missionActive = false;
    }
  }

  _updateMission() {
    if (this.missionIndex >= this.missionWaypoints.length) {
      this.missionActive = false;
      return;
    }

    const wp = this.missionWaypoints[this.missionIndex];
    const dist = this._distToTarget(wp.lat, wp.lng);

    if (dist < 5) {
      this.missionIndex++;
      if (this.missionIndex < this.missionWaypoints.length) {
        const next = this.missionWaypoints[this.missionIndex];
        this.targetLat = next.lat;
        this.targetLon = next.lng;
        if (next.alt != null) this.targetAlt = next.alt;
      }
    }
  }

  _distToTarget(tLat, tLon) {
    const dLatM = (tLat - this.lat) * 111320;
    const dLonM = (tLon - this.lon) * 111320 * Math.cos(this.lat * Math.PI / 180);
    return Math.sqrt(dLatM * dLatM + dLonM * dLonM);
  }
}

// ============================================================
// Server
// ============================================================
// Active formation name (stored for future use)
let activeFormation = 'V_FORMATION';

function sendAck(ws, command, droneId, success, message) {
  const ack = {
    type: 'ACK',
    command,
    drone_id: droneId || 'ALL',
    success,
    message: message || '',
  };
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(ack));
  }
}

function resolveDrones(drones, droneId) {
  if (!droneId || droneId === 'ALL') return drones;
  return drones.filter(d => d.id === droneId);
}

function handleCommand(cmd, drones, ws) {
  const params = cmd.params || {};
  const droneId = params.droneId || cmd.drone_id || 'ALL';
  const targets = resolveDrones(drones, droneId);
  const command = cmd.command;

  switch (command) {
    case 'ARM': {
      for (const d of targets) {
        d.commandDriven = true;
        d.armed = true;
        d.alt = 0;
        d.speed = 0;
      }
      sendAck(ws, command, droneId, true, `${targets.length} drone(s) armed`);
      break;
    }

    case 'DISARM': {
      for (const d of targets) {
        d.armed = false;
        d.flying = false;
        d.landing = false;
        d.rtlPhase = null;
        d.missionActive = false;
        d.alt = 0;
        d.speed = 0;
      }
      sendAck(ws, command, droneId, true, `${targets.length} drone(s) disarmed`);
      break;
    }

    case 'TAKEOFF': {
      const alt = params.altitude || 30;
      let count = 0;
      for (const d of targets) {
        if (!d.armed) continue;
        d.flying = true;
        d.landing = false;
        d.rtlPhase = null;
        d.targetAlt = alt;
        if (d.alt === 0) d.alt = 0.1;
        count++;
      }
      sendAck(ws, command, droneId, true, `${count} drone(s) taking off to ${alt}m`);
      break;
    }

    case 'LAND': {
      for (const d of targets) {
        if (d.flying) {
          d.landing = true;
          d.rtlPhase = null;
          d.missionActive = false;
        }
      }
      sendAck(ws, command, droneId, true, `${targets.length} drone(s) landing`);
      break;
    }

    case 'RTL': {
      for (const d of targets) {
        if (d.flying) {
          d.targetLat = CONFIG.centerLat;
          d.targetLon = CONFIG.centerLon;
          d.rtlPhase = 'transit';
          d.missionActive = false;
        }
      }
      sendAck(ws, command, droneId, true, `${targets.length} drone(s) returning to launch`);
      break;
    }

    case 'EMERGENCY_STOP': {
      for (const d of drones) {
        d.armed = false;
        d.flying = false;
        d.landing = false;
        d.rtlPhase = null;
        d.missionActive = false;
        d.speed = 0;
        d.vspeed = 0;
        d.alt = d.alt > 0 ? d.alt : 0; // freeze at current alt, let next tick ground it
      }
      // Force all to ground immediately
      for (const d of drones) {
        d.alt = 0;
      }
      sendAck(ws, command, 'ALL', true, `Emergency stop: all ${drones.length} drones halted`);
      break;
    }

    case 'GOTO': {
      const lat = params.lat;
      const lng = params.lng;
      if (lat == null || lng == null) {
        sendAck(ws, command, droneId, false, 'Missing lat/lng');
        break;
      }
      // Leader gets the direct target, followers maintain formation
      for (const d of targets) {
        if (d.role === 'PRIMARY') {
          d.targetLat = lat;
          d.targetLon = lng;
          d.missionActive = false;
          d.rtlPhase = null;
        }
      }
      sendAck(ws, command, droneId, true, `GOTO ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
      break;
    }

    case 'SET_FORMATION': {
      const formation = params.formation || 'V_FORMATION';
      activeFormation = formation;
      console.log(`Formation set to: ${formation}`);
      sendAck(ws, command, droneId, true, `Formation set to ${formation}`);
      break;
    }

    case 'SET_SPEED': {
      const spd = params.speed;
      if (spd == null) {
        sendAck(ws, command, droneId, false, 'Missing speed');
        break;
      }
      for (const d of targets) {
        d.targetSpeed = spd;
      }
      sendAck(ws, command, droneId, true, `Speed set to ${spd} m/s`);
      break;
    }

    case 'SET_ALTITUDE': {
      const alt = params.altitude;
      if (alt == null) {
        sendAck(ws, command, droneId, false, 'Missing altitude');
        break;
      }
      for (const d of targets) {
        d.targetAlt = alt;
      }
      sendAck(ws, command, droneId, true, `Altitude set to ${alt}m`);
      break;
    }

    case 'EXECUTE_MISSION': {
      const waypoints = params.waypoints;
      if (!waypoints || waypoints.length === 0) {
        sendAck(ws, command, droneId, false, 'No waypoints provided');
        break;
      }
      // Leader follows waypoints, followers maintain formation
      const leader = drones.find(d => d.role === 'PRIMARY');
      if (leader && leader.flying) {
        leader.missionWaypoints = waypoints;
        leader.missionIndex = 0;
        leader.missionActive = true;
        leader.rtlPhase = null;
        leader.targetLat = waypoints[0].lat;
        leader.targetLon = waypoints[0].lng;
        if (waypoints[0].alt != null) leader.targetAlt = waypoints[0].alt;
      }
      sendAck(ws, command, droneId, true, `Mission loaded: ${waypoints.length} waypoints`);
      break;
    }

    default: {
      sendAck(ws, command, droneId, false, `Unknown command: ${command}`);
      break;
    }
  }
}

function startServer() {
  const wss = new WebSocket.Server({ port: CONFIG.port });
  const drones = DRONES.slice(0, CONFIG.droneCount).map((d, i) => new SimulatedDrone(d, i));

  console.log(`OVERWATCH Simulator — ${drones.length} drones on ws://localhost:${CONFIG.port}`);

  const clients = new Set();

  wss.on('connection', (ws) => {
    clients.add(ws);
    console.log(`Client connected (${clients.size} total)`);

    ws.on('close', () => {
      clients.delete(ws);
      console.log(`Client disconnected (${clients.size} total)`);
    });

    ws.on('message', (msg) => {
      try {
        const parsed = JSON.parse(msg);
        console.log(`Command received: ${parsed.type}/${parsed.command} -> ${parsed.params?.droneId || parsed.drone_id || 'ALL'}`);

        if (parsed.type === 'CMD' && parsed.command) {
          handleCommand(parsed, drones, ws);
        }
      } catch (e) {
        console.error('Invalid message:', msg.toString());
      }
    });
  });

  // Update loop
  const intervalMs = 1000 / CONFIG.updateRateHz;
  setInterval(() => {
    const leaderDrone = drones[0];
    const leaderState = {
      lat: leaderDrone.lat,
      lon: leaderDrone.lon,
      heading: leaderDrone.heading,
    };

    const packets = drones.map(d => d.update(intervalMs / 1000, d.role === 'PRIMARY' ? null : leaderState));

    const payload = JSON.stringify(packets);
    for (const client of clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(payload);
      }
    }
  }, intervalMs);
}

startServer();
