// map.js — Leaflet map setup and marker/trail/FOV management
import { state } from './state.js';
import { CENTER_LAT, CENTER_LNG, ASSET_DEFS } from './constants.js';
import { _css, buildFovPoints } from './utils.js';

/* ==============================================================
   LEAFLET MAP SETUP
   ============================================================== */

function initMap() {
  state.map = L.map('map', {
    center: [CENTER_LAT, CENTER_LNG],
    zoom: 16,
    zoomControl: false,
    attributionControl: true,
  });
  L.control.zoom({ position: 'bottomleft' }).addTo(state.map);

  // Satellite base layer (darkened via CSS) for terrain visibility
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: '&copy; Esri',
    maxZoom: 19,
    className: 'sat-tiles'
  }).addTo(state.map);
  // Dark label overlay for roads/names
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd',
    maxZoom: 20,
    pane: 'overlayPane'
  }).addTo(state.map);

  // AO boundary polygons (geofences)
  const aoZones = [
    { name: 'AO-CAMPUS', color: _css('--accent'), offset: [0, 0], size: 0.008 },
    { name: 'AO-SPECTRUM', color: _css('--green'), offset: [0.006, -0.008], size: 0.005 },
    { name: 'AO-PARKWAY', color: _css('--amber'), offset: [-0.006, 0.008], size: 0.004 },
    { name: 'AO-RESERVOIR', color: _css('--text-dim'), offset: [0.010, 0.004], size: 0.004 }
  ];
  aoZones.forEach(ao => {
    const lat = CENTER_LAT + ao.offset[0];
    const lng = CENTER_LNG + ao.offset[1];
    const s = ao.size;
    L.polygon([
      [lat + s, lng - s], [lat + s, lng + s],
      [lat - s, lng + s], [lat - s, lng - s]
    ], {
      color: ao.color, weight: 1, opacity: 0.35,
      fillColor: ao.color, fillOpacity: 0.03,
      dashArray: '6,4', interactive: false
    }).addTo(state.map);
    L.marker([lat + s + 0.0005, lng - s], {
      icon: L.divIcon({
        className: '',
        html: '<div style="font:8px var(--font-label);color:' + ao.color + ';letter-spacing:0.5px;white-space:nowrap">' + ao.name + '</div>',
        iconAnchor: [0, 10]
      })
    }).addTo(state.map);
  });

  // Create markers, trails, pattern lines for each asset
  ASSET_DEFS.forEach(def => {
    // SVG chevron icon
    const icon = createDroneIcon(def.color, 0);
    const marker = L.marker([CENTER_LAT, CENTER_LNG], { icon: icon, zIndexOffset: def.role === 'PRIMARY' ? 1000 : 500 }).addTo(state.map);
    marker.bindTooltip(def.id, { permanent: true, direction: 'top', offset: [0, -16] });
    state.mapMarkers[def.id] = marker;

    // Trail polylines — 3 segments with fading opacity for gradient effect
    const trailSegs = [];
    const opacities = [0.15, 0.3, 0.5];
    const weights = [1, 1, 1];
    for (let seg = 0; seg < 3; seg++) {
      trailSegs.push(L.polyline([], {
        color: def.color, weight: weights[seg], opacity: opacities[seg], smoothFactor: 1
      }).addTo(state.map));
    }
    state.mapTrails[def.id] = trailSegs;
  });

  // Pattern lines from primary to each follower
  ASSET_DEFS.forEach((def, i) => {
    if (i === 0) return; // skip primary
    const line = L.polyline([], { color: _css('--border-light'), weight: 1, opacity: 0.6, dashArray: '6, 6' }).addTo(state.map);
    state.formationLines.push({ id: def.id, line });
  });

  // Sensor FOV cone polygons for each asset
  ASSET_DEFS.forEach(def => {
    const initialPts = buildFovPoints(CENTER_LAT, CENTER_LNG, 0);
    const fov = L.polygon(initialPts, {
      color: def.color,
      weight: 1,
      opacity: 0.15,
      fillColor: def.color,
      fillOpacity: 0.08,
      interactive: false,
      bubblingMouseEvents: false,
      pane: 'overlayPane'
    }).addTo(state.map);
    state.fovLayers[def.id] = fov;
  });
}

function createDroneIcon(color, heading) {
  const size = 30;
  const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${heading}, ${size/2}, ${size/2})">
      <polygon points="${size/2},4 ${size-6},${size-6} ${size/2},${size-10} 6,${size-6}" fill="${color}" fill-opacity="0.85" stroke="${color}" stroke-width="1.2"/>
    </g>
  </svg>`;
  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    tooltipAnchor: [0, -size / 2],
  });
}

function updateMapMarker(drone) {
  const marker = state.mapMarkers[drone.id];
  if (!marker) return;
  marker.setLatLng([drone.lat, drone.lng]);
  marker.setIcon(createDroneIcon(drone.color, drone.heading));

  // Update trail — 3 segments with fading opacity
  const trailSegs = state.mapTrails[drone.id];
  if (trailSegs && drone.trail.length > 0) {
    const pts = drone.trail.map(p => [p.lat, p.lng]);
    const len = pts.length;
    const third = Math.floor(len / 3);
    // Oldest third (faintest), middle third, newest third (brightest)
    trailSegs[0].setLatLngs(pts.slice(0, third + 1));
    trailSegs[1].setLatLngs(pts.slice(third, third * 2 + 1));
    trailSegs[2].setLatLngs(pts.slice(third * 2));
  }
}

function updateFormationLines(drones) {
  const leader = drones[0];
  state.formationLines.forEach(fl => {
    const follower = drones.find(d => d.id === fl.id);
    if (follower) {
      fl.line.setLatLngs([[leader.lat, leader.lng], [follower.lat, follower.lng]]);
    }
  });
}

function updateFovCone(drone) {
  const fov = state.fovLayers[drone.id];
  if (!fov) return;
  const pts = buildFovPoints(drone.lat, drone.lng, drone.heading);
  fov.setLatLngs(pts);
  // Highlight selected asset's FOV with higher opacity
  const isSelected = drone.id === state.selectedDroneId;
  fov.setStyle({
    fillOpacity: isSelected ? 0.15 : 0.08,
    opacity:     isSelected ? 0.25 : 0.15
  });
}

export { initMap, createDroneIcon, updateMapMarker, updateFormationLines, updateFovCone };
