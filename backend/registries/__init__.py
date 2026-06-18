"""Typed registry pattern for drone and connection state management."""
from __future__ import annotations

from registries.base import Registry
from registries.drone_registry import DroneRegistry
from registries.connection_registry import ConnectionInfo, ConnectionRegistry

__all__ = [
    "Registry",
    "DroneRegistry",
    "ConnectionInfo",
    "ConnectionRegistry",
]
