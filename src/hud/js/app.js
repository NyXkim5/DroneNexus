/* ==============================================================
   OVERWATCH ISR PLATFORM — APPLICATION ENTRY POINT
   Extracted from monolith index.html main() IIFE
   ============================================================== */

import { state } from './state.js';
import { CENTER_LAT, CENTER_LNG, DRONE_STATES, ASSET_DEFS, SPARKLINE_POINTS,
         EVENT_INTERVAL_MIN, EVENT_INTERVAL_MAX, PATTERN_OFFSETS } from './constants.js';
import { _css, _cssRgba, clamp, rand, degToRad, utcString, utcTimeStamp,
         batteryColor, showToast, playAlert, toMGRS } from './utils.js';
import { AssetSimulator } from './simulation.js';
import { Sparkline } from './sparkline.js';
import { DirectiveEngine, ObjectiveManager, PlatformLink, DebriefSystem } from './engine.js';
import { initMap, createDroneIcon, updateMapMarker, updateFormationLines,
         updateFovCone } from './map.js';
import { updateAssetExplorer, selectDrone, updateInspector, getDiagState,
         updateDiagnosticsPanel, updateHardwarePanel, addEvent,
         renderActivityStream, setEventCallback,
         generateActivity, generateStateCorrelatedEvents } from './panels.js';

/* ==============================================================
   MODE SWITCH
   ============================================================== */

function applyMode(mode) {
  state.currentMode = mode;
  document.body.dataset.mode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
}

export function setMode(mode) {
  if (mode === state.currentMode) return;
  const isInitial = state.currentMode === null;

  // ISR video management (integrated, not monkey-patched)
  const handleVideoPanel = () => {
    const videoPanel = document.getElementById('video-panel');
    const mapEl = document.getElementById('map');
    if (mode === 'ISR') {
      videoPanel.style.display = '';
      videoPanel.classList.remove('pip', 'fullscreen');
      mapEl.style.display = 'none';
      if (videoMgr) videoMgr.startTestPattern();
      if (!state.selectedDroneId && assets.length > 0) {
        state.selectedDroneId = assets[0].id;
      }
    } else {
      if (!videoPanel.classList.contains('pip')) {
        videoPanel.style.display = 'none';
      }
      mapEl.style.display = '';
      setTimeout(() => state.map.invalidateSize(), 50);
      if (videoMgr) videoMgr.stopTestPattern();
    }
  };

  if (!isInitial) {
    // Fade out panels briefly before switching
    document.body.classList.add('mode-transitioning');
    setTimeout(() => {
      applyMode(mode);
      handleVideoPanel();
      // Fade panels back in
      requestAnimationFrame(() => {
        document.body.classList.remove('mode-transitioning');
      });
    }, 150);
  } else {
    applyMode(mode);
    // On initial mode set, video elements may not exist yet
    try { handleVideoPanel(); } catch (e) { /* initial load */ }
  }
}

// Expose setMode globally so panels.js action buttons can use it
window.setMode = setMode;

/* ==============================================================
   MAIN APPLICATION
   ============================================================== */

// Initialize assets
const assets = ASSET_DEFS.map((def, i) => new AssetSimulator(def, i));
const drones = assets; // local alias used throughout app.js
state.assets = assets;
state.drones = assets;
window._overwatchAssets = assets; // expose for diagnostics panel

// Initialize map
try {
  initMap();
  setTimeout(() => state.map.invalidateSize(), 100);
} catch (e) {
  console.error('Map initialization failed:', e);
  document.getElementById('map').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font:12px var(--font-label)">MAP UNAVAILABLE</div>';
}

// Initialize sparklines
const sparkHealth = new Sparkline('spark-health', _css('--green'));
const sparkLatency = new Sparkline('spark-latency', _css('--amber'));
const sparkBattery = new Sparkline('spark-battery', _css('--accent'));
const sparkCoverage = new Sparkline('spark-coverage', _css('--cyan'));

// Initialize subsystems
const cmdEngine = new DirectiveEngine(assets, addEvent);
const wpManager = new ObjectiveManager(state.map);
const connManager = new PlatformLink(assets);
const replaySystem = new DebriefSystem(assets);
cmdEngine.connectionManager = connManager;

// Notification badge state (must be declared before setEventCallback)
let unreadEventCount = 0;
let eventLogFocused = false;
const eventBadge = document.getElementById('event-badge');

// Set up the addEvent callback for unread event count tracking
setEventCallback(function(ev) {
  unreadEventCount++;
  if (eventBadge) {
    eventBadge.textContent = unreadEventCount > 99 ? '99+' : String(unreadEventCount);
    eventBadge.classList.remove('hidden');
  }
});

// GOTO mode state
let gotoMode = false;

function enableGotoMode() {
  gotoMode = true;
  state.map.getContainer().style.cursor = 'crosshair';
  document.getElementById('goto-indicator').style.display = '';
  document.getElementById('goto-coords').textContent = 'Awaiting target designation...';
  const btn = document.getElementById('cmd-goto');
  if (btn) { btn.style.borderColor = 'var(--accent)'; btn.style.color = 'var(--accent)'; }
}

function disableGotoMode() {
  gotoMode = false;
  if (!wpManager.addMode) state.map.getContainer().style.cursor = '';
  document.getElementById('goto-indicator').style.display = 'none';
  const btn = document.getElementById('cmd-goto');
  if (btn) { btn.style.borderColor = ''; btn.style.color = ''; }
}

// ---- ARM/DISARM VISUAL STATE (Fix #2) ----
let armedState = true; // start armed since drones start FLYING
function updateArmDisarmVisuals() {
  const armBtn = document.getElementById('cmd-arm');
  const disarmBtn = document.getElementById('cmd-disarm');
  if (armedState) {
    armBtn.classList.add('state-active');
    armBtn.classList.remove('state-inactive');
    disarmBtn.classList.remove('state-disarmed-active');
    disarmBtn.classList.add('state-inactive');
  } else {
    armBtn.classList.remove('state-active');
    armBtn.classList.add('state-inactive');
    disarmBtn.classList.add('state-disarmed-active');
    disarmBtn.classList.remove('state-inactive');
  }
}
// Initialize
updateArmDisarmVisuals();

// ---- COMMAND BUTTON HANDLERS ----
document.getElementById('cmd-arm').addEventListener('click', () => {
  cmdEngine.execute('ARM', { droneId: state.selectedDroneId === drones[0].id ? null : state.selectedDroneId });
  armedState = true;
  updateArmDisarmVisuals();
});
document.getElementById('cmd-disarm').addEventListener('click', () => {
  cmdEngine.execute('DISARM', { droneId: state.selectedDroneId === drones[0].id ? null : state.selectedDroneId });
  armedState = false;
  updateArmDisarmVisuals();
});
document.getElementById('cmd-takeoff').addEventListener('click', () => {
  const alt = parseInt(document.getElementById('takeoff-alt').value);
  cmdEngine.execute('TAKEOFF', { altitude: alt });
});
document.getElementById('cmd-land').addEventListener('click', () => cmdEngine.execute('LAND'));
document.getElementById('cmd-rtl').addEventListener('click', () => cmdEngine.execute('RTB'));
document.getElementById('cmd-goto').addEventListener('click', () => {
  if (gotoMode) disableGotoMode(); else enableGotoMode();
});
document.getElementById('cmd-estop').addEventListener('click', function() {
  const btn = this;
  if (btn.classList.contains('confirming')) {
    // Second click within timeout -- execute
    clearTimeout(btn._confirmTimer);
    btn.classList.remove('confirming');
    btn.textContent = 'ABORT';
    cmdEngine.execute('EMERGENCY_STOP');
  } else {
    // First click -- enter confirm state
    btn.classList.add('confirming');
    btn.textContent = 'CONFIRM (3s)';
    btn._confirmTimer = setTimeout(() => {
      btn.classList.remove('confirming');
      btn.textContent = 'ABORT';
    }, 3000);
  }
});

// ---- SLIDER HANDLERS ----
document.getElementById('takeoff-alt').addEventListener('input', function() {
  document.getElementById('takeoff-alt-val').textContent = this.value + 'm';
});
document.getElementById('cmd-speed').addEventListener('input', function() {
  const valEl = document.getElementById('speed-val');
  valEl.textContent = this.value + ' m/s';
  valEl.style.color = 'var(--amber)';
});
document.getElementById('cmd-speed').addEventListener('change', function() {
  cmdEngine.execute('SET_SPEED', { speed: parseFloat(this.value) });
  document.getElementById('speed-val').style.color = '';
});
document.getElementById('cmd-altitude').addEventListener('input', function() {
  const valEl = document.getElementById('alt-val');
  valEl.textContent = this.value + 'm';
  valEl.style.color = 'var(--amber)';
});
document.getElementById('cmd-altitude').addEventListener('change', function() {
  cmdEngine.execute('SET_ALTITUDE', { altitude: parseInt(this.value) });
  document.getElementById('alt-val').style.color = '';
});

