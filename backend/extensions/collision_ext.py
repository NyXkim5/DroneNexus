"""
Extension wrapper for the CollisionAvoidance subsystem.

Exposes collision checking APIs without coupling to SwarmCoordinator.
"""
from __future__ import annotations

from typing import Any

from extensions.base import Extension
from swarm.collision import CollisionAvoidance


class CollisionExtension(Extension):
    """Wraps CollisionAvoidance as a loadable extension."""

    name = "collision"
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self._collision: CollisionAvoidance | None = None

    async def load(self, app_context: dict[str, Any]) -> None:
        settings = app_context.get("settings")
        safety_bubble = getattr(settings, "safety_bubble_m", 5.0) if settings else 5.0
        min_vert = getattr(settings, "min_vertical_sep_m", 3.0) if settings else 3.0
        self._collision = CollisionAvoidance(
            safety_bubble_m=safety_bubble,
            min_vertical_sep_m=min_vert,
        )

    async def unload(self) -> None:
        self._collision = None

    def exports(self) -> dict[str, Any]:
        if self._collision is None:
            return {}
        return {
            "check_all": self._collision.check_all,
        }
