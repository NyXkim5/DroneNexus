"use strict";

function _escapeHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

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
      const safeTargetId = _escapeHtml(p.target_id);
      const safeSource = _escapeHtml(p.source);
      const sourceIcon = safeSource === 'bulwark' ? '&#x1f6e1;' : '&#x1f3af;';
      const urgencyClass = p.normalized_score > 0.7 ? 'ep-urgent'
                         : p.normalized_score > 0.4 ? 'ep-moderate'
                         : 'ep-low';

      html += `<div class="ep-entry ${urgencyClass}" data-target="${safeTargetId}">
        <span class="ep-rank">#${i + 1}</span>
        <span class="ep-source">${sourceIcon}</span>
        <span class="ep-id">${safeTargetId}</span>
        <span class="ep-score">${(p.normalized_score * 100).toFixed(0)}%</span>
        ${p.personnel_impact > 0 ? `<span class="ep-personnel">${p.personnel_impact}p</span>` : ''}
        ${isAdvisory ? `<button class="ep-confirm" data-target-id="${safeTargetId}">CONFIRM</button>` : ''}
      </div>`;
    });

    this._container.innerHTML = html;
    this._container.querySelectorAll('.ep-confirm').forEach((btn) => {
      btn.addEventListener('click', () => this._onConfirm(btn.getAttribute('data-target-id')));
    });
  }
}