// ---- COLLECTION PATTERN DROPDOWN ----
document.getElementById('cmd-pattern').addEventListener('change', function() {
  cmdEngine.execute('SET_PATTERN', { pattern: this.value });
});

// ---- MAP CLICK ----
state.map.on('click', function(e) {
  if (gotoMode) {
    cmdEngine.execute('GOTO', { lat: e.latlng.lat, lng: e.latlng.lng });
    document.getElementById('goto-coords').textContent = e.latlng.lat.toFixed(5) + ', ' + e.latlng.lng.toFixed(5);
    setTimeout(disableGotoMode, 1500);
  } else if (wpManager.addMode && state.currentMode === 'TASK') {
    wpManager.addWaypoint(e.latlng.lat, e.latlng.lng);
  }
});

// ---- WAYPOINT TOOLBAR ----
document.getElementById('wp-add-mode').addEventListener('click', () => wpManager.toggleAddMode());
document.getElementById('wp-loiter').addEventListener('click', () => wpManager.setType('LOITER'));
document.getElementById('wp-orbit').addEventListener('click', () => wpManager.setType('ORBIT'));
document.getElementById('wp-clear').addEventListener('click', () => wpManager.clear());
document.getElementById('wp-execute').addEventListener('click', () => {
  const objs = wpManager.getObjectives();
  if (objs.length === 0) { showToast('Add objectives first', 'error'); return; }
  cmdEngine.execute('EXECUTE_MISSION', { waypoints: objs });
});

// ---- CONNECTION MODAL ----
function openConnectionModal() {
  const modal = document.getElementById('connection-modal');
  modal.style.pointerEvents = 'auto';
  requestAnimationFrame(() => {
    modal.querySelector('.connection-panel-backdrop').classList.add('visible');
    modal.querySelector('.connection-panel').classList.add('visible');
  });
}
function closeConnectionModal() {
  const modal = document.getElementById('connection-modal');
  modal.querySelector('.connection-panel-backdrop').classList.remove('visible');
  modal.querySelector('.connection-panel').classList.remove('visible');
  setTimeout(() => { modal.style.pointerEvents = 'none'; }, 250);
}
document.getElementById('connection-badge').addEventListener('click', () => {
  openConnectionModal();
});
document.getElementById('conn-modal-backdrop').addEventListener('click', () => {
  closeConnectionModal();
});
document.getElementById('conn-close').addEventListener('click', () => {
  closeConnectionModal();
});
document.getElementById('conn-connect').addEventListener('click', () => {
  const url = document.getElementById('conn-ws-url').value.trim();
  if (!url) { showToast('Enter a WebSocket URL', 'error'); return; }
  connManager.connect(url);
});
document.getElementById('conn-disconnect').addEventListener('click', () => {
  connManager.disconnect();
  showToast('Disconnected');
});

// ---- REPLAY CONTROLS ----
document.getElementById('replay-play-btn').addEventListener('click', () => replaySystem.play());
document.getElementById('replay-pause-btn').addEventListener('click', () => replaySystem.pause());
document.getElementById('replay-timeline').addEventListener('input', function() {
  replaySystem.seek(parseFloat(this.value) / parseFloat(this.max) * 100);
});
document.getElementById('replay-speed-toggle').addEventListener('click', function() {
  const speeds = [0.5, 1, 2, 4];
  const cur = speeds.indexOf(replaySystem.playSpeed);
  const next = speeds[(cur + 1) % speeds.length];
  replaySystem.setSpeed(next);
  this.textContent = next + 'x';
  document.querySelectorAll('.replay-speed-btn[data-speed]').forEach(b => {
    b.classList.toggle('active', parseFloat(b.dataset.speed) === next);
  });
});
document.querySelectorAll('.replay-speed-btn[data-speed]').forEach(btn => {
  btn.addEventListener('click', function() {
    const spd = parseFloat(this.dataset.speed);
    replaySystem.setSpeed(spd);
    document.querySelectorAll('.replay-speed-btn[data-speed]').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
    document.getElementById('replay-speed-toggle').textContent = spd + 'x';
  });
});

// ---- COMMAND TARGET UPDATES ----
function updateCmdTarget() {
  const el = document.getElementById('cmd-target');
  if (el) el.textContent = state.selectedDroneId || 'ALL ASSETS';
}

// Select first drone by default
state.selectedDroneId = assets[0].id;

// Event timer
let nextEventTime = performance.now() + rand(EVENT_INTERVAL_MIN, EVENT_INTERVAL_MAX);

// Seed initial events
for (let i = 0; i < 6; i++) {
  addEvent(generateActivity(assets));
}

// Last update tracking for 1s tick
let lastTickTime = 0;
let lastFrameTime = performance.now();
let replayTickCounter = 0;
let lastFpvUpdate = 0;
let lastSoloUpdate = 0;

// Coverage calculation -- percentage of AO area being surveilled
function calcCoverage() {
  const leader = drones[0];
  let totalDeviation = 0;
  let count = 0;
  const spacing = 0.00035;
  const hr = degToRad(leader.heading);
  const alongDir = { lat: Math.cos(hr + Math.PI / 2), lng: Math.sin(hr + Math.PI / 2) };
  const crossDir = { lat: -Math.sin(hr + Math.PI / 2), lng: Math.cos(hr + Math.PI / 2) };

  for (let i = 1; i < drones.length; i++) {
    const d = drones[i];
    const expectedLat = leader.lat + d.patternOffset.along * spacing * alongDir.lat + d.patternOffset.cross * spacing * crossDir.lat;
    const expectedLng = leader.lng + d.patternOffset.along * spacing * alongDir.lng + d.patternOffset.cross * spacing * crossDir.lng;
    const dLat = d.lat - expectedLat;
    const dLng = d.lng - expectedLng;
    const dist = Math.sqrt(dLat * dLat + dLng * dLng);
    totalDeviation += dist;
    count++;
  }
  const avgDev = totalDeviation / count;
  return clamp(100 - (avgDev / spacing) * 100, 70, 100);
}

/* ==============================================================
   FPV SUBSYSTEMS
   ============================================================== */

// ---- ISR Feed Manager ----
class ISRFeedManager {
  constructor() {
    this.videoEl = document.getElementById('fpv-video');
    this.mjpegEl = document.getElementById('fpv-mjpeg');
    this.canvasEl = document.getElementById('fpv-canvas');
    this.panelEl = document.getElementById('video-panel');
    this.activeMode = 'canvas';
    this.recording = false;
    this.mediaRecorder = null;
    this.recordedChunks = [];
    this._testPatternId = null;
    this._testFrame = 0;
  }

  startTestPattern() {
    this.activeMode = 'canvas';
    this.videoEl.style.display = 'none';
    this.mjpegEl.style.display = 'none';
    this.canvasEl.style.display = '';
    const ctx = this.canvasEl.getContext('2d');
    const w = 640, h = 480;
    const draw = () => {
      this._testFrame++;
      ctx.fillStyle = _css('--bg');
      ctx.fillRect(0, 0, w, h);
      // Grid lines
      ctx.strokeStyle = _cssRgba('--text-dim', 0.08);
      ctx.lineWidth = 1;
      for (let x = 0; x < w; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
      for (let y = 0; y < h; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
      // Crosshair
      ctx.strokeStyle = _cssRgba('--text-dim', 0.3);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(w/2 - 20, h/2); ctx.lineTo(w/2 + 20, h/2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(w/2, h/2 - 20); ctx.lineTo(w/2, h/2 + 20); ctx.stroke();
      // Text
      ctx.font = '16px ' + _css('--font-data');
      ctx.fillStyle = _css('--text-secondary');
      ctx.textAlign = 'center';
      ctx.fillText('OVERWATCH ISR TEST PATTERN', w/2, h/2 - 50);
      ctx.font = '12px ' + _css('--font-data');
      ctx.fillStyle = _css('--text-tertiary');
      ctx.fillText('No video source connected', w/2, h/2 + 50);
      ctx.fillText('Frame ' + this._testFrame, w/2, h/2 + 70);
      const t = new Date();
      ctx.fillStyle = _css('--text-secondary');
      ctx.fillText(t.toISOString().substring(11, 19) + 'Z', w/2, h/2 + 90);
      this._testPatternId = requestAnimationFrame(draw);
    };
    draw();
  }

  stopTestPattern() {
    if (this._testPatternId) { cancelAnimationFrame(this._testPatternId); this._testPatternId = null; }
  }

  connectMJPEG(url) {
    this.stopTestPattern();
    this.mjpegEl.src = url;
    this.mjpegEl.style.display = '';
    this.videoEl.style.display = 'none';
    this.canvasEl.style.display = 'none';
    this.activeMode = 'mjpeg';
  }

  toggleFullscreen() {
    if (this.panelEl.classList.contains('fullscreen')) {
      this.panelEl.classList.remove('fullscreen');
    } else {
      this.panelEl.classList.remove('pip');
      this.panelEl.classList.add('fullscreen');
    }
  }

  togglePIP() {
    if (this.panelEl.classList.contains('pip')) {
      this.panelEl.classList.remove('pip');
      if (state.currentMode !== 'ISR') this.panelEl.style.display = 'none';
    } else {
      this.panelEl.classList.remove('fullscreen');
      this.panelEl.classList.add('pip');
      this.panelEl.style.display = '';
    }
  }

  startRecording() {
    try {
      const stream = this.canvasEl.captureStream(30);
      this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
      this.recordedChunks = [];
      this.mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) this.recordedChunks.push(e.data); };
      this.mediaRecorder.start(1000);
      this.recording = true;
    } catch (e) {
      showToast('Recording not supported in this browser', 'warning');
    }
  }

  stopRecording() {
    if (!this.mediaRecorder) return;
    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.recordedChunks, { type: 'video/webm' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-recording-' + Date.now() + '.webm'; a.click();
      URL.revokeObjectURL(url);
    };
    this.mediaRecorder.stop();
    this.recording = false;
  }

  capturePhoto() {
    const canvas = document.createElement('canvas');
    const src = this.canvasEl;
    canvas.width = src.width; canvas.height = src.height;
    canvas.getContext('2d').drawImage(src, 0, 0);
    canvas.toBlob(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-capture-' + Date.now() + '.jpg'; a.click();
      URL.revokeObjectURL(url);
    }, 'image/jpeg', 0.95);
    showToast('Photo captured');
  }
}

