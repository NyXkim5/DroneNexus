// engine.js — DirectiveEngine, ObjectiveManager, PlatformLink, DebriefSystem
import { state } from './state.js';
import { DRONE_STATES, PATTERN_OFFSETS } from './constants.js';
import { showToast, utcTimeStamp, _css } from './utils.js';
import { getDiagState } from './panels.js';

/* ==============================================================
   DIRECTIVE ENGINE
   ============================================================== */
class DirectiveEngine {
  constructor(assets, eventCallback) {
    this.drones = assets; // internal compat
    this.addEvent = eventCallback;
    this.log = [];
    this.connectionManager = null;
  }

  execute(cmd, params) {
    params = params || {};
    const targets = params.droneId ? this.drones.filter(d => d.id === params.droneId) : this.drones;
    const targetLabel = params.droneId || 'ALL';

    switch (cmd) {
      case 'ARM': {
        // Pre-arm safety checks
        const failedChecks = [];
        targets.forEach(d => {
          if (d.battery < 20) failedChecks.push(d.id + ': Battery below 20% (' + d.battery.toFixed(0) + '%)');
          if (d.satellites < 6) failedChecks.push(d.id + ': Insufficient GPS (' + d.satellites + ' sats)');
          if (d.hdop > 2.5) failedChecks.push(d.id + ': GPS accuracy poor (HDOP ' + d.hdop.toFixed(1) + ')');
          const ds = getDiagState(d.id);
          if (ds && ds.overallHealth < 80) failedChecks.push(d.id + ': System health below 80% (' + ds.overallHealth.toFixed(0) + '%)');
        });

        if (failedChecks.length > 0) {
          showToast('PRE-ARM FAILED: ' + failedChecks[0], 'warning');
          this.addEvent({ time: utcTimeStamp(), source: targets[0]?.id || 'SYSTEM', msg: 'Pre-arm check failed: ' + failedChecks.join('; '), severity: 'warn' });
          return; // ARM failure emits its own event; skip generic post-switch event
        }

        targets.forEach(d => { d.armed = true; d.droneState = DRONE_STATES.ARMED; d.altitude = 0; d.speed = 0; });
        this._logCmd('ARM', targetLabel);
        showToast('ARMED — ' + targetLabel);
        this.addEvent({ time: utcTimeStamp(), source: targetLabel, msg: 'Pre-arm checks passed. Armed.', severity: 'ok' });
        state.objectivesCompleted++;
        return; // ARM emits its own event; skip generic post-switch event
      }

      case 'DISARM':
        targets.forEach(d => { d.armed = false; d.droneState = DRONE_STATES.IDLE; d.speed = 0; });
        this._logCmd('DISARM', targetLabel);
        showToast('DISARMED — ' + targetLabel);
        break;

      case 'TAKEOFF': {
        const alt = params.altitude || 30;
        targets.forEach(d => {
          if (!d.armed) { d.armed = true; }
          d.targetAltitude = alt;
          d.droneState = DRONE_STATES.TAKING_OFF;
          d.altitude = d.altitude || 0.5;
        });
        this._logCmd('LAUNCH ' + alt + 'm', targetLabel);
        showToast('LAUNCH ' + alt + 'm — ' + targetLabel);
        break;
      }

      case 'LAND':
        targets.forEach(d => { d.droneState = DRONE_STATES.LANDING; });
        this._logCmd('RECOVER', targetLabel);
        showToast('RECOVER — ' + targetLabel);
        break;

      case 'RTB':
        targets.forEach(d => { d.droneState = DRONE_STATES.RTB; });
        this._logCmd('RTB', targetLabel);
        showToast('RTB — ' + targetLabel, 'warning');
        break;

      case 'GOTO':
        this.drones.forEach(d => {
          d.targetLat = params.lat;
          d.targetLng = params.lng;
          d.droneState = DRONE_STATES.GOTO;
        });
        this._logCmd('GOTO ' + params.lat.toFixed(5) + ',' + params.lng.toFixed(5), 'ALL');
        showToast('GOTO — navigating to target');
        break;

      case 'SET_PATTERN': {
        const offsets = PATTERN_OFFSETS[params.pattern];
        if (offsets) {
          this.drones.forEach((d, i) => { d.patternOffset = offsets[i] || { along: 0, cross: 0 }; });
        }
        this._logCmd('PATTERN ' + params.pattern.replace(/_/g, ' '), 'ALL');
        showToast('Collection pattern: ' + params.pattern.replace(/_/g, ' '));
        const label = document.querySelector('.pattern-label');
        if (label) label.textContent = params.pattern.replace(/_/g, ' ');
        break;
      }

      case 'SET_SPEED':
        targets.forEach(d => { d.targetSpeed = params.speed; });
        this._logCmd('SPEED ' + params.speed + ' m/s', targetLabel);
        break;

      case 'SET_ALTITUDE':
        targets.forEach(d => { d.targetAltitude = params.altitude; });
        this._logCmd('ALT ' + params.altitude + 'm', targetLabel);
        break;

      case 'EMERGENCY_STOP':
        targets.forEach(d => { d.droneState = DRONE_STATES.EMERGENCY; d.armed = false; });
        this._logCmd('ABORT', targetLabel);
        showToast('ABORT — ' + targetLabel, 'error');
        break;

      case 'EXECUTE_MISSION': {
        const leader = this.drones[0];
        leader.missionWaypoints = params.waypoints || [];
        leader.missionIndex = 0;
        this.drones.forEach(d => { d.droneState = DRONE_STATES.MISSION; });
        this._logCmd('MISSION ' + (params.waypoints || []).length + ' waypoints', 'ALL');
        showToast('Mission started — ' + (params.waypoints || []).length + ' waypoints');
        break;
      }
    }

    // Send over WebSocket if connected
    if (this.connectionManager && this.connectionManager.ws) {
      try {
        this.connectionManager.ws.send(JSON.stringify({ type: 'DIRECTIVE', command: cmd, params: params }));
      } catch (e) { /* ignore send errors */ }
    }

    this.addEvent({ time: utcTimeStamp(), source: 'OVERWATCH', msg: cmd + ' → ' + targetLabel, severity: 'info' });
    state.objectivesCompleted++;
  }

