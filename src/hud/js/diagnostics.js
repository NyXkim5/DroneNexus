// diagnostics.js — Diagnostics + Hardware panels
import { state } from './state.js';
import { _css, clamp, rand, randInt, utcTimeStamp, showToast } from './utils.js';

/* ==============================================================
   DIAGNOSTICS PANEL RENDERER (Feature #4)
   ============================================================== */
// Store diagnostics state per asset
function getDiagState(assetId) {
  if (!state.diagState[assetId]) {
    // Seed with simulated stable data
    const seed = assetId.charCodeAt(0) + assetId.length;
    state.diagState[assetId] = {
      overallHealth: clamp(92 + (seed % 7), 85, 99),
      subsystems: {
        propulsion: clamp(90 + (seed % 9), 82, 99),
        avionics: clamp(93 + (seed % 6), 87, 99),
        comms: clamp(91 + (seed % 8), 84, 99),
        power: clamp(88 + (seed % 10), 80, 98),
        sensors: clamp(94 + (seed % 5), 88, 99),
      },
      motors: [
        { rpm: 4200 + (seed % 300), temp: 42 + (seed % 8), health: clamp(95 + (seed % 4), 88, 99) },
        { rpm: 4180 + ((seed * 2) % 280), temp: 41 + ((seed * 2) % 9), health: clamp(96 + ((seed * 2) % 3), 90, 99) },
        { rpm: 4220 + ((seed * 3) % 260), temp: 43 + ((seed * 3) % 7), health: clamp(93 + ((seed * 3) % 5), 86, 99) },
        { rpm: 4190 + ((seed * 4) % 290), temp: 44 + ((seed * 4) % 6), health: clamp(94 + ((seed * 4) % 4), 87, 99) },
      ],
      sensors: {
        gps: 'OK', barometer: 'OK', magnetometer: seed % 7 === 0 ? 'WARNING' : 'OK',
        accelerometer: 'OK', gyroscope: 'OK', lidar: seed % 11 === 0 ? 'WARNING' : 'OK',
      },
      escStatus: 'NOMINAL',
      imuCalibration: 'CALIBRATED',
      radioLink: clamp(93 + (seed % 6), 88, 99),
      antennaStatus: 'DUAL-ACTIVE',
      cellVoltages: [4.15, 4.13, 4.16, 4.12, 4.14, 4.11].map((v, i) => v + ((seed + i) % 5) * 0.01 - 0.02),
      bmsHealth: clamp(96 + (seed % 3), 93, 99),
      lastScanResults: null,
      scanning: false,
      scanProgress: 0,
    };
    // Inject a warning on motor 3 for some assets
    if (seed % 5 === 0) {
      state.diagState[assetId].motors[2].health = clamp(78 + (seed % 5), 74, 82);
    }
  }
  return state.diagState[assetId];
}

function diagHealthColor(val) {
  if (val >= 90) return 'var(--green)';
  if (val >= 75) return 'var(--amber)';
  return 'var(--red)';
}

function diagSensorDotClass(status) {
  if (status === 'OK') return 'ok';
  if (status === 'WARNING') return 'warning';
  return 'fault';
}

