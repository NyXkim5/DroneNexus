"""
Swarm coordination engine — runs at 10Hz alongside telemetry.
Each tick: compute formation targets, cohesion, collision avoidance.
"""
import asyncio
import math
import time
import logging
from typing import Dict, Optional

from telemetry.collector import DroneState
from swarm.formations import compute_formation_offsets, offset_to_latlon, calculate_cohesion
from swarm.collision import CollisionAvoidance
from swarm.geofence import Geofence
from protocol import OverlayType, OffsetVector, AssetClassification
from config import OverwatchSettings

logger = logging.getLogger("overwatch.coordinator")


class SwarmCoordinator:
    """Leader-follower swarm coordination engine."""

    def __init__(self, settings: OverwatchSettings, drone_states: Dict[str, DroneState]):
        self.settings = settings
        self.drone_states = drone_states
        self.collision = CollisionAvoidance(settings.safety_bubble_m)
        self.geofence = Geofence(
            vertices=settings.geofence_vertices,
            max_altitude_m=settings.max_altitude_m,
        )
        self.current_formation = OverlayType(settings.default_formation)
        self.formation_offsets = compute_formation_offsets(self.current_formation)
        self.target_speed: float = 10.0
        self.target_altitude: float = 30.0
        self._task: Optional[asyncio.Task] = None
        self.leader_id = "ALPHA-1"

        # Formation transition state
        self._transitioning = False
        self._transition_start: float = 0.0
        self._transition_duration: float = 3.0  # seconds
        self._old_offsets: Dict[str, OffsetVector] = {}
        self._new_offsets: Dict[str, OffsetVector] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("Swarm coordinator started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def set_formation(self, formation) -> None:
        if isinstance(formation, str):
            formation = OverlayType(formation)
        self.current_formation = formation
        new_offsets = compute_formation_offsets(
            formation, self.settings.formation_spacing_m,
        )

        # Store current offsets as old, new targets as new
        self._old_offsets = {
            drone_id: OffsetVector(dx=state.offset_dx, dy=state.offset_dy)
            for drone_id, state in self.drone_states.items()
        }
        self._new_offsets = new_offsets
        self._transition_start = time.monotonic()
        self._transitioning = True
        self.formation_offsets = new_offsets  # final target
        logger.info(f"Formation transition to {formation.value} ({self._transition_duration}s)")

    def _lerp_offset(self, old: OffsetVector, new: OffsetVector, t: float) -> OffsetVector:
        """Smooth interpolation with ease-in-out."""
        # Smoothstep for natural feel
        t = t * t * (3 - 2 * t)
        return OffsetVector(
            dx=old.dx + (new.dx - old.dx) * t,
            dy=old.dy + (new.dy - old.dy) * t,
        )

    def set_speed(self, speed: float) -> None:
        self.target_speed = min(speed, self.settings.max_speed_ms)

    def set_altitude(self, altitude: float) -> None:
        self.target_altitude = max(
            self.settings.min_altitude_m,
            min(altitude, self.settings.max_altitude_m),
        )

    async def _tick_loop(self) -> None:
        interval = 1.0 / self.settings.telemetry_rate_hz
        while True:
            t0 = time.monotonic()
            await self._tick()
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))

    async def _tick(self) -> None:
        leader = self.drone_states.get(self.leader_id)
        if not leader or not leader.in_air:
            return

        # Interpolate formation offsets during transitions
        if self._transitioning:
            elapsed = time.monotonic() - self._transition_start
            progress = min(1.0, elapsed / self._transition_duration)

            for drone_id, state in self.drone_states.items():
                old = self._old_offsets.get(drone_id, OffsetVector(dx=0, dy=0))
                new = self._new_offsets.get(drone_id, OffsetVector(dx=0, dy=0))
                interpolated = self._lerp_offset(old, new, progress)
                state.offset_dx = interpolated.dx
                state.offset_dy = interpolated.dy

            if progress >= 1.0:
                self._transitioning = False
                logger.info("Formation transition complete")

        meters_to_deg = 1.0 / 111320.0

        for drone_id, state in self.drone_states.items():
            if drone_id == self.leader_id:
                state.cohesion = 1.0
                continue

            expected = self.formation_offsets.get(drone_id)
            if not expected:
                continue

            # Compute actual offset from leader (for cohesion)
            dlat = (state.lat - leader.lat) / meters_to_deg
            dlon = (state.lon - leader.lon) / (
                meters_to_deg / math.cos(math.radians(leader.lat))
            )
            heading_rad = math.radians(leader.heading)
            actual_dx = dlon * math.cos(heading_rad) + dlat * math.sin(heading_rad)
            actual_dy = -dlon * math.sin(heading_rad) + dlat * math.cos(heading_rad)

            actual_offset = OffsetVector(dx=actual_dx, dy=actual_dy)
            state.cohesion = calculate_cohesion(
                actual_offset, expected, self.settings.formation_spacing_m,
            )

        # Collision avoidance
        self.collision.check_all(list(self.drone_states.values()))

        # Geofence enforcement
        violations = self.geofence.check_all(list(self.drone_states.values()))
        for v in violations:
            logger.warning(
                f"Geofence {v.violation_type}: {v.drone_id} "
                f"at ({v.current_pos[0]:.6f}, {v.current_pos[1]:.6f}) "
                f"distance={v.distance_to_fence:.1f}m -> {v.suggested_action}"
            )

    async def elect_leader(self) -> str:
        """Weighted leader election matching protocol.js calculateLeaderScore()."""
        best_id = self.leader_id
        best_score = -1.0

        for drone_id, state in self.drone_states.items():
            if state.status.value in ("OFFLINE", "GROUNDED"):
                continue

            battery_score = state.remaining_pct / 100
            gps_score = min(1.0, state.satellites / 20) * (1 - min(1.0, state.hdop / 5))
            link_score = (state.rssi / 100) * (state.quality / 100)
            position_score = state.cohesion or 0.5

            score = (
                0.30 * battery_score +
                0.25 * gps_score +
                0.25 * link_score +
                0.20 * position_score
            )

            if score > best_score:
                best_score = score
                best_id = drone_id

        self.leader_id = best_id
        logger.info(f"Leader elected: {best_id} (score={best_score:.3f})")
        return best_id
