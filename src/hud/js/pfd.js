/* ==============================================================
   PRIMARY FLIGHT DISPLAY (PFD)
   Canvas-drawn instrument panel for OVERWATCH ISR HUD.
   Displays artificial horizon, altitude tape, speed tape,
   and heading strip for the currently selected drone.
   ============================================================== */

"use strict";

export class PFD {
  constructor(canvasId, width = 300, height = 300) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) throw new Error('PFD: canvas #' + canvasId + ' not found');
    this.ctx = this.canvas.getContext('2d');
    this.w = width;
    this.h = height;
    this.canvas.width = width;
    this.canvas.height = height;

    // Layout constants
    this.TAPE_W = 44;        // width of each side tape
    this.HEAD_H = 28;        // height of heading strip
    this.HORIZON_W = this.w - this.TAPE_W * 2;
    this.HORIZON_H = this.h - this.HEAD_H;
    this.HORIZON_X = this.TAPE_W;
    this.HORIZON_Y = 0;
    this.CX = this.TAPE_W + this.HORIZON_W / 2;
    this.CY = this.HORIZON_H / 2;

    // Color palette
    this.C = {
      bg:     '#0a0a14',
      sky:    '#1a3a5c',
      ground: '#4a2c0a',
      green:  '#00ff00',
      white:  '#e8ecf0',
      amber:  '#ffaa00',
      dim:    '#445544',
      tape_bg:'rgba(10,10,20,0.88)',
      horizon_line: '#ffffff',
      aircraft: '#00ff00',
    };

    this.font = "10px 'JetBrains Mono', monospace";
    this.fontSm = "9px 'JetBrains Mono', monospace";
  }

  update(telemetry) {
    const t = telemetry || {};
    const roll       = t.roll       ?? 0;
    const pitch      = t.pitch      ?? 0;
    const yaw        = t.yaw        ?? 0;
    const altitude   = t.altitude   ?? 0;
    const speed      = t.speed      ?? 0;
    const heading    = t.heading    ?? yaw;
    const climbRate  = t.climb_rate ?? t.verticalSpeed ?? 0;

    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);

    // Draw in order: horizon, tapes, heading, aircraft symbol on top
    this._drawHorizon(roll, pitch);
    this._drawRollIndicator(roll);
    this._drawPitchLadder(roll, pitch);
    this._drawAircraftSymbol();
    this._drawSpeedTape(speed);
    this._drawAltitudeTape(altitude, climbRate);
    this._drawHeading(heading);
    this._drawBorder();
  }

  // ---- Artificial Horizon ----------------------------------------

  _drawHorizon(roll, pitch) {
    const ctx = this.ctx;
    const cx  = this.CX;
    const cy  = this.CY;
    const hw  = this.HORIZON_W;
    const hh  = this.HORIZON_H;

    // Clip to horizon region
    ctx.save();
    ctx.beginPath();
    ctx.rect(this.HORIZON_X, this.HORIZON_Y, hw, hh);
    ctx.clip();

    // Pitch offset: 1 degree = ~2.5px
    const pitchPx = pitch * 2.5;

    // Rotate around center for roll
    ctx.translate(cx, cy);
    ctx.rotate(-roll * Math.PI / 180);
    ctx.translate(-cx, -cy);

    // Diagonal extent large enough to always fill rotated canvas
    const ext = Math.sqrt(hw * hw + hh * hh);

    // Sky (top half)
    ctx.fillStyle = this.C.sky;
    ctx.fillRect(cx - ext, cy - ext + pitchPx, ext * 2, ext);

    // Ground (bottom half)
    ctx.fillStyle = this.C.ground;
    ctx.fillRect(cx - ext, cy + pitchPx, ext * 2, ext);

    // Horizon line
    ctx.strokeStyle = this.C.horizon_line;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(cx - ext, cy + pitchPx);
    ctx.lineTo(cx + ext, cy + pitchPx);
    ctx.stroke();

    ctx.restore();
  }

  // ---- Pitch Ladder -----------------------------------------------

  _drawPitchLadder(roll, pitch) {
    const ctx = this.ctx;
    const cx  = this.CX;
    const cy  = this.CY;
    const hw  = this.HORIZON_W;
    const hh  = this.HORIZON_H;

    ctx.save();
    ctx.beginPath();
    ctx.rect(this.HORIZON_X, this.HORIZON_Y, hw, hh);
    ctx.clip();

    // Rotate and translate for roll and pitch
    ctx.translate(cx, cy);
    ctx.rotate(-roll * Math.PI / 180);

    const pitchPx = pitch * 2.5;
    const lineHalfW = 22;
    const gapHalfW  = 8;

    ctx.strokeStyle = this.C.white;
    ctx.fillStyle   = this.C.white;
    ctx.lineWidth   = 1;
    ctx.font        = this.fontSm;
    ctx.textAlign   = 'left';

    for (let deg = -30; deg <= 30; deg += 10) {
      if (deg === 0) continue;
      const y = pitchPx - deg * 2.5;

      // Left segment
      ctx.beginPath();
      ctx.moveTo(-lineHalfW, y);
      ctx.lineTo(-gapHalfW, y);
      ctx.stroke();

      // Right segment
      ctx.beginPath();
      ctx.moveTo(gapHalfW, y);
      ctx.lineTo(lineHalfW, y);
      ctx.stroke();

      // Tick marks at ends
      const tickDir = deg > 0 ? -4 : 4;
      ctx.beginPath();
      ctx.moveTo(-lineHalfW, y);
      ctx.lineTo(-lineHalfW, y + tickDir);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(lineHalfW, y);
      ctx.lineTo(lineHalfW, y + tickDir);
      ctx.stroke();

      // Labels
      const label = String(Math.abs(deg));
      ctx.fillText(label, lineHalfW + 3, y + 3);
      ctx.textAlign = 'right';
      ctx.fillText(label, -lineHalfW - 3, y + 3);
      ctx.textAlign = 'left';
    }

    ctx.restore();
  }

  // ---- Roll Indicator Arc -----------------------------------------

  _drawRollIndicator(roll) {
    const ctx = this.ctx;
    const cx  = this.CX;
    const cy  = this.HORIZON_Y + 36;
    const r   = 32;

    ctx.save();
    ctx.beginPath();
    ctx.rect(this.HORIZON_X, this.HORIZON_Y, this.HORIZON_W, this.HORIZON_H);
    ctx.clip();

    // Arc from -60 to +60 deg, mapped to canvas angles
    // 0 deg roll = top of arc = -PI/2 in canvas terms
    const toCanvasAngle = (deg) => (deg - 90) * Math.PI / 180;

    ctx.strokeStyle = this.C.dim;
    ctx.lineWidth   = 1;

    // Background arc
    ctx.beginPath();
    ctx.arc(cx, cy, r, toCanvasAngle(-60), toCanvasAngle(60));
    ctx.stroke();

    // Tick marks at -60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60
    const ticks = [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60];
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 1;
    ticks.forEach(deg => {
      const angle = toCanvasAngle(deg);
      const len   = (deg % 30 === 0) ? 6 : 4;
      const x1    = cx + Math.cos(angle) * r;
      const y1    = cy + Math.sin(angle) * r;
      const x2    = cx + Math.cos(angle) * (r - len);
      const y2    = cy + Math.sin(angle) * (r - len);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    });

    // Bank angle pointer triangle — rotates with roll
    const pointerAngle = toCanvasAngle(roll);
    const px = cx + Math.cos(pointerAngle) * (r - 2);
    const py = cy + Math.sin(pointerAngle) * (r - 2);

    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(pointerAngle + Math.PI / 2);
    ctx.fillStyle   = this.C.green;
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-4, 7);
    ctx.lineTo(4, 7);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    ctx.restore();
  }

  // ---- Fixed Aircraft Symbol --------------------------------------

  _drawAircraftSymbol() {
    const ctx = this.ctx;
    const cx  = this.CX;
    const cy  = this.CY;

    ctx.save();
    ctx.strokeStyle = this.C.aircraft;
    ctx.lineWidth   = 2;

    // Left wing
    ctx.beginPath();
    ctx.moveTo(cx - 18, cy);
    ctx.lineTo(cx - 6, cy);
    ctx.stroke();

    // Right wing
    ctx.beginPath();
    ctx.moveTo(cx + 6, cy);
    ctx.lineTo(cx + 18, cy);
    ctx.stroke();

    // Left wing down-tick
    ctx.beginPath();
    ctx.moveTo(cx - 18, cy);
    ctx.lineTo(cx - 18, cy + 4);
    ctx.stroke();

    // Right wing down-tick
    ctx.beginPath();
    ctx.moveTo(cx + 18, cy);
    ctx.lineTo(cx + 18, cy + 4);
    ctx.stroke();

    // Center dot
    ctx.fillStyle = this.C.aircraft;
    ctx.beginPath();
    ctx.arc(cx, cy, 3, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }

  // ---- Altitude Tape (right side) ---------------------------------

  _drawAltitudeTape(altitude, climbRate) {
    const ctx = this.ctx;
    const x   = this.w - this.TAPE_W;
    const y   = this.HORIZON_Y;
    const w   = this.TAPE_W;
    const h   = this.HORIZON_H;
    const cy  = y + h / 2;

    // Background
    ctx.fillStyle = this.C.tape_bg;
    ctx.fillRect(x, y, w, h);

    // Separator line
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x, y + h);
    ctx.stroke();

    // Clip tape content
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();

    // px per meter
    const scale = 2.5;

    // Tick every 10m, label every 50m
    const range  = Math.ceil((h / 2) / scale) + 20;
    const altVal = Math.round(altitude);

    ctx.strokeStyle = this.C.green;
    ctx.fillStyle   = this.C.white;
    ctx.font        = this.fontSm;
    ctx.lineWidth   = 1;
    ctx.textAlign   = 'left';

    for (let m = altVal - range; m <= altVal + range; m++) {
      if (m % 10 !== 0) continue;
      const yPos = cy - (m - altitude) * scale;
      if (yPos < y || yPos > y + h) continue;

      const isMajor = m % 50 === 0;
      const tickLen = isMajor ? 8 : 4;

      ctx.beginPath();
      ctx.moveTo(x, yPos);
      ctx.lineTo(x + tickLen, yPos);
      ctx.stroke();

      if (isMajor) {
        ctx.fillText(String(m), x + 10, yPos + 3);
      }
    }

    ctx.restore();

    // Current value box
    const boxH = 16;
    const boxY = cy - boxH / 2;
    ctx.fillStyle = this.C.green;
    ctx.fillRect(x, boxY, w, boxH);
    ctx.fillStyle = this.C.bg;
    ctx.font      = this.font;
    ctx.textAlign = 'center';
    ctx.fillText(Math.round(altitude).toString(), x + w / 2, boxY + 11);

    // Trend arrow
    if (Math.abs(climbRate) > 0.2) {
      const arrowX  = x + w - 6;
      const arrowLen = Math.min(Math.abs(climbRate) * 3, h / 4);
      const dir      = climbRate > 0 ? -1 : 1;
      ctx.strokeStyle = this.C.amber;
      ctx.lineWidth   = 2;
      ctx.beginPath();
      ctx.moveTo(arrowX, cy);
      ctx.lineTo(arrowX, cy + dir * arrowLen);
      ctx.stroke();
      // Arrowhead
      ctx.fillStyle = this.C.amber;
      ctx.beginPath();
      ctx.moveTo(arrowX, cy + dir * arrowLen);
      ctx.lineTo(arrowX - 3, cy + dir * (arrowLen - 6));
      ctx.lineTo(arrowX + 3, cy + dir * (arrowLen - 6));
      ctx.closePath();
      ctx.fill();
    }

    // Label
    ctx.fillStyle = this.C.dim;
    ctx.font      = this.fontSm;
    ctx.textAlign = 'center';
    ctx.fillText('ALT', x + w / 2, y + h - 4);
  }

  // ---- Speed Tape (left side) -------------------------------------

  _drawSpeedTape(speed) {
    const ctx = this.ctx;
    const x   = 0;
    const y   = this.HORIZON_Y;
    const w   = this.TAPE_W;
    const h   = this.HORIZON_H;
    const cy  = y + h / 2;

    // Background
    ctx.fillStyle = this.C.tape_bg;
    ctx.fillRect(x, y, w, h);

    // Separator line
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.moveTo(x + w, y);
    ctx.lineTo(x + w, y + h);
    ctx.stroke();

    // Clip tape content
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();

    // px per m/s
    const scale  = 5;
    const range  = Math.ceil((h / 2) / scale) + 10;
    const spdVal = Math.max(speed, 0);

    ctx.strokeStyle = this.C.green;
    ctx.fillStyle   = this.C.white;
    ctx.font        = this.fontSm;
    ctx.lineWidth   = 1;
    ctx.textAlign   = 'right';

    for (let s = Math.floor(spdVal) - range; s <= spdVal + range; s++) {
      if (s < 0) continue;
      if (s % 5 !== 0) continue;
      const yPos = cy - (s - spdVal) * scale;
      if (yPos < y || yPos > y + h) continue;

      const isMajor = s % 10 === 0;
      const tickLen = isMajor ? 8 : 4;

      ctx.beginPath();
      ctx.moveTo(x + w, yPos);
      ctx.lineTo(x + w - tickLen, yPos);
      ctx.stroke();

      if (isMajor) {
        ctx.fillText(String(s), x + w - 10, yPos + 3);
      }
    }

    ctx.restore();

    // Current value box
    const boxH = 16;
    const boxY = cy - boxH / 2;
    ctx.fillStyle = this.C.green;
    ctx.fillRect(x, boxY, w, boxH);
    ctx.fillStyle = this.C.bg;
    ctx.font      = this.font;
    ctx.textAlign = 'center';
    ctx.fillText(spdVal.toFixed(1), x + w / 2, boxY + 11);

    // Label
    ctx.fillStyle = this.C.dim;
    ctx.font      = this.fontSm;
    ctx.textAlign = 'center';
    ctx.fillText('SPD', x + w / 2, y + h - 4);
  }

  // ---- Heading Strip (bottom) -------------------------------------

  _drawHeading(heading) {
    const ctx = this.ctx;
    const x   = 0;
    const y   = this.h - this.HEAD_H;
    const w   = this.w;
    const h   = this.HEAD_H;
    const cx  = w / 2;
    const cy  = y + h / 2;

    // Background
    ctx.fillStyle = this.C.tape_bg;
    ctx.fillRect(x, y, w, h);

    // Top separator
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + w, y);
    ctx.stroke();

    // Clip
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();

    // px per degree
    const scale = 2.5;

    ctx.strokeStyle = this.C.green;
    ctx.fillStyle   = this.C.white;
    ctx.font        = this.fontSm;
    ctx.lineWidth   = 1;
    ctx.textAlign   = 'center';

    const CARDINALS = { 0: 'N', 90: 'E', 180: 'S', 270: 'W', 360: 'N' };

    for (let deg = -180; deg <= 180; deg++) {
      const hdgDeg = ((heading + deg) % 360 + 360) % 360;
      const xPos   = cx + deg * scale;
      if (xPos < x || xPos > x + w) continue;

      if (hdgDeg % 10 === 0) {
        const isMajor = hdgDeg % 30 === 0;
        const tickH   = isMajor ? 6 : 3;

        ctx.beginPath();
        ctx.moveTo(xPos, y);
        ctx.lineTo(xPos, y + tickH);
        ctx.stroke();

        if (isMajor) {
          const label = CARDINALS[hdgDeg] || String(hdgDeg);
          ctx.fillStyle = CARDINALS[hdgDeg] ? this.C.amber : this.C.white;
          ctx.fillText(label, xPos, y + h - 3);
          ctx.fillStyle = this.C.white;
        }
      }
    }

    ctx.restore();

    // Current heading box (center)
    const boxW = 34;
    const boxH = 14;
    const boxX = cx - boxW / 2;
    const boxY = y + 1;
    ctx.fillStyle = this.C.green;
    ctx.fillRect(boxX, boxY, boxW, boxH);
    ctx.fillStyle = this.C.bg;
    ctx.font      = this.font;
    ctx.textAlign = 'center';
    ctx.fillText(Math.round(heading).toString().padStart(3, '0'), cx, boxY + 10);

    // Center tick above box
    ctx.strokeStyle = this.C.green;
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.moveTo(cx, y);
    ctx.lineTo(cx, y + 4);
    ctx.stroke();
  }

  // ---- Outer border -----------------------------------------------

  _drawBorder() {
    const ctx = this.ctx;
    ctx.strokeStyle = '#1a2a1a';
    ctx.lineWidth   = 1;
    ctx.strokeRect(0.5, 0.5, this.w - 1, this.h - 1);
  }
}