function updateDiagnosticsPanel(asset) {
  const el = document.getElementById('diagnostics-content');
  if (!el) return;
  const ds = getDiagState(asset.id);

  // Add slight jitter each render to make it look live
  ds.overallHealth = clamp(ds.overallHealth + (Math.random() - 0.5) * 0.3, 82, 99);
  ds.motors.forEach(m => {
    m.rpm = clamp(m.rpm + (Math.random() - 0.5) * 20, 3800, 4600);
    m.temp = clamp(m.temp + (Math.random() - 0.5) * 0.5, 38, 55);
  });
  ds.radioLink = clamp(ds.radioLink + (Math.random() - 0.5) * 0.5, 85, 99);

  const circ = 2 * Math.PI * 26;
  const healthOffset = circ - (ds.overallHealth / 100) * circ;
  const healthColor = diagHealthColor(ds.overallHealth);

  // Full innerHTML rebuild only when asset changes
  if (state.diagRenderedAsset !== asset.id) {
    state.diagRenderedAsset = asset.id;

    let html = '';

    // System Health
    html += '<div class="diag-section"><div class="diag-section-title">System Health</div>';
  html += '<div class="diag-health-score">';
  html += '<div class="diag-health-ring"><svg viewBox="0 0 64 64"><circle class="ring-bg" cx="32" cy="32" r="26"/>';
  html += '<circle id="dg-health-ring" class="ring-fill" cx="32" cy="32" r="26" stroke="' + healthColor + '" stroke-dasharray="' + circ.toFixed(1) + '" stroke-dashoffset="' + healthOffset.toFixed(1) + '"/>';
  html += '</svg><div id="dg-health-pct" class="diag-health-pct" style="color:' + healthColor + '">' + ds.overallHealth.toFixed(0) + '</div></div>';
  html += '<div><div class="diag-health-label">Overall System Health</div>';
  html += '<div style="font-family:var(--font-data);font-size:10px;color:var(--text-dim);margin-top:2px">' + asset.id + ' // ' + asset.role + '</div></div></div>';

  // Subsystem breakdown
  Object.entries(ds.subsystems).forEach(([name, val]) => {
    const c = diagHealthColor(val);
    html += '<div class="diag-subsystem"><span class="diag-subsystem-name">' + name.charAt(0).toUpperCase() + name.slice(1) + '</span>';
    html += '<div class="diag-subsystem-bar"><div id="dg-sub-' + name + '-bar" class="diag-subsystem-bar-fill" style="width:' + val + '%;background:' + c + '"></div></div>';
    html += '<span id="dg-sub-' + name + '" class="diag-subsystem-val" style="color:' + c + '">' + val.toFixed(0) + '%</span></div>';
  });
  html += '</div>';

  // Hardware — Motors
  html += '<div class="diag-section"><div class="diag-section-title">Hardware Status</div>';
  html += '<div class="diag-motor-grid">';
  ds.motors.forEach((m, i) => {
    const mc = diagHealthColor(m.health);
    html += '<div class="diag-motor-card"><div class="diag-motor-label">Motor ' + (i + 1) + '</div>';
    html += '<div class="diag-motor-stat">RPM: <span id="dg-motor-' + i + '-rpm" style="color:var(--text-bright)">' + m.rpm.toFixed(0) + '</span></div>';
    html += '<div class="diag-motor-stat">Temp: <span id="dg-motor-' + i + '-temp" style="color:' + (m.temp > 50 ? 'var(--amber)' : 'var(--text-bright)') + '">' + m.temp.toFixed(1) + '&deg;C</span></div>';
    html += '<div class="diag-motor-stat">Health: <span id="dg-motor-' + i + '-health" style="color:' + mc + '">' + m.health.toFixed(0) + '%</span></div></div>';
  });
  html += '</div>';
  html += '<div style="margin-top:8px"><div class="diag-status-indicator"><span class="diag-status-dot ok"></span><span style="color:var(--text)">ESC: ' + ds.escStatus + '</span></div>';
  html += '<div class="diag-status-indicator"><span class="diag-status-dot ok"></span><span style="color:var(--text)">IMU: ' + ds.imuCalibration + '</span></div></div>';
  html += '</div>';

  // Sensor Health
  html += '<div class="diag-section"><div class="diag-section-title">Sensor Health</div>';
  html += '<div class="diag-sensor-grid">';
  Object.entries(ds.sensors).forEach(([name, status]) => {
    const dc = diagSensorDotClass(status);
    html += '<div class="diag-status-indicator"><span class="diag-status-dot ' + dc + '"></span>';
    html += '<span style="color:var(--text)">' + name.charAt(0).toUpperCase() + name.slice(1) + '</span>';
    html += '<span style="color:' + (status === 'OK' ? 'var(--green)' : status === 'WARNING' ? 'var(--amber)' : 'var(--red)') + ';font-size:9px;margin-left:auto"> ' + status + '</span></div>';
  });
  html += '</div></div>';

  // Communication
  html += '<div class="diag-section"><div class="diag-section-title">Communication</div>';
  html += '<div class="diag-subsystem"><span class="diag-subsystem-name">Radio Link</span>';
  html += '<div class="diag-subsystem-bar"><div id="dg-sub-radio-bar" class="diag-subsystem-bar-fill" style="width:' + ds.radioLink + '%;background:' + diagHealthColor(ds.radioLink) + '"></div></div>';
  html += '<span id="dg-sub-radio" class="diag-subsystem-val" style="color:' + diagHealthColor(ds.radioLink) + '">' + ds.radioLink.toFixed(0) + '%</span></div>';
  html += '<div class="diag-status-indicator"><span class="diag-status-dot ok"></span><span style="color:var(--text)">Antenna: ' + ds.antennaStatus + '</span></div>';
  html += '<div class="diag-subsystem"><span class="diag-subsystem-name">Signal Quality</span>';
  const sq = asset.linkQuality || 95;
  html += '<div class="diag-subsystem-bar"><div id="dg-sub-signal-bar" class="diag-subsystem-bar-fill" style="width:' + sq + '%;background:' + diagHealthColor(sq) + '"></div></div>';
  html += '<span id="dg-sub-signal" class="diag-subsystem-val" style="color:' + diagHealthColor(sq) + '">' + sq.toFixed(0) + '%</span></div>';
  html += '</div>';

  // Power Systems
  html += '<div class="diag-section"><div class="diag-section-title">Power Systems (6S)</div>';
  ds.cellVoltages.forEach((v, i) => {
    const pct = clamp(((v - 3.3) / (4.2 - 3.3)) * 100, 0, 100);
    const cc = v < 3.5 ? 'var(--red)' : v < 3.7 ? 'var(--amber)' : 'var(--green)';
    html += '<div class="diag-cell-row"><span style="color:var(--text-dim);min-width:24px">C' + (i + 1) + '</span>';
    html += '<div class="diag-cell-bar"><div id="dg-cell-' + i + '-bar" class="diag-cell-bar-fill" style="width:' + pct.toFixed(0) + '%;background:' + cc + '"></div></div>';
    html += '<span id="dg-cell-' + i + '" style="color:' + cc + ';min-width:40px;text-align:right">' + v.toFixed(2) + 'V</span></div>';
  });
  html += '<div style="margin-top:6px"><div class="diag-status-indicator"><span class="diag-status-dot ok"></span><span style="color:var(--text)">BMS Health: ' + ds.bmsHealth + '%</span></div></div>';
  html += '</div>';

  // Run Diagnostics
  html += '<div class="diag-section">';
  html += '<button class="diag-run-btn" id="diag-run-btn" data-asset-id="' + asset.id + '"' + (ds.scanning ? ' disabled' : '') + '>' + (ds.scanning ? 'SCANNING...' : 'RUN DIAGNOSTICS') + '</button>';
  html += '<div class="diag-progress" id="diag-progress" style="' + (ds.scanning ? '' : 'display:none') + '"><div class="diag-progress-fill" id="diag-progress-fill" style="width:' + ds.scanProgress + '%"></div></div>';
  if (ds.lastScanResults) {
    html += '<div class="diag-results" id="diag-results">';
    html += '<div style="font-family:var(--font-label);font-size:9px;color:var(--text-dim);letter-spacing:0.5px;text-transform:uppercase;margin-bottom:6px">Scan Results</div>';
    ds.lastScanResults.forEach(r => {
      const ic = r.severity === 'ok' ? 'var(--green)' : r.severity === 'warning' ? 'var(--amber)' : 'var(--red)';
      const dot = r.severity === 'ok' ? 'ok' : r.severity === 'warning' ? 'warning' : 'fault';
      html += '<div class="diag-result-item"><span class="diag-result-icon"><span class="diag-status-dot ' + dot + '"></span></span><span style="color:' + ic + '">' + r.msg + '</span></div>';
    });
    html += '</div>';
  }
  html += '</div>';

  el.innerHTML = html;
  // Attach click handler after innerHTML (button is dynamically created)
  const diagBtn = document.getElementById('diag-run-btn');
  if (diagBtn) {
    diagBtn.addEventListener('click', function() {
      runDiagnosticScan(diagBtn.getAttribute('data-asset-id'));
    });
  }
    return;
  }

  // --- Targeted updates for changing values (same asset, subsequent ticks) ---

  // Overall health ring
  const ringEl = document.getElementById('dg-health-ring');
  if (ringEl) {
    ringEl.setAttribute('stroke', healthColor);
    ringEl.setAttribute('stroke-dashoffset', healthOffset.toFixed(1));
  }
  const pctEl = document.getElementById('dg-health-pct');
  if (pctEl) {
    pctEl.textContent = ds.overallHealth.toFixed(0);
    pctEl.style.color = healthColor;
  }

  // Subsystem bar widths and percentages
  Object.entries(ds.subsystems).forEach(([name, val]) => {
    const c = diagHealthColor(val);
    const barEl = document.getElementById('dg-sub-' + name + '-bar');
    if (barEl) { barEl.style.width = val + '%'; barEl.style.background = c; }
    const valEl = document.getElementById('dg-sub-' + name);
    if (valEl) { valEl.textContent = val.toFixed(0) + '%'; valEl.style.color = c; }
  });

  // Motor RPM, temp, health
  ds.motors.forEach((m, i) => {
    const mc = diagHealthColor(m.health);
    const rpmEl = document.getElementById('dg-motor-' + i + '-rpm');
    if (rpmEl) rpmEl.textContent = m.rpm.toFixed(0);
    const tempEl = document.getElementById('dg-motor-' + i + '-temp');
    if (tempEl) {
      tempEl.innerHTML = m.temp.toFixed(1) + '&deg;C';
      tempEl.style.color = m.temp > 50 ? 'var(--amber)' : 'var(--text-bright)';
    }
    const healthEl = document.getElementById('dg-motor-' + i + '-health');
    if (healthEl) { healthEl.textContent = m.health.toFixed(0) + '%'; healthEl.style.color = mc; }
  });

  // Radio link
  const radioBarEl = document.getElementById('dg-sub-radio-bar');
  if (radioBarEl) { radioBarEl.style.width = ds.radioLink + '%'; radioBarEl.style.background = diagHealthColor(ds.radioLink); }
  const radioValEl = document.getElementById('dg-sub-radio');
  if (radioValEl) { radioValEl.textContent = ds.radioLink.toFixed(0) + '%'; radioValEl.style.color = diagHealthColor(ds.radioLink); }

  // Signal quality
  const sq = asset.linkQuality || 95;
  const sigBarEl = document.getElementById('dg-sub-signal-bar');
  if (sigBarEl) { sigBarEl.style.width = sq + '%'; sigBarEl.style.background = diagHealthColor(sq); }
  const sigValEl = document.getElementById('dg-sub-signal');
  if (sigValEl) { sigValEl.textContent = sq.toFixed(0) + '%'; sigValEl.style.color = diagHealthColor(sq); }

  // Cell voltages
  ds.cellVoltages.forEach((v, i) => {
    const pct = clamp(((v - 3.3) / (4.2 - 3.3)) * 100, 0, 100);
    const cc = v < 3.5 ? 'var(--red)' : v < 3.7 ? 'var(--amber)' : 'var(--green)';
    const cellBarEl = document.getElementById('dg-cell-' + i + '-bar');
    if (cellBarEl) { cellBarEl.style.width = pct.toFixed(0) + '%'; cellBarEl.style.background = cc; }
    const cellValEl = document.getElementById('dg-cell-' + i);
    if (cellValEl) { cellValEl.textContent = v.toFixed(2) + 'V'; cellValEl.style.color = cc; }
  });
}

