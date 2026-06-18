"""ConnectionRegistry -- typed registry for drone connection state."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from registries.base import Registry

logger = logging.getLogger("registries.connection")


@dataclass
class ConnectionInfo:
    """Tracks a single drone connection."""
    connection_id: str
    drone_id: str
    protocol: str  # mavlink / msp / usb
    connected_at: float
    last_heartbeat: float
    status: str  # connected / disconnected / timeout


class ConnectionRegistry(Registry[ConnectionInfo]):
    """Registry for connection info with staleness and protocol queries."""

    def stale(self, timeout_s: float) -> list[ConnectionInfo]:
        """Return connections whose last heartbeat exceeds timeout_s."""
        now = time.monotonic()
        with self._lock:
            return [
                ci for ci in self._entries.values()
                if (now - ci.last_heartbeat) > timeout_s
            ]

    def get_by_protocol(self, protocol: str) -> list[ConnectionInfo]:
        """Return all connections using the given protocol."""
        with self._lock:
            return [
                ci for ci in self._entries.values()
                if ci.protocol == protocol
            ]
