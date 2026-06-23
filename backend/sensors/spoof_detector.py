"""GPS spoofing detection for Remote ID positions.

Flags physically impossible position reports: velocity, altitude rate,
teleportation, duplicate IDs, and velocity/position inconsistencies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from csontology import Detection, Vec3

MAX_SPEED_MS = 100.0         # consumer drone horizontal limit (m/s)
MAX_ALT_RATE_MS = 50.0       # altitude change limit (m/s)
TELEPORT_DIST_M = 500.0      # single-report jump threshold (m)
DUPLICATE_DIST_M = 200.0     # min separation for duplicate ID flag
VELOCITY_MISMATCH_RATIO = 3.0


@dataclass
class SpoofAlert:
    """One spoofing indicator for a tracked drone."""
    drone_id: str
    alert_type: str   # VELOCITY, ALTITUDE, TELEPORT, DUPLICATE, INCONSISTENT
    confidence: float
    details: str
    timestamp: float


@dataclass
class _TrackEntry:
    position: Vec3
    velocity: Vec3
    timestamp: float


def _dist3(a: Vec3, b: Vec3) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _hdist(a: Vec3, b: Vec3) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

def _mag(v: Vec3) -> float:
    return math.hypot(v[0], v[1], v[2])


@dataclass
class SpoofDetector:
    """Stateful detector that checks each Detection for spoofing indicators."""

    tracks: Dict[str, _TrackEntry] = field(default_factory=dict)

    def check(self, detection: Detection) -> List[SpoofAlert]:
        """Evaluate a single Detection. Returns any triggered alerts."""
        alerts: List[SpoofAlert] = []
        prev = self.tracks.get(detection.id)

        if prev is not None:
            dt = detection.timestamp - prev.timestamp
            if dt > 0:
                alerts.extend(
                    self._compare(detection, prev, dt)
                )

        # Duplicate ID: same ID reported from far-apart sensors at close time
        alerts.extend(self._check_duplicate(detection))

        self.tracks[detection.id] = _TrackEntry(
            position=detection.position,
            velocity=detection.velocity,
            timestamp=detection.timestamp,
        )
        return alerts

    def check_batch(self, detections: List[Detection]) -> List[SpoofAlert]:
        """Evaluate a batch of Detections and return all triggered alerts."""
        alerts: List[SpoofAlert] = []
        for det in detections:
            alerts.extend(self.check(det))
        return alerts

    # ------------------------------------------------------------------

    def _compare(self, det: Detection, prev: _TrackEntry, dt: float) -> List[SpoofAlert]:
        """Run per-pair checks between consecutive reports for same ID."""
        alerts: List[SpoofAlert] = []
        dist = _dist3(det.position, prev.position)
        h_dist = _hdist(det.position, prev.position)
        speed = dist / dt
        alt_rate = abs(det.position[2] - prev.position[2]) / dt
        _a = lambda typ, conf, msg: alerts.append(SpoofAlert(
            drone_id=det.id, alert_type=typ,
            confidence=min(1.0, conf), details=msg, timestamp=det.timestamp,
        ))
        if dist > TELEPORT_DIST_M:
            _a("TELEPORT", dist / (TELEPORT_DIST_M * 2), f"Jump {dist:.0f}m in {dt:.1f}s")
        if speed > MAX_SPEED_MS:
            _a("VELOCITY", speed / (MAX_SPEED_MS * 2),
               f"Computed {speed:.1f} m/s exceeds {MAX_SPEED_MS} m/s")
        if alt_rate > MAX_ALT_RATE_MS:
            _a("ALTITUDE", alt_rate / (MAX_ALT_RATE_MS * 2),
               f"Alt rate {alt_rate:.1f} m/s exceeds {MAX_ALT_RATE_MS} m/s")
        reported = _mag(det.velocity)
        computed = h_dist / dt
        if reported > 1.0 and computed > 1.0:
            ratio = max(reported, computed) / min(reported, computed)
            if ratio > VELOCITY_MISMATCH_RATIO:
                _a("INCONSISTENT", ratio / (VELOCITY_MISMATCH_RATIO * 2),
                   f"Reported {reported:.1f} vs computed {computed:.1f} m/s")
        return alerts

    def _check_duplicate(self, det: Detection) -> List[SpoofAlert]:
        """Flag same ID far apart within a very short time window."""
        prev = self.tracks.get(det.id)
        if prev is None or (det.timestamp - prev.timestamp) > 1.0:
            return []
        dist = _dist3(det.position, prev.position)
        if dist < DUPLICATE_DIST_M:
            return []
        dt = det.timestamp - prev.timestamp
        return [SpoofAlert(
            drone_id=det.id, alert_type="DUPLICATE",
            confidence=min(1.0, dist / (DUPLICATE_DIST_M * 4)),
            details=f"Same ID {dist:.0f}m apart in {dt:.2f}s",
            timestamp=det.timestamp,
        )]
