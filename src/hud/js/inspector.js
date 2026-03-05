// inspector.js — Inspector panel and properties
import { state } from './state.js';
import { ASSET_DEFS } from './constants.js';
import { _css, batteryColor, rssiColor, toMGRS, utcTimeStamp } from './utils.js';
import { updateDiagnosticsPanel, updateHardwarePanel } from './diagnostics.js';
import { updateInsightsPanel } from './insights.js';

/* ==============================================================
   MODE CALLBACK PROVIDER
   ============================================================== */
let _setMode = null;
export function setModeProvider(fn) { _setMode = fn; }

// Telemetry pulse animation helper
function pulseValue(el, newText) {
  if (el && el.textContent !== newText) {
    el.textContent = newText;
    el.classList.remove('value-updated');
    void el.offsetWidth; // force reflow
    el.classList.add('value-updated');
  }
}
function pulseTelemValues(asset) {
  const key = asset.id;
  const prev = state._prevTelemValues[key] || {};
  const cur = {
    lat: asset.lat.toFixed(6),
    lon: asset.lng.toFixed(6),
    alt: asset.altitude.toFixed(1),
    speed: asset.speed.toFixed(1),
    heading: asset.heading.toFixed(1),
    battery: asset.battery.toFixed(1) + '%'
  };
  // Only pulse if there is a meaningful change (not just floating point noise)
  const thresh = { lat: 0.00001, lon: 0.00001, alt: 0.3, speed: 0.2, heading: 1.0, battery: 0.2 };
  const numPrev = {
    lat: parseFloat(prev.lat || 0), lon: parseFloat(prev.lon || 0),
    alt: parseFloat(prev.alt || 0), speed: parseFloat(prev.speed || 0),
    heading: parseFloat(prev.heading || 0), battery: parseFloat(prev.battery || 0)
  };
  const numCur = {
    lat: parseFloat(cur.lat), lon: parseFloat(cur.lon),
    alt: parseFloat(cur.alt), speed: parseFloat(cur.speed),
    heading: parseFloat(cur.heading), battery: parseFloat(cur.battery)
  };
  const ids = { lat: 'tv-lat', lon: 'tv-lon', alt: 'tv-alt', speed: 'tv-speed', heading: 'tv-heading', battery: 'tv-battery' };
  for (const k of Object.keys(ids)) {
    const el = document.getElementById(ids[k]);
    if (el && prev[k] !== undefined && Math.abs(numCur[k] - numPrev[k]) > thresh[k]) {
      el.classList.remove('value-updated');
      void el.offsetWidth;
      el.classList.add('value-updated');
    }
  }
  state._prevTelemValues[key] = cur;
}

