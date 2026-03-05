/* ==============================================================
   DEBRIEF PANEL
   Extracted from app.js — updateDebriefPanel()
   ============================================================== */

import { state } from './state.js';
import { batteryColor } from './utils.js';

export function updateDebriefPanel(drones, debriefTracker, cmdEngine, replaySystem) {
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