// ---- ISR Overlay Renderer ----
class ISROverlayRenderer {
  constructor() {
    this.els = {
      battery: document.getElementById('osd-battery'),
      cell: document.getElementById('osd-cell'),
      mah: document.getElementById('osd-mah'),
      timer: document.getElementById('osd-timer'),
      armTimer: document.getElementById('osd-arm-timer'),
      gps: document.getElementById('osd-gps'),
      alt: document.getElementById('osd-alt'),
      speed: document.getElementById('osd-speed'),
      rssi: document.getElementById('osd-rssi'),
      home: document.getElementById('osd-home'),
      mode: document.getElementById('osd-mode'),
      warning: document.getElementById('osd-warning'),
    };
  }

  update(drone) {
    if (!drone) return;
    const f = drone.fpvData || {};
    const fmt = (v, d) => (v || 0).toFixed(d);

    this.els.battery.textContent = fmt(drone.voltage, 1) + 'V  ' + fmt(drone.battery, 0) + '%';
    this.els.battery.style.color = drone.battery < 25 ? _css('--red') : drone.battery < 50 ? _css('--amber') : _css('--text-bright');
    this.els.cell.textContent = fmt(f.cell_voltage || drone.voltage / 6, 2) + 'V/cell';
    this.els.mah.textContent = (f.mah_consumed || 0).toFixed(0) + ' mAh';

    const fs = f.flight_timer_s || 0;
    this.els.timer.textContent = Math.floor(fs / 60) + ':' + String(Math.floor(fs % 60)).padStart(2, '0');
    const as = f.arm_timer_s || 0;
    this.els.armTimer.textContent = 'ARM ' + Math.floor(as / 60) + ':' + String(Math.floor(as % 60)).padStart(2, '0');

    this.els.gps.textContent = drone.lat.toFixed(6) + '  ' + drone.lng.toFixed(6) + '  ' + drone.satellites + 'SAT';
    this.els.alt.textContent = fmt(drone.altitude, 1) + 'm';
    this.els.speed.textContent = fmt(drone.speed, 1) + 'm/s';
    this.els.rssi.textContent = 'RSSI ' + fmt(drone.rssi, 0);
    this.els.rssi.style.color = drone.rssi < 50 ? _css('--red') : drone.rssi < 70 ? _css('--amber') : _css('--text-bright');

    const hd = f.home_distance_m || 0;
    const hdir = f.home_direction_deg || 0;
    const dirs = ['N','NE','E','SE','S','SW','W','NW'];
    const compass = dirs[Math.round(((hdir % 360) + 360) % 360 / 45) % 8];
    this.els.home.textContent = hd.toFixed(0) + 'm ' + compass;
    this.els.mode.textContent = f.flight_mode || 'STABILIZE';

    // Artificial horizon rotation
    const horizonEl = document.getElementById('osd-horizon');
    if (horizonEl) {
      const rollDeg = drone.roll || 0;
      const pitchOffset = (drone.pitch || 0) * 1.5; // pixels per degree
      horizonEl.style.transform = 'translate(-50%, calc(-50% + ' + pitchOffset + 'px)) rotate(' + (-rollDeg) + 'deg)';
    }

    // Camera tilt readout
    const tiltEl = document.getElementById('osd-tilt');
    if (tiltEl) {
      const tiltVal = (drone.fpvData && drone.fpvData.camera_tilt) || 0;
      tiltEl.textContent = 'TILT ' + tiltVal + '\u00B0';
    }

    // HDOP readout
    const hdopEl = document.getElementById('osd-hdop');
    if (hdopEl) {
      hdopEl.textContent = 'HDOP ' + (drone.hdop || 0).toFixed(1);
      hdopEl.style.color = drone.hdop > 2.0 ? _css('--red') : drone.hdop > 1.5 ? _css('--amber') : '';
    }

    // Enhanced warnings
    const warnings = [];
    if (drone.battery < 20) warnings.push('LOW BATTERY ' + drone.battery.toFixed(0) + '%');
    if (drone.rssi < 50) warnings.push('LOW SIGNAL');
    if (drone.satellites < 8) warnings.push('GPS DEGRADED');
    if (drone.hdop > 2.0) warnings.push('GPS ACCURACY LOW');

    if (warnings.length > 0) {
      this.els.warning.textContent = warnings[0];
      this.els.warning.style.display = '';
      this.els.warning.style.color = _css('--red');
    } else {
      this.els.warning.style.display = 'none';
    }
  }
}

// ---- Recording Manager ----
class RecordingManager {
  constructor(videoMgr) {
    this.videoMgr = videoMgr;
    this.recording = false;
    this.startTime = 0;
    this.telemLog = [];
  }

  start() {
    this.videoMgr.startRecording();
    this.recording = true;
    this.startTime = performance.now();
    this.telemLog = [];
    document.getElementById('dvr-badge').style.display = '';
    document.getElementById('video-record').style.background = 'rgba(205,66,70,0.2)';
    showToast('DVR recording started');
  }

