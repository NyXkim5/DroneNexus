"""
FPV goggles telemetry bridge.
Parses telemetry from DJI FPV, HDZero, and Walksnail goggles
and translates it into OVERWATCH VideoLinkData.

DJI and Walksnail use MSP-based protocols for OSD telemetry.
HDZero uses a custom serial protocol for link statistics.
"""
import asyncio
import struct
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from msp.protocol import MSPEncoder, MSPDecoder, MSPCode

logger = logging.getLogger("overwatch.goggles")


@dataclass
class GogglesData:
    """Unified goggles telemetry data."""
    system: str = ""
    link_quality: int = 0
    channel: int = 0
    frequency_mhz: int = 0
    recording: bool = False
    bitrate_kbps: int = 0
    latency_ms: int = 0
    rssi_dbm: int = 0
    snr: float = 0.0
    connected: bool = False


class GogglesBase(ABC):
    """Abstract base for goggles telemetry parsers."""

    def __init__(self, port: str, baud_rate: int = 115200):
        self.port = port
        self.baud_rate = baud_rate
        self.connected = False
        self._reader = None
        self._writer = None
        self._task: Optional[asyncio.Task] = None
        self.latest_data = GogglesData()

    @abstractmethod
    async def _parse_loop(self) -> None:
        """Read serial data and populate self.latest_data."""

    async def start(self) -> bool:
        """Open serial port and begin parsing."""
        try:
            import serial_asyncio
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self.port, baudrate=self.baud_rate
            )
            self.connected = True
            self._task = asyncio.create_task(self._parse_loop())
            logger.info(f"[Goggles] {self.latest_data.system} started on {self.port}")
            return True
        except ImportError:
            logger.error("pyserial-asyncio not installed")
            return False
        except Exception as e:
            logger.error(f"[Goggles] Failed to connect: {e}")
            return False

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._writer:
            self._writer.close()
        self.connected = False

    def to_video_link_dict(self) -> dict:
        """Return a dict suitable for the VideoLinkData model."""
        d = self.latest_data
        return {
            'quality': d.link_quality,
            'channel': d.channel,
            'frequency_mhz': d.frequency_mhz,
            'recording': d.recording,
            'system': d.system,
        }


class DJIGogglesBridge(GogglesBase):
    """
    DJI FPV system telemetry parser.
    DJI goggles communicate OSD data via MSP protocol over UART.
    Uses MSP_DISPLAYPORT for OSD character rendering and
    standard MSP for flight data passthrough.
    """

    MSP_DISPLAYPORT = 182

    def __init__(self, port: str, baud_rate: int = 115200):
        super().__init__(port, baud_rate)
        self.latest_data.system = "DJI"
        self._decoder = MSPDecoder()

    async def _parse_loop(self) -> None:
        try:
            while True:
                data = await self._reader.read(256)
                if not data:
                    break
                messages = self._decoder.feed(data)
                for msg in messages:
                    self._process_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[DJI] Parse error: {e}")
            self.connected = False

    def _process_message(self, msg) -> None:
        if msg.code == self.MSP_DISPLAYPORT:
            self._parse_displayport(msg.payload)
        elif msg.code == MSPCode.MSP_ANALOG:
            self._parse_analog(msg.payload)
        elif msg.code == MSPCode.MSP_STATUS_EX:
            self.latest_data.connected = True

    def _parse_displayport(self, payload: bytes) -> None:
        """Parse MSP_DISPLAYPORT — DJI OSD character data."""
        if len(payload) < 4:
            return
        sub_cmd = payload[0]
        if sub_cmd == 3:  # Write string
            self.latest_data.connected = True

    def _parse_analog(self, payload: bytes) -> None:
        """Extract RSSI from MSP_ANALOG for link quality estimation."""
        if len(payload) >= 7:
            rssi_raw = struct.unpack('<H', payload[3:5])[0]
            self.latest_data.link_quality = min(100, int(rssi_raw / 10.24))
            self.latest_data.rssi_dbm = -90 + int(self.latest_data.link_quality * 0.6)


