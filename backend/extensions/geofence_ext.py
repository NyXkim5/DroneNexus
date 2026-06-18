"""
Extension wrapper for the Geofence subsystem.

Exposes geofence checking APIs without coupling to SwarmCoordinator.
"""
from __future__ import annotations

from typing import Any

from extensions.base import Extension
from swarm.geofence import Geofence


class GeofenceExtension(Extension):
    """Wraps Geofence as a loadable extension."""

    name = "geofence"
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self._geofence: Geofence | None = None

    async def load(self, app_context: dict[str, Any]) -> None:
        settings = app_context.get("settings")
        vertices = getattr(settings, "geofence_vertices", None) if settings else None
        if not vertices:
            vertices = [
                (35.0, -97.0),
                (35.0, -96.0),
                (36.0, -96.0),
                (36.0, -97.0),
            ]
        max_alt = getattr(settings, "max_altitude_m", 120.0) if settings else 120.0
        self._geofence = Geofence(
            vertices=vertices,
            max_altitude_m=max_alt,
        )

    async def unload(self) -> None:
        self._geofence = None

    def exports(self) -> dict[str, Any]:
        if self._geofence is None:
            return {}
        return {
            "check": self._geofence.check,
            "check_all": self._geofence.check_all,
            "contains": self._geofence._point_in_polygon,
        }
