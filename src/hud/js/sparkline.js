import { SPARKLINE_POINTS } from './constants.js';

export class Sparkline {
  constructor(canvasId, color, maxVal) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.color = color;
    this.maxVal = maxVal || 100;
    this.data = [];
  }

  push(val) {
    this.data.push(val);
    if (this.data.length > SPARKLINE_POINTS) this.data.shift();
  }

  draw() {
    const c = this.canvas;
    const ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    c.width = w * dpr;
    c.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (this.data.length < 2) return;

    const n = this.data.length;
    const stepX = w / (SPARKLINE_POINTS - 1);
    const padding = 2;
    const plotH = h - padding * 2;

    // Find data range for local scaling
    let minV = Infinity, maxV = -Infinity;
    for (let i = 0; i < n; i++) {
      if (this.data[i] < minV) minV = this.data[i];
      if (this.data[i] > maxV) maxV = this.data[i];
    }
    const range = maxV - minV || 1;

    const startX = (SPARKLINE_POINTS - n) * stepX;

    // Build path
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = startX + i * stepX;
      const y = padding + plotH - ((this.data[i] - minV) / range) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }

    // Stroke
    ctx.strokeStyle = this.color;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // Gradient fill
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, this.color + '30');
    grad.addColorStop(1, this.color + '00');
    ctx.lineTo(startX + (n - 1) * stepX, h);
    ctx.lineTo(startX, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Endpoint dot at rightmost data point
    if (this.data.length > 1) {
      const lastIdx = this.data.length - 1;
      const lastX = startX + lastIdx * stepX;
      const lastVal = this.data[lastIdx];
      const lastY = padding + plotH - ((lastVal - minV) / range) * plotH;
      ctx.beginPath();
      ctx.arc(lastX, lastY, 1.5 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = this.color;
      ctx.fill();
    }
  }
}