  _logCmd(text, target) {
    this.log.unshift({ time: utcTimeStamp(), text: text, target: target });
    if (this.log.length > 20) this.log.pop();
    this._renderLog();
  }

  _renderLog() {
    const el = document.getElementById('cmd-log');
    if (!el) return;
    el.innerHTML = this.log.slice(0, 5).map(e =>
      '<div style="padding:3px 0;border-bottom:1px solid var(--border);color:var(--text-dim)">' +
      '<span style="color:var(--text)">' + e.time + '</span> ' + e.text + ' → ' + e.target + '</div>'
    ).join('');
  }
}

/* ==============================================================
   OBJECTIVE MANAGER
   ============================================================== */
class ObjectiveManager {
  constructor(mapInstance) {
    this.map = mapInstance;
    this.objectives = [];
    this.markers = [];
    this.polyline = null;
    this.addMode = false;
    this.wpType = 'GOTO';
  }

  toggleAddMode() {
    this.addMode = !this.addMode;
    const btn = document.getElementById('wp-add-mode');
    if (btn) {
      btn.style.borderColor = this.addMode ? 'var(--accent)' : '';
      btn.style.color = this.addMode ? 'var(--accent)' : '';
    }
    this.map.getContainer().style.cursor = this.addMode ? 'crosshair' : '';
  }

  setType(type) { this.wpType = type; }

  addObjective(lat, lng) {
    if (!this.addMode) return;
    const idx = this.objectives.length + 1;
    const obj = { lat, lng, type: this.wpType, index: idx };
    this.objectives.push(obj);

    const marker = L.circleMarker([lat, lng], {
      radius: 10, color: _css('--accent'), fillColor: _css('--accent'), fillOpacity: 0.2, weight: 2,
    }).addTo(this.map);
    marker.bindTooltip('OBJ-' + idx + ' ' + this.wpType, { permanent: true, direction: 'top', offset: [0, -10] });
    this.markers.push(marker);

    this._updatePolyline();
    this._updateCount();
  }

