// assetExplorer.js — Asset list and selection
import { state } from './state.js';
import { ASSET_DEFS } from './constants.js';
import { _css } from './utils.js';

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
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', asset.id === state.selectedDroneId ? 'true' : 'false');
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
      const isSelected = asset.id === state.selectedDroneId;
      item.className = 'asset-item' + (isSelected ? ' selected' : '');
      item.setAttribute('aria-selected', String(isSelected));

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

export { updateAssetExplorer, selectDrone };