  stop() {
    this.videoMgr.stopRecording();
    this.recording = false;
    document.getElementById('dvr-badge').style.display = 'none';
    document.getElementById('video-record').style.background = '';
    showToast('DVR recording saved');
    // Export telemetry log
    if (this.telemLog.length > 0) {
      const blob = new Blob([JSON.stringify(this.telemLog, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-telem-' + Date.now() + '.json'; a.click();
      URL.revokeObjectURL(url);
    }
  }

  logTelemetry(drone) {
    if (!this.recording || !drone) return;
    this.telemLog.push({
      t: ((performance.now() - this.startTime) / 1000).toFixed(2),
      lat: drone.lat, lng: drone.lng, alt: drone.altitude,
      spd: drone.speed, hdg: drone.heading, bat: drone.battery,
      rssi: drone.rssi, roll: drone.roll, pitch: drone.pitch,
    });
  }

  updateTimer() {
    if (!this.recording) return;
    const s = (performance.now() - this.startTime) / 1000;
    document.getElementById('dvr-timer').textContent =
      'REC ' + Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  }
}

// ---- Initialize ISR subsystems ----
const videoMgr = new ISRFeedManager();
const osdRenderer = new ISROverlayRenderer();
const dvrMgr = new RecordingManager(videoMgr);

// ---- Debrief analytics tracking ----
const debriefTracker = {
  startTime: Date.now(),
  totalDistance: 0,
  lastPositions: {},
  keyEvents: [],
  addKeyEvent(msg, severity) {
    const elapsed = ((Date.now() - this.startTime) / 1000).toFixed(0);
    const mins = Math.floor(elapsed / 60);
    const secs = String(elapsed % 60).padStart(2, '0');
    this.keyEvents.push({ time: mins + ':' + secs, msg, severity });
    if (this.keyEvents.length > 30) this.keyEvents.shift();
  },
  trackDistance(droneId, lat, lng) {
    if (this.lastPositions[droneId]) {
      const dLat = (lat - this.lastPositions[droneId].lat) * 111320;
      const dLng = (lng - this.lastPositions[droneId].lng) * 111320 * Math.cos(lat * Math.PI / 180);
      this.totalDistance += Math.sqrt(dLat * dLat + dLng * dLng);
    }
    this.lastPositions[droneId] = { lat, lng };
  },
};

// Wrap cmdEngine.execute to track key events for debrief analytics
const _origExecute = cmdEngine.execute.bind(cmdEngine);
cmdEngine.execute = function(cmd, params) {
  _origExecute(cmd, params);
  const severityMap = { ARM: 'info', DISARM: 'info', TAKEOFF: 'ok', LAND: 'warn', RTB: 'warn', GOTO: 'info', EMERGENCY_STOP: 'alert', EXECUTE_MISSION: 'ok', SET_PATTERN: 'info' };
  debriefTracker.addKeyEvent(cmd + (params && params.droneId ? ' -> ' + params.droneId : ''), severityMap[cmd] || 'info');
};

function updateDebriefPanel() {
  const elapsedSec = (Date.now() - debriefTracker.startTime) / 1000;
  const mins = Math.floor(elapsedSec / 60);
  const secs = Math.floor(elapsedSec % 60);
  const ftEl = document.getElementById('debrief-flight-time');
  if (ftEl) ftEl.textContent = mins + ':' + String(secs).padStart(2, '0');

  const dirEl = document.getElementById('debrief-directives');
  if (dirEl) dirEl.textContent = cmdEngine.log.length;

  const avgBat = drones.reduce((s, d) => s + d.battery, 0) / drones.length;
  const abEl = document.getElementById('debrief-avg-batt');
  if (abEl) abEl.innerHTML = avgBat.toFixed(0) + '<span class="debrief-stat-unit">%</span>';

  const avgSpd = drones.reduce((s, d) => s + d.speed, 0) / drones.length;
  const asEl = document.getElementById('debrief-avg-speed');
  if (asEl) asEl.innerHTML = avgSpd.toFixed(1) + '<span class="debrief-stat-unit">m/s</span>';

  const distKm = debriefTracker.totalDistance / 1000;
  const distEl = document.getElementById('debrief-distance');
  if (distEl) distEl.innerHTML = distKm.toFixed(2) + '<span class="debrief-stat-unit">km</span>';

  const evEl = document.getElementById('debrief-events');
  if (evEl) evEl.textContent = state.activityStream.length;

  // Key events
  const keEl = document.getElementById('debrief-key-events');
  if (keEl) {
    if (debriefTracker.keyEvents.length === 0) {
      keEl.innerHTML = '<div style="font-family:var(--font-data);font-size:10px;color:var(--text-secondary)">No key events yet</div>';
    } else {
      keEl.innerHTML = debriefTracker.keyEvents.slice().reverse().map(e => {
        const dotColor = e.severity === 'ok' ? 'var(--green)' : e.severity === 'warn' ? 'var(--amber)' : e.severity === 'alert' ? 'var(--red)' : 'var(--accent)';
        return '<div class="debrief-timeline-item"><span class="debrief-timeline-time">' + e.time + '</span>' +
          '<span class="debrief-timeline-dot" style="background:' + dotColor + '"></span>' +
          '<span class="debrief-timeline-msg">' + e.msg + '</span></div>';
      }).join('');
    }
  }

  // Per-asset performance
  const perfEl = document.getElementById('debrief-asset-perf');
  if (perfEl) {
    perfEl.innerHTML = drones.map(d => {
      const bc = batteryColor(d.battery);
      return '<div class="debrief-asset-row">' +
        '<span style="width:6px;height:6px;border-radius:50%;background:' + d.color + ';flex-shrink:0"></span>' +
        '<span class="debrief-asset-name">' + d.id + '</span>' +
        '<div class="debrief-asset-bar"><div class="debrief-asset-bar-fill" style="width:' + d.battery + '%;background:' + bc + '"></div></div>' +
        '<span class="debrief-asset-pct" style="color:' + bc + '">' + d.battery.toFixed(0) + '%</span></div>';
    }).join('');
  }

  // Replay state indicator
  const stateEl = document.getElementById('debrief-replay-state');
  if (stateEl) {
    if (replaySystem.playing) {
      stateEl.className = 'debrief-replay-indicator playing';
      stateEl.textContent = 'REPLAYING';
    } else if (replaySystem.recording) {
      stateEl.className = 'debrief-replay-indicator recording';
      stateEl.textContent = 'RECORDING';
    } else {
      stateEl.className = 'debrief-replay-indicator paused';
      stateEl.textContent = 'PAUSED';
    }
  }
}

/* ==============================================================
   ANIMATION LOOP
   ============================================================== */
function frame(now) {
  requestAnimationFrame(frame);
  try {

  const dtMs = now - lastFrameTime;
  lastFrameTime = now;
  const dtSec = Math.min(dtMs / 1000, 0.1); // cap dt to avoid huge jumps

  // Update UTC clock every frame
  document.getElementById('utc-clock').textContent = utcString();

  // Replay mode: tick replay instead of simulation
  if (state.currentMode === 'DEBRIEF' && replaySystem.playing) {
    replayTickCounter++;
    if (replayTickCounter % 6 === 0) replaySystem.tick();
  } else if (state.currentMode === 'DEBRIEF' && !replaySystem.recording) {
    // Paused in debrief -- freeze, do nothing
  } else {
    // Update drone simulation (only in OBSERVE/TASK modes, or DEBRIEF while recording)
    const leader = drones[0];
    leader.update(dtSec, 0, 0, 0);
    for (let i = 1; i < drones.length; i++) {
      drones[i].update(dtSec, leader.heading, leader.lat, leader.lng);
    }
  }

  // Record replay frames
  replaySystem.recordFrame(now);

  // Update map markers and sensor FOV cones every frame (smooth movement)
  drones.forEach(d => {
    updateMapMarker(d);
    updateFovCone(d);
    // Update pulse ring position for selected drone
    if (d.id === state.selectedDroneId && state.mapMarkers[d.id] && state.mapMarkers[d.id]._pulseRing) {
      state.mapMarkers[d.id]._pulseRing.setLatLng([d.lat, d.lng]);
    }
  });
  updateFormationLines(drones);

  // 1-second tick for heavier UI updates
  if (now - lastTickTime >= 1000) {
    lastTickTime = now;

    // Render drone list
    updateAssetExplorer(drones);

    // Render inspector (OBSERVE mode)
    if (state.currentMode === 'OBSERVE') {
      const selected = drones.find(d => d.id === state.selectedDroneId);
      updateInspector(selected || null);
    }

    // Update command target
    updateCmdTarget();

    // Coverage
    const coverage = calcCoverage();
    document.getElementById('coverage-value').textContent = coverage.toFixed(0);

    // Bottom metrics
    // TF Health
    const avgBat = drones.reduce((s, d) => s + d.battery, 0) / drones.length;
    const allNominal = drones.every(d => d.status === 'NOMINAL' || d.status === 'FLYING');
    const healthPct = allNominal ? clamp(95 + rand(0, 5), 90, 100) : clamp(70 + avgBat * 0.3, 50, 95);
    document.getElementById('bm-health').textContent = healthPct.toFixed(0);

    // Commlink
    const avgLat = drones.reduce((s, d) => s + d.latency, 0) / drones.length;
    document.getElementById('bm-latency').textContent = avgLat.toFixed(0);

    const meshOk = avgLat < 40;
    const meshBadge = document.getElementById('mesh-badge');
    meshBadge.textContent = meshOk ? 'NOMINAL' : 'DEGRADED';
    meshBadge.style.background = meshOk ? _cssRgba('--green', 0.12) : _cssRgba('--amber', 0.15);
    meshBadge.style.color = meshOk ? 'var(--green)' : 'var(--amber)';

    // Power State
    const minBat = Math.min(...drones.map(d => d.battery));
    const maxBat = Math.max(...drones.map(d => d.battery));
    document.getElementById('bm-battery').textContent = avgBat.toFixed(0);
    document.getElementById('bm-bat-spread').textContent = `Range: ${minBat.toFixed(0)} - ${maxBat.toFixed(0)}%`;

    // Sparklines
    sparkHealth.push(healthPct);
    sparkLatency.push(avgLat);
    sparkBattery.push(avgBat);

    // Sensor coverage estimation
    const activeSensors = drones.filter(d => d.altitude > 10 && (d.status === 'NOMINAL' || d.status === 'FLYING')).length;
    const baseCov = (activeSensors / drones.length) * 100;
    // Add slight variance for realism, capped at useful range
    const coveragePct = clamp(baseCov * (0.85 + Math.random() * 0.15), 0, 100);
    document.getElementById('bm-coverage').textContent = coveragePct.toFixed(0);
    document.getElementById('bm-cov-assets').textContent = activeSensors + ' / ' + drones.length + ' sensors';
    const covBadge = document.getElementById('cov-badge');
    if (activeSensors === drones.length) {
      covBadge.textContent = 'FULL';
      covBadge.style.background = 'rgba(35,133,81,0.12)';
      covBadge.style.color = 'var(--green)';
    } else if (activeSensors >= drones.length * 0.5) {
      covBadge.textContent = 'PARTIAL';
      covBadge.style.background = 'rgba(200,118,25,0.12)';
      covBadge.style.color = 'var(--amber)';
    } else {
      covBadge.textContent = 'DEGRADED';
      covBadge.style.background = 'rgba(205,66,70,0.12)';
      covBadge.style.color = 'var(--red)';
    }
    sparkCoverage.push(coveragePct);

    // Draw sparklines via requestIdleCallback to avoid blocking the main thread
    if (typeof requestIdleCallback !== 'undefined') {
      requestIdleCallback(() => {
        sparkHealth.draw();
        sparkLatency.draw();
        sparkBattery.draw();
        if (sparkCoverage) sparkCoverage.draw();
      }, { timeout: 500 });
    } else {
      sparkHealth.draw();
      sparkLatency.draw();
      sparkBattery.draw();
      if (sparkCoverage) sparkCoverage.draw();
    }

    // Track distance for debrief
    drones.forEach(d => {
      if (d.altitude > 1) {
        debriefTracker.trackDistance(d.id, d.lat, d.lng);
      }
    });

    // Update debrief panel
    if (state.currentMode === 'DEBRIEF') {
      updateDebriefPanel();
    }

    // Update operations elapsed
    const opsElapsed = document.getElementById('ops-elapsed');
    if (opsElapsed) {
      const opsSec = Math.floor((Date.now() - debriefTracker.startTime) / 1000);
      const opsMin = Math.floor(opsSec / 60);
      opsElapsed.textContent = opsMin + ':' + String(opsSec % 60).padStart(2, '0');
    }

    // Update objectives completed count
    const opsObjCount = document.getElementById('ops-obj-count');
    if (opsObjCount) {
      opsObjCount.textContent = state.objectivesCompleted;
    }

    // Diagnostics + insights are already updated by updateInspector

    // Update arm/disarm visual state based on actual drone state
    const selDrone = drones.find(d => d.id === state.selectedDroneId);
    if (selDrone) {
      const isArmed = selDrone.armed || selDrone.droneState === 'FLYING' || selDrone.droneState === 'ARMED' ||
                       selDrone.droneState === 'TAKING_OFF' || selDrone.droneState === 'GOTO' || selDrone.droneState === 'MISSION';
      if (armedState !== isArmed) {
        armedState = isArmed;
        updateArmDisarmVisuals();
      }
    }

    // Inject random failures (rare)
    drones.forEach(d => {
      const failure = d.injectFailure();
      if (failure === 'GPS') addEvent({ time: utcTimeStamp(), source: d.id, msg: 'GPS signal anomaly detected \u2014 HDOP increasing', severity: 'warn' });
      else if (failure === 'BATTERY') addEvent({ time: utcTimeStamp(), source: d.id, msg: 'Abnormal battery drain detected \u2014 monitoring', severity: 'warn' });
      else if (failure === 'COMMS') addEvent({ time: utcTimeStamp(), source: d.id, msg: 'Communication link degradation \u2014 switching antenna', severity: 'warn' });
    });

    // State-correlated event generation
    generateStateCorrelatedEvents(drones);

    // Critical banner check
    const criticalDrone = drones.find(d => d.battery < 20 && d.armed && d.droneState !== 'LANDED');
    const banner = document.getElementById('critical-banner');
    if (criticalDrone && !banner.classList.contains('dismissed')) {
      document.getElementById('critical-msg').textContent =
        criticalDrone.id + ' BATTERY CRITICAL (' + criticalDrone.battery.toFixed(0) + '%) -- RTB RECOMMENDED';
      banner.classList.add('visible');
    } else if (!criticalDrone) {
      banner.classList.remove('visible', 'dismissed');
    }
  }

  // Random events
  if (now >= nextEventTime) {
    addEvent(generateActivity(assets));
    nextEventTime = now + rand(EVENT_INTERVAL_MIN, EVENT_INTERVAL_MAX);
  }

  // ---- FPV simulation data updates (throttled ~100ms) ----
  if (now - lastFpvUpdate > 100) {
    lastFpvUpdate = now;
    if (state.currentMode === 'ISR') {
      drones.forEach(d => {
        if (!d.isLive) {
          const f = d.fpvData;
          f.mah_consumed += Math.abs(d.current) * (1/10) / 3.6;
          f.cell_voltage = d.voltage / 6;
          if (d.droneState !== DRONE_STATES.LANDED && d.droneState !== DRONE_STATES.IDLE && d.armed) {
            f.flight_timer_s += 0.1;
          }
          if (d.armed) {
            f.arm_timer_s += 0.1;
          }
          const dlat = (d.lat - CENTER_LAT) * 111320;
          const dlng = (d.lng - CENTER_LNG) * 111320 * Math.cos(d.lat * Math.PI / 180);
          f.home_distance_m = Math.sqrt(dlat * dlat + dlng * dlng);
          f.home_direction_deg = ((Math.atan2(-dlng, -dlat) * 180 / Math.PI) + 360) % 360;
        }
      });
    }
  }

  // ---- SOLO mode update (throttled ~100ms) ----
  if (now - lastSoloUpdate > 100) {
    lastSoloUpdate = now;
    if (state.currentMode === 'ISR') {
      // ISR asset selector pills
      const pillContainer = document.getElementById('isr-asset-pills');
      if (pillContainer && pillContainer.children.length === 0) {
        drones.forEach(d => {
          const pill = document.createElement('button');
          pill.className = 'fpv-mode-btn';
          pill.style.cssText = 'font-size:9px;padding:2px 6px;min-width:0';
          pill.textContent = d.id.split('-')[0]; // ALPHA, BRAVO, etc.
          pill.dataset.assetId = d.id;
          pill.addEventListener('click', () => selectDrone(d.id));
          pillContainer.appendChild(pill);
        });
      }
      // Update active state
      if (pillContainer) {
        pillContainer.querySelectorAll('.fpv-mode-btn').forEach(p => {
          p.classList.toggle('active', p.dataset.assetId === state.selectedDroneId);
        });
      }

      const sel = drones.find(d => d.id === state.selectedDroneId) || drones[0];
      if (sel) {
        // Update OSD
        osdRenderer.update(sel);

        // Update DVR
        dvrMgr.logTelemetry(sel);
        dvrMgr.updateTimer();

        // Update right panel telemetry
        const f = sel.fpvData || {};
        const $ = id => document.getElementById(id);
        $('fpv-bat').textContent = sel.battery.toFixed(0) + '%';
        $('fpv-bat').style.color = sel.battery < 25 ? 'var(--red)' : sel.battery < 50 ? 'var(--amber)' : 'var(--green)';
        $('fpv-cell').textContent = (f.cell_voltage || sel.voltage / 6).toFixed(2) + 'V';
        $('fpv-alt').textContent = sel.altitude.toFixed(1) + 'm';
        $('fpv-spd').textContent = sel.speed.toFixed(1) + 'm/s';
        $('fpv-rssi').textContent = sel.rssi.toFixed(0);
        $('fpv-rssi').style.color = sel.rssi < 50 ? 'var(--red)' : sel.rssi < 70 ? 'var(--amber)' : 'var(--green)';
        $('fpv-mah').textContent = (f.mah_consumed || 0).toFixed(0);
        $('fpv-home').textContent = (f.home_distance_m || 0).toFixed(0) + 'm';
        $('fpv-sats').textContent = sel.satellites;

        // Cell voltage bars
        const cellBarsEl = document.getElementById('fpv-cell-bars');
        if (cellBarsEl) {
          const ds = getDiagState(sel.id);
          if (ds && ds.cellVoltages) {
            cellBarsEl.innerHTML = ds.cellVoltages.map((v, i) => {
              const pct = Math.max(0, Math.min(100, ((v - 3.3) / (4.2 - 3.3)) * 100));
              const c = v < 3.5 ? _css('--red') : v < 3.7 ? _css('--amber') : _css('--green');
              return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:1px">' +
                '<div style="width:100%;background:rgba(255,255,255,0.06);border-radius:1px;height:24px;position:relative;overflow:hidden">' +
                '<div style="position:absolute;bottom:0;width:100%;height:' + pct + '%;background:' + c + ';border-radius:1px;transition:height 0.3s"></div></div>' +
                '<span style="font-family:var(--font-data);font-size:8px;color:' + c + '">' + v.toFixed(2) + '</span></div>';
            }).join('');
          }
        }

        // Failsafe indicators
        const fsEl = document.getElementById('fpv-failsafe-items');
        if (fsEl) {
          const items = [];
          // Battery failsafe
          const batPct = sel.battery;
          const batStatus = batPct > 30 ? 'ok' : batPct > 20 ? 'warn' : 'crit';
          const batColor = batStatus === 'ok' ? _css('--green') : batStatus === 'warn' ? _css('--amber') : _css('--red');
          items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + batColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">RTB Battery</span><span style="font-family:var(--font-data);font-size:10px;color:' + batColor + ';margin-left:auto">' + (batStatus === 'crit' ? 'TRIGGERED' : batStatus === 'warn' ? 'WARNING' : 'OK') + '</span></div>');

          // GPS failsafe
          const gpsStatus = sel.satellites >= 8 ? 'ok' : sel.satellites >= 5 ? 'warn' : 'crit';
          const gpsColor = gpsStatus === 'ok' ? _css('--green') : gpsStatus === 'warn' ? _css('--amber') : _css('--red');
          items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + gpsColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">GPS Lock</span><span style="font-family:var(--font-data);font-size:10px;color:' + gpsColor + ';margin-left:auto">' + sel.satellites + ' sats</span></div>');

          // Signal failsafe
          const sigStatus = sel.linkQuality > 80 ? 'ok' : sel.linkQuality > 60 ? 'warn' : 'crit';
          const sigColor = sigStatus === 'ok' ? _css('--green') : sigStatus === 'warn' ? _css('--amber') : _css('--red');
          items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + sigColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">RC Link</span><span style="font-family:var(--font-data);font-size:10px;color:' + sigColor + ';margin-left:auto">' + sel.linkQuality.toFixed(0) + '%</span></div>');

          // Home distance
          const homeDist = sel.fpvData ? sel.fpvData.home_distance_m : 0;
          const homeStatus = homeDist < 500 ? 'ok' : homeDist < 1000 ? 'warn' : 'crit';
          const homeColor = homeStatus === 'ok' ? _css('--green') : homeStatus === 'warn' ? _css('--amber') : _css('--red');
          items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + homeColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">Home Dist</span><span style="font-family:var(--font-data);font-size:10px;color:' + homeColor + ';margin-left:auto">' + homeDist.toFixed(0) + 'm</span></div>');

          fsEl.innerHTML = items.join('');
        }

        // Stick input visualization (simulated from drone attitude)
        const drawStick = (canvasId, x, y) => {
          const c = document.getElementById(canvasId);
          if (!c) return;
          const ctx = c.getContext('2d');
          const dpr = window.devicePixelRatio || 1;
          c.width = 64 * dpr; c.height = 64 * dpr;
          ctx.scale(dpr, dpr);
          ctx.clearRect(0, 0, 64, 64);
          // Grid lines
          ctx.strokeStyle = _cssRgba('--text-bright', 0.08);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(32, 0); ctx.lineTo(32, 64); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, 32); ctx.lineTo(64, 32); ctx.stroke();
          // Stick position
          const px = 32 + x * 28;
          const py = 32 - y * 28;
          ctx.beginPath();
          ctx.arc(px, py, 6, 0, Math.PI * 2);
          ctx.fillStyle = _cssRgba('--accent', 0.8);
          ctx.fill();
          ctx.strokeStyle = _cssRgba('--accent', 0.4);
          ctx.lineWidth = 1;
          ctx.stroke();
        };

        // Left stick: throttle (vertical) from speed ratio, yaw from heading change
        const throttle = sel.speed / 15; // normalize to 0-1
        const yaw = Math.sin(sel.heading * Math.PI / 180) * 0.3;
        drawStick('stick-left', yaw, throttle - 0.5);

        // Right stick: roll and pitch from attitude
        const rollNorm = (sel.roll || 0) / 30; // normalize +/-30 to +/-1
        const pitchNorm = (sel.pitch || 0) / 20; // normalize +/-20 to +/-1
        drawStick('stick-right', rollNorm, -pitchNorm);

        // Motor status
        const motorEl = document.getElementById('fpv-motor-status');
        if (motorEl) {
          const ds = getDiagState(sel.id);
          if (ds && ds.motors) {
            motorEl.innerHTML = ds.motors.map((m, i) => {
              const tempColor = m.temp > 50 ? _css('--red') : m.temp > 45 ? _css('--amber') : _css('--green');
              const healthColor = m.health >= 90 ? _css('--green') : m.health >= 75 ? _css('--amber') : _css('--red');
              return '<div style="text-align:center;padding:3px;background:rgba(0,0,0,0.2);border-radius:1px">' +
                '<div style="font-family:var(--font-data);font-size:9px;color:var(--text-dim)">M' + (i + 1) + '</div>' +
                '<div style="font-family:var(--font-data);font-size:10px;color:' + healthColor + '">' + m.rpm.toFixed(0) + '</div>' +
                '<div style="font-family:var(--font-data);font-size:8px;color:' + tempColor + '">' + m.temp.toFixed(0) + '</div></div>';
            }).join('');
          }
        }

        // Video link info
        const vl = f.video_link || {};
        $('fpv-vq').textContent = (vl.quality || 0) + '%';
        $('fpv-vch').textContent = 'CH' + (vl.channel || '-');
        $('fpv-vfreq').textContent = (vl.frequency_mhz || 0) + ' MHz';
        $('fpv-proto').textContent = f.protocol || 'MAVLINK';

        // Update active flight mode button
        document.querySelectorAll('.fpv-mode-btn').forEach(b => {
          b.classList.toggle('active', b.dataset.fmode === (f.flight_mode || 'STABILIZE'));
        });
      }
    }
  }

  } catch(e) {
    console.error('Frame error:', e);
  }
}

// Kick off
requestAnimationFrame(frame);

// ---- Audio Toggle ----
document.getElementById('audio-toggle').addEventListener('click', function() {
  state.audioMuted = !state.audioMuted;
  this.style.opacity = state.audioMuted ? '0.4' : '1';
  this.textContent = state.audioMuted ? 'MUTE' : 'SND';
});

// ---- Critical Banner Dismiss / RTB ----
document.getElementById('critical-dismiss').addEventListener('click', () => {
  document.getElementById('critical-banner').classList.remove('visible');
  document.getElementById('critical-banner').classList.add('dismissed');
});
document.getElementById('critical-rtb').addEventListener('click', () => {
  const crit = drones.find(d => d.battery < 20 && d.armed);
  if (crit) cmdEngine.execute('RTB', { droneId: crit.id });
  document.getElementById('critical-banner').classList.remove('visible');
});

// ---- Quick Connect ----
document.getElementById('quick-connect-btn').addEventListener('click', async () => {
  const btn = document.getElementById('quick-connect-btn');
  btn.classList.add('scanning');
  btn.textContent = 'SCANNING...';
  try {
    // Try connecting to common local URLs
    const urls = ['ws://localhost:8765/ws/v1/stream', 'ws://localhost:8080/ws/v1/stream'];
    let connected = false;
    for (const url of urls) {
      try {
        connManager.connect(url);
        connected = true;
        showToast('Connecting to ' + url);
        break;
      } catch (e) { continue; }
    }
    if (!connected) showToast('No platform backend detected', 'warning');
  } finally {
    setTimeout(() => { btn.classList.remove('scanning'); btn.textContent = 'CONNECT'; }, 2000);
  }
});

// ---- Video controls ----
document.getElementById('video-fullscreen').addEventListener('click', () => videoMgr.toggleFullscreen());
document.getElementById('video-pip').addEventListener('click', () => videoMgr.togglePIP());
document.getElementById('video-record').addEventListener('click', () => {
  if (dvrMgr.recording) dvrMgr.stop(); else dvrMgr.start();
});
document.getElementById('video-photo').addEventListener('click', () => videoMgr.capturePhoto());

// Camera tilt
document.getElementById('camera-tilt').addEventListener('input', function() {
  document.getElementById('tilt-value').innerHTML = this.value + '&deg;';
  if (connManager.ws && connManager.connected) {
    connManager.ws.send(JSON.stringify({ type: 'CMD', command: 'CAMERA_TILT', params: { angle: parseInt(this.value) } }));
  }
});

// Video source selector
document.getElementById('video-source').addEventListener('change', function() {
  if (this.value === 'canvas') {
    videoMgr.stopTestPattern();
    videoMgr.startTestPattern();
  } else if (this.value) {
    videoMgr.connectMJPEG(this.value);
  }
});

// ---- FPV ARM/DISARM ----
document.getElementById('fpv-arm').addEventListener('click', () => {
  cmdEngine.execute('ARM', { droneId: state.selectedDroneId });
});
document.getElementById('fpv-disarm').addEventListener('click', () => {
  cmdEngine.execute('DISARM', { droneId: state.selectedDroneId });
});
document.getElementById('fpv-estop').addEventListener('click', function() {
  const btn = this;
  if (btn.classList.contains('confirming')) {
    // Second click within timeout -- execute
    clearTimeout(btn._confirmTimer);
    btn.classList.remove('confirming');
    btn.textContent = 'ABORT';
    cmdEngine.execute('EMERGENCY_STOP', { droneId: state.selectedDroneId });
  } else {
    // First click -- enter confirm state
    btn.classList.add('confirming');
    btn.textContent = 'CONFIRM (3s)';
    btn._confirmTimer = setTimeout(() => {
      btn.classList.remove('confirming');
      btn.textContent = 'ABORT';
    }, 3000);
  }
});

// ---- Flight mode buttons ----
document.querySelectorAll('.fpv-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.fmode;
    document.querySelectorAll('.fpv-mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const sel = drones.find(d => d.id === state.selectedDroneId);
    if (sel) sel.fpvData.flight_mode = mode;
    if (connManager.ws && connManager.connected) {
      connManager.ws.send(JSON.stringify({ type: 'CMD', command: 'MSP_SET_MODE', params: { mode } }));
    }
    showToast('Flight mode: ' + mode);
  });
});