function runDiagnosticScan(assetId) {
  const ds = getDiagState(assetId);
  if (ds.scanning) return;
  ds.scanning = true;
  ds.scanProgress = 0;
  ds.lastScanResults = null;

  const interval = setInterval(() => {
    ds.scanProgress += 2 + Math.random() * 3;
    if (ds.scanProgress >= 100) {
      ds.scanProgress = 100;
      clearInterval(interval);
      ds.scanning = false;
      // Generate simulated results
      const seed = assetId.charCodeAt(0) + Date.now() % 100;
      const results = [
        { msg: 'All flight controllers responsive', severity: 'ok' },
        { msg: 'GPS module firmware up to date', severity: 'ok' },
        { msg: 'ESC firmware verified', severity: 'ok' },
      ];
      // Inject random warnings
      const warnings = [
        'Motor 3 bearing wear detected — schedule inspection',
        'Magnetometer calibration drift +0.8 deg — recalibrate recommended',
        'Battery cell C4 slightly below nominal — monitor',
        'Propeller micro-crack detected on motor 2 — inspect before next flight',
        'IMU temperature compensation offset increased — re-baseline recommended',
        'Radio antenna VSWR slightly elevated — check connections',
      ];
      const numWarnings = (seed % 3 === 0) ? 2 : 1;
      for (let i = 0; i < numWarnings; i++) {
        results.push({ msg: warnings[(seed + i) % warnings.length], severity: 'warning' });
      }
      if (seed % 7 === 0) {
        results.push({ msg: 'Barometer pressure sensor drift exceeds threshold', severity: 'fault' });
      }
      results.push({ msg: 'Diagnostic scan complete — ' + results.filter(r => r.severity === 'warning').length + ' warning(s)', severity: 'ok' });
      ds.lastScanResults = results;
    }
    // Re-render if still viewing this asset
    if (state.selectedDroneId === assetId) {
      const progressEl = document.getElementById('diag-progress-fill');
      if (progressEl) progressEl.style.width = ds.scanProgress + '%';
      const progressBar = document.getElementById('diag-progress');
      if (progressBar) progressBar.style.display = '';
      const btn = document.getElementById('diag-run-btn');
      if (btn) {
        btn.disabled = ds.scanning;
        btn.textContent = ds.scanning ? 'SCANNING... ' + ds.scanProgress.toFixed(0) + '%' : 'RUN DIAGNOSTICS';
      }
      if (!ds.scanning && ds.lastScanResults) {
        // Full re-render to show results (reset tracking so full rebuild fires)
        state.diagRenderedAsset = null;
        const allAssets = state.assets || [];
        const asset = allAssets.find(a => a.id === assetId);
        if (asset) updateDiagnosticsPanel(asset);
      }
    }
  }, 80);
}

