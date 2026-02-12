"""
MSP serial connection manager.
Handles pyserial connection lifecycle, auto-detection of baud rate,
and bidirectional MSP message exchange with Betaflight flight controllers.
"""
import asyncio
import logging
from typing import Optional, Dict

from msp.protocol import (
    MSPCode, MSPEncoder, MSPDecoder, MSPMessage,
    parse_attitude, parse_raw_gps, parse_analog, parse_altitude,
    parse_status_ex, parse_battery_state, parse_api_version,
)

logger = logging.getLogger("nexus.msp")

# Parsers keyed by MSP code
PARSERS = {
    MSPCode.MSP_ATTITUDE: parse_attitude,
    MSPCode.MSP_RAW_GPS: parse_raw_gps,
    MSPCode.MSP_ANALOG: parse_analog,
    MSPCode.MSP_ALTITUDE: parse_altitude,
    MSPCode.MSP_STATUS_EX: parse_status_ex,
    MSPCode.MSP_BATTERY_STATE: parse_battery_state,
    MSPCode.MSP_API_VERSION: parse_api_version,
}


class MSPConnection:
    """Wraps an async serial connection to a Betaflight flight controller."""

    COMMON_BAUDS = [115200, 57600, 921600, 230400, 460800]

    def __init__(self, port: str, baud_rate: int = 115200):
        self.port = port
        self.baud_rate = baud_rate
        self.connected: bool = False
        self.fc_variant: str = ""
        self.api_version: str = ""
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._decoder = MSPDecoder()
        self._read_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Open serial port and verify MSP handshake."""
        try:
            import serial_asyncio
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self.port, baudrate=self.baud_rate
            )
        except ImportError:
            logger.error("pyserial-asyncio not installed — run: pip install pyserial-asyncio")
            return False
        except Exception as e:
            logger.error(f"[MSP] Failed to open {self.port}: {e}")
            return False

        self._read_task = asyncio.create_task(self._read_loop())

        resp = await self.request(MSPCode.MSP_API_VERSION, timeout=2.0)
        if resp is None:
            logger.error(f"[MSP] No MSP response on {self.port} at {self.baud_rate} baud")
            await self.disconnect()
            return False

        parsed = parse_api_version(resp.payload)
        self.api_version = f"{parsed.get('api_major', 0)}.{parsed.get('api_minor', 0)}"
        self.connected = True
        logger.info(f"[MSP] Connected on {self.port} — API v{self.api_version}")
        return True

    async def disconnect(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._writer:
            self._writer.close()
        self.connected = False
        self._decoder.reset()
        logger.info(f"[MSP] Disconnected from {self.port}")

    async def request(self, code: int, payload: bytes = b'',
                      timeout: float = 1.0) -> Optional[MSPMessage]:
        """Send MSP request and await the matching response."""
        async with self._lock:
            frame = MSPEncoder.encode(code, payload)
            if self._writer is None:
                return None
            self._writer.write(frame)
            await self._writer.drain()

            future = asyncio.get_event_loop().create_future()
            self._pending[code] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(code, None)
            return None

    async def poll_telemetry(self) -> dict:
        """Request all telemetry MSP codes and return a unified dict."""
        result = {}
        codes = [
            (MSPCode.MSP_ATTITUDE, 'attitude'),
            (MSPCode.MSP_RAW_GPS, 'gps'),
            (MSPCode.MSP_ANALOG, 'analog'),
            (MSPCode.MSP_ALTITUDE, 'altitude'),
            (MSPCode.MSP_STATUS_EX, 'status'),
            (MSPCode.MSP_BATTERY_STATE, 'battery'),
        ]
        for code, key in codes:
            resp = await self.request(code, timeout=0.5)
            if resp is not None:
                parser = PARSERS.get(code)
                if parser:
                    result[key] = parser(resp.payload)
        return result

    async def _read_loop(self) -> None:
        """Background task — reads serial data and dispatches responses."""
        try:
            while True:
                data = await self._reader.read(256)
                if not data:
                    break
                messages = self._decoder.feed(data)
                for msg in messages:
                    future = self._pending.pop(msg.code, None)
                    if future and not future.done():
                        future.set_result(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[MSP] Read loop error: {e}")
            self.connected = False

    @classmethod
    async def auto_detect_baud(cls, port: str) -> Optional['MSPConnection']:
        """Try common baud rates and return a connected MSPConnection or None."""
        for baud in cls.COMMON_BAUDS:
            conn = cls(port, baud)
            if await conn.connect():
                return conn
            await conn.disconnect()
        return None
