/**
 * HeatmapOverlay
 *
 * Canvas overlay that renders the detection heatmap received over WebSocket
 * on top of the Leaflet map. The backend sends a base64-encoded PNG alongside
 * ENU spatial metadata. This class decodes that image and draws it onto an
 * absolutely-positioned canvas that tracks the map's pixel dimensions.
 */

'use strict';

class HeatmapOverlay {
  /**
   * @param {string} canvasId - The id of the <canvas> element to draw on.
   *   The canvas must already exist in the DOM and be positioned as an overlay
   *   on top of the Leaflet map container.
   */
  constructor(canvasId) {
    this._canvas = document.getElementById(canvasId);
    if (!this._canvas) {
      throw new Error(`HeatmapOverlay: canvas element #${canvasId} not found`);
    }
    this._ctx = this._canvas.getContext('2d');
    this._visible = true;
    this._lastData = null;
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /**
   * Decode and render an incoming heatmap payload.
   *
   * @param {Object} heatmapData
   * @param {string} heatmapData.image_b64 - Base64-encoded PNG (RGBA).
   * @param {number} heatmapData.bounds_m  - Half-extent in meters (e.g. 5000).
   * @param {number} heatmapData.width     - Grid columns.
   * @param {number} heatmapData.height    - Grid rows.
   */
  update(heatmapData) {
    if (!heatmapData || !heatmapData.image_b64) {
      return;
    }
    this._lastData = heatmapData;
    if (this._visible) {
      this._draw(heatmapData);
    }
  }

  /** Show the overlay (no-op if already visible). */
  show() {
    this._visible = true;
    if (this._lastData) {
      this._draw(this._lastData);
    }
  }

  /** Hide the overlay by clearing the canvas. */
  hide() {
    this._visible = false;
    this._clear();
  }

  /** Toggle visibility. */
  toggle() {
    if (this._visible) {
      this.hide();
    } else {
      this.show();
    }
  }

  /** Remove all drawn content from the canvas. */
  clear() {
    this._lastData = null;
    this._clear();
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  _clear() {
    this._ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
  }

  /**
   * Decode the base64 PNG and blit it onto the canvas, scaled to fill the
   * canvas while preserving the correct ENU aspect ratio.
   *
   * @param {Object} heatmapData
   */
  _draw(heatmapData) {
    const { image_b64 } = heatmapData;

    const img = new Image();
    img.onload = () => {
      this._clear();
      // Draw stretched to fill the entire canvas. The heatmap grid is
      // square and covers the same physical extent in x and y, so
      // stretching to a non-square canvas introduces minor distortion —
      // acceptable for a spatial density overlay.
      this._ctx.drawImage(img, 0, 0, this._canvas.width, this._canvas.height);
    };
    img.onerror = (err) => {
      console.error('HeatmapOverlay: failed to decode heatmap image', err);
    };
    img.src = `data:image/png;base64,${image_b64}`;
  }
}

// ---------------------------------------------------------------------------
// Exports (CommonJS for Node test environments; global for browser)
// ---------------------------------------------------------------------------
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HeatmapOverlay };
}