// ---- Collection pattern overlay click to cycle ----
const patternOrder = ['ORBIT', 'RACETRACK', 'SEARCH_GRID', 'POINT_STARE'];
let currentPatternIndex = 0;
document.getElementById('pattern-overlay').addEventListener('click', () => {
  currentPatternIndex = (currentPatternIndex + 1) % patternOrder.length;
  const pattern = patternOrder[currentPatternIndex];
  cmdEngine.execute('SET_PATTERN', { pattern });
  document.getElementById('cmd-pattern').value = pattern;
});

// ---- Notification badge (Fix #3) ----
const eventLogSection = document.querySelector('.event-log-section');

// Track when user scrolls to bottom of event log
const eventLogEl = document.getElementById('event-log');
if (eventLogEl) {
  eventLogEl.addEventListener('scroll', () => {
    if (eventLogEl.scrollTop + eventLogEl.clientHeight >= eventLogEl.scrollHeight - 20) {
      unreadEventCount = 0;
      if (eventBadge) { eventBadge.textContent = '0'; eventBadge.classList.add('hidden'); }
    }
  });
}

// ---- Map Tools (Geospatial) ----
const mapTools = {
  activeTool: null,
  measurePoints: [],
  measureLayers: L.layerGroup().addTo(state.map),
  areaPoints: [],
  areaLayers: L.layerGroup().addTo(state.map),
  annotationLayers: L.layerGroup().addTo(state.map),

  activate(tool) {
    if (this.activeTool === tool) { this.deactivate(); return; }
    this.deactivate();
    this.activeTool = tool;
    document.querySelectorAll('.map-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === tool));

    if (tool === 'measure') {
      this.measurePoints = [];
      state.map.getContainer().style.cursor = 'crosshair';
      state.map.on('click', this._onMeasureClick, this);
    } else if (tool === 'area') {
      this.areaPoints = [];
      state.map.getContainer().style.cursor = 'crosshair';
      state.map.on('click', this._onAreaClick, this);
      state.map.on('dblclick', this._onAreaDblClick, this);
    } else if (tool === 'annotate') {
      state.map.getContainer().style.cursor = 'crosshair';
      state.map.on('click', this._onAnnotateClick, this);
    }
  },

  deactivate() {
    this.activeTool = null;
    document.querySelectorAll('.map-tool-btn').forEach(b => b.classList.remove('active'));
    state.map.getContainer().style.cursor = '';
    state.map.off('click', this._onMeasureClick, this);
    state.map.off('click', this._onAreaClick, this);
    state.map.off('dblclick', this._onAreaDblClick, this);
    state.map.off('click', this._onAnnotateClick, this);
  },

  clearAll() {
    this.measureLayers.clearLayers();
    this.areaLayers.clearLayers();
    this.annotationLayers.clearLayers();
    this.measurePoints = [];
    this.areaPoints = [];
    this.deactivate();
    showToast('Map layers cleared');
  },

  _onMeasureClick(e) {
    this.measurePoints.push(e.latlng);
    // Add point marker
    L.circleMarker(e.latlng, { radius: 4, color: _css('--accent'), fillColor: _css('--accent'), fillOpacity: 1, weight: 1 }).addTo(this.measureLayers);

    if (this.measurePoints.length === 2) {
      const p1 = this.measurePoints[0];
      const p2 = this.measurePoints[1];
      const distM = state.map.distance(p1, p2);
      const distKm = (distM / 1000).toFixed(2);
      const distNm = (distM / 1852).toFixed(2);

      // Draw line
      const line = L.polyline([p1, p2], { color: _css('--accent'), weight: 2, dashArray: '6,4' }).addTo(this.measureLayers);

      // Add label at midpoint
      const midLat = (p1.lat + p2.lat) / 2;
      const midLng = (p1.lng + p2.lng) / 2;
      L.marker([midLat, midLng], {
        icon: L.divIcon({ className: 'map-measurement-label', html: distKm + ' km / ' + distNm + ' nm', iconSize: null })
      }).addTo(this.measureLayers);

      this.measurePoints = [];
      this.deactivate();
      showToast('Distance: ' + distKm + ' km');
    }
  },

  _onAreaClick(e) {
    this.areaPoints.push(e.latlng);
    L.circleMarker(e.latlng, { radius: 4, color: _css('--amber'), fillColor: _css('--amber'), fillOpacity: 1, weight: 1 }).addTo(this.areaLayers);

    if (this.areaPoints.length > 1) {
      // Draw preview line
      this.areaLayers.eachLayer(l => { if (l instanceof L.Polyline && !(l instanceof L.Polygon)) this.areaLayers.removeLayer(l); });
      L.polyline(this.areaPoints, { color: _css('--amber'), weight: 2, dashArray: '4,4' }).addTo(this.areaLayers);
    }
  },

  _onAreaDblClick(e) {
    e.originalEvent.preventDefault();
    if (this.areaPoints.length < 3) { showToast('Need at least 3 points'); return; }

    // Clear preview layers
    this.areaLayers.clearLayers();

    // Draw polygon
    const polygon = L.polygon(this.areaPoints, { color: _css('--amber'), fillColor: _css('--amber'), fillOpacity: 0.15, weight: 2 }).addTo(this.areaLayers);

    // Calculate area (approximate)
    const latlngs = this.areaPoints;
    let area = 0;
    for (let i = 0; i < latlngs.length; i++) {
      const j = (i + 1) % latlngs.length;
      area += latlngs[i].lng * latlngs[j].lat;
      area -= latlngs[j].lng * latlngs[i].lat;
    }
    area = Math.abs(area) / 2;
    const areaSqKm = (area * 111.32 * 111.32 * Math.cos(latlngs[0].lat * Math.PI / 180)).toFixed(3);

    // Add label at centroid
    const centLat = latlngs.reduce((s, p) => s + p.lat, 0) / latlngs.length;
    const centLng = latlngs.reduce((s, p) => s + p.lng, 0) / latlngs.length;
    L.marker([centLat, centLng], {
      icon: L.divIcon({ className: 'map-measurement-label', html: areaSqKm + ' km\u00B2', iconSize: null })
    }).addTo(this.areaLayers);

    this.areaPoints = [];
    this.deactivate();
    showToast('Area: ' + areaSqKm + ' km\u00B2');
  },

  _onAnnotateClick(e) {
    const self = this;
    const popup = L.popup({ closeButton: true, className: 'map-measurement-label' })
      .setLatLng(e.latlng)
      .setContent('<input id="annotate-input" type="text" placeholder="Label..." style="background:var(--panel);border:1px solid var(--border-light);color:var(--text);font-family:var(--font-data);font-size:11px;padding:4px 8px;outline:none;width:120px" autofocus>')
      .openOn(state.map);

    setTimeout(() => {
      const inp = document.getElementById('annotate-input');
      if (inp) {
        inp.focus();
        inp.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' && inp.value.trim()) {
            state.map.closePopup(popup);
            L.marker(e.latlng, {
              icon: L.divIcon({ className: 'map-measurement-label', html: '\u271A ' + inp.value.trim(), iconSize: null })
            }).addTo(self.annotationLayers);
            showToast('Marker: ' + inp.value.trim());
          }
          if (ev.key === 'Escape') state.map.closePopup(popup);
        });
      }
    }, 100);

    this.deactivate();
  }
};