// Build the full properties HTML template for an asset (used on first render / asset switch)
function buildPropsHTML(asset) {
  const batColor = batteryColor(asset.battery);
  const vsColor = asset.verticalSpeed >= 0 ? 'var(--green)' : 'var(--red)';
  const vsSign = asset.verticalSpeed >= 0 ? '+' : '';
  const rssiC = rssiColor(asset.rssi);

  return `
    <div class="ontology-breadcrumb" id="tv-ontology-breadcrumb">
      <span class="ontology-breadcrumb-type">ISR_ASSET</span>
      <span class="ontology-breadcrumb-sep">&rsaquo;</span>
      <span class="ontology-breadcrumb-id" id="tv-breadcrumb-id">${asset.id}</span>
      <span class="ontology-type-badge">OBJECT</span>
    </div>
    <div class="inspector-actions">
      <button class="inspector-action-btn" data-action="command">COMMAND</button>
      <button class="inspector-action-btn" data-action="isr">ISR FEED</button>
      <button class="inspector-action-btn" data-action="focus">FOCUS MAP</button>
      <button class="inspector-action-btn" data-action="history">HISTORY</button>
    </div>
    <!-- ATTITUDE -->
    <div class="detail-section">
      <div class="detail-section-title">Attitude <span class="prop-type-tag">DOUBLE</span><span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via IMU</span></div>
      <div class="attitude-container">
        <div class="attitude-ball" id="attitude-ball" style="transform: translate(-50%, -50%) rotate(${asset.roll}deg) translateY(${-asset.pitch * 1.5}px);">
          <div class="attitude-sky"></div>
          <div class="attitude-ground"></div>
          <div class="attitude-horizon"></div>
        </div>
        <div class="attitude-reticle">
          <div class="reticle-dot"></div>
          <div class="reticle-wing-left"></div>
          <div class="reticle-wing-right"></div>
          <div class="reticle-stem"></div>
        </div>
      </div>
      <div class="attitude-values">
        <div class="attitude-val">
          <div class="attitude-val-label">Roll</div>
          <div class="attitude-val-num" id="tv-roll" style="color:var(--text-secondary)">${asset.roll.toFixed(1)}&deg;</div>
        </div>
        <div class="attitude-val">
          <div class="attitude-val-label">Pitch</div>
          <div class="attitude-val-num" id="tv-pitch" style="color:var(--text-secondary)">${asset.pitch.toFixed(1)}&deg;</div>
        </div>
        <div class="attitude-val">
          <div class="attitude-val-label">Yaw</div>
          <div class="attitude-val-num" id="tv-yaw" style="color:var(--text-secondary)">${asset.yaw.toFixed(1)}&deg;</div>
        </div>
      </div>
    </div>

    <!-- POSITION -->
    <div class="detail-section">
      <div class="detail-section-title">Position<span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via MAVLINK</span></div>
      <div class="telem-grid">
        <div class="telem-cell">
          <div class="telem-cell-label">Lat</div>
          <div class="telem-cell-value" id="tv-lat" style="font-size:12px">${asset.lat.toFixed(6)}</div>
        </div>
        <div class="telem-cell">
          <div class="telem-cell-label">Lon</div>
          <div class="telem-cell-value" id="tv-lon" style="font-size:12px">${asset.lng.toFixed(6)}</div>
        </div>
        <div class="telem-cell">
          <div class="telem-cell-label">Alt AGL (m)</div>
          <div class="telem-cell-value" id="tv-alt">${asset.altitude.toFixed(1)}</div>
        </div>
        <div class="telem-cell">
          <div class="telem-cell-label">Alt MSL (m)</div>
          <div class="telem-cell-value" id="tv-alt-msl">${(asset.altitude + 25.0).toFixed(1)}</div>
        </div>
      </div>
      <div class="telem-pair"><span class="telem-label">MGRS</span><span class="telem-value" id="tv-mgrs">${toMGRS(asset.lat, asset.lng)}</span></div>
    </div>

    <!-- VELOCITY -->
    <div class="detail-section">
      <div class="detail-section-title">Velocity <span class="prop-type-tag">DOUBLE</span><span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via MAVLINK</span></div>
      <div class="telem-grid">
        <div class="telem-cell">
          <div class="telem-cell-label">Ground Speed (m/s)</div>
          <div class="telem-cell-value" id="tv-speed">${asset.speed.toFixed(1)}</div>
        </div>
        <div class="telem-cell">
          <div class="telem-cell-label">Vert Speed (m/s)</div>
          <div class="telem-cell-value" id="tv-vspeed" style="color:${vsColor}">${vsSign}${asset.verticalSpeed.toFixed(2)}</div>
        </div>
        <div class="telem-cell" style="grid-column:1/-1">
          <div class="telem-cell-label">Heading (&deg;)</div>
          <div class="telem-cell-value" id="tv-heading">${asset.heading.toFixed(1)}</div>
        </div>
      </div>
    </div>

    <!-- POWER STATE -->
    <div class="detail-section">
      <div class="detail-section-title">Power State <span class="prop-type-tag">DOUBLE</span><span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via BMS</span></div>
      <div class="battery-detail">
        <div class="battery-big-num" id="tv-battery" style="color:${batColor}">${asset.battery.toFixed(1)}%</div>
        <div class="battery-meta">
          <div class="battery-meta-row"><span class="battery-meta-label">Voltage</span><span id="tv-voltage">${asset.voltage.toFixed(1)}V</span></div>
          <div class="battery-meta-row"><span class="battery-meta-label">Current</span><span id="tv-current">${asset.current.toFixed(1)}A</span></div>
        </div>
      </div>
      <div class="battery-detail-bar">
        <div class="battery-detail-bar-fill" id="tv-battery-bar" style="width:${asset.battery}%;background:${batColor}"></div>
      </div>
    </div>

    <!-- POSITIONING -->
    <div class="detail-section">
      <div class="detail-section-title">Positioning <span class="prop-type-tag">GEO_POINT</span><span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via GNSS</span></div>
      <div class="gps-fix-badge">3D FIX</div>
      <div class="gps-grid">
        <div class="gps-cell">
          <div class="gps-cell-label">Satellites</div>
          <div class="gps-cell-value" id="tv-sats">${asset.satellites}</div>
        </div>
        <div class="gps-cell">
          <div class="gps-cell-label">HDOP</div>
          <div class="gps-cell-value" id="tv-hdop">${asset.hdop.toFixed(2)}</div>
        </div>
      </div>
    </div>

    <!-- COMMLINK -->
    <div class="detail-section">
      <div class="detail-section-title">Commlink<span class="prop-type-tag" style="margin-left:auto;opacity:0.5">via MESH-CTL</span></div>
      <div class="link-cards">
        <div class="link-card">
          <div class="link-card-value" id="tv-rssi" style="color:${rssiC}">${asset.rssi.toFixed(0)}</div>
          <div class="link-card-label">RSSI</div>
        </div>
        <div class="link-card">
          <div class="link-card-value" id="tv-linkq">${asset.linkQuality.toFixed(0)}<span class="link-card-unit">%</span></div>
          <div class="link-card-label">Quality</div>
        </div>
        <div class="link-card">
          <div class="link-card-value" id="tv-latency">${asset.latency.toFixed(0)}<span class="link-card-unit">ms</span></div>
          <div class="link-card-label">Latency</div>
        </div>
      </div>
    </div>

    <!-- STATUS -->
    <div class="detail-section">
      <div class="detail-section-title">Status <span class="prop-type-tag">ENUM</span></div>
      <div class="drone-status-badge ${asset.status.toLowerCase()}" id="tv-status" style="display:inline-block;font-size:11px;padding:3px 10px">${asset.status.replace(/_/g,' ')}</div>
    </div>
    <div class="detail-section" style="padding:4px 12px 8px">
      <div class="detail-section-title" id="provenance-toggle" style="cursor:pointer;user-select:none">
        Provenance <span class="prop-type-tag">AUDIT</span>
        <span style="float:right;font-size:8px;color:var(--text-tertiary)" id="provenance-arrow">&#9656;</span>
      </div>
      <div id="provenance-chain" style="display:none;font:9px var(--font-data);color:var(--text-dim);line-height:1.8;padding-top:4px">
        <div>v4 // SIM-ENGINE // <span id="tv-provenance"></span>Z // telemetry_update</div>
        <div>v3 // MAVLINK-BRIDGE // <span class="prov-ts">-3s</span> // position_transform</div>
        <div>v2 // MESH-CONTROLLER // <span class="prov-ts">-5s</span> // link_quality_check</div>
        <div>v1 // ASSET-REGISTRY // 2026-03-04T14:00:00Z // initial_registration</div>
      </div>
    </div>
  `;
}

