"""
MAVSDK connection manager — manages System lifecycle for each drone.
Used when simulation_mode = False.
"""
import asyncio
import logging
from typing import Dict, Optional

from config import OverwatchSettings, ASSET_ROSTER

logger = logging.getLogger("overwatch.mavlink")


class DroneConnection:
    """Wraps a single MAVSDK System with connection state."""

    def __init__(self, drone_id: str, address: str):
        self.drone_id = drone_id
        self.address = address
        self.system = None
        self.connected: bool = False
        self.last_heartbeat: float = 0.0
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        try:
            from mavsdk import System
            self.system = System()
            await self.system.connect(system_address=self.address)
            logger.info(f"[{self.drone_id}] Connecting to {self.address}")

            async for state in self.system.core.connection_state():
                if state.is_connected:
                    self.connected = True
                    logger.info(f"[{self.drone_id}] Connected")
                    break

            self._heartbeat_task = asyncio.create_task(self._monitor_heartbeat())
        except ImportError:
            logger.error("mavsdk not installed — run: pip install mavsdk")
        except Exception as e:
            logger.error(f"[{self.drone_id}] Connection failed: {e}")

    async def disconnect(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self.connected = False

    async def _monitor_heartbeat(self) -> None:
        async for state in self.system.core.connection_state():
            if state.is_connected:
                self.last_heartbeat = asyncio.get_event_loop().time()
            else:
                self.connected = False
                logger.warning(f"[{self.drone_id}] Lost connection")


class MAVLinkConnectionManager:
    """Manages all drone connections for SITL or real hardware."""

    def __init__(self, settings: OverwatchSettings):
        self.settings = settings
        self.connections: Dict[str, DroneConnection] = {}

    async def connect_all(self) -> None:
        tasks = []
        for i, drone_cfg in enumerate(ASSET_ROSTER[:self.settings.sitl_drone_count]):
            port = self.settings.sitl_base_port + i
            address = f"udp://:{port}"
            conn = DroneConnection(drone_cfg.id, address)
            self.connections[drone_cfg.id] = conn
            tasks.append(conn.connect())
        results = await asyncio.gather(*tasks, return_exceptions=True)
        connected = sum(1 for c in self.connections.values() if c.connected)
        logger.info(f"Connected {connected}/{len(self.connections)} assets")

    async def disconnect_all(self) -> None:
        for conn in self.connections.values():
            await conn.disconnect()

    def get(self, drone_id: str) -> Optional[DroneConnection]:
        return self.connections.get(drone_id)