// Bind toolbar buttons
document.querySelectorAll('.map-tool-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const tool = btn.dataset.tool;
    if (tool === 'clear') mapTools.clearAll();
    else mapTools.activate(tool);
  });
});

// ---- Command Palette (Cmd+K) ----
const cmdPalette = {
  el: null,
  input: null,
  results: null,
  items: [],
  selectedIndex: 0,
  visible: false,

  init() {
    this.el = document.getElementById('command-palette');
    this.input = document.getElementById('cmd-palette-input');
    this.results = document.getElementById('cmd-palette-results');

    // Backdrop click closes
    this.el.querySelector('.cmd-palette-backdrop').addEventListener('click', () => this.close());

    // Input handler
    this.input.addEventListener('input', () => this.search(this.input.value));
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); this.close(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); this.moveSelection(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this.moveSelection(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); this.executeSelected(); }
    });
  },

  open() {
    this.visible = true;
    this.el.classList.remove('hidden');
    requestAnimationFrame(() => { this.el.classList.add('visible'); });
    this.input.value = '';
    this.selectedIndex = 0;
    this.search('');
    setTimeout(() => this.input.focus(), 50);
  },

  close() {
    this.visible = false;
    this.el.classList.remove('visible');
    setTimeout(() => { this.el.classList.add('hidden'); }, 150);
    this.input.blur();
  },

  toggle() {
    if (this.visible) this.close(); else this.open();
  },

  buildItems() {
    const items = [];
    // Assets
    (window._overwatchAssets || []).forEach(d => {
      items.push({ category: 'ASSETS', icon: '\u25C6', label: d.id, hint: d.role + ' // ' + d.status, action: () => { selectDrone(d.id); } });
    });
    // Commands
    const cmds = [
      { label: 'LAUNCH PREP', hint: 'Arm selected asset', action: () => { cmdEngine.execute('ARM', { droneId: state.selectedDroneId }); } },
      { label: 'STAND DOWN', hint: 'Disarm selected asset', action: () => { cmdEngine.execute('DISARM', { droneId: state.selectedDroneId }); } },
      { label: 'LAUNCH', hint: 'Takeoff to set altitude', action: () => { cmdEngine.execute('TAKEOFF', { droneId: state.selectedDroneId }); } },
      { label: 'RECOVER', hint: 'Land selected asset', action: () => { cmdEngine.execute('LAND', { droneId: state.selectedDroneId }); } },
      { label: 'RTB', hint: 'Return to base', action: () => { cmdEngine.execute('RTB', { droneId: state.selectedDroneId }); } },
      { label: 'ABORT', hint: 'Emergency stop all', action: () => { cmdEngine.execute('EMERGENCY_STOP', {}); } },
    ];
    cmds.forEach(c => items.push({ category: 'COMMANDS', icon: '\u25B6', label: c.label, hint: c.hint, action: c.action }));
    // Collection patterns
    const patterns = [
      { label: 'ORBIT', hint: 'Circular orbit around a point', action: () => { cmdEngine.execute('SET_PATTERN', { pattern: 'ORBIT' }); } },
      { label: 'RACETRACK', hint: 'Elongated oval for route surveillance', action: () => { cmdEngine.execute('SET_PATTERN', { pattern: 'RACETRACK' }); } },
      { label: 'SEARCH GRID', hint: 'Systematic wide area coverage', action: () => { cmdEngine.execute('SET_PATTERN', { pattern: 'SEARCH_GRID' }); } },
      { label: 'POINT STARE', hint: 'Converge on high-value target', action: () => { cmdEngine.execute('SET_PATTERN', { pattern: 'POINT_STARE' }); } },
    ];
    patterns.forEach(p => items.push({ category: 'COLLECTION PATTERNS', icon: '\u25CB', label: p.label, hint: p.hint, action: p.action }));
    // Modes
    const modes = [
      { label: 'OBSERVE', hint: 'Real-time fleet monitoring', action: () => setMode('OBSERVE') },
      { label: 'TASK', hint: 'Directive center', action: () => setMode('TASK') },
      { label: 'DEBRIEF', hint: 'Replay & analytics', action: () => setMode('DEBRIEF') },
      { label: 'ISR FEED', hint: 'FPV camera view', action: () => setMode('ISR') },
    ];
    modes.forEach(m => items.push({ category: 'MODES', icon: '\u25A0', label: m.label, hint: m.hint, action: m.action }));
    return items;
  },

  search(query) {
    const allItems = this.buildItems();
    const q = query.toLowerCase().trim();
    this.items = q ? allItems.filter(it => it.label.toLowerCase().includes(q) || it.hint.toLowerCase().includes(q) || it.category.toLowerCase().includes(q)) : allItems;
    this.selectedIndex = 0;
    this.render();
  },

  render() {
    if (this.items.length === 0) {
      this.results.innerHTML = '<div class="cmd-palette-empty">No results found</div>';
      return;
    }
    let html = '';
    let lastCat = '';
    this.items.forEach((it, i) => {
      if (it.category !== lastCat) {
        html += '<div class="cmd-palette-category">' + it.category + '</div>';
        lastCat = it.category;
      }
      html += '<div class="cmd-palette-item' + (i === this.selectedIndex ? ' selected' : '') + '" data-index="' + i + '">';
      html += '<span class="cmd-palette-item-icon">' + it.icon + '</span>';
      html += '<span class="cmd-palette-item-label">' + it.label + '</span>';
      html += '<span class="cmd-palette-item-hint">' + it.hint + '</span>';
      html += '</div>';
    });
    this.results.innerHTML = html;
    // Click handlers
    this.results.querySelectorAll('.cmd-palette-item').forEach(el => {
      el.addEventListener('click', () => {
        this.selectedIndex = parseInt(el.dataset.index);
        this.executeSelected();
      });
      el.addEventListener('mouseenter', () => {
        this.selectedIndex = parseInt(el.dataset.index);
        this.render();
      });
    });
  },

  moveSelection(dir) {
    this.selectedIndex = Math.max(0, Math.min(this.items.length - 1, this.selectedIndex + dir));
    this.render();
    // Scroll into view
    const sel = this.results.querySelector('.cmd-palette-item.selected');
    if (sel) sel.scrollIntoView({ block: 'nearest' });
  },

  executeSelected() {
    const item = this.items[this.selectedIndex];
    if (item && item.action) {
      item.action();
      this.close();
      showToast(item.label);
    }
  }
};

