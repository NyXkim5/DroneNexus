import { state } from './state.js';

// Convert decimal lat/lng to MGRS string
export function toMGRS(lat, lng) {
  const MGRS_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  const setOrigin = [1, 0]; // false easting/northing for UTM

  // UTM zone
  let zone = Math.floor((lng + 180) / 6) + 1;

  // UTM letter
  const latBand = 'CDEFGHJKLMNPQRSTUVWX'[Math.floor((lat + 80) / 8)];

  // Convert to UTM
  const latRad = lat * Math.PI / 180;
  const lngRad = lng * Math.PI / 180;
  const centralMeridian = ((zone - 1) * 6 - 180 + 3) * Math.PI / 180;

  const a = 6378137; // WGS84 semi-major axis
  const f = 1 / 298.257223563;
  const e = Math.sqrt(2 * f - f * f);
  const e2 = e * e / (1 - e * e);
  const n = a / Math.sqrt(1 - e * e * Math.sin(latRad) * Math.sin(latRad));
  const t = Math.tan(latRad);
  const c = e2 * Math.cos(latRad) * Math.cos(latRad);
  const A = Math.cos(latRad) * (lngRad - centralMeridian);

  const M = a * ((1 - e*e/4 - 3*e*e*e*e/64 - 5*e*e*e*e*e*e/256) * latRad
    - (3*e*e/8 + 3*e*e*e*e/32 + 45*e*e*e*e*e*e/1024) * Math.sin(2*latRad)
    + (15*e*e*e*e/256 + 45*e*e*e*e*e*e/1024) * Math.sin(4*latRad)
    - (35*e*e*e*e*e*e/3072) * Math.sin(6*latRad));

  let easting = 500000 + 0.9996 * n * (A + (1-t*t+c)*A*A*A/6 + (5-18*t*t+t*t*t*t+72*c-58*e2)*A*A*A*A*A/120);
  let northing = 0.9996 * (M + n * t * (A*A/2 + (5-t*t+9*c+4*c*c)*A*A*A*A/24 + (61-58*t*t+t*t*t*t+600*c-330*e2)*A*A*A*A*A*A/720));
  if (lat < 0) northing += 10000000;

  // 100km grid square letters
  const setParm = zone % 6;
  const e100k = Math.floor(easting / 100000);
  const n100k = Math.floor(northing / 100000) % 20;
  const col = MGRS_LETTERS[(setParm - 1) * 8 + e100k - 1] || '?';
  const row = MGRS_LETTERS[n100k] || '?';

  // 5-digit grid ref
  const e5 = String(Math.round(easting % 100000)).padStart(5, '0');
  const n5 = String(Math.round(northing % 100000)).padStart(5, '0');

  return zone + latBand + ' ' + col + row + ' ' + e5 + ' ' + n5;
}

// ---- CSS VARIABLE HELPER ----
export const _css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const _cssRgba = (v, a) => { const h = _css(v); const r = parseInt(h.slice(1,3),16), g = parseInt(h.slice(3,5),16), b = parseInt(h.slice(5,7),16); return `rgba(${r},${g},${b},${a})`; };

// ---- UTILITY ----
export function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
export function lerp(a, b, t) { return a + (b - a) * t; }
export function rand(lo, hi) { return lo + Math.random() * (hi - lo); }
export function randInt(lo, hi) { return Math.floor(rand(lo, hi + 1)); }
export function padZ(n, d) { return String(n).padStart(d, '0'); }
export function degToRad(d) { return d * Math.PI / 180; }
export function radToDeg(r) { return r * 180 / Math.PI; }

// ---- AUDIO ALERT SYSTEM ----
let audioCtx = null;
export function playAlert(type) {
  if (state.audioMuted) return;
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  gain.gain.value = 0.12;
  if (type === 'critical') {
    osc.frequency.value = 880; osc.type = 'square';
    gain.gain.value = 0.15;
  } else if (type === 'warning') {
    osc.frequency.value = 660; osc.type = 'triangle';
  } else {
    osc.frequency.value = 440; osc.type = 'sine';
    gain.gain.value = 0.08;
  }
  osc.start();
  osc.stop(audioCtx.currentTime + 0.12);
}

// Compute a lat/lng offset given a distance (meters) and bearing (degrees)
export function offsetLatLng(lat, lng, distMeters, bearingDeg) {
  const R = 6371000; // Earth radius in meters
  const d = distMeters / R;
  const brng = bearingDeg * Math.PI / 180;
  const lat1 = lat * Math.PI / 180;
  const lng1 = lng * Math.PI / 180;
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brng));
  const lng2 = lng1 + Math.atan2(Math.sin(brng) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
  return [lat2 * 180 / Math.PI, lng2 * 180 / Math.PI];
}

// Build FOV cone polygon points for a given position, heading, and range
export const FOV_HALF_ANGLE = 30;    // degrees -- 60-degree total spread
export const FOV_RANGE      = 250;   // meters forward from drone
export const FOV_ARC_STEPS  = 7;     // intermediate points along the arc

export function buildFovPoints(lat, lng, headingDeg) {
  const pts = [[lat, lng]]; // apex at drone position
  const startBearing = headingDeg - FOV_HALF_ANGLE;
  const endBearing   = headingDeg + FOV_HALF_ANGLE;
  const step = (endBearing - startBearing) / FOV_ARC_STEPS;
  for (let i = 0; i <= FOV_ARC_STEPS; i++) {
    const bearing = startBearing + step * i;
    pts.push(offsetLatLng(lat, lng, FOV_RANGE, bearing));
  }
  return pts;
}

export function utcString() {
  const d = new Date();
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  return padZ(d.getUTCDate(), 2) + months[d.getUTCMonth()] + d.getUTCFullYear() + ' ' +
         padZ(d.getUTCHours(), 2) + padZ(d.getUTCMinutes(), 2) + 'Z';
}

export function utcTimeStamp() {
  const d = new Date();
  return padZ(d.getUTCHours(), 2) + ':' + padZ(d.getUTCMinutes(), 2) + ':' + padZ(d.getUTCSeconds(), 2);
}

export function batteryColor(pct) {
  if (pct > 50) return _css('--green');
  if (pct > 25) return _css('--amber');
  return _css('--red');
}

export function rssiColor(v) {
  if (v > 80) return _css('--green');
  if (v > 50) return _css('--amber');
  return _css('--red');
}

// ---- TOAST NOTIFICATION ----
let activeToasts = [];
export function showToast(msg, type) {
  type = type || 'success';
  if (type === 'error' || type === 'critical') playAlert('critical');
  else if (type === 'warning') playAlert('warning');
  const el = document.createElement('div');
  el.className = 'cmd-toast ' + type;
  el.textContent = msg;
  const bottomOffset = 24 + activeToasts.length * 46;
  el.style.bottom = bottomOffset + 'px';
  activeToasts.push(el);
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add('toast-exit'); }, 2000);
  setTimeout(() => { activeToasts = activeToasts.filter(t => t !== el); el.remove(); }, 2200);
}
