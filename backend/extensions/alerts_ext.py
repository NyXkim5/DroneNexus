"""
Extension wrapper for the AlertEngine subsystem.

Depends on collision and geofence extensions. Runs a periodic alert
check loop that evaluates drone states against safety rules.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from extensions.base import Extension
from swarm.alerts import AlertEngine, AlertPacket

logger = logging.getLogger("dronenexus.extensions.alerts")


class AlertsExtension(Extension):
    """Alert engine extension with periodic evaluation loop."""

    name = "alerts"
    dependencies: tuple[str, ...] = ("collision", "geofence")

    def __init__(self) -> None:
        super().__init__()
        self._engine: AlertEngine | None = None
        self._active_alerts: list[AlertPacket] = []
        self._acknowledged: set[str] = set()
        self._check_collision: Any = None
        self._check_geofence: Any = None

    async def load(self, app_context: dict[str, Any]) -> None:
        settings = app_context.get("settings")
        if settings is None:
            raise ValueError("AlertsExtension requires 'settings' in app_context")
        self._engine = AlertEngine(settings)

        manager = app_context.get("extension_manager")
        if manager:
            self._check_collision = manager.get_export("collision", "check_all")
            self._check_geofence = manager.get_export("geofence", "check_all")

    async def run(self, app_context: dict[str, Any]) -> None:
        """Periodic alert evaluation at 1 Hz."""
        drone_states = app_context.get("drone_states", {})
        while True:
            self._evaluate(drone_states)
            await asyncio.sleep(1.0)

    def _evaluate(self, drone_states: dict[str, Any]) -> None:
        """Run alert engine and update active alerts list."""
        if not self._engine or not drone_states:
            return
        packets = self._engine.check_all(drone_states)
        fresh = [
            p for p in packets
            if f"{p.drone_id}:{p.alert_type}" not in self._acknowledged
        ]
        self._active_alerts = fresh
        if fresh:
            logger.debug("Active alerts: %d", len(fresh))

    def _acknowledge(self, drone_id: str, alert_type: str) -> None:
        """Acknowledge an alert so it stops appearing in active list."""
        key = f"{drone_id}:{alert_type}"
        self._acknowledged.add(key)
        logger.info("Acknowledged alert: %s", key)

    async def unload(self) -> None:
        self._engine = None
        self._active_alerts.clear()
        self._acknowledged.clear()
        self._check_collision = None
        self._check_geofence = None

    def exports(self) -> dict[str, Any]:
        return {
            "active_alerts": self._active_alerts,
            "acknowledge": self._acknowledge,
        }
