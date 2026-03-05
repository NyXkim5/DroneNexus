/* ==============================================================
   OVERWATCH ISR PLATFORM — APPLICATION ENTRY POINT
   Extracted from monolith index.html main() IIFE
   ============================================================== */

import { state } from './state.js';
import { ASSET_DEFS,
         EVENT_INTERVAL_MIN, EVENT_INTERVAL_MAX } from './constants.js';
import { _css, clamp, rand, degToRad, utcString, utcTimeStamp,
         showToast } from './utils.js';
import { AssetSimulator } from './simulation.js';
import { Sparkline } from './sparkline.js';
import { DirectiveEngine, ObjectiveManager, PlatformLink, DebriefSystem, setDiagStateProvider } from './engine.js';
import { initMap, createDroneIcon, updateMapMarker, updateFormationLines,
         updateFovCone } from './map.js';
import { generateActivity, generateStateCorrelatedEvents, addEvent,
         renderActivityStream, setEventCallback } from './activity.js';
import { updateAssetExplorer, selectDrone } from './assetExplorer.js';
import { updateInspector, setModeProvider } from './inspector.js';
import { getDiagState, updateDiagnosticsPanel, updateHardwarePanel } from './diagnostics.js';
import { ISRFeedManager, ISROverlayRenderer, RecordingManager } from './isrFeed.js';
import { updateDebriefPanel } from './debrief.js';
import { setFrameDeps, updateBottomMetrics, checkCriticalBanner,
         updateFPVData, updateISRFeed } from './frameHelpers.js';

/* ==============================================================
   MODE SWITCH
   ============================================================== */

function applyMode(mode) {
  state.currentMode = mode;
  document.body.dataset.mode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  // Deactivate map tools when entering OBSERVE (read-only mode)
  if (mode === 'OBSERVE' && typeof mapTools !== 'undefined') {
    mapTools.deactivate();
    if (gotoMode) disableGotoMode();
  }
}

let _modeTransitioning = false;
export function setMode(mode) {
  if (mode === state.currentMode || _modeTransitioning) return;
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
    // Phase 1: scan-line + panel exit
    const scanline = document.createElement('div');
    scanline.className = 'mode-scanline';
    const flash = document.createElement('div');
    flash.className = 'mode-flash';
    _modeTransitioning = true;
    document.body.appendChild(scanline);
    document.body.appendChild(flash);
    document.body.classList.add('mode-exit');

    // Phase 2: at midpoint, switch mode data while panels are invisible
    setTimeout(() => {
      applyMode(mode);
      handleVideoPanel();

      // Phase 3: panels enter with stagger
      document.body.classList.remove('mode-exit');
      document.body.classList.add('mode-enter');

      // Phase 4: cleanup after animations complete
      setTimeout(() => {
        document.body.classList.remove('mode-enter');
        scanline.remove();
        flash.remove();
        _modeTransitioning = false;
      }, 450);
    }, 250);
  } else {
    applyMode(mode);
    // On initial mode set, video elements may not exist yet
    try { handleVideoPanel(); } catch (e) { /* initial load */ }
  }
}

// Inject setMode into inspector.js via callback provider
setModeProvider(setMode);

/* ==============================================================
   MAIN APPLICATION
   ============================================================== */

// Initialize assets
const assets = ASSET_DEFS.map((def, i) => new AssetSimulator(def, i));
const drones = assets; // local alias used throughout app.js
state.assets = assets;
state.drones = assets;
// assets are available via state.assets (set above)

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
setDiagStateProvider(getDiagState);
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
  if (state.currentMode === 'OBSERVE') {
    showToast('Switch to TASK mode to issue commands', 'warning');
    return;
  }
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

// ---- Initialize ISR subsystems ----
const videoMgr = new ISRFeedManager();
const osdRenderer = new ISROverlayRenderer();
const dvrMgr = new RecordingManager(videoMgr);

