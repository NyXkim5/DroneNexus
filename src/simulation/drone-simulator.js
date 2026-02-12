/**
 * NEXUS — Standalone Drone Simulator
 *
 * Node.js-based simulator that generates realistic telemetry data
 * and publishes it over WebSocket. Used for testing the ground
 * station and HUD without hardware.
 *
 * Usage:
 *   node drone-simulator.js [--drones 6] [--port 8765]
 */

const WebSocket = require('ws');
const { V_FORMATION_OFFSETS, DroneStatus, FlightMode, ProtocolType, createFPVTelemetryPacket } = require('../shared/protocol');

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
  { id: 'ALPHA-1',   role: 'LEADER',  color: '#00ff88' },
  { id: 'BRAVO-2',   role: 'WINGMAN', color: '#3388ff' },
  { id: 'CHARLIE-3', role: 'RECON',   color: '#ffaa00' },
  { id: 'DELTA-4',   role: 'WINGMAN', color: '#ff3355' },
  { id: 'ECHO-5',    role: 'SUPPORT', color: '#00ccff' },
  { id: 'FOXTROT-6', role: 'TAIL',    color: '#aa55ff' },
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
  }

  update(dt, leaderState) {
    this.seq++;
    const t = Date.now() / 1000;

    if (this.role === 'LEADER') {
      // Leader orbits the center point
      this.orbitAngle += this.orbitSpeed * dt;
      this.lat = CONFIG.centerLat + Math.cos(this.orbitAngle) * this.orbitRadius;
      this.lon = CONFIG.centerLon + Math.sin(this.orbitAngle) * this.orbitRadius;
      this.heading = ((this.orbitAngle * 180 / Math.PI) + 90) % 360;
    } else if (leaderState) {
      // Followers maintain formation offset
      const offset = V_FORMATION_OFFSETS[this.id] || { dx: 0, dy: 0 };
      const headingRad = leaderState.heading * Math.PI / 180;
      const metersToDeg = 1 / 111320;

      const rotatedDx = offset.dx * Math.cos(headingRad) - offset.dy * Math.sin(headingRad);
      const rotatedDy = offset.dx * Math.sin(headingRad) + offset.dy * Math.cos(headingRad);

      const targetLat = leaderState.lat + rotatedDy * metersToDeg;
      const targetLon = leaderState.lon + rotatedDx * metersToDeg / Math.cos(leaderState.lat * Math.PI / 180);

      // Smooth approach to target position
      this.lat += (targetLat - this.lat) * 0.1;
      this.lon += (targetLon - this.lon) * 0.1;
      this.heading = leaderState.heading + (Math.random() - 0.5) * 2;
    }

    // Altitude oscillation
    this.alt = 120 + Math.sin(t * 0.1 + this.phaseOffset) * 5;
    this.vspeed = Math.cos(t * 0.1 + this.phaseOffset) * 0.5;

    // Attitude oscillation
    this.roll = Math.sin(t * 0.3 + this.phaseOffset) * 8;
    this.pitch = Math.sin(t * 0.2 + this.phaseOffset * 1.5) * 3;
    this.yaw = this.heading;

    // Speed variation
    this.speed = 10 + Math.sin(t * 0.05 + this.index) * 3;

    // Battery drain
    this.battery = Math.max(0, this.battery - 0.01 * dt);
    this.voltage = 18 + (this.battery / 100) * 7;
    this.current = 8 + Math.random() * 5;

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
    this.armTimer += dt;
    const dLat = (this.lat - CONFIG.centerLat) * 111320;
    const dLon = (this.lon - CONFIG.centerLon) * 111320 * Math.cos(this.lat * Math.PI / 180);
    this.homeDistance = Math.sqrt(dLat * dLat + dLon * dLon);
    this.homeDirection = ((Math.atan2(-dLon, -dLat) * 180 / Math.PI) + 360) % 360;
    this.videoLinkQuality = Math.max(60, Math.min(100, this.videoLinkQuality + (Math.random() - 0.5) * 2));

    // Status derivation
    let status = DroneStatus.ACTIVE;
    if (this.battery < 25) status = DroneStatus.LOW_BATT;
    else if (this.rssi < 60) status = DroneStatus.WEAK_SIGNAL;

    return createFPVTelemetryPacket(this.id, {
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
}

// ============================================================
// Server
// ============================================================
function startServer() {
  const wss = new WebSocket.Server({ port: CONFIG.port });
  const drones = DRONES.slice(0, CONFIG.droneCount).map((d, i) => new SimulatedDrone(d, i));

  console.log(`NEXUS Simulator — ${drones.length} drones on ws://localhost:${CONFIG.port}`);

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
        const cmd = JSON.parse(msg);
        console.log(`Command received: ${cmd.type} -> ${cmd.drone_id || 'ALL'}`);
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

    const packets = drones.map(d => d.update(intervalMs / 1000, d.role === 'LEADER' ? null : leaderState));

    const payload = JSON.stringify(packets);
    for (const client of clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(payload);
      }
    }
  }, intervalMs);
}

startServer();
