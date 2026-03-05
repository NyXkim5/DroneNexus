// insights.js — Insights/INTEL panel
import { state } from './state.js';
import { _css, clamp, utcTimeStamp } from './utils.js';
import { getDiagState } from './diagnostics.js';

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

export { updateInsightsPanel };