// Init palette after DOM ready
cmdPalette.init();

// Global Cmd+K / Ctrl+K listener
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    cmdPalette.toggle();
  }
  if (e.key === 'Escape' && cmdPalette.visible) {
    e.preventDefault();
    cmdPalette.close();
  }
});

// ---- Global keyboard shortcuts (Fix #3) ----
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

  const key = e.key;

  // Number keys 1-6 to select assets
  if (key >= '1' && key <= '6') {
    const idx = parseInt(key) - 1;
    if (idx < drones.length) {
      e.preventDefault();
      selectDrone(drones[idx].id);
      showToast('Selected ' + drones[idx].id);
    }
    return;
  }

  // Mode shortcuts (only when not in ISR mode, which has its own shortcuts)
  if (state.currentMode !== 'ISR') {
    if (key.toLowerCase() === 'o') { e.preventDefault(); setMode('OBSERVE'); return; }
    if (key.toLowerCase() === 't') { e.preventDefault(); setMode('TASK'); return; }
    if (key.toLowerCase() === 'd') { e.preventDefault(); setMode('DEBRIEF'); return; }
    if (key.toLowerCase() === 'i') { e.preventDefault(); setMode('ISR'); return; }
  }

  if (key === 'm' && state.currentMode !== 'ISR') {
    e.preventDefault();
    mapTools.activate('measure');
  }
});

