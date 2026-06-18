/* ==============================================================
   OVERWATCH ISR PLATFORM — TELEMETRY STALENESS TRACKER
   Tracks last-seen timestamps per drone and derives visual decay
   properties (opacity, color) for map marker rendering.
   ============================================================== */

export class StalenessTracker {
    constructor() {
        this._lastUpdate = {};  // drone_id -> timestamp_ms
    }

    touch(droneId) {
        this._lastUpdate[droneId] = Date.now();
    }

    getAge(droneId) {
        const last = this._lastUpdate[droneId];
        if (!last) return Infinity;
        return (Date.now() - last) / 1000;  // seconds
    }

    getOpacity(droneId) {
        const age = this.getAge(droneId);
        if (age < 2)  return 1.0;   // fresh
        if (age < 5)  return 0.7;   // slightly stale
        if (age < 10) return 0.4;   // stale
        if (age < 30) return 0.2;   // very stale
        return 0.1;                  // ghost
    }

    getColor(droneId) {
        const age = this.getAge(droneId);
        if (age < 2)  return '#00ff00';  // green  - live
        if (age < 5)  return '#ffff00';  // yellow - aging
        if (age < 10) return '#ff8800';  // orange - stale
        return '#ff0000';                // red    - lost
    }

    isLost(droneId, threshold = 30) {
        return this.getAge(droneId) > threshold;
    }
}

/* --------------------------------------------------------------
   panToInstant — synchronous Leaflet pan with no CSS animation.
   Replaces map.panTo() when tracking a moving drone to eliminate
   the visible lag introduced by Leaflet's default transition.
   -------------------------------------------------------------- */
export function panToInstant(map, latlng) {
    const px     = map.latLngToContainerPoint(latlng);
    const center = map.getSize().divideBy(2);
    const offset = center.subtract(px);
    map._rawPanBy(offset);
}
