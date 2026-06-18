"""
CoT XML receiver for OVERWATCH/BULWARK — the inbound TAK channel.

Listens on a UDP multicast socket (default 239.2.3.1:6969) and parses every
incoming CoT XML datagram into an IncomingCoTEvent. Registered callbacks are
invoked synchronously in the receive loop so callers can feed events into
pipelines without additional wiring.

Usage
-----
    receiver = CoTReceiver()
    receiver.on_event(lambda e: print(e.callsign, e.event_type))
    await receiver.start()
    # ... later ...
    await receiver.stop()
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 6969
DEFAULT_MULTICAST_GROUP = "239.2.3.1"

# Events older than this are excluded from recent_events.
_RECENT_WINDOW_S = 60.0


def _parse_cot_timestamp(ts_str: str) -> float:
    """Parse a CoT ISO-8601 UTC timestamp string into a POSIX float."""
    ts_str = ts_str.rstrip("Z")
    try:
        dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return time.time()


@dataclass
class IncomingCoTEvent:
    """One parsed CoT event received from the TAK network."""
    uid: str
    event_type: str
    lat: float
    lon: float
    altitude: float
    callsign: str
    remarks: str
    timestamp: float
    stale_time: float
    raw_xml: str


class _CoTReceiverProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol that hands raw datagrams to CoTReceiver."""

    def __init__(self, receiver: "CoTReceiver") -> None:
        self._receiver = receiver

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        event = self._receiver.parse_cot_xml(data)
        if event is None:
            return
        self._receiver._events.append(event)
        for handler in self._receiver._handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.error("CoT event handler raised: %s", exc)

    def error_received(self, exc: Exception) -> None:
        logger.error("CoT receiver UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.warning("CoT receiver connection lost: %s", exc)


class CoTReceiver:
    """Receives and parses CoT XML events from the TAK network.

    Joins the specified multicast group and processes every incoming UDP
    datagram. Parsed events are stored internally and passed to any registered
    handlers.
    """

    def __init__(
        self,
        listen_host: str = DEFAULT_LISTEN_HOST,
        listen_port: int = DEFAULT_LISTEN_PORT,
        multicast_group: str = DEFAULT_MULTICAST_GROUP,
    ) -> None:
        self._host = listen_host
        self._port = listen_port
        self._multicast = multicast_group
        self._handlers: List[Callable[[IncomingCoTEvent], None]] = []
        self._events: List[IncomingCoTEvent] = []
        self._transport: Optional[asyncio.DatagramTransport] = None

    def on_event(self, handler: Callable[[IncomingCoTEvent], None]) -> None:
        """Register a callback invoked for every successfully parsed event."""
        self._handlers.append(handler)

    async def start(self) -> None:
        """Join the multicast group and begin receiving CoT datagrams."""
        loop = asyncio.get_running_loop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT lets multiple processes share the port on platforms that support it.
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind((self._host, self._port))

        # Join the multicast group on all interfaces.
        group = socket.inet_aton(self._multicast)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        transport, _ = await loop.create_datagram_endpoint(
            lambda: _CoTReceiverProtocol(self),
            sock=sock,
        )
        self._transport = transport  # type: ignore[assignment]
        logger.info(
            "CoTReceiver listening on %s:%d multicast=%s",
            self._host, self._port, self._multicast,
        )

    async def stop(self) -> None:
        """Close the UDP socket and stop receiving."""
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.info("CoTReceiver stopped")

    def parse_cot_xml(self, xml_bytes: bytes) -> Optional[IncomingCoTEvent]:
        """Parse a raw CoT XML datagram into an IncomingCoTEvent.

        Returns None when the input is not valid CoT XML or is missing required
        fields. Never raises.
        """
        try:
            root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            logger.debug("Malformed CoT XML: %s", exc)
            return None

        if root.tag != "event":
            return None

        uid = root.get("uid", "")
        event_type = root.get("type", "")
        time_str = root.get("time", "")
        stale_str = root.get("stale", "")

        if not uid or not event_type:
            return None

        point = root.find("point")
        if point is None:
            return None

        try:
            lat = float(point.get("lat", "0"))
            lon = float(point.get("lon", "0"))
            altitude = float(point.get("hae", "0"))
        except ValueError:
            return None

        detail = root.find("detail")
        callsign = ""
        remarks = ""
        if detail is not None:
            contact = detail.find("contact")
            if contact is not None:
                callsign = contact.get("callsign", "")
            remarks_el = detail.find("remarks")
            if remarks_el is not None:
                remarks = remarks_el.text or ""

        timestamp = _parse_cot_timestamp(time_str) if time_str else time.time()
        stale_time = _parse_cot_timestamp(stale_str) if stale_str else timestamp + 60.0

        return IncomingCoTEvent(
            uid=uid,
            event_type=event_type,
            lat=lat,
            lon=lon,
            altitude=altitude,
            callsign=callsign,
            remarks=remarks,
            timestamp=timestamp,
            stale_time=stale_time,
            raw_xml=xml_bytes.decode("utf-8", errors="replace"),
        )

    def classify_event(self, event: IncomingCoTEvent) -> str:
        """Classify an incoming CoT event by its type code prefix.

        CoT type codes use a hierarchical dot-separated scheme encoded with
        hyphens. The first two segments determine the broad category:
          a-h-*  hostile atom (unit/track)
          a-f-*  friendly atom
          a-u-*  unknown atom
          b-m-p-* map marker / waypoint
          b-r-f-h-c  medevac 9-line request
        """
        t = event.event_type
        if t.startswith("a-h-"):
            return "hostile_report"
        if t.startswith("a-f-"):
            return "friendly_position"
        if t.startswith("b-m-p-"):
            return "marker"
        if t == "b-r-f-h-c":
            return "medevac_request"
        if t.startswith("a-u-"):
            return "unknown_contact"
        return "other"

    @property
    def recent_events(self) -> List[IncomingCoTEvent]:
        """All events received within the last 60 seconds."""
        cutoff = time.time() - _RECENT_WINDOW_S
        return [e for e in self._events if e.timestamp >= cutoff]
