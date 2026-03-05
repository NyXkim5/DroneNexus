// panels.js — All panel renderers, activity stream, event generators
import { state } from './state.js';
import { ASSET_DEFS, MAX_EVENTS } from './constants.js';
import { _css, clamp, rand, randInt, utcTimeStamp, batteryColor, rssiColor, showToast, toMGRS } from './utils.js';

/* ==============================================================
   ACTIVITY GENERATOR
   ============================================================== */
const EVENT_TEMPLATES = [
  { msg: 'OBJ-{n} reached', source: null, severity: 'ok' },
  { msg: 'Deconfliction maneuver initiated', source: null, severity: 'warn' },
  { msg: 'Wind compensation: {w}m/s {dir}', source: null, severity: 'info' },
  { msg: 'Sensor calibration complete', source: null, severity: 'ok' },
  { msg: 'LIDAR sweep complete', source: null, severity: 'info' },
  { msg: 'Altitude hold adjusted', source: null, severity: 'info' },
  { msg: 'Orbit position corrected', source: null, severity: 'info' },
  { msg: 'COMMLINK mesh topology updated', source: 'COMMS', severity: 'ok' },
  { msg: 'Link quality restored', source: 'COMMS', severity: 'ok' },
  { msg: 'Peer handoff complete', source: 'COMMS', severity: 'info' },
  { msg: 'Navigation update applied', source: 'TASKFORCE', severity: 'info' },
  { msg: 'GPS multipath correction', source: 'TASKFORCE', severity: 'info' },
  { msg: 'Geofence check passed', source: 'TASKFORCE', severity: 'ok' },
  { msg: 'AO coverage nominal', source: 'OVERWATCH', severity: 'ok' },
  { msg: 'Telemetry snapshot archived', source: 'OVERWATCH', severity: 'info' },
  { msg: 'Collection pattern adjusted', source: 'OVERWATCH', severity: 'info' },
  { msg: 'Power state management optimized', source: 'OVERWATCH', severity: 'info' },
  { msg: 'Thermal updraft detected', source: null, severity: 'info' },
  { msg: 'Barometric pressure shift', source: null, severity: 'warn' },
  { msg: 'RF environment scan complete', source: 'COMMS', severity: 'ok' },
];

const WIND_DIRS = ['N', 'NE', 'NW', 'S', 'SE', 'SW', 'E', 'W'];

function generateActivity(assets) {
  const tpl = EVENT_TEMPLATES[randInt(0, EVENT_TEMPLATES.length - 1)];
  let source = tpl.source;
  if (!source) {
    source = assets[randInt(0, assets.length - 1)].id;
  }
  let msg = tpl.msg;
  msg = msg.replace('{n}', randInt(1, 12));
  msg = msg.replace('{w}', (rand(1, 6)).toFixed(1));
  msg = msg.replace('{dir}', WIND_DIRS[randInt(0, WIND_DIRS.length - 1)]);
  return { time: utcTimeStamp(), source, msg, severity: tpl.severity };
}

function generateStateCorrelatedEvents(assets) {
  assets.forEach(d => {
    // Battery warnings
    if (d.battery < 25 && d.battery > 24.5) {
      addEvent({ time: utcTimeStamp(), source: d.id, msg: 'Battery below 25% — monitor required', severity: 'warn' });
    }
    if (d.battery < 15 && d.battery > 14.5) {
      addEvent({ time: utcTimeStamp(), source: d.id, msg: 'Battery critical — RTB recommended', severity: 'alert' });
    }
    // Signal warnings
    if (d.linkQuality < 80 && Math.random() < 0.05) {
      addEvent({ time: utcTimeStamp(), source: d.id, msg: 'Signal degraded: ' + d.linkQuality.toFixed(0) + '% quality', severity: 'warn' });
    }
    // GPS warnings
    if (d.hdop > 2.0 && Math.random() < 0.1) {
      addEvent({ time: utcTimeStamp(), source: d.id, msg: 'GPS accuracy reduced — HDOP ' + d.hdop.toFixed(1), severity: 'warn' });
    }
    // Comms latency
    if (d.latency > 100 && Math.random() < 0.05) {
      addEvent({ time: utcTimeStamp(), source: d.id, msg: 'High latency: ' + d.latency.toFixed(0) + 'ms — check link', severity: 'warn' });
    }
  });
}

