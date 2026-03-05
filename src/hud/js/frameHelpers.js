/* ==============================================================
   FRAME HELPERS
   Extracted from app.js — per-frame UI update functions
   ============================================================== */

import { state } from './state.js';
import { _css, _cssRgba, clamp } from './utils.js';
import { CENTER_LAT, CENTER_LNG, DRONE_STATES } from './constants.js';
import { getDiagState } from './diagnostics.js';
import { selectDrone } from './assetExplorer.js';

/* ---- Dependency injection for module-level references ---- */
let _deps = {};

export function setFrameDeps(deps) { _deps = deps; }

/** Update bottom-bar metrics: TF Health, Commlink, Power State, Sensor Coverage, Sparklines */
export function updateBottomMetrics(drones) {
    // TF Health
    const avgBat = drones.reduce((s, d) => s + d.battery, 0) / drones.length;
    const allNominal = drones.every(d => d.status === 'NOMINAL' || d.status === 'FLYING');
    const healthPct = allNominal ? clamp(95 + rand(0, 5), 90, 100) : clamp(70 + avgBat * 0.3, 50, 95);
    document.getElementById('bm-health').textContent = healthPct.toFixed(0);

    // Commlink
    const avgLat = drones.reduce((s, d) => s + d.latency, 0) / drones.length;
    document.getElementById('bm-latency').textContent = avgLat.toFixed(0);

    const meshOk = avgLat < 40;
    const meshBadge = document.getElementById('mesh-badge');
    meshBadge.textContent = meshOk ? 'NOMINAL' : 'DEGRADED';
    meshBadge.style.background = meshOk ? _cssRgba('--green', 0.12) : _cssRgba('--amber', 0.15);
    meshBadge.style.color = meshOk ? 'var(--green)' : 'var(--amber)';

    // Power State
    const minBat = Math.min(...drones.map(d => d.battery));
    const maxBat = Math.max(...drones.map(d => d.battery));
    document.getElementById('bm-battery').textContent = avgBat.toFixed(0);
    document.getElementById('bm-bat-spread').textContent = `Range: ${minBat.toFixed(0)} - ${maxBat.toFixed(0)}%`;

    // Sparklines
    const { sparkHealth, sparkLatency, sparkBattery, sparkCoverage } = _deps;
    sparkHealth.push(healthPct);
    sparkLatency.push(avgLat);
    sparkBattery.push(avgBat);

    // Sensor coverage estimation
    const activeSensors = drones.filter(d => d.altitude > 10 && (d.status === 'NOMINAL' || d.status === 'FLYING')).length;
    const baseCov = (activeSensors / drones.length) * 100;
    // Add slight variance for realism, capped at useful range
    const coveragePct = clamp(baseCov * (0.85 + Math.random() * 0.15), 0, 100);
    document.getElementById('bm-coverage').textContent = coveragePct.toFixed(0);
    document.getElementById('bm-cov-assets').textContent = activeSensors + ' / ' + drones.length + ' sensors';
    const covBadge = document.getElementById('cov-badge');
    if (activeSensors === drones.length) {
      covBadge.textContent = 'FULL';
      covBadge.style.background = 'rgba(35,133,81,0.12)';
      covBadge.style.color = 'var(--green)';
    } else if (activeSensors >= drones.length * 0.5) {
      covBadge.textContent = 'PARTIAL';
      covBadge.style.background = 'rgba(200,118,25,0.12)';
      covBadge.style.color = 'var(--amber)';
    } else {
      covBadge.textContent = 'DEGRADED';
      covBadge.style.background = 'rgba(205,66,70,0.12)';
      covBadge.style.color = 'var(--red)';
    }
    sparkCoverage.push(coveragePct);

    // Draw sparklines via requestIdleCallback to avoid blocking the main thread
    if (typeof requestIdleCallback !== 'undefined') {
      requestIdleCallback(() => {
        sparkHealth.draw();
        sparkLatency.draw();
        sparkBattery.draw();
        if (sparkCoverage) sparkCoverage.draw();
      }, { timeout: 500 });
    } else {
      sparkHealth.draw();
      sparkLatency.draw();
      sparkBattery.draw();
      if (sparkCoverage) sparkCoverage.draw();
    }
}

/** Check for critical battery and show/hide the banner */
export function checkCriticalBanner(drones) {
    const criticalDrone = drones.find(d => d.battery < 20 && d.armed && d.droneState !== 'LANDED');
    const banner = document.getElementById('critical-banner');
    if (criticalDrone && !banner.classList.contains('dismissed')) {
      document.getElementById('critical-msg').textContent =
        criticalDrone.id + ' BATTERY CRITICAL (' + criticalDrone.battery.toFixed(0) + '%) -- RTB RECOMMENDED';
      banner.classList.add('visible');
    } else if (!criticalDrone) {
      banner.classList.remove('visible', 'dismissed');
    }
}