// ---- Keyboard shortcuts (SOLO mode) ----
document.addEventListener('keydown', (e) => {
  if (state.currentMode !== 'ISR') return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const key = e.key.toLowerCase();
  if (key === 'f') { e.preventDefault(); videoMgr.toggleFullscreen(); }
  if (key === 'r') { e.preventDefault(); if (dvrMgr.recording) dvrMgr.stop(); else dvrMgr.start(); }
  if (key === 'p') { e.preventDefault(); videoMgr.capturePhoto(); }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    const slider = document.getElementById('camera-tilt');
    slider.value = Math.min(30, parseInt(slider.value) + 5);
    slider.dispatchEvent(new Event('input'));
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    const slider = document.getElementById('camera-tilt');
    slider.value = Math.max(-90, parseInt(slider.value) - 5);
    slider.dispatchEvent(new Event('input'));
  }
  if (key === 'escape') {
    const vp = document.getElementById('video-panel');
    if (vp.classList.contains('fullscreen')) vp.classList.remove('fullscreen');
  }
});

// ? key shows shortcut help
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '?') {
    e.preventDefault();
    const help = document.getElementById('shortcut-help');
    if (help.classList.contains('visible')) {
      help.classList.remove('visible');
      help.classList.add('hidden');
    } else {
      help.classList.remove('hidden');
      requestAnimationFrame(() => { help.classList.add('visible'); });
    }
  }
});

// Click backdrop to close
document.getElementById('shortcut-help').addEventListener('click', (e) => {
  if (e.target.id === 'shortcut-help') {
    e.target.classList.remove('visible');
    e.target.classList.add('hidden');
  }
});

/* ==============================================================
   DOM INIT — Mode buttons, inspector tabs, tree toggles
   ============================================================== */

// Mode button click handlers
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => setMode(btn.dataset.mode));
});

// Inspector tab switching
document.querySelectorAll('.inspector-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.inspector-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.inspector-tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    const targetEl = document.getElementById('inspector-' + target);
    if (targetEl) targetEl.classList.add('active');
  });
});

// Tree section toggles
document.getElementById('tree-assets-toggle').addEventListener('click', () => {
  const list = document.getElementById('asset-list');
  const arrow = document.getElementById('tree-assets-arrow');
  if (list.style.display === 'none') { list.style.display = ''; arrow.innerHTML = '&#9662;'; }
  else { list.style.display = 'none'; arrow.innerHTML = '&#9656;'; }
});
document.getElementById('tree-ops-toggle').addEventListener('click', () => {
  const list = document.getElementById('ops-list');
  const arrow = document.getElementById('tree-ops-arrow');
  if (list.style.display === 'none') { list.style.display = ''; arrow.innerHTML = '&#9662;'; }
  else { list.style.display = 'none'; arrow.innerHTML = '&#9656;'; }
});
document.getElementById('tree-aoi-toggle').addEventListener('click', () => {
  const list = document.getElementById('aoi-list');
  const arrow = document.getElementById('tree-aoi-arrow');
  if (list.style.display === 'none') { list.style.display = ''; arrow.innerHTML = '&#9662;'; }
  else { list.style.display = 'none'; arrow.innerHTML = '&#9656;'; }
});

// Initialize mode
setMode('OBSERVE');
