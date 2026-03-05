/* ==============================================================
   DEBRIEF PANEL
   Extracted from app.js — updateDebriefPanel()
   ============================================================== */

import { state } from './state.js';
import { batteryColor, el } from './utils.js';

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
  if (abEl) { abEl.textContent = ''; abEl.appendChild(document.createTextNode(avgBat.toFixed(0))); abEl.appendChild(el('span', { className: 'debrief-stat-unit', textContent: '%' })); }

  const avgSpd = drones.reduce((s, d) => s + d.speed, 0) / drones.length;
  const asEl = document.getElementById('debrief-avg-speed');
  if (asEl) { asEl.textContent = ''; asEl.appendChild(document.createTextNode(avgSpd.toFixed(1))); asEl.appendChild(el('span', { className: 'debrief-stat-unit', textContent: 'm/s' })); }

  const distKm = debriefTracker.totalDistance / 1000;
  const distEl = document.getElementById('debrief-distance');
  if (distEl) { distEl.textContent = ''; distEl.appendChild(document.createTextNode(distKm.toFixed(2))); distEl.appendChild(el('span', { className: 'debrief-stat-unit', textContent: 'km' })); }

  const evEl = document.getElementById('debrief-events');
  if (evEl) evEl.textContent = state.activityStream.length;

  // Key events
  const keEl = document.getElementById('debrief-key-events');
  if (keEl) {
    keEl.textContent = '';
    if (debriefTracker.keyEvents.length === 0) {
      keEl.appendChild(el('div', { style: { fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--text-secondary)' }, textContent: 'No key events yet' }));
    } else {
      debriefTracker.keyEvents.slice().reverse().forEach(e => {
        const dotColor = e.severity === 'ok' ? 'var(--green)' : e.severity === 'warn' ? 'var(--amber)' : e.severity === 'alert' ? 'var(--red)' : 'var(--accent)';
        keEl.appendChild(
          el('div', { className: 'debrief-timeline-item' },
            el('span', { className: 'debrief-timeline-time', textContent: e.time }),
            el('span', { className: 'debrief-timeline-dot', style: { background: dotColor } }),
            el('span', { className: 'debrief-timeline-msg', textContent: e.msg })
          )
        );
      });
    }
  }

  // Per-asset performance
  const perfEl = document.getElementById('debrief-asset-perf');
  if (perfEl) {
    perfEl.textContent = '';
    drones.forEach(d => {
      const bc = batteryColor(d.battery);
      perfEl.appendChild(
        el('div', { className: 'debrief-asset-row' },
          el('span', { style: { width: '6px', height: '6px', borderRadius: '50%', background: d.color, flexShrink: '0' } }),
          el('span', { className: 'debrief-asset-name', textContent: d.id }),
          el('div', { className: 'debrief-asset-bar' },
            el('div', { className: 'debrief-asset-bar-fill', style: { width: d.battery + '%', background: bc } })
          ),
          el('span', { className: 'debrief-asset-pct', style: { color: bc }, textContent: d.battery.toFixed(0) + '%' })
        )
      );
    });
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
