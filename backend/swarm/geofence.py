"""
Geofence engine — polygon boundary + altitude ceiling enforcement.
Uses ray-casting point-in-polygon for boundary checks and approximate
geodesic distance to nearest fence edge for violation severity.
"""
import math
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from telemetry.collector import DroneState

logger = logging.getLogger("overwatch.geofence")

# Earth radius in meters for haversine calculations
_EARTH_R = 6371000.0


@dataclass
class GeofenceViolation:
    """Describes a single geofence breach."""
    drone_id: str
    violation_type: str          # "BOUNDARY" or "ALTITUDE"
    distance_to_fence: float     # meters (positive = outside boundary / above ceiling)
    current_pos: Tuple[float, float]  # (lat, lon)
    suggested_action: str = "RTL"


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in meters between two lat/lon points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return _EARTH_R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _point_to_segment_distance(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """
    Minimum distance from point P to line segment A-B, all in flat
    metre-projected coordinates.
    """
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab_sq = abx * abx + aby * aby
    if ab_sq == 0.0:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab_sq))
    proj_x = ax + t * abx
    proj_y = ay + t * aby
    return math.hypot(px - proj_x, py - proj_y)


class Geofence:
    """
    Polygon geofence with altitude ceiling.

    Parameters
    ----------
    vertices : list of (lat, lon) tuples
        Ordered polygon vertices (automatically closed).
    max_altitude_m : float
        Hard altitude ceiling in metres MSL.
    """

    def __init__(
        self,
        vertices: List[Tuple[float, float]],
        max_altitude_m: float = 120.0,
    ) -> None:
        if len(vertices) < 3:
            raise ValueError("Geofence requires at least 3 vertices")
        self.vertices = list(vertices)
        self.max_altitude_m = max_altitude_m

        # Pre-compute a flat-metre projection of vertices centred on the
        # polygon centroid for fast distance calculations.
        self._centre_lat = sum(v[0] for v in self.vertices) / len(self.vertices)
        self._centre_lon = sum(v[1] for v in self.vertices) / len(self.vertices)
        self._m_per_deg_lat = math.radians(1) * _EARTH_R  # ~111 320 m
        self._m_per_deg_lon = (
            math.radians(1) * _EARTH_R * math.cos(math.radians(self._centre_lat))
        )
        self._verts_m: List[Tuple[float, float]] = [
            self._to_metres(lat, lon) for lat, lon in self.vertices
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_metres(self, lat: float, lon: float) -> Tuple[float, float]:
        """Project lat/lon to flat metres relative to polygon centroid."""
        mx = (lon - self._centre_lon) * self._m_per_deg_lon
        my = (lat - self._centre_lat) * self._m_per_deg_lat
        return (mx, my)

    def _point_in_polygon(self, lat: float, lon: float) -> bool:
        """
        Ray-casting algorithm.  Cast a ray in the +lon direction and
        count edge crossings.  Odd count => inside.
        """
        n = len(self.vertices)
        inside = False
        x, y = lon, lat

        j = n - 1
        for i in range(n):
            xi, yi = self.vertices[i][1], self.vertices[i][0]
            xj, yj = self.vertices[j][1], self.vertices[j][0]

            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i

        return inside

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def distance_to_boundary(self, lat: float, lon: float) -> float:
        """
        Approximate distance in metres from (lat, lon) to the nearest
        fence edge.  Positive means inside the fence; negative means
        outside.
        """
        px, py = self._to_metres(lat, lon)
        n = len(self._verts_m)

        min_dist = float("inf")
        for i in range(n):
            ax, ay = self._verts_m[i]
            bx, by = self._verts_m[(i + 1) % n]
            d = _point_to_segment_distance(px, py, ax, ay, bx, by)
            if d < min_dist:
                min_dist = d

        if self._point_in_polygon(lat, lon):
            return min_dist   # positive  => inside
        return -min_dist      # negative  => outside

    def check(self, state: DroneState) -> Optional[GeofenceViolation]:
        """
        Check a single drone against the geofence.

        Only airborne drones (``state.in_air``) are evaluated; grounded
        drones are assumed safe.

        Returns a :class:`GeofenceViolation` if a breach is detected,
        otherwise ``None``.
        """
        if not state.in_air:
            return None

        # --- Boundary check (higher priority) ---
        if not self._point_in_polygon(state.lat, state.lon):
            dist = abs(self.distance_to_boundary(state.lat, state.lon))
            return GeofenceViolation(
                drone_id=state.drone_id,
                violation_type="BOUNDARY",
                distance_to_fence=dist,
                current_pos=(state.lat, state.lon),
                suggested_action="RTL",
            )

        # --- Altitude check ---
        if state.alt_msl > self.max_altitude_m:
            excess = state.alt_msl - self.max_altitude_m
            return GeofenceViolation(
                drone_id=state.drone_id,
                violation_type="ALTITUDE",
                distance_to_fence=excess,
                current_pos=(state.lat, state.lon),
                suggested_action="RTL",
            )

        return None

    def check_all(self, states: List[DroneState]) -> List[GeofenceViolation]:
        """Check every drone and return a list of violations (may be empty)."""
        violations: List[GeofenceViolation] = []
        for state in states:
            v = self.check(state)
            if v is not None:
                violations.append(v)
        return violations
