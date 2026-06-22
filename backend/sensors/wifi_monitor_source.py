"""
WiFiMonitorSource -- passive Wi-Fi drone detection sensor for OVERWATCH.

Monitors Wi-Fi traffic to detect drone controller MAC addresses by matching
against known drone manufacturer OUI (Organizationally Unique Identifier)
prefixes. Wraps the SensorSource ABC so fusion can consume detections without
caring about the underlying hardware.

Supports a mock mode for pipeline testing without root or hardware.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import AsyncIterator, Optional

from csontology import Detection, Vec3
from sensors.base import SensorSource

logger = logging.getLogger(__name__)

# Known drone manufacturer OUI prefixes (first 3 octets of MAC)
DRONE_OUIS: dict[str, str] = {
    "62:60:1F": "DJI",
    "60:60:1F": "DJI",
    "00:12:1C": "DJI",
    "A0:14:3D": "Parrot",
    "90:3A:E6": "Parrot",
    "90:03:B7": "Parrot",
    "00:26:7E": "Parrot",
    "38:1D:14": "Skydio",
    "48:D6:D5": "Autel",
}

_MAC_PATTERN = re.compile(
    r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}",
)

_CONFIDENCE_BASE = 0.6
_CONFIDENCE_RSSI_BONUS = 0.15
_CONFIDENCE_SIGHTING_BONUS = 0.05
_CONFIDENCE_CAP = 0.95
_RSSI_STRONG_THRESHOLD = -60

_MOCK_INTERVAL_S = 2.0
_MOCK_RSSI_RANGE = (-85, -30)


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to uppercase colon-separated form."""
    cleaned = mac.strip().upper()
    if len(cleaned) == 12 and ":" not in cleaned:
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    return cleaned


def extract_oui(mac: str) -> str:
    """Extract the OUI (first 3 octets) from a normalized MAC address."""
    parts = mac.split(":")
    if len(parts) < 3:
        return ""
    return ":".join(parts[:3])


def match_oui(mac: str) -> Optional[str]:
    """Return the manufacturer name if the MAC matches a known drone OUI."""
    oui = extract_oui(normalize_mac(mac))
    return DRONE_OUIS.get(oui)


def parse_macs_from_line(line: str) -> list[str]:
    """Extract all MAC addresses from a text line."""
    return [normalize_mac(m) for m in _MAC_PATTERN.findall(line)]


def compute_confidence(
    rssi: int,
    sighting_count: int,
) -> float:
    """Compute detection confidence from RSSI and sighting count.

    OUI match gives a base of 0.6. Strong RSSI (above -60 dBm) adds 0.15.
    Each additional sighting beyond the first adds 0.05. Capped at 0.95.
    """
    conf = _CONFIDENCE_BASE
    if rssi > _RSSI_STRONG_THRESHOLD:
        conf += _CONFIDENCE_RSSI_BONUS
    extra_sightings = max(0, sighting_count - 1)
    conf += extra_sightings * _CONFIDENCE_SIGHTING_BONUS
    return min(conf, _CONFIDENCE_CAP)


def _build_wifi_detection(
    mac: str,
    manufacturer: str,
    rssi: int,
    sensor_id: str,
    confidence: float,
    seq: int,
) -> Detection:
    """Build a Detection for a matched drone MAC."""
    det_id = f"{sensor_id}-wifi-{mac.replace(':', '')}-{seq}"
    return Detection(
        id=det_id,
        timestamp=time.time(),
        position=(0.0, 0.0, 0.0),
        velocity=(0.0, 0.0, 0.0),
        confidence=confidence,
        sensor_id=sensor_id,
        size_rcs=float(rssi),
    )


class WiFiMonitorSource(SensorSource):
    """Passive Wi-Fi drone detection via OUI matching.

    In mock mode, generates synthetic detections without hardware.
    In live mode, reads from an airodump-ng subprocess (requires root).
    """

    def __init__(
        self,
        sensor_id: str = "wifi-monitor-1",
        mock: bool = False,
        interface: Optional[str] = None,
    ) -> None:
        super().__init__(sensor_id)
        self._mock = mock
        self._interface = interface
        self._stop_event = asyncio.Event()
        self._sighting_counts: dict[str, int] = {}
        self._seq = 0

    async def start(self) -> None:
        """Start the Wi-Fi monitor source."""
        self._stop_event.clear()
        self._sighting_counts.clear()
        self._seq = 0
        self._running = True
        mode = "mock" if self._mock else f"live ({self._interface})"
        logger.info("WiFi monitor %s started in %s mode", self.sensor_id, mode)

    async def stop(self) -> None:
        """Stop the Wi-Fi monitor source."""
        self._running = False
        self._stop_event.set()
        logger.info("WiFi monitor %s stopped", self.sensor_id)

    async def stream(self) -> AsyncIterator[Detection]:
        """Yield Detection events for matched drone MACs."""
        if not self._running:
            raise RuntimeError("stream() called before start()")
        if self._mock:
            async for det in self._mock_stream():
                yield det
        else:
            async for det in self._live_stream():
                yield det

    async def _mock_stream(self) -> AsyncIterator[Detection]:
        """Generate fake drone detections for testing."""
        mock_macs = self._generate_mock_macs()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=_MOCK_INTERVAL_S,
                )
                return
            except asyncio.TimeoutError:
                pass
            count = random.randint(1, 3)
            chosen = random.sample(mock_macs, min(count, len(mock_macs)))
            for mac in chosen:
                rssi = random.randint(*_MOCK_RSSI_RANGE)
                det = self._process_mac(mac, rssi)
                if det is not None:
                    yield det

    async def _live_stream(self) -> AsyncIterator[Detection]:
        """Read from airodump-ng and yield matched detections."""
        if self._interface is None:
            raise RuntimeError("interface required for live mode")
        proc = await asyncio.create_subprocess_exec(
            "airodump-ng", "--update", "1", "--berlin", "20",
            self._interface,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                if self._stop_event.is_set():
                    break
                line = line_bytes.decode("utf-8", errors="replace")
                for mac in parse_macs_from_line(line):
                    det = self._process_mac(mac, _RSSI_STRONG_THRESHOLD)
                    if det is not None:
                        yield det
        finally:
            proc.terminate()
            await proc.wait()

    def _process_mac(self, mac: str, rssi: int) -> Optional[Detection]:
        """Check a MAC against the OUI database and build a detection."""
        manufacturer = match_oui(mac)
        if manufacturer is None:
            return None
        self._seq += 1
        self._sighting_counts[mac] = self._sighting_counts.get(mac, 0) + 1
        conf = compute_confidence(rssi, self._sighting_counts[mac])
        logger.info(
            "Drone detected: %s (%s) RSSI=%d conf=%.2f sightings=%d",
            mac, manufacturer, rssi, conf, self._sighting_counts[mac],
        )
        return _build_wifi_detection(
            mac, manufacturer, rssi, self.sensor_id, conf, self._seq,
        )

    def _generate_mock_macs(self) -> list[str]:
        """Build a pool of fake drone MACs using known OUI prefixes."""
        oui_list = list(DRONE_OUIS.keys())
        macs: list[str] = []
        for oui in oui_list[:5]:
            suffix = ":".join(f"{random.randint(0, 255):02X}" for _ in range(3))
            macs.append(f"{oui}:{suffix}")
        return macs
