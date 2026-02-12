"""
USB serial device scanner.
Detects connected flight controllers by VID/PID and serial port probing.
Auto-detects whether a device speaks MAVLink or MSP.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Set

logger = logging.getLogger("nexus.usb")

KNOWN_FC_DEVICES = [
    {'vid': 0x10c4, 'pid': 0xea60, 'label': 'CP2102 Flight Controller'},
    {'vid': 0x0483, 'pid': 0x5740, 'label': 'STM32 Betaflight FC'},
    {'vid': 0x1a86, 'pid': 0x7523, 'label': 'CH340 Serial Adapter'},
    {'vid': 0x2341, 'pid': 0x0043, 'label': 'Arduino (potential FC)'},
    {'vid': 0x0403, 'pid': 0x6001, 'label': 'FTDI Serial Adapter'},
    {'vid': 0x26ac, 'pid': 0x0011, 'label': 'DJI FC USB'},
]

KNOWN_VIDS = {d['vid'] for d in KNOWN_FC_DEVICES}


@dataclass
class DetectedDevice:
    port: str
    vid: int
    pid: int
    description: str
    serial_number: str
    protocol: str       # 'MAVLINK' | 'MSP' | 'UNKNOWN'
    baud_rate: int
    label: str


class USBScanner:
    """Scans for USB serial devices and identifies flight controllers."""

    def __init__(self, extra_vids: Optional[Set[int]] = None):
        self._known_vids = KNOWN_VIDS | (extra_vids or set())

    async def scan(self) -> List[DetectedDevice]:
        """List all serial ports and filter for potential flight controllers."""
        try:
            import serial.tools.list_ports
        except ImportError:
            logger.error("pyserial not installed — run: pip install pyserial")
            return []

        ports = serial.tools.list_ports.comports()
        devices = []

        for port in ports:
            if not self._is_potential_fc(port):
                continue

            label = self._get_label(port)
            protocol, baud = await self._detect_protocol(port.device)

            devices.append(DetectedDevice(
                port=port.device,
                vid=port.vid or 0,
                pid=port.pid or 0,
                description=port.description or '',
                serial_number=port.serial_number or '',
                protocol=protocol,
                baud_rate=baud,
                label=label,
            ))

        logger.info(f"[USB] Scan found {len(devices)} device(s)")
        return devices

    def _is_potential_fc(self, port) -> bool:
        """Check if a serial port could be a flight controller."""
        if port.vid and port.vid in self._known_vids:
            return True
        desc = (port.description or '').lower()
        return any(kw in desc for kw in ['serial', 'uart', 'cp210', 'ch340', 'ftdi', 'stm32'])

    def _get_label(self, port) -> str:
        """Get a human-readable label for a device."""
        if port.vid:
            for dev in KNOWN_FC_DEVICES:
                if dev['vid'] == port.vid and dev['pid'] == port.pid:
                    return dev['label']
        return port.description or 'Unknown Device'

    async def _detect_protocol(self, port: str) -> tuple:
        """Try MSP then MAVLink to identify what protocol a device speaks."""
        # Try MSP first (faster handshake)
        msp_result = await self._try_msp(port)
        if msp_result:
            return msp_result

        # Try MAVLink heartbeat
        mavlink_result = await self._try_mavlink(port)
        if mavlink_result:
            return mavlink_result

        return ('UNKNOWN', 115200)

    async def _try_msp(self, port: str) -> Optional[tuple]:
        """Attempt MSP handshake at common baud rates."""
        from msp.protocol import MSPEncoder, MSPDecoder, MSPCode

        for baud in [115200, 57600, 921600]:
            try:
                import serial
                ser = serial.Serial(port, baud, timeout=0.5)
                frame = MSPEncoder.encode(MSPCode.MSP_API_VERSION)
                ser.write(frame)
                await asyncio.sleep(0.3)
                data = ser.read(ser.in_waiting or 64)
                ser.close()
                if data:
                    decoder = MSPDecoder()
                    messages = decoder.feed(data)
                    if messages and messages[0].code == MSPCode.MSP_API_VERSION:
                        logger.info(f"[USB] MSP detected on {port} at {baud}")
                        return ('MSP', baud)
            except Exception:
                continue
        return None

    async def _try_mavlink(self, port: str) -> Optional[tuple]:
        """Attempt to detect MAVLink heartbeat at common baud rates."""
        MAVLINK_HEADER = 0xFD  # MAVLink v2 start byte

        for baud in [921600, 57600, 115200]:
            try:
                import serial
                ser = serial.Serial(port, baud, timeout=1.0)
                await asyncio.sleep(0.5)
                data = ser.read(ser.in_waiting or 256)
                ser.close()
                if MAVLINK_HEADER in data:
                    logger.info(f"[USB] MAVLink detected on {port} at {baud}")
                    return ('MAVLINK', baud)
            except Exception:
                continue
        return None
