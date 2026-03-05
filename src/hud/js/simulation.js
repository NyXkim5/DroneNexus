import { CENTER_LAT, CENTER_LNG, ORBIT_RADIUS, DRONE_STATES, TRAIL_LENGTH } from './constants.js';
import { rand, randInt, clamp, lerp, radToDeg, degToRad } from './utils.js';

export const VALID_TRANSITIONS = {
  IDLE:       ['ARMED'],
  ARMED:      ['IDLE', 'TAKING_OFF', 'EMERGENCY'],
  TAKING_OFF: ['FLYING', 'EMERGENCY'],
  FLYING:     ['LANDING', 'RTB', 'GOTO', 'MISSION', 'EMERGENCY'],
  GOTO:       ['FLYING', 'RTB', 'EMERGENCY'],
  MISSION:    ['FLYING', 'RTB', 'EMERGENCY'],
  LANDING:    ['LANDED', 'EMERGENCY'],
  LANDED:     ['IDLE', 'ARMED'],
  RTB:        ['LANDING', 'EMERGENCY'],
  EMERGENCY:  ['IDLE', 'LANDED'],
};

export class AssetSimulator {
  constructor(def, index) {
    this.id = def.id;
    this.color = def.color;
    this.rgb = def.rgb;
    this.role = def.role;
    this.patternOffset = def.patternOffset;
    this.index = index;

    // Orbital phase offset
    this.phaseOffset = index * 0.05;
    this.altPhaseOffset = index * 1.1;

    // State
    this.lat = CENTER_LAT;
    this.lng = CENTER_LNG;
    this.heading = 0;
    this.altitude = rand(118, 125);
    this.speed = rand(10, 13);
    this.verticalSpeed = 0;
    this.roll = 0;
    this.pitch = 0;
    this.yaw = 0;
    this.battery = rand(75, 100);
    this.voltage = rand(22.0, 24.8);
    this.current = rand(8, 16);
    this.satellites = randInt(13, 16);
    this.hdop = rand(0.6, 1.0);
    this.rssi = rand(82, 95);
    this.linkQuality = rand(90, 100);
    this.latency = rand(18, 35);
    this.temperature = rand(40, 48);
    this.status = 'NOMINAL';

    // Trail history
    this.trail = [];

    // Internal sim time
    this.t = rand(0, Math.PI * 2);

    // Command state machine
    this.droneState = DRONE_STATES.FLYING;
    this.armed = true;
    this.targetAltitude = 120;
    this.targetSpeed = 12;
    this.targetLat = null;
    this.targetLng = null;
    this.homePosition = { lat: CENTER_LAT, lng: CENTER_LNG };
    this.missionWaypoints = [];
    this.missionIndex = 0;
    this.isLive = false;

    // FPV extensions
    this.fpvData = {
      flight_mode: 'STABILIZE',
      camera_tilt: 0,
      mah_consumed: 0,
      cell_voltage: (this.voltage / 6),
      flight_timer_s: 0,
      arm_timer_s: 0,
      home_distance_m: 0,
      home_direction_deg: 0,
      video_link: { quality: 95, channel: 1 + index, frequency_mhz: 5740 + index * 20, recording: false, system: 'Simulation' },
      protocol: 'MAVLINK',
    };
  }

  canTransition(newState) {
    const allowed = VALID_TRANSITIONS[this.droneState];
    return allowed && allowed.includes(newState);
  }

  transition(newState) {
    if (!this.canTransition(newState)) return false;
    this.droneState = newState;
    return true;
  }

  _flyFormation(dt, leaderHeading, leaderLat, leaderLng) {
    if (this.role === 'PRIMARY') {
      this.lat = CENTER_LAT + Math.cos(this.t) * ORBIT_RADIUS;
      this.lng = CENTER_LNG + Math.sin(this.t) * ORBIT_RADIUS * 1.3;
      this.heading = radToDeg(Math.atan2(
        Math.cos(this.t) * ORBIT_RADIUS * 1.3,
        -Math.sin(this.t) * ORBIT_RADIUS
      ));
      if (this.heading < 0) this.heading += 360;
    } else {
      const hr = degToRad(leaderHeading);
      const spacing = 0.00035;
      const alongDir = { lat: Math.cos(hr + Math.PI / 2), lng: Math.sin(hr + Math.PI / 2) };
      const crossDir = { lat: -Math.sin(hr + Math.PI / 2), lng: Math.cos(hr + Math.PI / 2) };
      const tLat = leaderLat + this.patternOffset.along * spacing * alongDir.lat + this.patternOffset.cross * spacing * crossDir.lat;
      const tLng = leaderLng + this.patternOffset.along * spacing * alongDir.lng + this.patternOffset.cross * spacing * crossDir.lng;
      this.lat = lerp(this.lat, tLat, 0.08);
      this.lng = lerp(this.lng, tLng, 0.08);
      this.heading = leaderHeading + Math.sin(this.t * 0.7 + this.index) * 3;
      if (this.heading < 0) this.heading += 360;
      if (this.heading >= 360) this.heading -= 360;
    }
    const prevAlt = this.altitude;
    this.altitude = this.targetAltitude + Math.sin(this.t * 0.5 + this.altPhaseOffset) * 5;
    this.verticalSpeed = (this.altitude - prevAlt) / dt;
    this.roll = Math.sin(this.t * 1.3 + this.index * 0.7) * 8;
    this.pitch = Math.sin(this.t * 0.9 + this.index * 1.2) * 3;
    this.yaw = this.heading;
    this.speed = this.targetSpeed + Math.sin(this.t * 0.4 + this.index) * 2;
  }

