"use strict";

class CascadeView {
  constructor(canvasId) {
    this._canvas = document.getElementById(canvasId);
    this._ctx = this._canvas ? this._canvas.getContext('2d') : null;
    this._targets = [];
    this._cascadeScores = [];
    this._selectedTargetId = null;
    this._tacticalMode = false;
  }

  updateTargets(targets) {
    this._targets = targets || [];
    this._draw();
  }

  updateCascadeScores(scores) {
    this._cascadeScores = scores || [];
    this._draw();
  }

  toggleTacticalMode() {
    this._tacticalMode = !this._tacticalMode;
    this._draw();
    return this._tacticalMode;
  }

  selectTarget(targetId) {
    this._selectedTargetId = targetId === this._selectedTargetId ? null : targetId;
    this._draw();
  }

  _draw() {
    if (!this._ctx) return;
    const ctx = this._ctx;
    const w = this._canvas.width;
    const h = this._canvas.height;
    ctx.clearRect(0, 0, w, h);

    const scoreMap = {};
    this._cascadeScores.forEach((cs, i) => {
      scoreMap[cs.target_id] = { rank: i + 1, ...cs };
    });

    for (const t of this._targets) {
      const cs = scoreMap[t.id];
      const rank = cs ? cs.rank : 999;
      const bb = t.bounding_box;

      const color = rank <= 1 ? '#ff3333'
                  : rank <= 3 ? '#ffaa00'
                  : '#33cc33';

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(bb.x, bb.y, bb.width, bb.height);

      ctx.fillStyle = color;
      ctx.font = 'bold 12px JetBrains Mono, monospace';
      const label = `#${rank}`;
      ctx.fillText(label, bb.x, bb.y - 4);

      if (cs) {
        const evLabel = `$${(cs.expected_value / 1000).toFixed(0)}k`;
        ctx.fillStyle = '#ffffff';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.fillText(evLabel, bb.x + bb.width + 4, bb.y + 12);
      }

      if (this._tacticalMode && cs && cs.cascade_chain && cs.cascade_chain.length > 1) {
        ctx.beginPath();
        ctx.arc(
          bb.x + bb.width / 2,
          bb.y + bb.height / 2,
          t.blast_radius_m * 0.5,
          0, Math.PI * 2
        );
        ctx.strokeStyle = `${color}44`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      if (this._selectedTargetId === t.id && cs) {
        this._drawCascadeDetail(ctx, bb, cs);
      }
    }
  }

  _drawCascadeDetail(ctx, bb, cs) {
    const x = bb.x + bb.width + 8;
    const y = bb.y;
    ctx.fillStyle = '#000000cc';
    ctx.fillRect(x, y, 200, 20 + cs.cascade_chain.length * 16);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 11px JetBrains Mono, monospace';
    ctx.fillText(`CASCADE: $${(cs.cascade_value / 1000).toFixed(0)}k`, x + 4, y + 14);
    ctx.font = '10px JetBrains Mono, monospace';
    cs.cascade_chain.forEach((id, i) => {
      ctx.fillText(`${i + 1}. ${id}`, x + 8, y + 30 + i * 16);
    });
  }

  handleClick(event) {
    const rect = this._canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    for (const t of this._targets) {
      const bb = t.bounding_box;
      if (mx >= bb.x && mx <= bb.x + bb.width && my >= bb.y && my <= bb.y + bb.height) {
        this.selectTarget(t.id);
        return t.id;
      }
    }
    this.selectTarget(null);
    return null;
  }
}
