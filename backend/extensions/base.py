"""
Extension base class and supporting types for the DroneNexus plugin system.

Inspired by Skybrush Server's ext_manager pattern. Extensions decouple
subsystems (collision avoidance, geofencing, alerts) from the monolithic
SwarmCoordinator so they can be loaded, started, and stopped independently.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger("dronenexus.extensions")


class ExtensionState(Enum):
    """Lifecycle state of an extension."""

    UNLOADED = "UNLOADED"
    LOADING = "LOADING"
    LOADED = "LOADED"
    RUNNING = "RUNNING"
    ERROR = "ERROR"


class ExtensionError(Exception):
    """Raised when an extension operation fails."""


class Extension(ABC):
    """Base class for all DroneNexus extensions.

    Subclasses must set ``name`` and optionally ``dependencies``.
    The manager calls lifecycle methods in order:
    ``load`` -> ``run`` (long-running) -> ``unload``.
    """

    name: str
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.state: ExtensionState = ExtensionState.UNLOADED

    @abstractmethod
    async def load(self, app_context: dict[str, Any]) -> None:
        """Initialize the extension using shared application context."""

    async def run(self, app_context: dict[str, Any]) -> None:
        """Optional long-running task. Override for periodic work."""

    async def unload(self) -> None:
        """Tear down resources acquired during load."""

    def exports(self) -> dict[str, Any]:
        """Return APIs exposed to other extensions.

        Keys are export names, values are callables or objects.
        """
        return {}

    def __repr__(self) -> str:
        return f"<Extension {self.name} state={self.state.value}>"