/* ==============================================================
   HARDWARE MANIFEST PANEL RENDERER
   ============================================================== */

function updateHardwarePanel(asset) {
  const el = document.getElementById('hardware-content');
  if (!el) return;
  if (!asset) { el.innerHTML = '<div class="no-selection">SELECT AN ASSET</div>'; state.hwRenderedAsset = null; return; }

  const ds = getDiagState(asset.id);

  // If same asset, only update motor status badges (the only dynamic part)
  if (state.hwRenderedAsset === asset.id) {
    for (let mi = 0; mi < 4; mi++) {
      const statusEl = document.getElementById('hw-motor-' + mi + '-status');
      if (statusEl) {
        const h = ds.motors[mi].health;
        if (h > 90) { statusEl.className = 'hw-part-status nominal'; statusEl.textContent = 'NOMINAL'; }
        else if (h >= 70) { statusEl.className = 'hw-part-status warning'; statusEl.textContent = 'DEGRADED'; }
        else { statusEl.className = 'hw-part-status fault'; statusEl.textContent = 'FAULT'; }
      }
    }
    return;
  }
  state.hwRenderedAsset = asset.id;

  const color = asset.color || 'var(--accent)';

  // Motor health color helper
  function motorColor(health) {
    if (health > 90) return color;
    if (health >= 70) return 'var(--amber)';
    return 'var(--red)';
  }

  // Status helpers
  function motorStatus(health, idx) {
    if (health > 90) return '<span id="hw-motor-' + idx + '-status" class="hw-part-status nominal">NOMINAL</span>';
    if (health >= 70) return '<span id="hw-motor-' + idx + '-status" class="hw-part-status warning">DEGRADED</span>';
    return '<span id="hw-motor-' + idx + '-status" class="hw-part-status fault">FAULT</span>';
  }
  function subsysStatus(val) {
    if (val > 90) return '<span class="hw-part-status nominal">NOMINAL</span>';
    if (val >= 70) return '<span class="hw-part-status warning">WARNING</span>';
    return '<span class="hw-part-status fault">FAULT</span>';
  }

  // Motor colors from diagnostics
  const m1c = motorColor(ds.motors[0].health);
  const m2c = motorColor(ds.motors[1].health);
  const m3c = motorColor(ds.motors[2].health);
  const m4c = motorColor(ds.motors[3].health);

  const armColor = 'var(--border-light)';
  const bodyColor = 'var(--border-light)';
  const gpsColor = 'var(--green)';
  const camColor = 'var(--cyan)';
  const labelColor = 'var(--text-dim)';
  const batColor = 'var(--amber)';
  const fcColor = 'var(--accent)';
  const rxColor = 'var(--purple)';

  // ---- SVG DIAGRAM ----
  const svg = `<div class="hw-diagram"><svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
    <!-- Arms (diagonals from center to motor positions) -->
    <line x1="60" y1="60" x2="100" y2="100" stroke="${armColor}" stroke-width="3" stroke-linecap="round"/>
    <line x1="140" y1="60" x2="100" y2="100" stroke="${armColor}" stroke-width="3" stroke-linecap="round"/>
    <line x1="60" y1="140" x2="100" y2="100" stroke="${armColor}" stroke-width="3" stroke-linecap="round"/>
    <line x1="140" y1="140" x2="100" y2="100" stroke="${armColor}" stroke-width="3" stroke-linecap="round"/>

    <!-- Center body (frame/FC) -->
    <rect class="hw-component" x="80" y="80" width="40" height="40" rx="6" ry="6" fill="${bodyColor}" opacity="0.7"/>

    <!-- Flight controller (inside body) -->
    <rect class="hw-component" x="88" y="88" width="16" height="12" rx="2" ry="2" fill="${fcColor}" opacity="0.6"/>
    <text class="hw-label" x="96" y="96" font-size="6" fill="${labelColor}">FC</text>

    <!-- Battery (inside body, offset below FC) -->
    <rect class="hw-component" x="86" y="103" width="20" height="10" rx="2" ry="2" fill="${batColor}" opacity="0.5"/>
    <text class="hw-label" x="96" y="110" font-size="6" fill="${labelColor}">BAT</text>

    <!-- Motor 1 (front-left) -->
    <circle class="hw-component" cx="60" cy="60" r="14" fill="none" stroke="${m1c}" stroke-width="2" opacity="0.8"/>
    <circle cx="60" cy="60" r="4" fill="${m1c}" opacity="0.9"/>
    <text class="hw-label" x="60" y="42" font-size="7" fill="${labelColor}">M1</text>

    <!-- Motor 2 (front-right) -->
    <circle class="hw-component" cx="140" cy="60" r="14" fill="none" stroke="${m2c}" stroke-width="2" opacity="0.8"/>
    <circle cx="140" cy="60" r="4" fill="${m2c}" opacity="0.9"/>
    <text class="hw-label" x="140" y="42" font-size="7" fill="${labelColor}">M2</text>

    <!-- Motor 3 (rear-left) -->
    <circle class="hw-component" cx="60" cy="140" r="14" fill="none" stroke="${m3c}" stroke-width="2" opacity="0.8"/>
    <circle cx="60" cy="140" r="4" fill="${m3c}" opacity="0.9"/>
    <text class="hw-label" x="60" y="162" font-size="7" fill="${labelColor}">M3</text>

    <!-- Motor 4 (rear-right) -->
    <circle class="hw-component" cx="140" cy="140" r="14" fill="none" stroke="${m4c}" stroke-width="2" opacity="0.8"/>
    <circle cx="140" cy="140" r="4" fill="${m4c}" opacity="0.9"/>
    <text class="hw-label" x="140" y="162" font-size="7" fill="${labelColor}">M4</text>

    <!-- GPS module (top-center) -->
    <rect class="hw-component" x="93" y="66" width="14" height="8" rx="2" ry="2" fill="${gpsColor}" opacity="0.6"/>
    <text class="hw-label" x="100" y="64" font-size="6" fill="${labelColor}">GPS</text>

    <!-- Camera/Gimbal (bottom-center) -->
    <rect class="hw-component" x="92" y="126" width="16" height="10" rx="2" ry="2" fill="${camColor}" opacity="0.6"/>
    <text class="hw-label" x="100" y="143" font-size="6" fill="${labelColor}">CAM</text>

    <!-- RX antenna (right side of body) -->
    <circle class="hw-component" cx="126" cy="96" r="4" fill="${rxColor}" opacity="0.6"/>
    <text class="hw-label" x="126" y="88" font-size="6" fill="${labelColor}">RX</text>

    <!-- VTX (left side of body) -->
    <rect class="hw-component" x="68" y="93" width="8" height="6" rx="1" ry="1" fill="${camColor}" opacity="0.4"/>
    <text class="hw-label" x="72" y="88" font-size="6" fill="${labelColor}">VTX</text>
  </svg></div>`;

  // ---- PARTS MANIFEST ----
  // Role-based payload variation
  const role = asset.role || 'PRIMARY';
  let payloadParts = '';
  if (role === 'ISR') {
    payloadParts = `
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Gimbal</span><span class="hw-part-model">Gremsy S1V3</span>${subsysStatus(ds.subsystems.sensors)}</div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Camera</span><span class="hw-part-model">Sony A7R IV + 85mm</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">IR Sensor</span><span class="hw-part-model">FLIR Boson 640</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">SAR Module</span><span class="hw-part-model">uRAD A1-160GHz</span><span class="hw-part-status nominal">NOMINAL</span></div>`;
  } else if (role === 'OVERWATCH') {
    payloadParts = `
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Gimbal</span><span class="hw-part-model">Gremsy T3V3</span>${subsysStatus(ds.subsystems.sensors)}</div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Camera</span><span class="hw-part-model">Sony A7R IV + 200mm</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">IR Sensor</span><span class="hw-part-model">FLIR Boson 640</span><span class="hw-part-status nominal">NOMINAL</span></div>`;
  } else if (role === 'LOGISTICS') {
    payloadParts = `
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Gimbal</span><span class="hw-part-model">Gremsy S1V3</span>${subsysStatus(ds.subsystems.sensors)}</div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Camera</span><span class="hw-part-model">Sony A6400 + 20mm</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Drop Module</span><span class="hw-part-model">SkyDrop DM-200</span><span class="hw-part-status nominal">NOMINAL</span></div>`;
  } else if (role === 'ESCORT') {
    payloadParts = `
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Gimbal</span><span class="hw-part-model">Gremsy Pixy U</span>${subsysStatus(ds.subsystems.sensors)}</div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Camera</span><span class="hw-part-model">Sony A7R IV + 35mm</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">IR Sensor</span><span class="hw-part-model">FLIR Boson 320</span><span class="hw-part-status nominal">NOMINAL</span></div>`;
  } else {
    // PRIMARY / default
    payloadParts = `
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Gimbal</span><span class="hw-part-model">Gremsy S1V3</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Camera</span><span class="hw-part-model">Sony A7R IV + 35mm</span><span class="hw-part-status nominal">NOMINAL</span></div>
      <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">IR Sensor</span><span class="hw-part-model">FLIR Boson 640</span><span class="hw-part-status nominal">NOMINAL</span></div>`;
  }

  const partsHtml = `<div class="hw-parts-list">
    <!-- Frame & Propulsion -->
    <div class="hw-section-title">Frame &amp; Propulsion</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${armColor}"></span><span class="hw-part-name">Frame</span><span class="hw-part-model">Sentinel X4 Carbon 450mm</span>${subsysStatus(ds.subsystems.propulsion)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${m1c}"></span><span class="hw-part-name">Motor 1</span><span class="hw-part-model">T-Motor U8 Lite KV100</span>${motorStatus(ds.motors[0].health, 0)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${m2c}"></span><span class="hw-part-name">Motor 2</span><span class="hw-part-model">T-Motor U8 Lite KV100</span>${motorStatus(ds.motors[1].health, 1)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${m3c}"></span><span class="hw-part-name">Motor 3</span><span class="hw-part-model">T-Motor U8 Lite KV100</span>${motorStatus(ds.motors[2].health, 2)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${m4c}"></span><span class="hw-part-name">Motor 4</span><span class="hw-part-model">T-Motor U8 Lite KV100</span>${motorStatus(ds.motors[3].health, 3)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${armColor}"></span><span class="hw-part-name">ESC</span><span class="hw-part-model">Hobbywing X-Rotor 40A</span>${subsysStatus(ds.subsystems.propulsion)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${armColor}"></span><span class="hw-part-name">Propeller</span><span class="hw-part-model">T-Motor P18x6.1 CF</span><span class="hw-part-status nominal">NOMINAL</span></div>

    <!-- Avionics -->
    <div class="hw-section-title">Avionics</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${fcColor}"></span><span class="hw-part-name">Flight Controller</span><span class="hw-part-model">Cube Orange+ H7</span>${subsysStatus(ds.subsystems.avionics)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${gpsColor}"></span><span class="hw-part-name">GPS/GNSS</span><span class="hw-part-model">Here3+ RTK</span>${subsysStatus(ds.subsystems.avionics)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${fcColor}"></span><span class="hw-part-name">IMU</span><span class="hw-part-model">ICM-42688-P (x2 redundant)</span>${subsysStatus(ds.subsystems.sensors)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${fcColor}"></span><span class="hw-part-name">Barometer</span><span class="hw-part-model">MS5611 (x2 redundant)</span>${subsysStatus(ds.subsystems.sensors)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${fcColor}"></span><span class="hw-part-name">Magnetometer</span><span class="hw-part-model">RM3100</span>${subsysStatus(ds.subsystems.sensors)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${fcColor}"></span><span class="hw-part-name">LiDAR Altimeter</span><span class="hw-part-model">TFMini-S</span>${subsysStatus(ds.subsystems.sensors)}</div>

    <!-- Communications -->
    <div class="hw-section-title">Communications</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${rxColor}"></span><span class="hw-part-name">Telemetry Radio</span><span class="hw-part-model">RFD900x 900MHz</span>${subsysStatus(ds.subsystems.comms)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${rxColor}"></span><span class="hw-part-name">RC Receiver</span><span class="hw-part-model">TBS Crossfire Nano</span>${subsysStatus(ds.subsystems.comms)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${camColor}"></span><span class="hw-part-name">Video TX</span><span class="hw-part-model">DJI O3 Air Unit</span>${subsysStatus(ds.subsystems.comms)}</div>

    <!-- Power -->
    <div class="hw-section-title">Power</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${batColor}"></span><span class="hw-part-name">Battery</span><span class="hw-part-model">Tattu 6S 10000mAh 25C</span>${subsysStatus(ds.subsystems.power)}</div>
    <div class="hw-part-row"><span class="hw-part-dot" style="background:${batColor}"></span><span class="hw-part-name">PDB/BEC</span><span class="hw-part-model">Matek FCHUB-6S</span><span class="hw-part-status nominal">NOMINAL</span></div>

    <!-- Payload -->
    <div class="hw-section-title">Payload</div>
    ${payloadParts}
  </div>`;

  el.innerHTML = svg + partsHtml;
}

export { getDiagState, updateDiagnosticsPanel, runDiagnosticScan, updateHardwarePanel };
