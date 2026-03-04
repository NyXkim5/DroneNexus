"""
Auto-connection manager.
Runs periodic USB scans and connects detected flight controllers
to the appropriate protocol handler (MSP or MAVLink).
"""
import asyncio
import logging
from typing import Dict, Set, Optional, TYPE_CHECKING

from usb.scanner import USBScanner, DetectedDevice

if TYPE_CHECKING:
    from msp.connection import MSPConnection

logger = logging.getLogger("overwatch.usb.auto")


class AutoConnector:
    """Background task that periodically scans USB ports and auto-connects FCs."""

    def __init__(self):
        self.scanner = USBScanner()
        self.connected_ports: Set[str] = set()
        self.msp_connections: Dict[str, 'MSPConnection'] = {}
        self.mavlink_ports: Dict[str, DetectedDevice] = {}
        self._task: Optional[asyncio.Task] = None
        self._on_device_connected = None
        self._on_device_disconnected = None

    def on_connected(self, callback):
        """Register callback(device: DetectedDevice, connection) for new connections."""
        self._on_device_connected = callback

    def on_disconnected(self, callback):
        """Register callback(port: str) for disconnected devices."""
        self._on_device_disconnected = callback

    async def start(self, interval: float = 5.0) -> None:
        """Start the background scan loop."""
        logger.info(f"[AutoConnect] Starting scan loop (interval={interval}s)")
        self._task = asyncio.create_task(self._scan_loop(interval))

    async def stop(self) -> None:
        """Stop the background scan loop and disconnect all."""
        if self._task:
            self._task.cancel()
            self._task = None
        for port, conn in list(self.msp_connections.items()):
            await conn.disconnect()
        self.msp_connections.clear()
        self.connected_ports.clear()

    async def scan_once(self):
        """Run a single scan and return detected devices."""
        return await self.scanner.scan()

    async def connect_device(self, device: DetectedDevice) -> bool:
        """Manually connect to a specific detected device."""
        return await self._connect(device)

    async def _scan_loop(self, interval: float) -> None:
        try:
            while True:
                await self._scan_and_connect()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _scan_and_connect(self) -> None:
        devices = await self.scanner.scan()
        current_ports = {d.port for d in devices}

        # Detect disconnected devices
        for port in list(self.connected_ports):
            if port not in current_ports:
                logger.info(f"[AutoConnect] Device disconnected: {port}")
                self.connected_ports.discard(port)
                conn = self.msp_connections.pop(port, None)
                if conn:
                    await conn.disconnect()
                self.mavlink_ports.pop(port, None)
                if self._on_device_disconnected:
                    self._on_device_disconnected(port)

        # Connect new devices
        for device in devices:
            if device.port not in self.connected_ports:
                await self._connect(device)

    async def _connect(self, device: DetectedDevice) -> bool:
        if device.protocol == 'MSP':
            return await self._connect_msp(device)
        elif device.protocol == 'MAVLINK':
            return await self._connect_mavlink(device)
        else:
            logger.info(f"[AutoConnect] Unknown protocol on {device.port}, skipping")
            return False

    async def _connect_msp(self, device: DetectedDevice) -> bool:
        from msp.connection import MSPConnection

        conn = MSPConnection(device.port, device.baud_rate)
        if await conn.connect():
            self.msp_connections[device.port] = conn
            self.connected_ports.add(device.port)
            logger.info(f"[AutoConnect] MSP connected: {device.port} ({device.label})")
            if self._on_device_connected:
                self._on_device_connected(device, conn)
            return True
        return False

    async def _connect_mavlink(self, device: DetectedDevice) -> bool:
        self.mavlink_ports[device.port] = device
        self.connected_ports.add(device.port)
        logger.info(f"[AutoConnect] MAVLink device registered: {device.port} ({device.label})")
        if self._on_device_connected:
            self._on_device_connected(device, None)
        return True