  // backward compat
  addWaypoint(lat, lng) { return this.addObjective(lat, lng); }

  _updatePolyline() {
    if (this.polyline) this.map.removeLayer(this.polyline);
    if (this.objectives.length > 1) {
      this.polyline = L.polyline(
        this.objectives.map(w => [w.lat, w.lng]),
        { color: _css('--accent'), weight: 1, opacity: 0.6, dashArray: '6, 6' }
      ).addTo(this.map);
    }
  }

  _updateCount() {
    const el = document.getElementById('wp-count');
    if (el) el.textContent = this.objectives.length + ' objective' + (this.objectives.length !== 1 ? 's' : '');
  }

  clear() {
    this.markers.forEach(m => this.map.removeLayer(m));
    if (this.polyline) this.map.removeLayer(this.polyline);
    this.objectives = [];
    this.markers = [];
    this.polyline = null;
    this._updateCount();
    if (this.addMode) this.toggleAddMode();
  }

  getObjectives() {
    return this.objectives.map(w => ({ lat: w.lat, lng: w.lng }));
  }

  getWaypoints() { return this.getObjectives(); }
}

/* ==============================================================
   PLATFORM LINK
   ============================================================== */
class PlatformLink {
  constructor(assets) {
    this.drones = assets; // internal compat
    this.ws = null;
    this.connected = false;
    this.reconnectTimer = null;
  }

  connect(url) {
    if (this.ws) this.disconnect();
    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      this._updateUI(false, 'Connection failed');
      return;
    }

    this.ws.onopen = () => {
      this.connected = true;
      this._updateUI(true, 'Connected to ' + url);
      showToast('Connected to OVERWATCH platform');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const packets = Array.isArray(data) ? data : [data];
        packets.forEach(p => {
          if (!p.drone_id) return;
          const drone = this.drones.find(d => d.id === p.drone_id);
          if (drone) {
            drone.isLive = true;
            if (p.position) { drone.lat = p.position.lat; drone.lng = p.position.lon; }
            if (p.attitude) { drone.roll = p.attitude.roll; drone.pitch = p.attitude.pitch; drone.yaw = p.attitude.yaw; }
            if (p.velocity) { drone.speed = p.velocity.ground_speed; drone.heading = p.velocity.heading; drone.verticalSpeed = p.velocity.vertical_speed; }
            if (p.battery) { drone.battery = p.battery.remaining_pct; drone.voltage = p.battery.voltage; drone.current = p.battery.current; }
            if (p.gps) { drone.satellites = p.gps.satellites; drone.hdop = p.gps.hdop; }
            if (p.link) { drone.rssi = p.link.rssi; drone.linkQuality = p.link.quality; drone.latency = p.link.latency_ms; }
            if (p.status) drone.status = p.status;
            if (p.position && p.position.alt_agl !== undefined) drone.altitude = p.position.alt_agl;
            if (p.fpv) drone.fpvData = p.fpv;
          }
        });
      } catch (e) { /* ignore parse errors */ }
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.drones.forEach(d => { d.isLive = false; });
      this._updateUI(false, 'Disconnected — exercise mode');
      this.reconnectTimer = setTimeout(() => this.connect(url), 3000);
    };

    this.ws.onerror = () => {
      this._updateUI(false, 'Connection error');
    };
  }

  disconnect() {
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
    this.connected = false;
    this.drones.forEach(d => { d.isLive = false; });
    this._updateUI(false, 'Disconnected — exercise mode');
  }

  _updateUI(connected, statusText) {
    const badge = document.getElementById('connection-badge');
    const label = document.getElementById('connection-label');
    const liveLabel = document.getElementById('live-label');
    const statusEl = document.getElementById('conn-status');
    const connectBtn = document.getElementById('conn-connect');
    const disconnectBtn = document.getElementById('conn-disconnect');
    const urlInput = document.getElementById('conn-ws-url');

    if (badge) badge.className = 'connection-badge ' + (connected ? 'live' : 'sim');
    if (label) label.textContent = connected ? 'OPERATIONAL' : 'EXERCISE';
    if (liveLabel) liveLabel.textContent = connected ? 'OPERATIONAL' : 'EXERCISE';
    if (statusEl) statusEl.textContent = statusText || '';
    if (connectBtn) connectBtn.disabled = connected;
    if (disconnectBtn) disconnectBtn.disabled = !connected;
    if (urlInput) urlInput.disabled = connected;
  }
}