/* ==============================================================
   LEFT PANEL — ASSET EXPLORER RENDERING
   ============================================================== */
function updateAssetExplorer(assets) {
  const container = document.getElementById('asset-list');
  const countEl = document.getElementById('tree-assets-count');
  if (countEl) countEl.textContent = '(' + assets.length + ')';

  assets.forEach(asset => {
    let item = container.querySelector('[data-asset-id="' + asset.id + '"]');

    if (!item) {
      // First render: create the DOM element
      item = document.createElement('div');
      item.className = 'asset-item' + (asset.id === state.selectedDroneId ? ' selected' : '');
      item.dataset.assetId = asset.id;
      item.addEventListener('click', () => selectDrone(asset.id));
      item.setAttribute('role', 'button');
      item.setAttribute('tabindex', '0');
      item.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectDrone(asset.id); } });

      const roleClass = asset.role.toLowerCase();
      const statusClass = asset.status.toLowerCase().replace(/_/g, '_');

      const bat = asset.battery != null ? asset.battery : 100;
      const sig = asset.linkQuality != null ? asset.linkQuality : 100;
      const batCol = bat > 50 ? 'var(--green)' : bat > 20 ? 'var(--amber)' : 'var(--red)';
      const sigCol = sig > 85 ? 'var(--green)' : sig > 60 ? 'var(--amber)' : 'var(--red)';

      item.innerHTML = `
        <div class="asset-item-top">
          <span class="asset-dot" style="background:${asset.color}"></span>
          <span class="asset-designator">${asset.id}</span>
          <span class="asset-classification ${roleClass}">${asset.role}</span>
        </div>
        <div style="padding-left:12px;margin-top:2px">
          <span class="asset-status ${statusClass}" data-field="status">${asset.status.replace(/_/g,' ')}</span>
        </div>
        <div class="asset-vitals">
          <div class="asset-micro-bar">
            <span class="asset-micro-bar-label">BAT</span>
            <div class="asset-micro-bar-track">
              <div class="asset-micro-bar-fill" data-field="bat-fill" style="width:${bat}%;background:${batCol}"></div>
            </div>
            <span class="asset-micro-bar-pct" data-field="bat-pct" style="color:${batCol}">${Math.round(bat)}%</span>
          </div>
          <div class="asset-micro-bar">
            <span class="asset-micro-bar-label">SIG</span>
            <div class="asset-micro-bar-track">
              <div class="asset-micro-bar-fill" data-field="sig-fill" style="width:${sig}%;background:${sigCol}"></div>
            </div>
            <span class="asset-micro-bar-pct" data-field="sig-pct" style="color:${sigCol}">${Math.round(sig)}%</span>
          </div>
        </div>
      `;
      container.appendChild(item);
    } else {
      // Update: only change what varies
      item.className = 'asset-item' + (asset.id === state.selectedDroneId ? ' selected' : '');

      const statusEl = item.querySelector('[data-field="status"]');
      if (statusEl) {
        const statusText = asset.status.replace(/_/g, ' ');
        const statusClass = asset.status.toLowerCase().replace(/_/g, '_');
        if (statusEl.textContent !== statusText) statusEl.textContent = statusText;
        statusEl.className = 'asset-status ' + statusClass;
      }

      // In-place update battery micro-bar
      const bat = asset.battery != null ? asset.battery : 100;
      const batCol = bat > 50 ? 'var(--green)' : bat > 20 ? 'var(--amber)' : 'var(--red)';
      const batFill = item.querySelector('[data-field="bat-fill"]');
      if (batFill) {
        batFill.style.width = bat + '%';
        batFill.style.background = batCol;
      }
      const batPct = item.querySelector('[data-field="bat-pct"]');
      if (batPct) {
        batPct.textContent = Math.round(bat) + '%';
        batPct.style.color = batCol;
      }

      // In-place update signal micro-bar
      const sig = asset.linkQuality != null ? asset.linkQuality : 100;
      const sigCol = sig > 85 ? 'var(--green)' : sig > 60 ? 'var(--amber)' : 'var(--red)';
      const sigFill = item.querySelector('[data-field="sig-fill"]');
      if (sigFill) {
        sigFill.style.width = sig + '%';
        sigFill.style.background = sigCol;
      }
      const sigPct = item.querySelector('[data-field="sig-pct"]');
      if (sigPct) {
        sigPct.textContent = Math.round(sig) + '%';
        sigPct.style.color = sigCol;
      }
    }
  });

  // Remove items for assets that no longer exist
  container.querySelectorAll('[data-asset-id]').forEach(el => {
    if (!assets.find(a => a.id === el.dataset.assetId)) el.remove();
  });
}