class HDZeroBridge(GogglesBase):
    """
    HDZero goggles telemetry parser.
    HDZero provides link statistics via a custom serial protocol
    on the goggles' UART output.
    """

    HEADER = bytes([0xAA, 0x55])

    def __init__(self, port: str, baud_rate: int = 115200):
        super().__init__(port, baud_rate)
        self.latest_data.system = "HDZero"

    async def _parse_loop(self) -> None:
        buffer = bytearray()
        try:
            while True:
                data = await self._reader.read(128)
                if not data:
                    break
                buffer.extend(data)
                while len(buffer) >= 12:
                    idx = buffer.find(self.HEADER)
                    if idx < 0:
                        buffer.clear()
                        break
                    if idx > 0:
                        buffer = buffer[idx:]
                    if len(buffer) < 12:
                        break
                    self._parse_packet(bytes(buffer[:12]))
                    buffer = buffer[12:]
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[HDZero] Parse error: {e}")
            self.connected = False

    def _parse_packet(self, data: bytes) -> None:
        """Parse HDZero link stats packet."""
        if len(data) < 12 or data[:2] != self.HEADER:
            return
        self.latest_data.connected = True
        self.latest_data.channel = data[2]
        self.latest_data.frequency_mhz = struct.unpack('<H', data[3:5])[0]
        self.latest_data.link_quality = data[5]
        self.latest_data.rssi_dbm = struct.unpack('<b', bytes([data[6]]))[0]
        self.latest_data.snr = data[7] / 4.0
        self.latest_data.recording = bool(data[8] & 0x01)
        self.latest_data.bitrate_kbps = struct.unpack('<H', data[9:11])[0]


class WalksnailBridge(GogglesBase):
    """
    Walksnail Avatar goggles telemetry parser.
    Uses MSP passthrough (same protocol as DJI but with Walksnail-specific
    extensions for link quality data).
    """

    MSP_WALKSNAIL_LINK = 0x4010

    def __init__(self, port: str, baud_rate: int = 115200):
        super().__init__(port, baud_rate)
        self.latest_data.system = "Walksnail"
        self._decoder = MSPDecoder()

    async def _parse_loop(self) -> None:
        try:
            while True:
                data = await self._reader.read(256)
                if not data:
                    break
                messages = self._decoder.feed(data)
                for msg in messages:
                    self._process_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[Walksnail] Parse error: {e}")
            self.connected = False

    def _process_message(self, msg) -> None:
        if msg.code == MSPCode.MSP_ANALOG:
            if len(msg.payload) >= 7:
                rssi_raw = struct.unpack('<H', msg.payload[3:5])[0]
                self.latest_data.link_quality = min(100, int(rssi_raw / 10.24))
                self.latest_data.connected = True
        elif msg.code == MSPCode.MSP_STATUS_EX:
            self.latest_data.connected = True


class GogglesBridgeFactory:
    """Auto-detects and creates the appropriate goggles bridge."""

    SYSTEMS = {
        'dji': DJIGogglesBridge,
        'hdzero': HDZeroBridge,
        'walksnail': WalksnailBridge,
    }

    @classmethod
    async def create(cls, port: str, system: str = "auto",
                     baud_rate: int = 115200) -> Optional[GogglesBase]:
        """Create and connect a goggles bridge."""
        if system != "auto" and system in cls.SYSTEMS:
            bridge = cls.SYSTEMS[system](port, baud_rate)
            if await bridge.start():
                return bridge
            return None

        # Auto-detect: try DJI/Walksnail (MSP-based) first, then HDZero
        for name, klass in cls.SYSTEMS.items():
            bridge = klass(port, baud_rate)
            if await bridge.start():
                # Give it a moment to receive data
                await asyncio.sleep(1.0)
                if bridge.latest_data.connected:
                    logger.info(f"[Goggles] Auto-detected: {name}")
                    return bridge
                await bridge.stop()

        logger.warning(f"[Goggles] Could not detect system on {port}")
        return None
