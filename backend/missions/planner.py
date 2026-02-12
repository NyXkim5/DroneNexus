"""
Mission planning — converts NEXUS waypoints to MAVSDK MissionItems.
"""
import math
from typing import List
from protocol import Waypoint


class MissionPlanner:
    """Builds MAVSDK-compatible mission plans from NEXUS waypoint lists."""

    @staticmethod
    def build_orbit_waypoints(wp: Waypoint, num_points: int = 16) -> List[Waypoint]:
        """Approximate an orbit by placing waypoints in a circle."""
        radius = wp.radius or 50.0
        orbit_speed = wp.speed or 10.0
        waypoints = []
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points
            if wp.direction == "CCW":
                angle = -angle
            lat = wp.lat + (radius / 111320) * math.cos(angle)
            lng = wp.lng + (radius / 111320) * math.sin(angle) / math.cos(math.radians(wp.lat))
            waypoints.append(Waypoint(
                lat=lat, lng=lng, alt=wp.alt,
                type="WAYPOINT", speed=orbit_speed,
            ))
        return waypoints

    @staticmethod
    def apply_formation_offsets(
        waypoints: List[Waypoint],
        offset_dx: float, offset_dy: float,
        leader_heading: float = 0.0,
    ) -> List[Waypoint]:
        """Offset waypoints for a follower drone based on formation position."""
        heading_rad = math.radians(leader_heading)
        meters_to_deg = 1.0 / 111320.0
        result = []
        for wp in waypoints:
            rotated_dx = offset_dx * math.cos(heading_rad) - offset_dy * math.sin(heading_rad)
            rotated_dy = offset_dx * math.sin(heading_rad) + offset_dy * math.cos(heading_rad)
            result.append(Waypoint(
                lat=wp.lat + rotated_dy * meters_to_deg,
                lng=wp.lng + rotated_dx * meters_to_deg / math.cos(math.radians(wp.lat)),
                alt=wp.alt,
                type=wp.type,
                speed=wp.speed,
                radius=wp.radius,
                direction=wp.direction,
            ))
        return result
