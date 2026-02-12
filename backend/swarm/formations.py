"""
Formation geometry — computes per-drone offset vectors.
All offsets in meters relative to leader position.
Matches protocol.js V_FORMATION_OFFSETS.
"""
import math
from typing import Dict
from protocol import FormationType, OffsetVector

DRONE_ORDER = ["ALPHA-1", "BRAVO-2", "CHARLIE-3", "DELTA-4", "ECHO-5", "FOXTROT-6"]

# Canonical V-Formation matching protocol.js
V_FORMATION_OFFSETS: Dict[str, OffsetVector] = {
    "ALPHA-1":   OffsetVector(dx=0,   dy=0),
    "BRAVO-2":   OffsetVector(dx=-12, dy=-10),
    "CHARLIE-3": OffsetVector(dx=12,  dy=-10),
    "DELTA-4":   OffsetVector(dx=-24, dy=-20),
    "ECHO-5":    OffsetVector(dx=24,  dy=-20),
    "FOXTROT-6": OffsetVector(dx=0,   dy=-30),
}


def compute_formation_offsets(
    formation: FormationType,
    spacing: float = 15.0,
) -> Dict[str, OffsetVector]:
    if formation == FormationType.V_FORMATION:
        return dict(V_FORMATION_OFFSETS)

    elif formation == FormationType.LINE_ABREAST:
        return {
            "ALPHA-1":   OffsetVector(dx=0, dy=0),
            "BRAVO-2":   OffsetVector(dx=-spacing, dy=0),
            "CHARLIE-3": OffsetVector(dx=spacing, dy=0),
            "DELTA-4":   OffsetVector(dx=-2*spacing, dy=0),
            "ECHO-5":    OffsetVector(dx=2*spacing, dy=0),
            "FOXTROT-6": OffsetVector(dx=-3*spacing, dy=0),
        }

    elif formation == FormationType.COLUMN:
        return {
            DRONE_ORDER[i]: OffsetVector(dx=0, dy=-i * spacing)
            for i in range(len(DRONE_ORDER))
        }

    elif formation == FormationType.DIAMOND:
        return {
            "ALPHA-1":   OffsetVector(dx=0, dy=0),
            "BRAVO-2":   OffsetVector(dx=-spacing, dy=-spacing),
            "CHARLIE-3": OffsetVector(dx=spacing, dy=-spacing),
            "DELTA-4":   OffsetVector(dx=0, dy=-2*spacing),
            "ECHO-5":    OffsetVector(dx=-spacing, dy=-2*spacing),
            "FOXTROT-6": OffsetVector(dx=spacing, dy=-2*spacing),
        }

    elif formation == FormationType.ORBIT:
        n = len(DRONE_ORDER)
        radius = spacing * 2
        offsets = {}
        for i, drone_id in enumerate(DRONE_ORDER):
            angle = (2 * math.pi * i) / n
            offsets[drone_id] = OffsetVector(
                dx=radius * math.cos(angle),
                dy=radius * math.sin(angle),
            )
        return offsets

    elif formation == FormationType.SCATTER:
        import hashlib
        offsets = {}
        box = spacing * 4
        for drone_id in DRONE_ORDER:
            h = int(hashlib.md5(drone_id.encode()).hexdigest()[:8], 16)
            offsets[drone_id] = OffsetVector(
                dx=(h % 1000) / 1000 * box - box / 2,
                dy=((h >> 10) % 1000) / 1000 * box - box / 2,
            )
        return offsets

    return dict(V_FORMATION_OFFSETS)


def offset_to_latlon(
    leader_lat: float, leader_lon: float, leader_heading: float,
    offset: OffsetVector,
) -> tuple:
    """Convert formation offset to absolute lat/lon, rotated by heading."""
    heading_rad = math.radians(leader_heading)
    meters_to_deg = 1.0 / 111320.0
    rotated_dx = offset.dx * math.cos(heading_rad) - offset.dy * math.sin(heading_rad)
    rotated_dy = offset.dx * math.sin(heading_rad) + offset.dy * math.cos(heading_rad)
    target_lat = leader_lat + rotated_dy * meters_to_deg
    target_lon = leader_lon + rotated_dx * meters_to_deg / math.cos(math.radians(leader_lat))
    return target_lat, target_lon


def calculate_cohesion(
    actual: OffsetVector, expected: OffsetVector, spacing: float = 15.0,
) -> float:
    """Cohesion score 0.0-1.0. Matches protocol.js calculateCohesion()."""
    dx = actual.dx - expected.dx
    dy = actual.dy - expected.dy
    error = math.sqrt(dx * dx + dy * dy)
    return max(0.0, 1.0 - error / spacing)