// Wire dependencies for extracted frame helpers
setFrameDeps({ osdRenderer, dvrMgr, sparkHealth, sparkLatency, sparkBattery, sparkCoverage });

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
  mapTools.updateRouteProgress(drones[0]);

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

    updateBottomMetrics(drones);

    // Track distance for debrief
    drones.forEach(d => {
      if (d.altitude > 1) {
        debriefTracker.trackDistance(d.id, d.lat, d.lng);
      }
    });

    // Update debrief panel
    if (state.currentMode === 'DEBRIEF') {
      updateDebriefPanel(drones, debriefTracker, cmdEngine, replaySystem);
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

    checkCriticalBanner(drones);
  }

  // Random events
  if (now >= nextEventTime) {
    addEvent(generateActivity(assets));
    nextEventTime = now + rand(EVENT_INTERVAL_MIN, EVENT_INTERVAL_MAX);
  }

  // ---- FPV simulation data updates (throttled ~100ms) ----
  if (now - lastFpvUpdate > 100) {
    lastFpvUpdate = now;
    if (state.currentMode === 'ISR') updateFPVData(drones);
  }

  // ---- ISR feed UI update (throttled ~100ms) ----
  if (now - lastSoloUpdate > 100) {
    lastSoloUpdate = now;
    if (state.currentMode === 'ISR') updateISRFeed(drones);
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
  routePoints: [],
  routeLayers: L.layerGroup().addTo(state.map),
  routeExecuteEl: null,
  routeProgressLayers: L.layerGroup().addTo(state.map),
  _prevMissionIndex: -1,

  activate(tool) {
    if (this.activeTool === tool) { this.deactivate(); return; }
    // OBSERVE mode is read-only — only measure is allowed
    if (state.currentMode === 'OBSERVE' && tool !== 'measure' && tool !== 'clear') {
      showToast('Switch to TASK mode to use map tools', 'warning');
      return;
    }
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
    } else if (tool === 'route') {
      this.routePoints = [];
      this.routeLayers.clearLayers();
      this._removeRouteExecuteBtn();
      state.map.getContainer().style.cursor = 'crosshair';
      state.map.on('click', this._onRouteClick, this);
      state.map.on('dblclick', this._onRouteFinish, this);
      showToast('Click to place waypoints. Double-click to finish.');
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
    state.map.off('click', this._onRouteClick, this);
    state.map.off('dblclick', this._onRouteFinish, this);
  },

  clearAll() {
    this.measureLayers.clearLayers();
    this.areaLayers.clearLayers();
    this.annotationLayers.clearLayers();
    this.routeLayers.clearLayers();
    this.routeProgressLayers.clearLayers();
    this.routePoints = [];
    this._removeRouteExecuteBtn();
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
  },

  // ---- Route tool handlers ----
  _onRouteClick(e) {
    // Guard against double-click duplicating the last point
    if (this.routePoints.length > 0) {
      const last = this.routePoints[this.routePoints.length - 1];
      if (Math.abs(last.lat - e.latlng.lat) < 0.000001 && Math.abs(last.lng - e.latlng.lng) < 0.000001) return;
    }
    this.routePoints.push(e.latlng);
    const idx = this.routePoints.length;

    // Numbered waypoint marker
    const routeGrey = _css('--text-secondary');
    const marker = L.circleMarker(e.latlng, {
      radius: 10, color: routeGrey, fillColor: routeGrey, fillOpacity: 0.15, weight: 2,
    }).addTo(this.routeLayers);
    marker.bindTooltip('WP-' + idx, { permanent: true, direction: 'top', offset: [0, -10],
      className: 'map-measurement-label' });

    // Update connecting polyline
    if (this.routePoints.length > 1) {
      // Remove old polyline and distance labels
      this.routeLayers.eachLayer(l => {
        if (l instanceof L.Polyline && !(l instanceof L.Polygon)) this.routeLayers.removeLayer(l);
        if (l._isDistLabel) this.routeLayers.removeLayer(l);
      });
      // Draw full route line
      L.polyline(this.routePoints.map(p => [p.lat, p.lng]), {
        color: _css('--text-secondary'), weight: 2, opacity: 0.8
      }).addTo(this.routeLayers);

      // Distance label on latest segment
      const prev = this.routePoints[this.routePoints.length - 2];
      const curr = e.latlng;
      const distM = state.map.distance(prev, curr);
      const midLat = (prev.lat + curr.lat) / 2;
      const midLng = (prev.lng + curr.lng) / 2;
      const label = L.marker([midLat, midLng], {
        icon: L.divIcon({ className: 'map-measurement-label',
          html: (distM / 1000).toFixed(1) + ' km', iconSize: null })
      }).addTo(this.routeLayers);
      label._isDistLabel = true;
    }

    // Total distance label on last waypoint
    if (this.routePoints.length > 1) {
      this.routeLayers.eachLayer(l => { if (l._isTotalLabel) this.routeLayers.removeLayer(l); });
      let totalM = 0;
      for (let i = 1; i < this.routePoints.length; i++) {
        totalM += state.map.distance(this.routePoints[i - 1], this.routePoints[i]);
      }
      const totalLabel = L.marker(e.latlng, {
        icon: L.divIcon({ className: 'route-progress-label',
          html: 'TOTAL ' + (totalM / 1000).toFixed(1) + ' km \u00B7 ' + this.routePoints.length + ' WP',
          iconSize: null, iconAnchor: [0, -16] })
      }).addTo(this.routeLayers);
      totalLabel._isTotalLabel = true;
    }
  },

  _onRouteFinish(e) {
    e.originalEvent.preventDefault();
    if (this.routePoints.length < 2) {
      showToast('Need at least 2 waypoints', 'error');
      return;
    }
    this.deactivate();
    this._showRouteExecuteBtn();
  },

  _showRouteExecuteBtn() {
    this._removeRouteExecuteBtn();
    // Calculate total distance
    let totalM = 0;
    for (let i = 1; i < this.routePoints.length; i++) {
      totalM += state.map.distance(this.routePoints[i - 1], this.routePoints[i]);
    }
    // Fixed bar at bottom of map
    const bar = document.createElement('div');
    bar.className = 'route-execute-bar';
    bar.innerHTML =
      '<span class="route-execute-info">' +
        '<span class="route-execute-label">ROUTE PLANNED</span>' +
        '<span class="route-execute-stats">' + this.routePoints.length + ' WP \u00B7 ' + (totalM / 1000).toFixed(1) + ' km</span>' +
      '</span>' +
      '<button class="route-execute-go">\u25B6 EXECUTE ROUTE</button>' +
      '<button class="route-execute-cancel">\u2715</button>';
    bar.querySelector('.route-execute-go').addEventListener('click', () => this._executeRoute());
    bar.querySelector('.route-execute-cancel').addEventListener('click', () => {
      this._removeRouteExecuteBtn();
      this.routeLayers.clearLayers();
      this.routePoints = [];
      showToast('Route cleared');
    });
    state.map.getContainer().appendChild(bar);
    this.routeExecuteEl = bar;
  },

  _removeRouteExecuteBtn() {
    if (this.routeExecuteEl) {
      this.routeExecuteEl.remove();
      this.routeExecuteEl = null;
    }
  },

  _executeRoute() {
    const waypoints = this.routePoints.map(p => ({ lat: p.lat, lng: p.lng }));
    cmdEngine.execute('EXECUTE_MISSION', { waypoints });
    this._removeRouteExecuteBtn();
    this._prevMissionIndex = -1;
    showToast('Route executing \u2014 ' + waypoints.length + ' waypoints');
  },

  // ---- Live route progress (called from frame loop) ----
  updateRouteProgress(leader) {
    if (!leader || leader.droneState !== 'MISSION' || leader.missionWaypoints.length === 0) {
      if (this._prevMissionIndex >= 0) {
        // Mission just completed
        showToast('Route complete');
        this._prevMissionIndex = -1;
        // Fade out route after 3s
        setTimeout(() => {
          this.routeProgressLayers.clearLayers();
          this.routeLayers.clearLayers();
          this.routePoints = [];
        }, 3000);
      }
      return;
    }
    const wps = leader.missionWaypoints;
    const idx = leader.missionIndex;

    // Only rebuild when waypoint index changes
    if (idx === this._prevMissionIndex) return;
    this._prevMissionIndex = idx;
    this.routeProgressLayers.clearLayers();

    // Completed segments (dim)
    for (let i = 0; i < idx && i < wps.length - 1; i++) {
      L.polyline([[wps[i].lat, wps[i].lng], [wps[i + 1].lat, wps[i + 1].lng]], {
        color: _css('--text-dim'), weight: 2, opacity: 0.4
      }).addTo(this.routeProgressLayers);
    }

    // Current segment (bright, animated dash)
    if (idx < wps.length) {
      const rGrey = _css('--text-secondary');
      const from = idx > 0 ? wps[idx - 1] : { lat: leader.lat, lng: leader.lng };
      L.polyline([[from.lat, from.lng], [wps[idx].lat, wps[idx].lng]], {
        color: rGrey, weight: 3, opacity: 0.9, dashArray: '8,6'
      }).addTo(this.routeProgressLayers);

      // Active waypoint pulsing marker
      L.circleMarker([wps[idx].lat, wps[idx].lng], {
        radius: 12, color: rGrey, fillColor: rGrey,
        fillOpacity: 0.2, weight: 2, className: 'wp-active-pulse'
      }).addTo(this.routeProgressLayers);

      // Progress label
      L.marker([wps[idx].lat, wps[idx].lng], {
        icon: L.divIcon({ className: 'route-progress-label',
          html: 'WP ' + (idx + 1) + '/' + wps.length,
          iconSize: null, iconAnchor: [0, -18] })
      }).addTo(this.routeProgressLayers);
    }

    // Remaining segments (dashed)
    for (let i = idx; i < wps.length - 1; i++) {
      L.polyline([[wps[i].lat, wps[i].lng], [wps[i + 1].lat, wps[i + 1].lng]], {
        color: _css('--text-secondary'), weight: 1.5, opacity: 0.4, dashArray: '4,6'
      }).addTo(this.routeProgressLayers);
    }
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
    (state.assets || []).forEach(d => {
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
    cmds.push({ label: 'PLAN ROUTE', hint: 'Draw flight route on map (R)', action: () => { mapTools.activate('route'); } });
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
  if (key === 'r' && state.currentMode !== 'ISR') {
    e.preventDefault();
    mapTools.activate('route');
  }
  // Escape cancels active route planning
  if (key === 'Escape' && mapTools.activeTool === 'route') {
    e.preventDefault();
    mapTools.routePoints = [];
    mapTools.routeLayers.clearLayers();
    mapTools.deactivate();
    showToast('Route cancelled');
  }
  // Enter finalizes route (same as double-click)
  if (key === 'Enter' && mapTools.activeTool === 'route' && mapTools.routePoints.length >= 2) {
    e.preventDefault();
    mapTools.deactivate();
    mapTools._showRouteExecuteBtn();
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
const tabList = document.getElementById('inspector-tabs');
const tabs = Array.from(tabList.querySelectorAll('.inspector-tab'));

function activateTab(tab) {
  tabs.forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
    t.setAttribute('tabindex', '-1');
  });
  document.querySelectorAll('.inspector-tab-content').forEach(c => c.classList.remove('active'));
  tab.classList.add('active');
  tab.setAttribute('aria-selected', 'true');
  tab.setAttribute('tabindex', '0');
  const targetEl = document.getElementById('inspector-' + tab.dataset.tab);
  if (targetEl) targetEl.classList.add('active');
}

tabs.forEach(tab => {
  tab.addEventListener('click', () => activateTab(tab));
});

tabList.addEventListener('keydown', (e) => {
  const currentIndex = tabs.indexOf(document.activeElement);
  if (currentIndex === -1) return;
  let newIndex;
  if (e.key === 'ArrowRight') {
    newIndex = (currentIndex + 1) % tabs.length;
  } else if (e.key === 'ArrowLeft') {
    newIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  } else {
    return;
  }
  e.preventDefault();
  tabs[newIndex].focus();
  activateTab(tabs[newIndex]);
});

// Tree section toggles — click + keyboard (Enter/Space) for role="button" elements
function wireTreeToggle(toggleId, listId, arrowId) {
  const toggle = document.getElementById(toggleId);
  const handler = () => {
    const list = document.getElementById(listId);
    const arrow = document.getElementById(arrowId);
    const expanded = list.style.display !== 'none';
    list.style.display = expanded ? 'none' : '';
    arrow.innerHTML = expanded ? '&#9656;' : '&#9662;';
    toggle.setAttribute('aria-expanded', String(!expanded));
  };
  toggle.addEventListener('click', handler);
  toggle.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handler(); }
  });
}
wireTreeToggle('tree-assets-toggle', 'asset-list', 'tree-assets-arrow');
wireTreeToggle('tree-ops-toggle', 'ops-list', 'tree-ops-arrow');
wireTreeToggle('tree-aoi-toggle', 'aoi-list', 'tree-aoi-arrow');

// Initialize mode
setMode('OBSERVE');
