"""
Swarm coordination engine -- runs at 10Hz alongside telemetry.
Each tick: compute formation targets, cohesion, collision avoidance.
"""
from __future__ import annotations

import asyncio
import math
import time
import logging
from typing import Any, Dict, Optional, Union

from telemetry.collector import DroneState
from swarm.formations import compute_formation_offsets, offset_to_latlon, calculate_cohesion
from swarm.collision import CollisionAvoidance
from swarm.geofence import Geofence
from protocol import OverlayType, OffsetVector, AssetClassification
from config import OverwatchSettings
from extensions.base import Extension
from extensions.manager import ExtensionManager
from extensions.collision_ext import CollisionExtension
from extensions.geofence_ext import GeofenceExtension
from extensions.alerts_ext import AlertsExtension
from registries.drone_registry import DroneRegistry
from registries.connection_registry import ConnectionRegistry, ConnectionInfo

logger = logging.getLogger("overwatch.coordinator")

STALE_CONNECTION_TIMEOUT_S = 10.0


class SwarmCoordinator:
    """Leader-follower swarm coordination engine."""

    def __init__(
        self,
        settings: OverwatchSettings,
        drone_states: Union[Dict[str, DroneState], DroneRegistry],
    ):
        self.settings = settings

        # Wrap raw dict in DroneRegistry for backward compatibility
        if isinstance(drone_states, dict):
            self._registry = DroneRegistry()
            for drone_id, state in drone_states.items():
                self._registry.add(drone_id, state)
        else:
            self._registry = drone_states

        self._register_registry_callbacks()

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

        # Connection tracking
        self._connections = ConnectionRegistry()

        # Extension system
        self._ext_manager = ExtensionManager()
        self._register_default_extensions()

        # Backward-compatible attributes (delegates to extensions after load)
        self.collision = CollisionAvoidance(settings.safety_bubble_m)
        self.geofence = Geofence(
            vertices=settings.geofence_vertices,
            max_altitude_m=settings.max_altitude_m,
        )

    def _register_registry_callbacks(self) -> None:
        """Register on_added and on_removed callbacks for drone lifecycle logging."""
        self._registry.on_added.append(self._on_drone_added)
        self._registry.on_removed.append(self._on_drone_removed)

    @staticmethod
    def _on_drone_added(drone_id: str, state: DroneState) -> None:
        logger.info("Drone joined swarm: %s", drone_id)

    @staticmethod
    def _on_drone_removed(drone_id: str, state: DroneState) -> None:
        logger.info("Drone left swarm: %s", drone_id)

    @property
    def drone_states(self) -> dict[str, DroneState]:
        """Backward-compatible dict view of the drone registry."""
        return dict(self._registry.items())

    def register_connection(
        self, conn_id: str, drone_id: str, protocol: str,
    ) -> None:
        """Register a new connection in the connection registry."""
        now = time.monotonic()
        info = ConnectionInfo(
            connection_id=conn_id,
            drone_id=drone_id,
            protocol=protocol,
            connected_at=now,
            last_heartbeat=now,
            status="connected",
        )
        self._connections.add(conn_id, info)
        logger.info("Connection registered: %s (drone=%s, proto=%s)", conn_id, drone_id, protocol)

    def unregister_connection(self, conn_id: str) -> None:
        """Remove a connection from the connection registry."""
        self._connections.remove(conn_id)
        logger.info("Connection unregistered: %s", conn_id)

    def _register_default_extensions(self) -> None:
        """Register the built-in collision, geofence, and alerts extensions."""
        self._ext_manager.register(CollisionExtension())
        self._ext_manager.register(GeofenceExtension())
        self._ext_manager.register(AlertsExtension())

    def _build_app_context(self) -> dict[str, Any]:
        """Build the shared application context dict for extensions."""
        return {
            "settings": self.settings,
            "drone_states": self.drone_states,
            "extension_manager": self._ext_manager,
        }

    def register_extension(self, ext: Extension) -> None:
        """Register a custom extension with the coordinator."""
        self._ext_manager.register(ext)

    async def start(self) -> None:
        app_context = self._build_app_context()
        await self._ext_manager.load_all(app_context)
        await self._ext_manager.start_all(app_context)
        self._sync_compat_attrs()
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("Swarm coordinator started")

    def _sync_compat_attrs(self) -> None:
        """Sync backward-compatible attributes from loaded extensions."""
        try:
            collision_ext = self._ext_manager.get("collision")
            inner = getattr(collision_ext, "_collision", None)
            if inner is not None:
                self.collision = inner
        except Exception:
            logger.debug("Collision extension not available for compat sync")
        try:
            geofence_ext = self._ext_manager.get("geofence")
            inner = getattr(geofence_ext, "_geofence", None)
            if inner is not None:
                self.geofence = inner
        except Exception:
            logger.debug("Geofence extension not available for compat sync")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._ext_manager.stop_all()
        await self._ext_manager.unload_all()

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
            for drone_id, state in self._registry.items()
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
        leader = self._registry.get(self.leader_id)
        if not leader or not leader.in_air:
            return

        self._tick_transitions(leader)
        self._tick_cohesion(leader)
        self._tick_extension_checks()
        self._tick_stale_connections()

    def _tick_transitions(self, leader: DroneState) -> None:
        """Interpolate formation offsets during transitions."""
        if not self._transitioning:
            return

        elapsed = time.monotonic() - self._transition_start
        progress = min(1.0, elapsed / self._transition_duration)

        for drone_id, state in self._registry.items():
            old = self._old_offsets.get(drone_id, OffsetVector(dx=0, dy=0))
            new = self._new_offsets.get(drone_id, OffsetVector(dx=0, dy=0))
            interpolated = self._lerp_offset(old, new, progress)
            state.offset_dx = interpolated.dx
            state.offset_dy = interpolated.dy

        if progress >= 1.0:
            self._transitioning = False
            logger.info("Formation transition complete")

    def _tick_cohesion(self, leader: DroneState) -> None:
        """Compute cohesion scores for all drones relative to leader."""
        meters_to_deg = 1.0 / 111320.0

        for drone_id, state in self._registry.items():
            if drone_id == self.leader_id:
                state.cohesion = 1.0
                continue

            expected = self.formation_offsets.get(drone_id)
            if not expected:
                continue

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

    def _tick_extension_checks(self) -> None:
        """Run collision and geofence checks via the extension system."""
        collision_check = self._ext_manager.get_export("collision", "check_all")
        collision_check(list(self._registry.values()))

        geofence_check = self._ext_manager.get_export("geofence", "check_all")
        violations = geofence_check(list(self._registry.values()))
        for v in violations:
            logger.warning(
                f"Geofence {v.violation_type}: {v.drone_id} "
                f"at ({v.current_pos[0]:.6f}, {v.current_pos[1]:.6f}) "
                f"distance={v.distance_to_fence:.1f}m -> {v.suggested_action}"
            )

    def _tick_stale_connections(self) -> None:
        """Log warnings for connections with stale heartbeats."""
        stale = self._connections.stale(STALE_CONNECTION_TIMEOUT_S)
        for conn in stale:
            age = time.monotonic() - conn.last_heartbeat
            logger.warning(
                "Stale connection: %s (drone=%s, last heartbeat %.1fs ago)",
                conn.connection_id, conn.drone_id, age,
            )

    async def elect_leader(self) -> str:
        """Weighted leader election matching protocol.js calculateLeaderScore()."""
        best_id = self.leader_id
        best_score = -1.0

        for drone_id, state in self._registry.items():
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
