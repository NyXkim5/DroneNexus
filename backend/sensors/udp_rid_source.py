"""
UDPRIDSensorSource -- async UDP listener for pre-decoded Remote ID JSON.

Listens on a UDP port for JSON lines containing decoded ASTM F3411 Remote
ID data. Each datagram is a single JSON object with position, speed, heading,
and identity fields. Valid messages are converted to Detection objects and
yielded through the SensorSource interface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import AsyncIterator, Optional

from csontology import Detection, Vec3, latlon_to_enu
from sensors.base import SensorSource

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 9999
_DEFAULT_CONFIDENCE = 0.75


def _valid_latlon(lat: Optional[float], lon: Optional[float]) -> bool:
    """Return True if lat/lon are valid non-zero coordinates."""
    if lat is None or lon is None:
        return False
    if lat == 0.0 or lon == 0.0:
        return False
    if abs(lat) > 90.0 or abs(lon) > 180.0:
        return False
    return True


def _heading_to_velocity(speed: float, heading: float) -> Vec3:
    """Convert speed and compass heading to ENU velocity."""
    rad = math.radians(heading)
    vx = speed * math.sin(rad)
    vy = speed * math.cos(rad)
    return (vx, vy, 0.0)


def _parse_rid_json(raw: bytes) -> Optional[dict]:
    """Parse a single UDP datagram as RID JSON, returning None on error."""
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except (UnicodeDecodeError, AttributeError):
        return None
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.debug("UDP RID JSON parse error: %.80s", text)
        return None


class _UDPProtocol(asyncio.DatagramProtocol):
    """Internal protocol handler that pushes datagrams into an asyncio.Queue."""

    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self._queue = queue

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("UDP RID queue full, dropping datagram")

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP RID protocol error: %s", exc)


class UDPRIDSensorSource(SensorSource):
    """SensorSource that listens for pre-decoded RID JSON on UDP."""

    def __init__(
        self,
        port: int = _DEFAULT_PORT,
        sensor_id: str = "udp-rid",
    ) -> None:
        super().__init__(sensor_id)
        self._port = port
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1024)
        self._stop_event = asyncio.Event()
        self._seq = 0

    async def start(self) -> None:
        """Bind the UDP socket and begin receiving datagrams."""
        self._stop_event.clear()
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self._queue),
            local_addr=("0.0.0.0", self._port),
        )
        self._transport = transport
        self._running = True
        logger.info("UDP RID source %s listening on port %d", self.sensor_id, self._port)

    async def stop(self) -> None:
        """Close the UDP socket and stop streaming."""
        self._running = False
        self._stop_event.set()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        logger.info("UDP RID source %s stopped", self.sensor_id)

    async def stream(self) -> AsyncIterator[Detection]:
        """Receive UDP datagrams and yield Detection objects."""
        if not self._running:
            raise RuntimeError("stream() called before start()")
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            detection = self._datagram_to_detection(raw)
            if detection is not None:
                yield detection

    def _datagram_to_detection(self, raw: bytes) -> Optional[Detection]:
        """Convert a raw UDP datagram to a Detection, or None."""
        msg = _parse_rid_json(raw)
        if msg is None:
            return None

        lat = msg.get("lat")
        lon = msg.get("lon")
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            return None

        if not _valid_latlon(lat, lon):
            return None

        return self._build_detection(msg, lat, lon)

    def _build_detection(
        self, msg: dict, lat: float, lon: float,
    ) -> Detection:
        """Construct a Detection from a validated RID JSON message."""
        self._seq += 1
        alt = float(msg.get("alt") or 0.0)
        speed = float(msg.get("speed") or 0.0)
        heading = float(msg.get("hdg") or 0.0)
        position = latlon_to_enu(lat, lon, alt)
        velocity = _heading_to_velocity(speed, heading)
        rid_id = msg.get("id") or msg.get("mac") or "unknown"
        ts = float(msg.get("t") or time.time())

        return Detection(
            id=f"{self.sensor_id}-{rid_id}-{self._seq}",
            timestamp=ts,
            position=position,
            velocity=velocity,
            confidence=_DEFAULT_CONFIDENCE,
            sensor_id=self.sensor_id,
            size_rcs=None,
        )
