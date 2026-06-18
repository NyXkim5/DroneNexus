"""DroneRegistry -- typed registry specialized for DroneState entries."""
from __future__ import annotations

import logging
from telemetry.collector import DroneState
from registries.base import Registry

logger = logging.getLogger("registries.drone")


class DroneRegistry(Registry[DroneState]):
    """Registry for drone states with convenience query helpers."""

    @property
    def active_drones(self) -> int:
        """Count of drones currently in the registry."""
        return len(self)

    def get_by_status(self, status: str) -> list[DroneState]:
        """Return all drone states matching a given fix_type status."""
        with self._lock:
            return [
                ds for ds in self._entries.values()
                if ds.fix_type == status
            ]

    def get_positions(self) -> dict[str, tuple[float, float, float]]:
        """Return a dict mapping drone_id to (lat, lon, alt_msl)."""
        with self._lock:
            return {
                drone_id: (ds.lat, ds.lon, ds.alt_msl)
                for drone_id, ds in self._entries.items()
            }