function updateInspector(asset) {
  if (!asset) {
    document.getElementById('detail-header').textContent = 'INSPECTOR';
    document.getElementById('detail-content').innerHTML = '<div class="no-selection">SELECT AN ASSET</div>';
    document.getElementById('relationships-content').innerHTML = '';
    document.getElementById('timeline-content').innerHTML = '';
    const diagEl = document.getElementById('diagnostics-content');
    if (diagEl) diagEl.innerHTML = '<div class="no-selection">SELECT AN ASSET</div>';
    const hwEl = document.getElementById('hardware-content');
    if (hwEl) hwEl.innerHTML = '<div class="no-selection">SELECT AN ASSET</div>';
    state.inspectorRenderedAsset = null;
    return;
  }

  document.getElementById('detail-header').textContent = asset.id + ' // ' + asset.role;

  const content = document.getElementById('detail-content');

  // Full DOM rebuild only when switching to a different asset
  if (state.inspectorRenderedAsset !== asset.id) {
    state.inspectorRenderedAsset = asset.id;
    content.innerHTML = buildPropsHTML(asset);

    // Provenance toggle handler
    const provToggle = document.getElementById('provenance-toggle');
    if (provToggle) {
      provToggle.addEventListener('click', function() {
        const chain = document.getElementById('provenance-chain');
        const arrow = document.getElementById('provenance-arrow');
        if (chain && arrow) {
          const isHidden = chain.style.display === 'none';
          chain.style.display = isHidden ? 'block' : 'none';
          arrow.innerHTML = isHidden ? '&#9662;' : '&#9656;';
        }
      });
    }

    // Action button handlers
    const actionBtns = content.querySelectorAll('.inspector-action-btn');
    actionBtns.forEach(function(btn) {
      btn.addEventListener('click', function() {
        const action = btn.getAttribute('data-action');
        if (action === 'command') { if (_setMode) _setMode('TASK'); }
        else if (action === 'isr') { if (_setMode) _setMode('ISR'); }
        else if (action === 'focus') { state.map.setView([asset.lat, asset.lng], 17); }
        else if (action === 'history') {
          const timeTab = document.querySelector('.inspector-tab[data-tab="timeline"]');
          if (timeTab) timeTab.click();
        }
      });
    });

    // Relationships tab (rebuilt on asset switch only — static per asset)
    const relContent = document.getElementById('relationships-content');
    const isPrimary = asset.role === 'PRIMARY';
    relContent.innerHTML = `
      <div style="padding:8px 12px">
        <div class="relationship-card">
          <div class="link-type-label">Link[memberOf] <span class="link-cardinality">1:1</span></div>
          <div class="link-direction">${asset.id} <span style="color:var(--accent)">&rarr;</span> TF-SABER-01</div>
        </div>
        ${isPrimary ? '' : `<div class="relationship-card">
          <div class="link-type-label">Link[reportsTo] <span class="link-cardinality">N:1</span></div>
          <div class="link-direction">${asset.id} <span style="color:var(--accent)">&rarr;</span> ALPHA-1</div>
        </div>`}
        <div class="relationship-card">
          <div class="link-type-label">Link[assignedRole] <span class="link-cardinality">1:1</span></div>
          <div class="link-direction">${asset.id} <span style="color:var(--accent)">&rarr;</span> ${asset.role}</div>
        </div>
        <div class="relationship-card">
          <div class="link-type-label">Link[operatesIn] <span class="link-cardinality">N:1</span></div>
          <div class="link-direction">${asset.id} <span style="color:var(--accent)">&rarr;</span> AO-CAMPUS</div>
        </div>
        <div class="relationship-card">
          <div class="link-type-label">Link[commLink] <span class="link-cardinality">1:N</span></div>
          <div class="link-direction">${asset.id} <span style="color:var(--accent)">&harr;</span> MESH-NET-01</div>
        </div>
      </div>
    `;
  }

  // --- Targeted DOM updates for every tick (no innerHTML rebuild) ---
  const el = (id) => document.getElementById(id);

  // Attitude values
  const rollEl = el('tv-roll');
  if (rollEl) rollEl.textContent = asset.roll.toFixed(1) + '\u00B0';
  const pitchEl = el('tv-pitch');
  if (pitchEl) pitchEl.textContent = asset.pitch.toFixed(1) + '\u00B0';
  const yawEl = el('tv-yaw');
  if (yawEl) yawEl.textContent = asset.yaw.toFixed(1) + '\u00B0';

  // Attitude ball CSS transform
  const ball = el('attitude-ball');
  if (ball) ball.style.transform = `translate(-50%, -50%) rotate(${asset.roll}deg) translateY(${-asset.pitch * 1.5}px)`;

  // Position — use pulseValue for change-detection animation
  pulseValue(el('tv-lat'), asset.lat.toFixed(6));
  pulseValue(el('tv-lon'), asset.lng.toFixed(6));
  pulseValue(el('tv-alt'), asset.altitude.toFixed(1));
  pulseValue(el('tv-alt-msl'), (asset.altitude + 25.0).toFixed(1));

  // MGRS targeted update
  const mgrsEl = document.getElementById('tv-mgrs');
  if (mgrsEl) mgrsEl.textContent = toMGRS(asset.lat, asset.lng);

  // Velocity
  pulseValue(el('tv-speed'), asset.speed.toFixed(1));
  const vsColor = asset.verticalSpeed >= 0 ? 'var(--green)' : 'var(--red)';
  const vsSign = asset.verticalSpeed >= 0 ? '+' : '';
  const vspeedEl = el('tv-vspeed');
  if (vspeedEl) {
    pulseValue(vspeedEl, vsSign + asset.verticalSpeed.toFixed(2));
    vspeedEl.style.color = vsColor;
  }
  pulseValue(el('tv-heading'), asset.heading.toFixed(1));

  // Power state — battery with color
  const batColor = batteryColor(asset.battery);
  const batEl = el('tv-battery');
  if (batEl) {
    pulseValue(batEl, asset.battery.toFixed(1) + '%');
    batEl.style.color = batColor;
  }
  const voltEl = el('tv-voltage');
  if (voltEl) voltEl.textContent = asset.voltage.toFixed(1) + 'V';
  const curEl = el('tv-current');
  if (curEl) curEl.textContent = asset.current.toFixed(1) + 'A';
  const batBar = el('tv-battery-bar');
  if (batBar) {
    batBar.style.width = asset.battery + '%';
    batBar.style.background = batColor;
  }

  // GPS / Positioning
  const satsEl = el('tv-sats');
  if (satsEl) satsEl.textContent = asset.satellites;
  const hdopEl = el('tv-hdop');
  if (hdopEl) hdopEl.textContent = asset.hdop.toFixed(2);

  // Commlink — RSSI with color
  const rssiC = rssiColor(asset.rssi);
  const rssiEl = el('tv-rssi');
  if (rssiEl) {
    rssiEl.textContent = asset.rssi.toFixed(0);
    rssiEl.style.color = rssiC;
  }
  const linkqEl = el('tv-linkq');
  if (linkqEl) linkqEl.innerHTML = asset.linkQuality.toFixed(0) + '<span class="link-card-unit">%</span>';
  const latencyEl = el('tv-latency');
  if (latencyEl) latencyEl.innerHTML = asset.latency.toFixed(0) + '<span class="link-card-unit">ms</span>';

  // Status badge — update class and text
  const statusEl = el('tv-status');
  if (statusEl) {
    const statusLower = asset.status.toLowerCase();
    statusEl.className = 'drone-status-badge ' + statusLower;
    statusEl.textContent = asset.status.replace(/_/g, ' ');
  }

  // Provenance timestamp
  const provEl = el('tv-provenance');
  if (provEl) provEl.textContent = new Date().toISOString().substring(11, 19);

  // Pulse telemetry values that changed significantly (existing animation system)
  pulseTelemValues(asset);

  // Timeline tab — only rebuild when event count changes
  const tlContent = document.getElementById('timeline-content');
  const events = state.assetTimelines[asset.id] || [];
  if (events.length !== state._lastTimelineCount[asset.id]) {
    state._lastTimelineCount[asset.id] = events.length;
    tlContent.innerHTML = events.slice(-20).reverse().map(e =>
      '<div class="timeline-item"><span class="timeline-time">' + e.time + '</span><span class="timeline-desc">' + e.msg + '</span></div>'
    ).join('') || '<div style="padding:14px;color:var(--text-secondary);font-size:11px">No events recorded</div>';
  }

  // Diagnostics tab (Feature #4)
  updateDiagnosticsPanel(asset);

  // HW tab
  updateHardwarePanel(asset);

  // Insights tab (Feature #5 — Foundry-level analytics)
  updateInsightsPanel(asset);
}

export { updateInspector, buildPropsHTML, pulseValue };
