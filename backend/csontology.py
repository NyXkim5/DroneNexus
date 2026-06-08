"""
BULWARK counter-swarm ontology — the shared world model.

Every downstream module codes against these dataclasses and enums. The fusion
engine, threat classifier, allocation engine, and wargame runner all read and
write these objects. Keep this file stable. Changes here ripple everywhere.

Coordinate frame
----------------
All positions are in a local ENU (East, North, Up) frame in meters with origin
at the site center. The origin matches mock_drone (lat 33.6405, lon -117.8443).
  x = East  meters
  y = North meters
  z = Up    meters (altitude above the origin ground plane)
Velocities are in meters per second in the same ENU axes.

The HUD renders on a map, so this module ships two converters:
  latlon_to_enu(lat, lon, alt) -> (x, y, z)
  enu_to_latlon(x, y, z)       -> (lat, lon, alt)
These use the same flat-earth approximation as mock_drone for consistency. They
are accurate enough for a site-scale picture (a few kilometers).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ---- Coordinate frame ----

ORIGIN_LAT = 33.6405
ORIGIN_LON = -117.8443
_METERS_PER_DEG = 111320.0


def latlon_to_enu(lat: float, lon: float, alt: float = 0.0) -> Tuple[float, float, float]:
    """Convert geodetic lat/lon/alt to local ENU meters about the site origin.

    Uses a flat-earth approximation matching mock_drone. Good to a few km.
    """
    x = (lon - ORIGIN_LON) * _METERS_PER_DEG * math.cos(math.radians(ORIGIN_LAT))
    y = (lat - ORIGIN_LAT) * _METERS_PER_DEG
    z = alt
    return x, y, z


def enu_to_latlon(x: float, y: float, z: float = 0.0) -> Tuple[float, float, float]:
    """Convert local ENU meters back to geodetic lat/lon/alt about the origin."""
    lat = ORIGIN_LAT + y / _METERS_PER_DEG
    lon = ORIGIN_LON + x / (_METERS_PER_DEG * math.cos(math.radians(ORIGIN_LAT)))
    alt = z
    return lat, lon, alt


# ---- Type aliases ----

# A 3D vector in ENU meters or m/s, as (x, y, z).
Vec3 = Tuple[float, float, float]


# ---- Enums ----

class DefenderKind(str, Enum):
    """Effector category, kinetic and non-kinetic.

    INTERCEPTOR and NET are kinetic single-target effectors. JAMMER and EW are
    soft-kill RF effectors that defeat control and navigation. HPM is a
    high-power-microwave area effector that fries electronics across a cone.
    LASER is directed energy, single-target with a near-zero marginal cost.
    Non-kinetic area effectors are what flip the cost-exchange ratio: one cheap,
    reusable shot neutralizes many drones at once.
    """
    INTERCEPTOR = "INTERCEPTOR"
    NET = "NET"
    JAMMER = "JAMMER"
    EW = "EW"
    HPM = "HPM"
    LASER = "LASER"


class EngagementStatus(str, Enum):
    """Lifecycle of one defender-versus-threat engagement."""
    PENDING = "PENDING"
    HIT = "HIT"
    MISS = "MISS"
    LEAK = "LEAK"


class SwarmIntent(str, Enum):
    """Inferred coordinated behavior of a hostile swarm."""
    SATURATION = "SATURATION"
    WAVES = "WAVES"
    DECOY = "DECOY"
    PROBE = "PROBE"
    UNKNOWN = "UNKNOWN"


class TrackClass(str, Enum):
    """Hostility classification of a fused track."""
    HOSTILE = "HOSTILE"
    FRIENDLY = "FRIENDLY"
    UNKNOWN = "UNKNOWN"


class DefenderStatus(str, Enum):
    """Readiness of a defender effector."""
    READY = "READY"
    ENGAGING = "ENGAGING"
    RELOADING = "RELOADING"
    DEPLETED = "DEPLETED"
    OFFLINE = "OFFLINE"


# ---- World-model dataclasses ----

@dataclass(frozen=True)
class Detection:
    """One raw contact from a single sensor at one instant.

    Immutable. The fusion engine consumes streams of these and never mutates
    them. Position and velocity are ENU meters and m/s about the site origin.
    """
    id: str
    timestamp: float
    position: Vec3
    velocity: Vec3
    confidence: float
    sensor_id: str
    size_rcs: Optional[float] = None


@dataclass
class Track:
    """A fused, time-stable estimate of one real object across sensors.

    Mutable. The fusion engine updates it each tick, coasts it on dropout, and
    expires it after a timeout. covariance is a compact summary of position
    uncertainty as ENU standard deviations in meters (sigma_x, sigma_y, sigma_z).
    """
    id: str
    position: Vec3
    velocity: Vec3
    covariance: Vec3
    last_update: float
    age: float = 0.0
    classification: TrackClass = TrackClass.UNKNOWN
    confidence: float = 0.0
    source_detection_ids: List[str] = field(default_factory=list)
    history: List[Vec3] = field(default_factory=list)
    # Observed battle-damage assessment: how many times an effector kind has been
    # fired at this track and the airframe survived. The threat layer turns a high
    # count into an "ineffective" belief so the allocator stops wasting that
    # effector and escalates, which is how the defense learns a target is jam
    # resistant or hardened without being told the ground truth.
    effector_misses: Dict[str, int] = field(default_factory=dict)


@dataclass
class Swarm:
    """A group of tracks judged to act as one coordinated unit."""
    id: str
    member_track_ids: List[str]
    centroid: Vec3
    formation: str
    intent: SwarmIntent
    size: int
    first_seen: float


@dataclass
class Threat:
    """A scored, prioritized danger derived from a track or a swarm.

    track_id is the airframe this threat represents and is always set in the live
    pipeline. swarm_id is optional situational-awareness context naming the swarm
    the track belongs to. score is 0..1. time_to_impact_s is seconds until the
    threat reaches a protected asset, or None if it is not closing. value_at_risk
    is the expected dollar damage the threat would do if it leaks.
    """
    id: str
    score: float
    time_to_impact_s: Optional[float]
    value_at_risk: float
    priority_rank: int
    track_id: Optional[str] = None
    swarm_id: Optional[str] = None
    intent: "SwarmIntent" = SwarmIntent.UNKNOWN
    confidence: float = 1.0
    # Effector kinds observed to be ineffective against this threat, so the
    # allocator can skip them and reach for an effector that works. Carried from
    # the underlying track's observed survivals.
    ineffective_kinds: frozenset = field(default_factory=frozenset)


@dataclass
class Defender:
    """A finite effector the allocator can assign to threats.

    range_m is engagement reach in meters. capacity is remaining shots or
    simultaneous targets. reload_s is recovery time between engagements.
    kill_prob is single-shot kill probability 0..1. unit_cost is dollars per
    engagement, used for the cost-exchange metric.

    effect_radius_m is the lethal radius around an aim point. Zero means a single
    point target. A positive radius makes this an area effector, like HPM or wide
    EW, that neutralizes every drone within the radius of its aim point in one
    shot. max_simultaneous caps how many drones one area shot can neutralize, so
    a cheap reusable effector kills many drones per engagement and drives the
    cost per kill far below the attacker cost per drone.
    """
    id: str
    position: Vec3
    kind: DefenderKind
    capacity: int
    range_m: float
    reload_s: float
    kill_prob: float
    unit_cost: float
    status: DefenderStatus = DefenderStatus.READY
    effect_radius_m: float = 0.0
    max_simultaneous: int = 1


@dataclass
class Engagement:
    """One assignment of a defender against a threat and its outcome.

    target_threat_id is the primary threat. For an area effector, aim_point is the
    ENU point the shot is centered on and neutralized_threat_ids lists every
    threat the shot removed on a HIT, so one engagement can credit many kills.
    The fields also carry the decision lineage for audit and replay.
    """
    id: str
    defender_id: str
    target_threat_id: str
    start_time: float
    status: EngagementStatus = EngagementStatus.PENDING
    cost: float = 0.0
    aim_point: Optional[Vec3] = None
    neutralized_threat_ids: List[str] = field(default_factory=list)


@dataclass
class Site:
    """The defended location and what it protects."""
    id: str
    position: Vec3
    protected_assets: List[str]
    value: float


def now() -> float:
    """Wall-clock seconds, the shared timestamp source for the world model."""
    return time.time()