/** Update FPV simulation data (mAh consumed, cell voltage, timers, home distance) */
export function updateFPVData(drones) {
    drones.forEach(d => {
      if (!d.isLive) {
        const f = d.fpvData;
        f.mah_consumed += Math.abs(d.current) * (1/10) / 3.6;
        f.cell_voltage = d.voltage / 6;
        if (d.droneState !== DRONE_STATES.LANDED && d.droneState !== DRONE_STATES.IDLE && d.armed) {
          f.flight_timer_s += 0.1;
        }
        if (d.armed) {
          f.arm_timer_s += 0.1;
        }
        const dlat = (d.lat - CENTER_LAT) * 111320;
        const dlng = (d.lng - CENTER_LNG) * 111320 * Math.cos(d.lat * Math.PI / 180);
        f.home_distance_m = Math.sqrt(dlat * dlat + dlng * dlng);
        f.home_direction_deg = ((Math.atan2(-dlng, -dlat) * 180 / Math.PI) + 360) % 360;
      }
    });
}

/** Draw a stick-input indicator on a small canvas */
export function drawStick(canvasId, x, y) {
    const c = document.getElementById(canvasId);
    if (!c) return;
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    c.width = 64 * dpr; c.height = 64 * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, 64, 64);
    // Grid lines
    ctx.strokeStyle = _cssRgba('--text-bright', 0.08);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(32, 0); ctx.lineTo(32, 64); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, 32); ctx.lineTo(64, 32); ctx.stroke();
    // Stick position
    const px = 32 + x * 28;
    const py = 32 - y * 28;
    ctx.beginPath();
    ctx.arc(px, py, 6, 0, Math.PI * 2);
    ctx.fillStyle = _cssRgba('--accent', 0.8);
    ctx.fill();
    ctx.strokeStyle = _cssRgba('--accent', 0.4);
    ctx.lineWidth = 1;
    ctx.stroke();
}