/* ==============================================================
   RIGHT PANEL — INSPECTOR RENDERING
   ============================================================== */
function selectDrone(id) {
  // Remove previous pulse ring
  if (state.selectedDroneId && state.mapMarkers[state.selectedDroneId] && state.mapMarkers[state.selectedDroneId]._pulseRing) {
    state.map.removeLayer(state.mapMarkers[state.selectedDroneId]._pulseRing);
    state.mapMarkers[state.selectedDroneId]._pulseRing = null;
  }

  state.selectedDroneId = id;
  // Pan map to asset location (Issue #3)
  if (state.map && state.mapMarkers[id]) {
    const latlng = state.mapMarkers[id].getLatLng();
    state.map.panTo(latlng, { animate: true, duration: 0.5 });

    // Add pulse ring around selected marker
    const def = ASSET_DEFS.find(d => d.id === id);
    const ringColor = def ? def.color : _css('--accent');
    const ring = L.circleMarker(latlng, {
      radius: 16,
      color: ringColor,
      fill: false,
      weight: 2,
      opacity: 0.5,
      className: 'marker-pulse-ring'
    }).addTo(state.map);
    const el = ring.getElement();
    if (el) el.style.animation = 'marker-ring-pulse 2s ease-in-out infinite';
    state.mapMarkers[id]._pulseRing = ring;
  }
  // Update FOV cone highlight for selection change
  Object.keys(state.fovLayers).forEach(assetId => {
    const isSelected = assetId === id;
    state.fovLayers[assetId].setStyle({
      fillOpacity: isSelected ? 0.15 : 0.08,
      opacity:     isSelected ? 0.25 : 0.15
    });
  });
}

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
        if (action === 'command') { window.setMode('TASK'); }
        else if (action === 'isr') { window.setMode('ISR'); }
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
  html += '<button class="diag-run-btn" id="diag-run-btn" onclick="runDiagnosticScan(\'' + asset.id + '\')"' + (ds.scanning ? ' disabled' : '') + '>' + (ds.scanning ? 'SCANNING...' : 'RUN DIAGNOSTICS') + '</button>';
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
        const allAssets = window._overwatchAssets || [];
        const asset = allAssets.find(a => a.id === assetId);
        if (asset) updateDiagnosticsPanel(asset);
      }
    }
  }, 80);
}

/* ==============================================================
   INSIGHTS PANEL RENDERER (Feature #5 — Foundry-level Analytics)
   ============================================================== */
function getInsightState(assetId) {
  if (!state.insightState[assetId]) {
    state.insightState[assetId] = {
      batteryHistory: [],
      speedHistory: [],
      altHistory: [],
      signalHistory: [],
      anomalies: [],
      lastUpdate: 0,
    };
  }
  return state.insightState[assetId];
}

