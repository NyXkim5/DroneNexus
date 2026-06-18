"""
AntSDRSensorSource -- async TCP adapter for AntSDR DJI drone detection.

Connects to an AntSDR receiver over TCP, reads binary DJI DroneID frames,
decodes them with dji_decoder, and yields Detection objects through the
SensorSource interface. Emits up to 3 detections per frame: UAS position,
operator position, and home position.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from csontology import Detection, Vec3, latlon_to_enu
from sensors.base import SensorSource
from sensors.dji_decoder import parse_dji_binary_frame, parse_dji_frame

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 41030
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 2.0
_READ_CHUNK = 4096
_DEFAULT_CONFIDENCE = 0.7


def _valid_latlon(lat: Optional[float], lon: Optional[float]) -> bool:
    """Return True if lat/lon are valid non-zero coordinates."""
    if lat is None or lon is None:
        return False
    if lat == 0.0 or lon == 0.0:
        return False
    if abs(lat) > 90.0 or abs(lon) > 180.0:
        return False
    return True


def _speed_to_velocity(
    speed_e: Optional[float],
    speed_n: Optional[float],
    speed_u: Optional[float],
) -> Vec3:
    """Convert DJI speed components to an ENU velocity vector."""
    return (
        float(speed_e or 0.0),
        float(speed_n or 0.0),
        float(speed_u or 0.0),
    )


def _build_detection(
    det_id: str,
    lat: float,
    lon: float,
    alt: float,
    velocity: Vec3,
    sensor_id: str,
    confidence: float,
    rssi: Optional[int],
) -> Detection:
    """Build a Detection from geodetic coordinates."""
    position = latlon_to_enu(lat, lon, alt)
    rcs = float(rssi) if rssi is not None else None
    return Detection(
        id=det_id,
        timestamp=time.time(),
        position=position,
        velocity=velocity,
        confidence=confidence,
        sensor_id=sensor_id,
        size_rcs=rcs,
    )


def _extract_frames(buffer: bytes) -> tuple[list[dict], bytes]:
    """Extract complete DJI frames from buffer, return (parsed_list, remainder)."""
    parsed_list: list[dict] = []
    while len(buffer) >= 5:
        pkg_len = int.from_bytes(buffer[3:5], "little")
        if pkg_len < 5 or len(buffer) < pkg_len:
            break
        frame = buffer[:pkg_len]
        buffer = buffer[pkg_len:]
        result = parse_dji_frame(frame)
        if result is None:
            continue
        _, data = result
        parsed = parse_dji_binary_frame(data)
        if parsed is not None:
            parsed_list.append(parsed)
    return parsed_list, buffer


class AntSDRSensorSource(SensorSource):
    """SensorSource that reads DJI DroneID frames from an AntSDR over TCP."""

    def __init__(
        self,
        host: str,
        port: int = _DEFAULT_PORT,
        sensor_id: str = "antsdr-1",
    ) -> None:
        super().__init__(sensor_id)
        self._host = host
        self._port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._stop_event = asyncio.Event()
        self._seq = 0

    async def start(self) -> None:
        """Open TCP connection to the AntSDR receiver."""
        self._stop_event.clear()
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port,
        )
        self._running = True
        logger.info(
            "AntSDR connected to %s:%d as %s",
            self._host, self._port, self.sensor_id,
        )

    async def stop(self) -> None:
        """Close TCP connection and stop streaming."""
        self._running = False
        self._stop_event.set()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None
        logger.info("AntSDR source %s stopped", self.sensor_id)

    async def stream(self) -> AsyncIterator[Detection]:
        """Read TCP chunks and yield Detection objects."""
        if not self._running:
            raise RuntimeError("stream() called before start()")
        retries = 0
        while not self._stop_event.is_set():
            try:
                async for detection in self._read_loop():
                    yield detection
                return
            except (ConnectionError, OSError) as exc:
                retries += 1
                if retries > _MAX_RETRIES:
                    logger.error("AntSDR max retries exceeded: %s", exc)
                    raise
                logger.warning(
                    "AntSDR connection error (retry %d/%d): %s",
                    retries, _MAX_RETRIES, exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_S * retries)
                await self._reconnect()

    async def _read_loop(self) -> AsyncIterator[Detection]:
        """Inner loop that reads and decodes frames."""
        assert self._reader is not None
        buffer = b""
        while not self._stop_event.is_set():
            chunk = await self._reader.read(_READ_CHUNK)
            if not chunk:
                logger.info("AntSDR TCP stream ended")
                return
            buffer += chunk
            parsed_list, buffer = _extract_frames(buffer)
            for parsed in parsed_list:
                for det in self._detections_from_parsed(parsed):
                    yield det

    def _detections_from_parsed(self, parsed: dict) -> list[Detection]:
        """Build up to 3 detections from a parsed DJI frame."""
        self._seq += 1
        sn = parsed.get("serial_number") or "unknown"
        velocity = _speed_to_velocity(
            parsed.get("speed_e"),
            parsed.get("speed_n"),
            parsed.get("speed_u"),
        )
        rssi = parsed.get("rssi")
        alt = float(parsed.get("altitude") or 0.0)
        out: list[Detection] = []

        if _valid_latlon(parsed.get("uas_lat"), parsed.get("uas_lon")):
            out.append(_build_detection(
                f"{self.sensor_id}-{sn}-uas-{self._seq}",
                parsed["uas_lat"], parsed["uas_lon"], alt,
                velocity, self.sensor_id, _DEFAULT_CONFIDENCE, rssi,
            ))

        if _valid_latlon(parsed.get("op_lat"), parsed.get("op_lon")):
            out.append(_build_detection(
                f"{self.sensor_id}-{sn}-op-{self._seq}",
                parsed["op_lat"], parsed["op_lon"], 0.0,
                (0.0, 0.0, 0.0), self.sensor_id, 0.5, rssi,
            ))

        if _valid_latlon(parsed.get("home_lat"), parsed.get("home_lon")):
            out.append(_build_detection(
                f"{self.sensor_id}-{sn}-home-{self._seq}",
                parsed["home_lat"], parsed["home_lon"], 0.0,
                (0.0, 0.0, 0.0), self.sensor_id, 0.4, rssi,
            ))

        return out

    async def _reconnect(self) -> None:
        """Attempt to re-establish the TCP connection."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port,
        )
        logger.info("AntSDR reconnected to %s:%d", self._host, self._port)
