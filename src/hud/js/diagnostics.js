// diagnostics.js — Diagnostics + Hardware panels
import { state } from './state.js';
import { _css, clamp, rand, randInt, utcTimeStamp, showToast, el } from './utils.js';

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
  const container = document.getElementById('diagnostics-content');
  if (!container) return;
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

  // Full DOM rebuild only when asset changes
  if (state.diagRenderedAsset !== asset.id) {
    state.diagRenderedAsset = asset.id;

    // Helper to create SVG elements (el() creates HTML elements only)
    const svgNS = 'http://www.w3.org/2000/svg';
    function svgEl(tag, attrs) {
      const e = document.createElementNS(svgNS, tag);
      if (attrs) Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
      return e;
    }

    // System Health section
    const svgRoot = svgEl('svg', { viewBox: '0 0 64 64' });
    const ringBg = svgEl('circle', { class: 'ring-bg', cx: '32', cy: '32', r: '26' });
    const ringFill = svgEl('circle', { id: 'dg-health-ring', class: 'ring-fill', cx: '32', cy: '32', r: '26', stroke: healthColor, 'stroke-dasharray': circ.toFixed(1), 'stroke-dashoffset': healthOffset.toFixed(1) });
    svgRoot.appendChild(ringBg);
    svgRoot.appendChild(ringFill);

    const healthPct = el('div', { id: 'dg-health-pct', className: 'diag-health-pct', style: { color: healthColor }, textContent: ds.overallHealth.toFixed(0) });
    const healthRing = el('div', { className: 'diag-health-ring' }, svgRoot, healthPct);

    const healthInfo = el('div', null,
      el('div', { className: 'diag-health-label', textContent: 'Overall System Health' }),
      el('div', { style: { fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--text-dim)', marginTop: '2px' }, textContent: asset.id + ' // ' + asset.role })
    );

    const healthScore = el('div', { className: 'diag-health-score' }, healthRing, healthInfo);

    const systemHealthSection = el('div', { className: 'diag-section' },
      el('div', { className: 'diag-section-title', textContent: 'System Health' }),
      healthScore
    );

    // Subsystem breakdown
    Object.entries(ds.subsystems).forEach(([name, val]) => {
      const c = diagHealthColor(val);
      systemHealthSection.appendChild(
        el('div', { className: 'diag-subsystem' },
          el('span', { className: 'diag-subsystem-name', textContent: name.charAt(0).toUpperCase() + name.slice(1) }),
          el('div', { className: 'diag-subsystem-bar' },
            el('div', { id: 'dg-sub-' + name + '-bar', className: 'diag-subsystem-bar-fill', style: { width: val + '%', background: c } })
          ),
          el('span', { id: 'dg-sub-' + name, className: 'diag-subsystem-val', style: { color: c }, textContent: val.toFixed(0) + '%' })
        )
      );
    });

    // Hardware — Motors section
    const motorGrid = el('div', { className: 'diag-motor-grid' });
    ds.motors.forEach((m, i) => {
      const mc = diagHealthColor(m.health);
      motorGrid.appendChild(
        el('div', { className: 'diag-motor-card' },
          el('div', { className: 'diag-motor-label', textContent: 'Motor ' + (i + 1) }),
          el('div', { className: 'diag-motor-stat' },
            'RPM: ',
            el('span', { id: 'dg-motor-' + i + '-rpm', style: { color: 'var(--text-bright)' }, textContent: m.rpm.toFixed(0) })
          ),
          el('div', { className: 'diag-motor-stat' },
            'Temp: ',
            el('span', { id: 'dg-motor-' + i + '-temp', style: { color: m.temp > 50 ? 'var(--amber)' : 'var(--text-bright)' }, textContent: m.temp.toFixed(1) + '\u00b0C' })
          ),
          el('div', { className: 'diag-motor-stat' },
            'Health: ',
            el('span', { id: 'dg-motor-' + i + '-health', style: { color: mc }, textContent: m.health.toFixed(0) + '%' })
          )
        )
      );
    });

    const hwStatusSection = el('div', { className: 'diag-section' },
      el('div', { className: 'diag-section-title', textContent: 'Hardware Status' }),
      motorGrid,
      el('div', { style: { marginTop: '8px' } },
        el('div', { className: 'diag-status-indicator' },
          el('span', { className: 'diag-status-dot ok' }),
          el('span', { style: { color: 'var(--text)' }, textContent: 'ESC: ' + ds.escStatus })
        ),
        el('div', { className: 'diag-status-indicator' },
          el('span', { className: 'diag-status-dot ok' }),
          el('span', { style: { color: 'var(--text)' }, textContent: 'IMU: ' + ds.imuCalibration })
        )
      )
    );

    // Sensor Health section
    const sensorGrid = el('div', { className: 'diag-sensor-grid' });
    Object.entries(ds.sensors).forEach(([name, status]) => {
      const dc = diagSensorDotClass(status);
      const statusColor = status === 'OK' ? 'var(--green)' : status === 'WARNING' ? 'var(--amber)' : 'var(--red)';
      sensorGrid.appendChild(
        el('div', { className: 'diag-status-indicator' },
          el('span', { className: 'diag-status-dot ' + dc }),
          el('span', { style: { color: 'var(--text)' }, textContent: name.charAt(0).toUpperCase() + name.slice(1) }),
          el('span', { style: { color: statusColor, fontSize: '9px', marginLeft: 'auto' }, textContent: ' ' + status })
        )
      );
    });

    const sensorSection = el('div', { className: 'diag-section' },
      el('div', { className: 'diag-section-title', textContent: 'Sensor Health' }),
      sensorGrid
    );

    // Communication section
    const sq = asset.linkQuality || 95;
    const commSection = el('div', { className: 'diag-section' },
      el('div', { className: 'diag-section-title', textContent: 'Communication' }),
      el('div', { className: 'diag-subsystem' },
        el('span', { className: 'diag-subsystem-name', textContent: 'Radio Link' }),
        el('div', { className: 'diag-subsystem-bar' },
          el('div', { id: 'dg-sub-radio-bar', className: 'diag-subsystem-bar-fill', style: { width: ds.radioLink + '%', background: diagHealthColor(ds.radioLink) } })
        ),
        el('span', { id: 'dg-sub-radio', className: 'diag-subsystem-val', style: { color: diagHealthColor(ds.radioLink) }, textContent: ds.radioLink.toFixed(0) + '%' })
      ),
      el('div', { className: 'diag-status-indicator' },
        el('span', { className: 'diag-status-dot ok' }),
        el('span', { style: { color: 'var(--text)' }, textContent: 'Antenna: ' + ds.antennaStatus })
      ),
      el('div', { className: 'diag-subsystem' },
        el('span', { className: 'diag-subsystem-name', textContent: 'Signal Quality' }),
        el('div', { className: 'diag-subsystem-bar' },
          el('div', { id: 'dg-sub-signal-bar', className: 'diag-subsystem-bar-fill', style: { width: sq + '%', background: diagHealthColor(sq) } })
        ),
        el('span', { id: 'dg-sub-signal', className: 'diag-subsystem-val', style: { color: diagHealthColor(sq) }, textContent: sq.toFixed(0) + '%' })
      )
    );

    // Power Systems section
    const powerSection = el('div', { className: 'diag-section' },
      el('div', { className: 'diag-section-title', textContent: 'Power Systems (6S)' })
    );
    ds.cellVoltages.forEach((v, i) => {
      const pct = clamp(((v - 3.3) / (4.2 - 3.3)) * 100, 0, 100);
      const cc = v < 3.5 ? 'var(--red)' : v < 3.7 ? 'var(--amber)' : 'var(--green)';
      powerSection.appendChild(
        el('div', { className: 'diag-cell-row' },
          el('span', { style: { color: 'var(--text-dim)', minWidth: '24px' }, textContent: 'C' + (i + 1) }),
          el('div', { className: 'diag-cell-bar' },
            el('div', { id: 'dg-cell-' + i + '-bar', className: 'diag-cell-bar-fill', style: { width: pct.toFixed(0) + '%', background: cc } })
          ),
          el('span', { id: 'dg-cell-' + i, style: { color: cc, minWidth: '40px', textAlign: 'right' }, textContent: v.toFixed(2) + 'V' })
        )
      );
    });
    powerSection.appendChild(
      el('div', { style: { marginTop: '6px' } },
        el('div', { className: 'diag-status-indicator' },
          el('span', { className: 'diag-status-dot ok' }),
          el('span', { style: { color: 'var(--text)' }, textContent: 'BMS Health: ' + ds.bmsHealth + '%' })
        )
      )
    );

    // Run Diagnostics section
    const diagButton = el('button', {
      className: 'diag-run-btn',
      id: 'diag-run-btn',
      dataset: { assetId: asset.id },
      textContent: ds.scanning ? 'SCANNING...' : 'RUN DIAGNOSTICS',
      onClick: function() { runDiagnosticScan(asset.id); }
    });
    if (ds.scanning) diagButton.disabled = true;

    const progressFill = el('div', { className: 'diag-progress-fill', id: 'diag-progress-fill', style: { width: ds.scanProgress + '%' } });
    const progressBar = el('div', { className: 'diag-progress', id: 'diag-progress' }, progressFill);
    if (!ds.scanning) progressBar.style.display = 'none';

    const runSection = el('div', { className: 'diag-section' }, diagButton, progressBar);

    if (ds.lastScanResults) {
      const resultsDiv = el('div', { className: 'diag-results', id: 'diag-results' },
        el('div', { style: { fontFamily: 'var(--font-label)', fontSize: '9px', color: 'var(--text-dim)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: '6px' }, textContent: 'Scan Results' })
      );
      ds.lastScanResults.forEach(r => {
        const ic = r.severity === 'ok' ? 'var(--green)' : r.severity === 'warning' ? 'var(--amber)' : 'var(--red)';
        const dot = r.severity === 'ok' ? 'ok' : r.severity === 'warning' ? 'warning' : 'fault';
        resultsDiv.appendChild(
          el('div', { className: 'diag-result-item' },
            el('span', { className: 'diag-result-icon' },
              el('span', { className: 'diag-status-dot ' + dot })
            ),
            el('span', { style: { color: ic }, textContent: r.msg })
          )
        );
      });
      runSection.appendChild(resultsDiv);
    }

    // Assemble and replace content
    container.textContent = '';
    container.appendChild(systemHealthSection);
    container.appendChild(hwStatusSection);
    container.appendChild(sensorSection);
    container.appendChild(commSection);
    container.appendChild(powerSection);
    container.appendChild(runSection);
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
      tempEl.textContent = m.temp.toFixed(1) + '\u00b0C';
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
  const hwContainer = document.getElementById('hardware-content');
  if (!hwContainer) return;
  if (!asset) { hwContainer.innerHTML = '<div class="no-selection">SELECT AN ASSET</div>'; state.hwRenderedAsset = null; return; }

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

  // Status helpers — return DOM elements instead of HTML strings
  function motorStatusEl(health, idx) {
    if (health > 90) return el('span', { id: 'hw-motor-' + idx + '-status', className: 'hw-part-status nominal', textContent: 'NOMINAL' });
    if (health >= 70) return el('span', { id: 'hw-motor-' + idx + '-status', className: 'hw-part-status warning', textContent: 'DEGRADED' });
    return el('span', { id: 'hw-motor-' + idx + '-status', className: 'hw-part-status fault', textContent: 'FAULT' });
  }
  function subsysStatusEl(val) {
    if (val > 90) return el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' });
    if (val >= 70) return el('span', { className: 'hw-part-status warning', textContent: 'WARNING' });
    return el('span', { className: 'hw-part-status fault', textContent: 'FAULT' });
  }

  // Helper to build a part row
  function partRow(dotColor, name, model, statusNode) {
    return el('div', { className: 'hw-part-row' },
      el('span', { className: 'hw-part-dot', style: { background: dotColor } }),
      el('span', { className: 'hw-part-name', textContent: name }),
      el('span', { className: 'hw-part-model', textContent: model }),
      statusNode
    );
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

  // ---- SVG DIAGRAM (built with SVG namespace) ----
  const svgNS = 'http://www.w3.org/2000/svg';
  function svgEl(tag, attrs) {
    const e = document.createElementNS(svgNS, tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  }
  function svgText(tag, attrs, text) {
    const e = svgEl(tag, attrs);
    e.textContent = text;
    return e;
  }

  const svgRoot = svgEl('svg', { viewBox: '0 0 200 200', xmlns: svgNS });

  // Arms
  [[60,60],[140,60],[60,140],[140,140]].forEach(([x, y]) => {
    svgRoot.appendChild(svgEl('line', { x1: String(x), y1: String(y), x2: '100', y2: '100', stroke: armColor, 'stroke-width': '3', 'stroke-linecap': 'round' }));
  });

  // Center body
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '80', y: '80', width: '40', height: '40', rx: '6', ry: '6', fill: bodyColor, opacity: '0.7' }));

  // Flight controller
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '88', y: '88', width: '16', height: '12', rx: '2', ry: '2', fill: fcColor, opacity: '0.6' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '96', y: '96', 'font-size': '6', fill: labelColor }, 'FC'));

  // Battery
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '86', y: '103', width: '20', height: '10', rx: '2', ry: '2', fill: batColor, opacity: '0.5' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '96', y: '110', 'font-size': '6', fill: labelColor }, 'BAT'));

  // Motors
  const motorConfigs = [
    { cx: '60', cy: '60', labelY: '42', mc: m1c, label: 'M1' },
    { cx: '140', cy: '60', labelY: '42', mc: m2c, label: 'M2' },
    { cx: '60', cy: '140', labelY: '162', mc: m3c, label: 'M3' },
    { cx: '140', cy: '140', labelY: '162', mc: m4c, label: 'M4' },
  ];
  motorConfigs.forEach(cfg => {
    svgRoot.appendChild(svgEl('circle', { class: 'hw-component', cx: cfg.cx, cy: cfg.cy, r: '14', fill: 'none', stroke: cfg.mc, 'stroke-width': '2', opacity: '0.8' }));
    svgRoot.appendChild(svgEl('circle', { cx: cfg.cx, cy: cfg.cy, r: '4', fill: cfg.mc, opacity: '0.9' }));
    svgRoot.appendChild(svgText('text', { class: 'hw-label', x: cfg.cx, y: cfg.labelY, 'font-size': '7', fill: labelColor }, cfg.label));
  });

  // GPS
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '93', y: '66', width: '14', height: '8', rx: '2', ry: '2', fill: gpsColor, opacity: '0.6' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '100', y: '64', 'font-size': '6', fill: labelColor }, 'GPS'));

  // Camera/Gimbal
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '92', y: '126', width: '16', height: '10', rx: '2', ry: '2', fill: camColor, opacity: '0.6' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '100', y: '143', 'font-size': '6', fill: labelColor }, 'CAM'));

  // RX antenna
  svgRoot.appendChild(svgEl('circle', { class: 'hw-component', cx: '126', cy: '96', r: '4', fill: rxColor, opacity: '0.6' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '126', y: '88', 'font-size': '6', fill: labelColor }, 'RX'));

  // VTX
  svgRoot.appendChild(svgEl('rect', { class: 'hw-component', x: '68', y: '93', width: '8', height: '6', rx: '1', ry: '1', fill: camColor, opacity: '0.4' }));
  svgRoot.appendChild(svgText('text', { class: 'hw-label', x: '72', y: '88', 'font-size': '6', fill: labelColor }, 'VTX'));

  const diagramDiv = el('div', { className: 'hw-diagram' }, svgRoot);

  // ---- PARTS MANIFEST ----
  const partsList = el('div', { className: 'hw-parts-list' });

  // Frame & Propulsion
  partsList.appendChild(el('div', { className: 'hw-section-title', textContent: 'Frame & Propulsion' }));
  partsList.appendChild(partRow(armColor, 'Frame', 'Sentinel X4 Carbon 450mm', subsysStatusEl(ds.subsystems.propulsion)));
  partsList.appendChild(partRow(m1c, 'Motor 1', 'T-Motor U8 Lite KV100', motorStatusEl(ds.motors[0].health, 0)));
  partsList.appendChild(partRow(m2c, 'Motor 2', 'T-Motor U8 Lite KV100', motorStatusEl(ds.motors[1].health, 1)));
  partsList.appendChild(partRow(m3c, 'Motor 3', 'T-Motor U8 Lite KV100', motorStatusEl(ds.motors[2].health, 2)));
  partsList.appendChild(partRow(m4c, 'Motor 4', 'T-Motor U8 Lite KV100', motorStatusEl(ds.motors[3].health, 3)));
  partsList.appendChild(partRow(armColor, 'ESC', 'Hobbywing X-Rotor 40A', subsysStatusEl(ds.subsystems.propulsion)));
  partsList.appendChild(partRow(armColor, 'Propeller', 'T-Motor P18x6.1 CF', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));

  // Avionics
  partsList.appendChild(el('div', { className: 'hw-section-title', textContent: 'Avionics' }));
  partsList.appendChild(partRow(fcColor, 'Flight Controller', 'Cube Orange+ H7', subsysStatusEl(ds.subsystems.avionics)));
  partsList.appendChild(partRow(gpsColor, 'GPS/GNSS', 'Here3+ RTK', subsysStatusEl(ds.subsystems.avionics)));
  partsList.appendChild(partRow(fcColor, 'IMU', 'ICM-42688-P (x2 redundant)', subsysStatusEl(ds.subsystems.sensors)));
  partsList.appendChild(partRow(fcColor, 'Barometer', 'MS5611 (x2 redundant)', subsysStatusEl(ds.subsystems.sensors)));
  partsList.appendChild(partRow(fcColor, 'Magnetometer', 'RM3100', subsysStatusEl(ds.subsystems.sensors)));
  partsList.appendChild(partRow(fcColor, 'LiDAR Altimeter', 'TFMini-S', subsysStatusEl(ds.subsystems.sensors)));

  // Communications
  partsList.appendChild(el('div', { className: 'hw-section-title', textContent: 'Communications' }));
  partsList.appendChild(partRow(rxColor, 'Telemetry Radio', 'RFD900x 900MHz', subsysStatusEl(ds.subsystems.comms)));
  partsList.appendChild(partRow(rxColor, 'RC Receiver', 'TBS Crossfire Nano', subsysStatusEl(ds.subsystems.comms)));
  partsList.appendChild(partRow(camColor, 'Video TX', 'DJI O3 Air Unit', subsysStatusEl(ds.subsystems.comms)));

  // Power
  partsList.appendChild(el('div', { className: 'hw-section-title', textContent: 'Power' }));
  partsList.appendChild(partRow(batColor, 'Battery', 'Tattu 6S 10000mAh 25C', subsysStatusEl(ds.subsystems.power)));
  partsList.appendChild(partRow(batColor, 'PDB/BEC', 'Matek FCHUB-6S', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));

  // Payload (role-based)
  partsList.appendChild(el('div', { className: 'hw-section-title', textContent: 'Payload' }));
  const role = asset.role || 'PRIMARY';
  if (role === 'ISR') {
    partsList.appendChild(partRow(camColor, 'Gimbal', 'Gremsy S1V3', subsysStatusEl(ds.subsystems.sensors)));
    partsList.appendChild(partRow(camColor, 'Camera', 'Sony A7R IV + 85mm', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'IR Sensor', 'FLIR Boson 640', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'SAR Module', 'uRAD A1-160GHz', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
  } else if (role === 'OVERWATCH') {
    partsList.appendChild(partRow(camColor, 'Gimbal', 'Gremsy T3V3', subsysStatusEl(ds.subsystems.sensors)));
    partsList.appendChild(partRow(camColor, 'Camera', 'Sony A7R IV + 200mm', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'IR Sensor', 'FLIR Boson 640', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
  } else if (role === 'LOGISTICS') {
    partsList.appendChild(partRow(camColor, 'Gimbal', 'Gremsy S1V3', subsysStatusEl(ds.subsystems.sensors)));
    partsList.appendChild(partRow(camColor, 'Camera', 'Sony A6400 + 20mm', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'Drop Module', 'SkyDrop DM-200', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
  } else if (role === 'ESCORT') {
    partsList.appendChild(partRow(camColor, 'Gimbal', 'Gremsy Pixy U', subsysStatusEl(ds.subsystems.sensors)));
    partsList.appendChild(partRow(camColor, 'Camera', 'Sony A7R IV + 35mm', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'IR Sensor', 'FLIR Boson 320', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
  } else {
    // PRIMARY / default
    partsList.appendChild(partRow(camColor, 'Gimbal', 'Gremsy S1V3', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'Camera', 'Sony A7R IV + 35mm', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
    partsList.appendChild(partRow(camColor, 'IR Sensor', 'FLIR Boson 640', el('span', { className: 'hw-part-status nominal', textContent: 'NOMINAL' })));
  }

  hwContainer.textContent = '';
  hwContainer.appendChild(diagramDiv);
  hwContainer.appendChild(partsList);
}

export { getDiagState, updateDiagnosticsPanel, runDiagnosticScan, updateHardwarePanel };
