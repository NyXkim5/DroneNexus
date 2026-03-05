/* ==============================================================
   ISR FEED SUBSYSTEMS
   Extracted from app.js — FPV video, overlay, recording
   ============================================================== */

import { state } from './state.js';
import { _css, _cssRgba, showToast } from './utils.js';

// ---- ISR Feed Manager ----
class ISRFeedManager {
  constructor() {
    this.videoEl = document.getElementById('fpv-video');
    this.mjpegEl = document.getElementById('fpv-mjpeg');
    this.canvasEl = document.getElementById('fpv-canvas');
    this.panelEl = document.getElementById('video-panel');
    this.activeMode = 'canvas';
    this.recording = false;
    this.mediaRecorder = null;
    this.recordedChunks = [];
    this._testPatternId = null;
    this._testFrame = 0;
  }

  startTestPattern() {
    this.activeMode = 'canvas';
    this.videoEl.style.display = 'none';
    this.mjpegEl.style.display = 'none';
    this.canvasEl.style.display = '';
    const ctx = this.canvasEl.getContext('2d');
    const w = 640, h = 480;
    const draw = () => {
      this._testFrame++;
      ctx.fillStyle = _css('--bg');
      ctx.fillRect(0, 0, w, h);
      // Grid lines
      ctx.strokeStyle = _cssRgba('--text-dim', 0.08);
      ctx.lineWidth = 1;
      for (let x = 0; x < w; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
      for (let y = 0; y < h; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
      // Crosshair
      ctx.strokeStyle = _cssRgba('--text-dim', 0.3);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(w/2 - 20, h/2); ctx.lineTo(w/2 + 20, h/2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(w/2, h/2 - 20); ctx.lineTo(w/2, h/2 + 20); ctx.stroke();
      // Text
      ctx.font = '16px ' + _css('--font-data');
      ctx.fillStyle = _css('--text-secondary');
      ctx.textAlign = 'center';
      ctx.fillText('OVERWATCH ISR TEST PATTERN', w/2, h/2 - 50);
      ctx.font = '12px ' + _css('--font-data');
      ctx.fillStyle = _css('--text-tertiary');
      ctx.fillText('No video source connected', w/2, h/2 + 50);
      ctx.fillText('Frame ' + this._testFrame, w/2, h/2 + 70);
      const t = new Date();
      ctx.fillStyle = _css('--text-secondary');
      ctx.fillText(t.toISOString().substring(11, 19) + 'Z', w/2, h/2 + 90);
      this._testPatternId = requestAnimationFrame(draw);
    };
    draw();
  }

  stopTestPattern() {
    if (this._testPatternId) { cancelAnimationFrame(this._testPatternId); this._testPatternId = null; }
  }

  connectMJPEG(url) {
    this.stopTestPattern();
    this.mjpegEl.src = url;
    this.mjpegEl.style.display = '';
    this.videoEl.style.display = 'none';
    this.canvasEl.style.display = 'none';
    this.activeMode = 'mjpeg';
  }

  toggleFullscreen() {
    if (this.panelEl.classList.contains('fullscreen')) {
      this.panelEl.classList.remove('fullscreen');
    } else {
      this.panelEl.classList.remove('pip');
      this.panelEl.classList.add('fullscreen');
    }
  }

  togglePIP() {
    if (this.panelEl.classList.contains('pip')) {
      this.panelEl.classList.remove('pip');
      if (state.currentMode !== 'ISR') this.panelEl.style.display = 'none';
    } else {
      this.panelEl.classList.remove('fullscreen');
      this.panelEl.classList.add('pip');
      this.panelEl.style.display = '';
    }
  }

  startRecording() {
    try {
      const stream = this.canvasEl.captureStream(30);
      this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
      this.recordedChunks = [];
      this.mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) this.recordedChunks.push(e.data); };
      this.mediaRecorder.start(1000);
      this.recording = true;
    } catch (e) {
      showToast('Recording not supported in this browser', 'warning');
    }
  }

  stopRecording() {
    if (!this.mediaRecorder) return;
    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.recordedChunks, { type: 'video/webm' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-recording-' + Date.now() + '.webm'; a.click();
      URL.revokeObjectURL(url);
    };
    this.mediaRecorder.stop();
    this.recording = false;
  }

  capturePhoto() {
    const canvas = document.createElement('canvas');
    const src = this.canvasEl;
    canvas.width = src.width; canvas.height = src.height;
    canvas.getContext('2d').drawImage(src, 0, 0);
    canvas.toBlob(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-capture-' + Date.now() + '.jpg'; a.click();
      URL.revokeObjectURL(url);
    }, 'image/jpeg', 0.95);
    showToast('Photo captured');
  }
}

// ---- ISR Overlay Renderer ----
class ISROverlayRenderer {
  constructor() {
    this.els = {
      battery: document.getElementById('osd-battery'),
      cell: document.getElementById('osd-cell'),
      mah: document.getElementById('osd-mah'),
      timer: document.getElementById('osd-timer'),
      armTimer: document.getElementById('osd-arm-timer'),
      gps: document.getElementById('osd-gps'),
      alt: document.getElementById('osd-alt'),
      speed: document.getElementById('osd-speed'),
      rssi: document.getElementById('osd-rssi'),
      home: document.getElementById('osd-home'),
      mode: document.getElementById('osd-mode'),
      warning: document.getElementById('osd-warning'),
    };
  }

