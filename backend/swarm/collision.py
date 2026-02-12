"""
Pairwise collision avoidance using safety bubbles.
When two drones are within safety_bubble_m, the lower-altitude drone descends.
"""
import math
import logging
from dataclasses import dataclass
from typing import List
from telemetry.collector import DroneState

logger = logging.getLogger("nexus.collision")


@dataclass
class AvoidanceCommand:
    drone_id: str
    alt_delta: float  # meters to adjust (negative = descend)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in meters between two lat/lon points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class CollisionAvoidance:
    """Checks all drone pairs for safety bubble violations."""

    def __init__(self, safety_bubble_m: float = 5.0, min_vertical_sep_m: float = 3.0):
        self.safety_bubble_m = safety_bubble_m
        self.min_vertical_sep_m = min_vertical_sep_m

    def check_all(self, states: List[DroneState]) -> List[AvoidanceCommand]:
        commands: List[AvoidanceCommand] = []
        airborne = [s for s in states if s.in_air]

        for i in range(len(airborne)):
            for j in range(i + 1, len(airborne)):
                a, b = airborne[i], airborne[j]
                horiz = haversine_meters(a.lat, a.lon, b.lat, b.lon)
                vert = abs(a.alt_msl - b.alt_msl)
                dist_3d = math.sqrt(horiz ** 2 + vert ** 2)

                if dist_3d < self.safety_bubble_m:
                    yielder = b if a.alt_msl >= b.alt_msl else a
                    commands.append(AvoidanceCommand(
                        drone_id=yielder.drone_id,
                        alt_delta=-self.min_vertical_sep_m,
                    ))
                    logger.warning(
                        f"Safety bubble: {a.drone_id}<->{b.drone_id} "
                        f"dist={dist_3d:.1f}m, {yielder.drone_id} descending"
                    )

        return commands