/** Update the ISR feed UI: asset pills, OSD, DVR, right-panel telemetry, sticks, motors, video link */
export function updateISRFeed(drones) {
    const { osdRenderer, dvrMgr } = _deps;

    // ISR asset selector pills
    const pillContainer = document.getElementById('isr-asset-pills');
    if (pillContainer && pillContainer.children.length === 0) {
      drones.forEach(d => {
        const pill = document.createElement('button');
        pill.className = 'fpv-mode-btn';
        pill.style.cssText = 'font-size:9px;padding:2px 6px;min-width:0';
        pill.textContent = d.id.split('-')[0]; // ALPHA, BRAVO, etc.
        pill.dataset.assetId = d.id;
        pill.addEventListener('click', () => selectDrone(d.id));
        pillContainer.appendChild(pill);
      });
    }
    // Update active state
    if (pillContainer) {
      pillContainer.querySelectorAll('.fpv-mode-btn').forEach(p => {
        p.classList.toggle('active', p.dataset.assetId === state.selectedDroneId);
      });
    }

    const sel = drones.find(d => d.id === state.selectedDroneId) || drones[0];
    if (sel) {
      // Update OSD
      osdRenderer.update(sel);

      // Update DVR
      dvrMgr.logTelemetry(sel);
      dvrMgr.updateTimer();

      // Update right panel telemetry
      const f = sel.fpvData || {};
      const $ = id => document.getElementById(id);
      $('fpv-bat').textContent = sel.battery.toFixed(0) + '%';
      $('fpv-bat').style.color = sel.battery < 25 ? 'var(--red)' : sel.battery < 50 ? 'var(--amber)' : 'var(--green)';
      $('fpv-cell').textContent = (f.cell_voltage || sel.voltage / 6).toFixed(2) + 'V';
      $('fpv-alt').textContent = sel.altitude.toFixed(1) + 'm';
      $('fpv-spd').textContent = sel.speed.toFixed(1) + 'm/s';
      $('fpv-rssi').textContent = sel.rssi.toFixed(0);
      $('fpv-rssi').style.color = sel.rssi < 50 ? 'var(--red)' : sel.rssi < 70 ? 'var(--amber)' : 'var(--green)';
      $('fpv-mah').textContent = (f.mah_consumed || 0).toFixed(0);
      $('fpv-home').textContent = (f.home_distance_m || 0).toFixed(0) + 'm';
      $('fpv-sats').textContent = sel.satellites;

      // Cell voltage bars
      const cellBarsEl = document.getElementById('fpv-cell-bars');
      if (cellBarsEl) {
        const ds = getDiagState(sel.id);
        if (ds && ds.cellVoltages) {
          cellBarsEl.innerHTML = ds.cellVoltages.map((v, i) => {
            const pct = Math.max(0, Math.min(100, ((v - 3.3) / (4.2 - 3.3)) * 100));
            const c = v < 3.5 ? _css('--red') : v < 3.7 ? _css('--amber') : _css('--green');
            return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:1px">' +
              '<div style="width:100%;background:rgba(255,255,255,0.06);border-radius:1px;height:24px;position:relative;overflow:hidden">' +
              '<div style="position:absolute;bottom:0;width:100%;height:' + pct + '%;background:' + c + ';border-radius:1px;transition:height 0.3s"></div></div>' +
              '<span style="font-family:var(--font-data);font-size:8px;color:' + c + '">' + v.toFixed(2) + '</span></div>';
          }).join('');
        }
      }

      // Failsafe indicators
      const fsEl = document.getElementById('fpv-failsafe-items');
      if (fsEl) {
        const items = [];
        // Battery failsafe
        const batPct = sel.battery;
        const batStatus = batPct > 30 ? 'ok' : batPct > 20 ? 'warn' : 'crit';
        const batColor = batStatus === 'ok' ? _css('--green') : batStatus === 'warn' ? _css('--amber') : _css('--red');
        items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + batColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">RTB Battery</span><span style="font-family:var(--font-data);font-size:10px;color:' + batColor + ';margin-left:auto">' + (batStatus === 'crit' ? 'TRIGGERED' : batStatus === 'warn' ? 'WARNING' : 'OK') + '</span></div>');

        // GPS failsafe
        const gpsStatus = sel.satellites >= 8 ? 'ok' : sel.satellites >= 5 ? 'warn' : 'crit';
        const gpsColor = gpsStatus === 'ok' ? _css('--green') : gpsStatus === 'warn' ? _css('--amber') : _css('--red');
        items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + gpsColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">GPS Lock</span><span style="font-family:var(--font-data);font-size:10px;color:' + gpsColor + ';margin-left:auto">' + sel.satellites + ' sats</span></div>');

        // Signal failsafe
        const sigStatus = sel.linkQuality > 80 ? 'ok' : sel.linkQuality > 60 ? 'warn' : 'crit';
        const sigColor = sigStatus === 'ok' ? _css('--green') : sigStatus === 'warn' ? _css('--amber') : _css('--red');
        items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + sigColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">RC Link</span><span style="font-family:var(--font-data);font-size:10px;color:' + sigColor + ';margin-left:auto">' + sel.linkQuality.toFixed(0) + '%</span></div>');

        // Home distance
        const homeDist = sel.fpvData ? sel.fpvData.home_distance_m : 0;
        const homeStatus = homeDist < 500 ? 'ok' : homeDist < 1000 ? 'warn' : 'crit';
        const homeColor = homeStatus === 'ok' ? _css('--green') : homeStatus === 'warn' ? _css('--amber') : _css('--red');
        items.push('<div style="display:flex;align-items:center;gap:6px"><span style="width:5px;height:5px;border-radius:50%;background:' + homeColor + ';flex-shrink:0"></span><span style="font-family:var(--font-data);font-size:10px;color:var(--text)">Home Dist</span><span style="font-family:var(--font-data);font-size:10px;color:' + homeColor + ';margin-left:auto">' + homeDist.toFixed(0) + 'm</span></div>');

        fsEl.innerHTML = items.join('');
      }

      // Stick input visualization (simulated from drone attitude)
      // Left stick: throttle (vertical) from speed ratio, yaw from heading change
      const throttle = sel.speed / 15; // normalize to 0-1
      const yaw = Math.sin(sel.heading * Math.PI / 180) * 0.3;
      drawStick('stick-left', yaw, throttle - 0.5);

      // Right stick: roll and pitch from attitude
      const rollNorm = (sel.roll || 0) / 30; // normalize +/-30 to +/-1
      const pitchNorm = (sel.pitch || 0) / 20; // normalize +/-20 to +/-1
      drawStick('stick-right', rollNorm, -pitchNorm);

      // Motor status
      const motorEl = document.getElementById('fpv-motor-status');
      if (motorEl) {
        const ds = getDiagState(sel.id);
        if (ds && ds.motors) {
          motorEl.innerHTML = ds.motors.map((m, i) => {
            const tempColor = m.temp > 50 ? _css('--red') : m.temp > 45 ? _css('--amber') : _css('--green');
            const healthColor = m.health >= 90 ? _css('--green') : m.health >= 75 ? _css('--amber') : _css('--red');
            return '<div style="text-align:center;padding:3px;background:rgba(0,0,0,0.2);border-radius:1px">' +
              '<div style="font-family:var(--font-data);font-size:9px;color:var(--text-dim)">M' + (i + 1) + '</div>' +
              '<div style="font-family:var(--font-data);font-size:10px;color:' + healthColor + '">' + m.rpm.toFixed(0) + '</div>' +
              '<div style="font-family:var(--font-data);font-size:8px;color:' + tempColor + '">' + m.temp.toFixed(0) + '</div></div>';
          }).join('');
        }
      }

      // Video link info
      const vl = f.video_link || {};
      $('fpv-vq').textContent = (vl.quality || 0) + '%';
      $('fpv-vch').textContent = 'CH' + (vl.channel || '-');
      $('fpv-vfreq').textContent = (vl.frequency_mhz || 0) + ' MHz';
      $('fpv-proto').textContent = f.protocol || 'MAVLINK';

      // Update active flight mode button
      document.querySelectorAll('.fpv-mode-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.fmode === (f.flight_mode || 'STABILIZE'));
      });
    }
}

/* ---- Local helper (used only in updateBottomMetrics) ---- */
function rand(lo, hi) { return lo + Math.random() * (hi - lo); }