  update(drone) {
    if (!drone) return;
    const f = drone.fpvData || {};
    const fmt = (v, d) => (v || 0).toFixed(d);

    this.els.battery.textContent = fmt(drone.voltage, 1) + 'V  ' + fmt(drone.battery, 0) + '%';
    this.els.battery.style.color = drone.battery < 25 ? _css('--red') : drone.battery < 50 ? _css('--amber') : _css('--text-bright');
    this.els.cell.textContent = fmt(f.cell_voltage || drone.voltage / 6, 2) + 'V/cell';
    this.els.mah.textContent = (f.mah_consumed || 0).toFixed(0) + ' mAh';

    const fs = f.flight_timer_s || 0;
    this.els.timer.textContent = Math.floor(fs / 60) + ':' + String(Math.floor(fs % 60)).padStart(2, '0');
    const as = f.arm_timer_s || 0;
    this.els.armTimer.textContent = 'ARM ' + Math.floor(as / 60) + ':' + String(Math.floor(as % 60)).padStart(2, '0');

    this.els.gps.textContent = drone.lat.toFixed(6) + '  ' + drone.lng.toFixed(6) + '  ' + drone.satellites + 'SAT';
    this.els.alt.textContent = fmt(drone.altitude, 1) + 'm';
    this.els.speed.textContent = fmt(drone.speed, 1) + 'm/s';
    this.els.rssi.textContent = 'RSSI ' + fmt(drone.rssi, 0);
    this.els.rssi.style.color = drone.rssi < 50 ? _css('--red') : drone.rssi < 70 ? _css('--amber') : _css('--text-bright');

    const hd = f.home_distance_m || 0;
    const hdir = f.home_direction_deg || 0;
    const dirs = ['N','NE','E','SE','S','SW','W','NW'];
    const compass = dirs[Math.round(((hdir % 360) + 360) % 360 / 45) % 8];
    this.els.home.textContent = hd.toFixed(0) + 'm ' + compass;
    this.els.mode.textContent = f.flight_mode || 'STABILIZE';

    // Artificial horizon rotation
    const horizonEl = document.getElementById('osd-horizon');
    if (horizonEl) {
      const rollDeg = drone.roll || 0;
      const pitchOffset = (drone.pitch || 0) * 1.5; // pixels per degree
      horizonEl.style.transform = 'translate(-50%, calc(-50% + ' + pitchOffset + 'px)) rotate(' + (-rollDeg) + 'deg)';
    }

    // Camera tilt readout
    const tiltEl = document.getElementById('osd-tilt');
    if (tiltEl) {
      const tiltVal = (drone.fpvData && drone.fpvData.camera_tilt) || 0;
      tiltEl.textContent = 'TILT ' + tiltVal + '\u00B0';
    }

    // HDOP readout
    const hdopEl = document.getElementById('osd-hdop');
    if (hdopEl) {
      hdopEl.textContent = 'HDOP ' + (drone.hdop || 0).toFixed(1);
      hdopEl.style.color = drone.hdop > 2.0 ? _css('--red') : drone.hdop > 1.5 ? _css('--amber') : '';
    }

    // Enhanced warnings
    const warnings = [];
    if (drone.battery < 20) warnings.push('LOW BATTERY ' + drone.battery.toFixed(0) + '%');
    if (drone.rssi < 50) warnings.push('LOW SIGNAL');
    if (drone.satellites < 8) warnings.push('GPS DEGRADED');
    if (drone.hdop > 2.0) warnings.push('GPS ACCURACY LOW');

    if (warnings.length > 0) {
      this.els.warning.textContent = warnings[0];
      this.els.warning.style.display = '';
      this.els.warning.style.color = _css('--red');
    } else {
      this.els.warning.style.display = 'none';
    }
  }
}

// ---- Recording Manager ----
class RecordingManager {
  constructor(videoMgr) {
    this.videoMgr = videoMgr;
    this.recording = false;
    this.startTime = 0;
    this.telemLog = [];
  }

  start() {
    this.videoMgr.startRecording();
    this.recording = true;
    this.startTime = performance.now();
    this.telemLog = [];
    document.getElementById('dvr-badge').style.display = '';
    document.getElementById('video-record').style.background = 'rgba(205,66,70,0.2)';
    showToast('DVR recording started');
  }

  stop() {
    this.videoMgr.stopRecording();
    this.recording = false;
    document.getElementById('dvr-badge').style.display = 'none';
    document.getElementById('video-record').style.background = '';
    showToast('DVR recording saved');
    // Export telemetry log
    if (this.telemLog.length > 0) {
      const blob = new Blob([JSON.stringify(this.telemLog, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url;
      a.download = 'overwatch-telem-' + Date.now() + '.json'; a.click();
      URL.revokeObjectURL(url);
    }
  }

  logTelemetry(drone) {
    if (!this.recording || !drone) return;
    this.telemLog.push({
      t: ((performance.now() - this.startTime) / 1000).toFixed(2),
      lat: drone.lat, lng: drone.lng, alt: drone.altitude,
      spd: drone.speed, hdg: drone.heading, bat: drone.battery,
      rssi: drone.rssi, roll: drone.roll, pitch: drone.pitch,
    });
  }

  updateTimer() {
    if (!this.recording) return;
    const s = (performance.now() - this.startTime) / 1000;
    document.getElementById('dvr-timer').textContent =
      'REC ' + Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  }
}

export { ISRFeedManager, ISROverlayRenderer, RecordingManager };
