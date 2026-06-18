"use strict";

class EngagementPanel {
  constructor(containerId, onConfirm) {
    this._container = document.getElementById(containerId);
    this._onConfirm = onConfirm || (() => {});
    this._order = null;
  }

  update(engagementOrder) {
    this._order = engagementOrder;
    this._render();
  }

  _render() {
    if (!this._container) return;
    if (!this._order || !this._order.priorities || this._order.priorities.length === 0) {
      this._container.innerHTML = '<div class="ep-empty">NO TARGETS</div>';
      return;
    }

    const isAdvisory = this._order.mode === 'advisory';
    let html = '<div class="ep-header">ENGAGEMENT ORDER</div>';

    this._order.priorities.forEach((p, i) => {
      const sourceIcon = p.source === 'bulwark' ? '&#x1f6e1;' : '&#x1f3af;';
      const urgencyClass = p.normalized_score > 0.7 ? 'ep-urgent'
                         : p.normalized_score > 0.4 ? 'ep-moderate'
                         : 'ep-low';

      html += `<div class="ep-entry ${urgencyClass}" data-target="${p.target_id}">
        <span class="ep-rank">#${i + 1}</span>
        <span class="ep-source">${sourceIcon}</span>
        <span class="ep-id">${p.target_id}</span>
        <span class="ep-score">${(p.normalized_score * 100).toFixed(0)}%</span>
        ${p.personnel_impact > 0 ? `<span class="ep-personnel">${p.personnel_impact}p</span>` : ''}
        ${isAdvisory ? `<button class="ep-confirm" onclick="window._epConfirm('${p.target_id}')">CONFIRM</button>` : ''}
      </div>`;
    });

    this._container.innerHTML = html;
    window._epConfirm = (targetId) => this._onConfirm(targetId);
  }
}