function drawMiniSparkline(canvasEl, data, color) {
  if (!canvasEl || data.length < 2) return;
  const ctx = canvasEl.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvasEl.clientWidth;
  const h = canvasEl.clientHeight;
  canvasEl.width = w * dpr;
  canvasEl.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  let minV = Infinity, maxV = -Infinity;
  data.forEach(v => { if (v < minV) minV = v; if (v > maxV) maxV = v; });
  const range = maxV - minV || 1;
  const step = w / (data.length - 1);
  ctx.beginPath();
  data.forEach((v, i) => {
    const x = i * step;
    const y = 2 + (h - 4) - ((v - minV) / range) * (h - 4);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.lineJoin = 'round';
  ctx.stroke();
}

function updateInsightsPanel(asset) {
  const container = document.getElementById('insights-content');
  if (!container) return;
  if (!asset) {
    container.innerHTML = '<div class="no-selection">SELECT AN ASSET</div>';
    state.insightsRenderedAsset = null;
    return;
  }

  const is = getInsightState(asset.id);
  const now = Date.now();

  // Update rolling history (max 30 entries, throttle to ~1s intervals)
  if (now - is.lastUpdate > 900) {
    is.lastUpdate = now;
    is.batteryHistory.push(asset.battery);
    is.speedHistory.push(asset.speed || 0);
    is.altHistory.push(asset.altitude || 0);
    is.signalHistory.push(asset.linkQuality || 100);
    if (is.batteryHistory.length > 30) is.batteryHistory.shift();
    if (is.speedHistory.length > 30) is.speedHistory.shift();
    if (is.altHistory.length > 30) is.altHistory.shift();
    if (is.signalHistory.length > 30) is.signalHistory.shift();
  }

  // --- Compute predictive analytics ---
  const battery = asset.battery || 100;
  const speed = asset.speed || 0;
  const alt = asset.altitude || 0;
  const signal = asset.linkQuality || 100;

  // Battery drain rate estimation
  let drainRate = 0.5; // default %/min
  if (is.batteryHistory.length >= 5) {
    const oldest = is.batteryHistory[0];
    const newest = is.batteryHistory[is.batteryHistory.length - 1];
    const elapsed = (is.batteryHistory.length - 1); // seconds approx
    if (elapsed > 0 && oldest > newest) {
      drainRate = ((oldest - newest) / elapsed) * 60; // per minute
    }
  }
  if (drainRate < 0.1) drainRate = 0.5; // fallback

  const minsRemaining = battery / drainRate;
  const etaHrs = Math.floor(minsRemaining / 60);
  const etaMins = Math.floor(minsRemaining % 60);
  const etaStr = etaHrs > 0 ? etaHrs + 'h ' + etaMins + 'm' : etaMins + 'm';

  // Estimated range (km) — speed in m/s * time remaining in seconds / 1000
  const rangeKm = ((speed * minsRemaining * 60) / 1000).toFixed(1);

  // Distance from origin (approximate — use lat/lng delta)
  const originLat = asset.homePosition.lat;
  const originLng = asset.homePosition.lng;
  const dLat = (asset.lat - originLat) * 111320;
  const dLng = (asset.lng - originLng) * 111320 * Math.cos(asset.lat * Math.PI / 180);
  const distFromOrigin = Math.sqrt(dLat * dLat + dLng * dLng); // meters

  // Optimal RTB time — when to head back based on distance and speed
  const rtbTimeSec = speed > 0 ? distFromOrigin / speed : 0;
  const rtbBatteryNeeded = (rtbTimeSec / 60) * drainRate + 10; // +10% safety margin
  const optimalRtbMins = Math.max(0, (battery - rtbBatteryNeeded) / drainRate);
  const rtbStr = optimalRtbMins > 60 ? Math.floor(optimalRtbMins / 60) + 'h ' + Math.floor(optimalRtbMins % 60) + 'm' : Math.floor(optimalRtbMins) + 'm';

  // Mission completion probability
  const healthScore = getDiagState(asset.id).overallHealth;
  const batteryFactor = Math.min(battery / 30, 1); // <30% = reduced
  const healthFactor = Math.min(healthScore / 70, 1);
  const signalFactor = Math.min(signal / 80, 1);
  const missionProb = Math.min(100, Math.round(batteryFactor * healthFactor * signalFactor * 100));

  // --- Anomaly detection ---
  const anomalies = [];
  if (drainRate > 0.8) {
    anomalies.push({ msg: 'Abnormal battery drain: ' + drainRate.toFixed(2) + '%/min', severity: 'warning', icon: '!' });
  }
  if (drainRate > 1.5) {
    anomalies[anomalies.length - 1].severity = 'critical';
  }
  // Motor temp check (use diagnostics state if available)
  const ds = state.diagState[asset.id];
  if (ds) {
    ds.motors.forEach((m, i) => {
      if (m.temp > 48) {
        anomalies.push({ msg: 'Motor ' + (i + 1) + ' temp elevated: ' + m.temp.toFixed(1) + '\u00b0C', severity: m.temp > 55 ? 'critical' : 'warning', icon: 'T' });
      }
    });
  }
  if (signal < 90) {
    anomalies.push({ msg: 'Signal quality degraded: ' + signal.toFixed(0) + '%', severity: signal < 75 ? 'critical' : 'warning', icon: 'S' });
  }
  // Heading variance (check last few speed readings for erratic behavior)
  if (is.speedHistory.length >= 5) {
    const recentSpeeds = is.speedHistory.slice(-5);
    const avgSpd = recentSpeeds.reduce((a, b) => a + b, 0) / recentSpeeds.length;
    const variance = recentSpeeds.reduce((a, b) => a + (b - avgSpd) * (b - avgSpd), 0) / recentSpeeds.length;
    if (variance > 4) {
      anomalies.push({ msg: 'Velocity variance detected: \u03c3\u00b2=' + variance.toFixed(1), severity: 'warning', icon: '\u21c4' });
    }
  }
  if (anomalies.length === 0) {
    anomalies.push({ msg: 'All parameters nominal', severity: 'ok', icon: '\u2713' });
  }

  // --- Risk assessment ---
  let riskScore = 0;
  riskScore += Math.max(0, (100 - battery) * 0.3); // battery depletion
  riskScore += Math.max(0, (100 - healthScore) * 0.25);
  riskScore += Math.max(0, (100 - signal) * 0.2);
  riskScore += anomalies.filter(a => a.severity === 'warning').length * 8;
  riskScore += anomalies.filter(a => a.severity === 'critical').length * 15;
  riskScore = Math.min(100, Math.round(riskScore));
  const riskColor = riskScore < 30 ? 'var(--green)' : riskScore < 60 ? 'var(--amber)' : 'var(--red)';
  const riskLabel = riskScore < 30 ? 'LOW' : riskScore < 60 ? 'MODERATE' : 'HIGH';

  // --- Decision intelligence (recommendations) ---
  const recs = [];
  if (battery < 50) {
    recs.push({ icon: 'P', text: 'RTB recommended in ' + rtbStr + ' \u2014 battery at ' + battery.toFixed(0) + '%' });
  }
  if (ds) {
    const hotMotor = ds.motors.findIndex(m => m.temp > 48);
    if (hotMotor >= 0) {
      recs.push({ icon: 'T', text: 'Motor ' + (hotMotor + 1) + ' temperature elevated \u2014 reduce speed by 15%' });
    }
  }
  if (alt > 100) {
    recs.push({ icon: '\u2191', text: 'Current altitude ' + alt.toFixed(0) + 'm \u2014 consider descent to 85m for optimal wind profile' });
  } else if (alt < 40 && speed > 5) {
    recs.push({ icon: '\u2193', text: 'Low altitude at speed \u2014 increase to 60m minimum for terrain clearance' });
  }
  if (signal < 85) {
    recs.push({ icon: 'S', text: 'Signal at ' + signal.toFixed(0) + '% \u2014 reduce range or increase relay altitude' });
  }
  if (recs.length === 0) {
    recs.push({ icon: '\u2713', text: 'All parameters within optimal range \u2014 continue current mission profile' });
  }

  // --- Trend indicators ---
  function trendIndicator(history) {
    if (history.length < 3) return { cls: 'stable', sym: '\u2192' };
    const recent = history.slice(-3);
    const diff = recent[recent.length - 1] - recent[0];
    if (diff > 0.5) return { cls: 'up', sym: '\u2191' };
    if (diff < -0.5) return { cls: 'down', sym: '\u2193' };
    return { cls: 'stable', sym: '\u2192' };
  }

  const battTrend = trendIndicator(is.batteryHistory);
  const spdTrend = trendIndicator(is.speedHistory);
  const altTrend = trendIndicator(is.altHistory);
  const sigTrend = trendIndicator(is.signalHistory);

  // Build HTML
  let html = '';

  // Section 1: Predictive Analytics
  html += '<div class="insight-section"><div class="insight-section-title">Predictive Analytics</div>';
  html += '<div class="insight-card"><div class="insight-label">Battery Depletion ETA</div>';
  html += '<div class="insight-value"><span id="in-eta">' + etaStr + '</span> remaining<span id="in-batt-trend" class="insight-trend ' + battTrend.cls + '">' + battTrend.sym + '</span></div>';
  html += '<div class="insight-sub">Drain rate: <span id="in-drain">' + drainRate.toFixed(2) + '</span>%/min</div></div>';
  html += '<div class="insight-card"><div class="insight-label">Estimated Range</div>';
  html += '<div class="insight-value"><span id="in-range">' + rangeKm + '</span> km</div>';
  html += '<div class="insight-sub">At current speed: <span id="in-speed">' + speed.toFixed(1) + '</span> m/s</div></div>';
  html += '<div class="insight-card"><div class="insight-label">Optimal RTB Window</div>';
  html += '<div class="insight-value" id="in-rtb">' + rtbStr + '</div>';
  html += '<div class="insight-sub">Safety margin: 10% battery reserve</div></div>';
  html += '<div class="insight-card' + (missionProb < 40 ? ' critical' : missionProb < 70 ? ' warning' : ' positive') + '" id="in-prob-card">';
  html += '<div class="insight-label">Mission Completion Probability</div>';
  html += '<div class="insight-value" id="in-prob">' + missionProb + '%</div>';
  html += '<div class="insight-sub">Based on battery, health, signal composite</div></div>';
  html += '</div>';

  // Section 2: Anomaly Detection
  html += '<div class="insight-section"><div class="insight-section-title">Anomaly Detection</div>';
  anomalies.forEach(a => {
    const cardCls = a.severity === 'critical' ? ' critical' : a.severity === 'warning' ? ' warning' : ' positive';
    html += '<div class="insight-card' + cardCls + '">';
    html += '<div class="insight-label">' + a.icon + ' ' + (a.severity === 'ok' ? 'NOMINAL' : a.severity.toUpperCase()) + '</div>';
    html += '<div class="insight-sub">' + a.msg + '</div></div>';
  });
  html += '</div>';

  // Section 3: Trend Sparklines
  html += '<div class="insight-section"><div class="insight-section-title">Telemetry Trends</div>';
  const sparkData = [
    { label: 'Battery', data: is.batteryHistory, color: _css('--accent'), val: battery.toFixed(0) + '%', trend: battTrend },
    { label: 'Speed', data: is.speedHistory, color: _css('--green'), val: speed.toFixed(1) + ' m/s', trend: spdTrend },
    { label: 'Alt', data: is.altHistory, color: _css('--amber'), val: alt.toFixed(0) + ' m', trend: altTrend },
    { label: 'Signal', data: is.signalHistory, color: _css('--text'), val: signal.toFixed(0) + '%', trend: sigTrend },
  ];
  sparkData.forEach((s, idx) => {
    html += '<div class="insight-sparkline-row">';
    html += '<span class="insight-sparkline-label">' + s.label + '</span>';
    html += '<canvas class="insight-mini-spark" id="insight-spark-' + idx + '"></canvas>';
    html += '<span style="font-family:var(--font-data);font-size:10px;color:var(--text);min-width:55px;text-align:right">' + s.val + '</span>';
    html += '<span class="insight-trend ' + s.trend.cls + '">' + s.trend.sym + '</span>';
    html += '</div>';
  });
  html += '</div>';

  // Section 4: Risk Assessment
  html += '<div class="insight-section"><div class="insight-section-title">Risk Assessment</div>';
  html += '<div class="insight-card' + (riskScore >= 60 ? ' critical' : riskScore >= 30 ? ' warning' : ' positive') + '" id="in-risk-card">';
  html += '<div class="insight-label">Composite Risk Score</div>';
  html += '<div class="insight-value" id="in-risk-val" style="color:' + riskColor + '">' + riskScore + ' / 100 \u2014 ' + riskLabel + '</div>';
  html += '<div class="insight-risk-meter"><div class="insight-risk-fill" id="in-risk-fill" style="width:' + riskScore + '%;background:' + riskColor + '"></div></div>';
  html += '</div>';
  // Risk factors
  html += '<div style="margin-top:4px">';
  html += '<div class="insight-correlation"><span class="insight-corr-pair">Battery depletion</span><span id="in-rf-batt" class="insight-corr-val" style="color:' + (battery < 30 ? 'var(--red)' : battery < 50 ? 'var(--amber)' : 'var(--green)') + '">' + (100 - battery).toFixed(0) + '%</span></div>';
  html += '<div class="insight-correlation"><span class="insight-corr-pair">System health</span><span id="in-rf-health" class="insight-corr-val" style="color:' + (healthScore < 70 ? 'var(--red)' : healthScore < 85 ? 'var(--amber)' : 'var(--green)') + '">' + healthScore.toFixed(0) + '%</span></div>';
  html += '<div class="insight-correlation"><span class="insight-corr-pair">Signal integrity</span><span id="in-rf-signal" class="insight-corr-val" style="color:' + (signal < 75 ? 'var(--red)' : signal < 90 ? 'var(--amber)' : 'var(--green)') + '">' + signal.toFixed(0) + '%</span></div>';
  html += '<div class="insight-correlation"><span class="insight-corr-pair">Active anomalies</span><span id="in-rf-anom" class="insight-corr-val" style="color:' + (anomalies.filter(a => a.severity !== 'ok').length > 0 ? 'var(--amber)' : 'var(--green)') + '">' + anomalies.filter(a => a.severity !== 'ok').length + '</span></div>';
  html += '</div></div>';

  // Section 5: Decision Intelligence
  html += '<div class="insight-section"><div class="insight-section-title">Decision Intelligence</div>';
  recs.forEach(r => {
    html += '<div class="insight-rec">';
    html += '<div class="insight-rec-icon">' + r.icon + '</div>';
    html += '<div class="insight-rec-text">' + r.text + '</div>';
    html += '</div>';
  });
  html += '</div>';

  if (state.insightsRenderedAsset !== asset.id) {
    // Full rebuild on asset switch
    state.insightsRenderedAsset = asset.id;
    container.innerHTML = html;
  } else {
    // Targeted DOM updates for same asset
    const u = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    u('in-eta', etaStr);
    u('in-drain', drainRate.toFixed(2));
    u('in-range', rangeKm);
    u('in-speed', speed.toFixed(1));
    u('in-rtb', rtbStr);
    u('in-prob', missionProb + '%');
    u('in-risk-val', riskScore + ' / 100 \u2014 ' + riskLabel);
    u('in-rf-batt', (100 - battery).toFixed(0) + '%');
    u('in-rf-health', healthScore.toFixed(0) + '%');
    u('in-rf-signal', signal.toFixed(0) + '%');
    u('in-rf-anom', '' + anomalies.filter(a => a.severity !== 'ok').length);

    const riskFill = document.getElementById('in-risk-fill');
    if (riskFill) { riskFill.style.width = riskScore + '%'; riskFill.style.background = riskColor; }
    const riskVal = document.getElementById('in-risk-val');
    if (riskVal) riskVal.style.color = riskColor;
  }

  // Draw sparklines after DOM update
  requestAnimationFrame(() => {
    sparkData.forEach((s, idx) => {
      const canvas = document.getElementById('insight-spark-' + idx);
      drawMiniSparkline(canvas, s.data, s.color);
    });
  });
}


/* ==============================================================
   BOTTOM PANEL — ACTIVITY STREAM
   ============================================================== */

let _eventCallback = null;
export function setEventCallback(cb) { _eventCallback = cb; }

function addEvent(ev) {
  state.activityStream.push(ev);
  if (state.activityStream.length > MAX_EVENTS) state.activityStream.shift();
  // Track per-asset timeline
  if (ev.source && !['OVERWATCH','TASKFORCE','COMMS'].includes(ev.source)) {
    if (!state.assetTimelines[ev.source]) state.assetTimelines[ev.source] = [];
    state.assetTimelines[ev.source].push(ev);
    if (state.assetTimelines[ev.source].length > 50) state.assetTimelines[ev.source].shift();
  }
  renderActivityStream();
  if (_eventCallback) _eventCallback(ev);
}

function renderActivityStream() {
  const container = document.getElementById('event-log');
  const latest = state.activityStream[state.activityStream.length - 1];
  const item = document.createElement('div');
  item.className = 'event-item';
  item.style.animation = 'event-enter 0.2s ease';
  item.innerHTML = `
    <span class="event-time">${latest.time}</span>
    <span class="event-source severity-${latest.severity}">${latest.source}</span>
    <span class="event-msg">${latest.msg}</span>
  `;
  container.appendChild(item);

  while (container.children.length > MAX_EVENTS) {
    container.removeChild(container.firstChild);
  }

  container.scrollTop = container.scrollHeight;
}

// Expose runDiagnosticScan globally for onclick handler
window.runDiagnosticScan = runDiagnosticScan;

export { updateAssetExplorer, selectDrone, buildPropsHTML, updateInspector,
         getDiagState, updateDiagnosticsPanel, runDiagnosticScan,
         updateHardwarePanel, updateInsightsPanel,
         addEvent, renderActivityStream,
         generateActivity, generateStateCorrelatedEvents,
         EVENT_TEMPLATES, pulseValue };
