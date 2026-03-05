// activity.js — Event generation and activity stream
import { state } from './state.js';
import { MAX_EVENTS } from './constants.js';
import { rand, randInt, utcTimeStamp } from './utils.js';

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

export { generateActivity, generateStateCorrelatedEvents, addEvent, renderActivityStream, EVENT_TEMPLATES };
