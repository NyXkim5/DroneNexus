"""
IFF (Identification Friend or Foe) and airspace deconfliction for BULWARK/OVERWATCH.

IFFSystem interrogates tracks to classify them as friendly, hostile, neutral,
suspect, or unknown using a priority-ordered chain of methods: transponder
registry lookup, RF signature match, behavioral heuristics, then fallback to
UNKNOWN.

AirspaceDeconflictor prevents friendly fire by checking three conditions before
any engagement is authorized:
  1. No registered friendly is within the effector's blast radius of the target.
  2. No registered friendly lies along the line of fire (effector -> target)
     within a configurable corridor half-width.
  3. The engagement path does not cross a restricted airspace zone.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from csontology import Track, TrackClass, Vec3


# ---------------------------------------------------------------------------
# IFF enums and response
# ---------------------------------------------------------------------------

class IFFMode(Enum):
    UNKNOWN = "unknown"
    FRIENDLY = "friendly"
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    SUSPECT = "suspect"


@dataclass
class IFFResponse:
    track_id: str
    mode: IFFMode
    confidence: float   # 0-1
    method: str         # "transponder", "rf_signature", "visual", "behavioral", "track_class", "default"
    timestamp: float


# ---------------------------------------------------------------------------
# IFF System
# ---------------------------------------------------------------------------

# Speed threshold above which behavioral analysis flags a track as suspect.
_BEHAVIORAL_HIGH_SPEED_MS = 50.0


class IFFSystem:
    """Identification Friend or Foe system."""

    def __init__(self) -> None:
        self._friendly_registry: Dict[str, dict] = {}
        self._responses: List[IFFResponse] = []

    def register_friendly(
        self,
        drone_id: str,
        transponder_code: str = "",
        rf_signature: str = "",
    ) -> None:
        """Register a known friendly asset."""
        self._friendly_registry[drone_id] = {
            "transponder_code": transponder_code,
            "rf_signature": rf_signature,
        }

    def interrogate(
        self,
        track: Track,
        known_friendlies: Optional[List[str]] = None,
    ) -> IFFResponse:
        """
        Determine if a track is friend or foe.

        Methods checked in order:
        1. Transponder match against registry.
        2. RF signature match.
        3. TrackClass classification from the fusion engine.
        4. Behavioral analysis (speed heuristic).
        5. Default to UNKNOWN.
        """
        response = (
            self._check_transponder(track, known_friendlies)
            or self._check_rf_signature(track, known_friendlies)
            or self._check_track_class(track)
            or self._check_behavioral(track)
            or self._default_response(track)
        )
        self._responses.append(response)
        return response

    def is_engagement_safe(self, target_id: str, min_confidence: float = 0.8) -> bool:
        """Return True if engaging this target is safe (not classified friendly)."""
        matching = [r for r in self._responses if r.track_id == target_id]
        if not matching:
            return False
        latest = max(matching, key=lambda r: r.timestamp)
        if latest.mode == IFFMode.FRIENDLY and latest.confidence >= min_confidence:
            return False
        if latest.mode == IFFMode.UNKNOWN:
            return False
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_transponder(
        self,
        track: Track,
        known_friendlies: Optional[List[str]],
    ) -> Optional[IFFResponse]:
        """Match track id against the transponder registry."""
        candidates = set(self._friendly_registry.keys())
        if known_friendlies is not None:
            candidates &= set(known_friendlies)

        if track.id in candidates:
            entry = self._friendly_registry[track.id]
            if entry.get("transponder_code"):
                return IFFResponse(
                    track_id=track.id,
                    mode=IFFMode.FRIENDLY,
                    confidence=0.98,
                    method="transponder",
                    timestamp=time.time(),
                )
        return None

    def _check_rf_signature(
        self,
        track: Track,
        known_friendlies: Optional[List[str]],
    ) -> Optional[IFFResponse]:
        """Match track id against RF signature registry."""
        candidates = set(self._friendly_registry.keys())
        if known_friendlies is not None:
            candidates &= set(known_friendlies)

        if track.id in candidates:
            entry = self._friendly_registry[track.id]
            if entry.get("rf_signature"):
                return IFFResponse(
                    track_id=track.id,
                    mode=IFFMode.FRIENDLY,
                    confidence=0.90,
                    method="rf_signature",
                    timestamp=time.time(),
                )
        return None

    def _check_track_class(self, track: Track) -> Optional[IFFResponse]:
        """Use the fusion engine's track classification directly."""
        if track.classification == TrackClass.HOSTILE:
            return IFFResponse(
                track_id=track.id,
                mode=IFFMode.HOSTILE,
                confidence=track.confidence if track.confidence > 0.0 else 0.75,
                method="track_class",
                timestamp=time.time(),
            )
        if track.classification == TrackClass.FRIENDLY:
            return IFFResponse(
                track_id=track.id,
                mode=IFFMode.FRIENDLY,
                confidence=track.confidence if track.confidence > 0.0 else 0.75,
                method="track_class",
                timestamp=time.time(),
            )
        return None

    def _check_behavioral(self, track: Track) -> Optional[IFFResponse]:
        """Flag fast-moving unknown tracks as SUSPECT."""
        vx, vy, vz = track.velocity
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        if speed > _BEHAVIORAL_HIGH_SPEED_MS:
            return IFFResponse(
                track_id=track.id,
                mode=IFFMode.SUSPECT,
                confidence=0.55,
                method="behavioral",
                timestamp=time.time(),
            )
        return None

    def _default_response(self, track: Track) -> IFFResponse:
        return IFFResponse(
            track_id=track.id,
            mode=IFFMode.UNKNOWN,
            confidence=0.0,
            method="default",
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Airspace deconfliction
# ---------------------------------------------------------------------------

@dataclass
class DeconflictionZone:
    center: Vec3
    radius_m: float
    altitude_floor_m: float
    altitude_ceiling_m: float
    zone_type: str  # "engagement", "transit", "restricted"


# Half-width of the line-of-fire corridor used in friendly-intercept checks.
_DEFAULT_LOF_CORRIDOR_M = 5.0


class AirspaceDeconflictor:
    """Prevents friendly fire by managing airspace zones and friendly positions."""

    def __init__(self) -> None:
        self._zones: List[DeconflictionZone] = []
        self._friendly_positions: Dict[str, Vec3] = {}

    def add_zone(self, zone: DeconflictionZone) -> None:
        """Define an airspace zone."""
        self._zones.append(zone)

    def update_friendly(self, drone_id: str, position: Vec3) -> None:
        """Update known friendly position."""
        self._friendly_positions[drone_id] = position

    def check_engagement_safe(
        self,
        effector_position: Vec3,
        target_position: Vec3,
        effect_radius_m: float = 0.0,
        lof_corridor_m: float = _DEFAULT_LOF_CORRIDOR_M,
    ) -> Tuple[bool, str]:
        """
        Check whether an engagement is safe.

        Three conditions must all pass:
        1. No friendly within effect_radius_m of the target position.
        2. No friendly between effector and target along the line of fire
           (within lof_corridor_m of the line segment).
        3. No friendly inside a restricted zone that the engagement path crosses.

        Returns (safe, reason).
        """
        # 1. Blast radius check.
        for drone_id, pos in self._friendly_positions.items():
            d = _distance(pos, target_position)
            if d <= effect_radius_m:
                return (
                    False,
                    f"friendly {drone_id} within blast radius ({d:.1f} m <= {effect_radius_m:.1f} m)",
                )

        # 2. Line-of-fire check.
        for drone_id, pos in self._friendly_positions.items():
            d = _point_to_segment_distance(pos, effector_position, target_position)
            if d <= lof_corridor_m:
                return (
                    False,
                    f"friendly {drone_id} in line of fire ({d:.1f} m from firing line)",
                )

        # 3. Restricted zone crossing check.
        for zone in self._zones:
            if zone.zone_type != "restricted":
                continue
            if _segment_intersects_cylinder(
                effector_position,
                target_position,
                zone.center,
                zone.radius_m,
                zone.altitude_floor_m,
                zone.altitude_ceiling_m,
            ):
                return (False, f"engagement path crosses restricted zone at {zone.center}")

        return (True, "clear")

    def nearest_friendly_distance(
        self, position: Vec3
    ) -> Tuple[Optional[str], float]:
        """Find the closest friendly to a position. Returns (drone_id, distance_m)."""
        best_id: Optional[str] = None
        best_dist = math.inf
        for drone_id, pos in self._friendly_positions.items():
            d = _distance(pos, position)
            if d < best_dist:
                best_dist = d
                best_id = drone_id
        return best_id, best_dist

    def friendlies_in_radius(self, position: Vec3, radius_m: float) -> List[str]:
        """List all friendlies within radius_m of position."""
        return [
            drone_id
            for drone_id, pos in self._friendly_positions.items()
            if _distance(pos, position) <= radius_m
        ]


# ---------------------------------------------------------------------------
# Geometry helpers (module-private)
# ---------------------------------------------------------------------------

def _distance(a: Vec3, b: Vec3) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _point_to_segment_distance(point: Vec3, seg_a: Vec3, seg_b: Vec3) -> float:
    """Minimum 3-D distance from point to the line segment [seg_a, seg_b]."""
    ax, ay, az = seg_a
    bx, by, bz = seg_b
    px, py, pz = point

    abx, aby, abz = bx - ax, by - ay, bz - az
    apx, apy, apz = px - ax, py - ay, pz - az

    ab_sq = abx * abx + aby * aby + abz * abz
    if ab_sq == 0.0:
        # Segment is a point.
        return _distance(point, seg_a)

    t = (apx * abx + apy * aby + apz * abz) / ab_sq
    t = max(0.0, min(1.0, t))

    closest = (ax + t * abx, ay + t * aby, az + t * abz)
    return _distance(point, closest)


def _segment_intersects_cylinder(
    seg_a: Vec3,
    seg_b: Vec3,
    center: Vec3,
    radius_m: float,
    alt_floor: float,
    alt_ceil: float,
) -> bool:
    """
    Return True if the line segment passes through the vertical cylinder defined
    by center (x, y), radius_m, and [alt_floor, alt_ceil] in the z axis.

    Uses a simple parametric sweep: sample the segment at fine intervals and
    check both horizontal proximity and altitude band. Accurate enough for
    engagement-scale distances (sub-kilometer).
    """
    samples = 40
    for i in range(samples + 1):
        t = i / samples
        x = seg_a[0] + t * (seg_b[0] - seg_a[0])
        y = seg_a[1] + t * (seg_b[1] - seg_a[1])
        z = seg_a[2] + t * (seg_b[2] - seg_a[2])

        dx = x - center[0]
        dy = y - center[1]
        horiz_dist = math.sqrt(dx * dx + dy * dy)

        if horiz_dist <= radius_m and alt_floor <= z <= alt_ceil:
            return True
    return False