  _flyToward(targetLat, targetLng, dt) {
    const dLat = targetLat - this.lat;
    const dLng = targetLng - this.lng;
    const dist = Math.sqrt(dLat * dLat + dLng * dLng);
    if (dist < 0.00005) return true; // arrived (~5m)
    const step = Math.min(dist, this.targetSpeed * dt / 111320);
    this.lat += (dLat / dist) * step;
    this.lng += (dLng / dist) * step;
    this.heading = radToDeg(Math.atan2(dLng, dLat));
    if (this.heading < 0) this.heading += 360;
    this.yaw = this.heading;
    this.roll = Math.sin(this.t * 1.3) * 4;
    this.pitch = Math.sin(this.t * 0.9) * 2;
    this.speed = this.targetSpeed;
    return false;
  }

  update(dt, leaderHeading, leaderLat, leaderLng) {
    if (this.isLive) return; // skip sim for live drones
    this.t += dt * 0.3;

    switch (this.droneState) {
      case DRONE_STATES.IDLE:
      case DRONE_STATES.LANDED:
        this.speed = 0; this.verticalSpeed = 0; this.roll = 0; this.pitch = 0;
        break;

      case DRONE_STATES.ARMED:
        this.speed = 0; this.verticalSpeed = 0; this.roll = 0; this.pitch = 0;
        break;

      case DRONE_STATES.TAKING_OFF:
        this.speed = 0;
        this.verticalSpeed = 2;
        this.altitude += 2 * dt;
        this.roll = 0; this.pitch = 0;
        if (this.altitude >= this.targetAltitude) {
          this.altitude = this.targetAltitude;
          this.transition(DRONE_STATES.FLYING);
          this.verticalSpeed = 0;
        }
        break;

      case DRONE_STATES.FLYING:
        this._flyFormation(dt, leaderHeading, leaderLat, leaderLng);
        break;

      case DRONE_STATES.GOTO:
        if (this.role === 'PRIMARY') {
          const arrived = this._flyToward(this.targetLat, this.targetLng, dt);
          if (arrived) this.transition(DRONE_STATES.FLYING);
        } else {
          this._flyFormation(dt, leaderHeading, leaderLat, leaderLng);
        }
        this.altitude = this.targetAltitude + Math.sin(this.t * 0.5) * 2;
        this.verticalSpeed = Math.cos(this.t * 0.5) * 0.3;
        break;

      case DRONE_STATES.MISSION:
        if (this.role === 'PRIMARY') {
          if (this.missionIndex < this.missionWaypoints.length) {
            const wp = this.missionWaypoints[this.missionIndex];
            const arrived = this._flyToward(wp.lat, wp.lng, dt);
            if (arrived) this.missionIndex++;
          } else {
            this.transition(DRONE_STATES.FLYING);
            this.missionIndex = 0;
          }
        } else {
          this._flyFormation(dt, leaderHeading, leaderLat, leaderLng);
        }
        this.altitude = this.targetAltitude + Math.sin(this.t * 0.5) * 2;
        this.verticalSpeed = Math.cos(this.t * 0.5) * 0.3;
        break;

      case DRONE_STATES.LANDING:
        this.verticalSpeed = -1.5;
        this.altitude = Math.max(0, this.altitude - 1.5 * dt);
        this.speed = Math.max(0, this.speed - 2 * dt);
        this.roll = 0; this.pitch = 0;
        if (this.altitude <= 0.5) {
          this.altitude = 0; this.transition(DRONE_STATES.LANDED);
          this.armed = false; this.verticalSpeed = 0; this.speed = 0;
        }
        break;

      case DRONE_STATES.RTB:
        if (this._flyToward(this.homePosition.lat, this.homePosition.lng, dt)) {
          this.transition(DRONE_STATES.LANDING);
        }
        this.altitude = this.targetAltitude + Math.sin(this.t * 0.3) * 2;
        this.verticalSpeed = Math.cos(this.t * 0.3) * 0.3;
        break;

      case DRONE_STATES.EMERGENCY:
        this.verticalSpeed = -5;
        this.altitude = Math.max(0, this.altitude - 5 * dt);
        this.speed = Math.max(0, this.speed - 5 * dt);
        this.roll = rand(-15, 15); this.pitch = rand(-10, 10);
        if (this.altitude <= 0) {
          this.altitude = 0; this.transition(DRONE_STATES.LANDED);
          this.armed = false; this.verticalSpeed = 0; this.speed = 0;
        }
        break;
    }

    // Common sensor updates for airborne states
    const airborne = [DRONE_STATES.FLYING, DRONE_STATES.GOTO, DRONE_STATES.MISSION,
                      DRONE_STATES.RTB, DRONE_STATES.TAKING_OFF, DRONE_STATES.LANDING];
    if (airborne.includes(this.droneState)) {
      this.battery = Math.max(0, this.battery - 0.01 * dt);
      this.voltage = 20.5 + this.battery * 0.047 + Math.sin(this.t) * 0.1;
      this.current = 10 + Math.sin(this.t * 0.3) * 4;
      this.satellites = clamp(Math.round(14 + Math.sin(this.t * 0.2 + this.index) * 2), 12, 16);
      this.hdop = clamp(0.8 + Math.sin(this.t * 0.15 + this.index) * 0.3, 0.6, 1.2);
      this.rssi = clamp(85 + Math.sin(this.t * 0.6 + this.index * 2) * 10 + rand(-2, 2), 55, 95);
      this.linkQuality = clamp(93 + Math.sin(this.t * 0.25 + this.index) * 5 + rand(-1, 1), 85, 100);
      this.latency = clamp(28 + Math.sin(this.t * 0.35 + this.index) * 12 + rand(-3, 3), 15, 45);
      this.temperature = clamp(44 + Math.sin(this.t * 0.18 + this.index * 0.5) * 6 + rand(-1, 1), 38, 52);
    }

    // Failure effects
    if (this._gpsFailureUntil && Date.now() < this._gpsFailureUntil) {
      this.hdop = clamp(this.hdop + Math.random() * 0.5, 0.6, 3.5);
      this.satellites = clamp(this.satellites - Math.floor(Math.random() * 2), 4, 16);
    } else if (this._gpsFailureUntil) {
      this._gpsFailureUntil = null;
    }

    if (this._batteryDrainUntil && Date.now() < this._batteryDrainUntil) {
      this.battery = clamp(this.battery - 0.15 * dt, 5, 100);
    } else if (this._batteryDrainUntil) {
      this._batteryDrainUntil = null;
    }

    if (this._commsDegradedUntil && Date.now() < this._commsDegradedUntil) {
      this.linkQuality = clamp(this.linkQuality - Math.random() * 8, 40, 100);
      this.rssi = clamp(this.rssi - Math.random() * 5, 55, 95);
      this.latency = clamp(this.latency + Math.random() * 30, 18, 200);
    } else if (this._commsDegradedUntil) {
      this._commsDegradedUntil = null;
    }

    // Status derivation
    const activeStates = [DRONE_STATES.FLYING, DRONE_STATES.GOTO, DRONE_STATES.MISSION];
    if (activeStates.includes(this.droneState)) {
      if (this.battery < 25) this.status = 'DEGRADED';
      else if (this.rssi < 60) this.status = 'COMMS_DEGRADED';
      else this.status = 'NOMINAL';
    } else {
      this.status = this.droneState;
    }

    // Trail (only when airborne)
    if (this.altitude > 1) {
      this.trail.push({ lat: this.lat, lng: this.lng });
      if (this.trail.length > TRAIL_LENGTH) this.trail.shift();
    }
  }

  injectFailure() {
    const roll = Math.random();
    if (roll < 0.003) {
      // GPS degradation: spike hdop for a few seconds
      this._gpsFailureUntil = Date.now() + 5000 + Math.random() * 5000;
      return 'GPS';
    } else if (roll < 0.006) {
      // Battery anomaly: accelerate drain for a few seconds
      this._batteryDrainUntil = Date.now() + 8000 + Math.random() * 7000;
      return 'BATTERY';
    } else if (roll < 0.009) {
      // Comms degradation
      this._commsDegradedUntil = Date.now() + 6000 + Math.random() * 6000;
      return 'COMMS';
    }
    return null;
  }
}