/* ==============================================================
   DEBRIEF SYSTEM
   ============================================================== */
class DebriefSystem {
  constructor(assets) {
    this.drones = assets; // internal compat
    this.frames = [];
    this.recording = true;
    this.playing = false;
    this.playIndex = 0;
    this.playSpeed = 1;
    this.recordInterval = 100; // ms
    this.lastRecordTime = 0;
  }

  recordFrame(now) {
    if (!this.recording || this.playing) return;
    if (now - this.lastRecordTime < this.recordInterval) return;
    this.lastRecordTime = now;
    const snapshot = this.drones.map(d => ({
      id: d.id, lat: d.lat, lng: d.lng, heading: d.heading, altitude: d.altitude,
      speed: d.speed, battery: d.battery, status: d.status, roll: d.roll, pitch: d.pitch,
      yaw: d.yaw, verticalSpeed: d.verticalSpeed, droneState: d.droneState,
    }));
    this.frames.push(snapshot);
  }

  play() {
    if (this.frames.length === 0) return;
    this.playing = true;
    this.recording = false;
    this.playIndex = 0;
    this._updateUI();
  }

  pause() { this.playing = false; this._updateUI(); }
  stop() { this.playing = false; this.playIndex = 0; this.recording = true; this._updateUI(); }

  setSpeed(s) { this.playSpeed = s; }

  seek(pct) {
    this.playIndex = Math.floor((pct / 100) * (this.frames.length - 1));
    this._applyFrame();
  }

  tick() {
    if (!this.playing || this.frames.length === 0) return;
    this.playIndex += this.playSpeed;
    if (this.playIndex >= this.frames.length) { this.playIndex = this.frames.length - 1; this.pause(); }
    this._applyFrame();
    this._updateUI();
  }

  _applyFrame() {
    const frame = this.frames[Math.floor(this.playIndex)];
    if (!frame) return;
    frame.forEach(snap => {
      const drone = this.drones.find(d => d.id === snap.id);
      if (!drone) return;
      Object.assign(drone, { lat: snap.lat, lng: snap.lng, heading: snap.heading, altitude: snap.altitude,
        speed: snap.speed, battery: snap.battery, status: snap.status, roll: snap.roll, pitch: snap.pitch,
        yaw: snap.yaw, verticalSpeed: snap.verticalSpeed, droneState: snap.droneState });
    });
  }

  _updateUI() {
    const timeline = document.getElementById('replay-timeline');
    const cur = document.getElementById('replay-time-current');
    const total = document.getElementById('replay-time-total');
    const info = document.getElementById('replay-info');
    if (timeline && this.frames.length > 0) {
      timeline.max = this.frames.length - 1;
      timeline.value = Math.floor(this.playIndex);
    }
    const curSec = Math.floor(this.playIndex * this.recordInterval / 1000);
    const totalSec = Math.floor(this.frames.length * this.recordInterval / 1000);
    if (cur) cur.textContent = Math.floor(curSec / 60) + ':' + String(curSec % 60).padStart(2, '0');
    if (total) total.textContent = Math.floor(totalSec / 60) + ':' + String(totalSec % 60).padStart(2, '0');
    if (info) info.textContent = this.frames.length + ' frames / ' + totalSec + 's — ' + (this.playing ? 'Playing' : this.recording ? 'Recording' : 'Paused');
  }
}

export { DirectiveEngine, ObjectiveManager, PlatformLink, DebriefSystem };
