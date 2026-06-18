"""
Async UDP sender for CoT XML.

Uses asyncio DatagramProtocol so the send path never blocks the event loop.
Default endpoint is the TAK standard multicast group 239.2.3.1:6969.

Usage
-----
    sender = await CoTSender.create()
    await sender.send(xml_string)
    sender.close()

Or as an async context manager:

    async with CoTSender.create() as sender:
        await sender.send(xml_string)
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "239.2.3.1"
DEFAULT_PORT = 6969
# Multicast TTL: 1 = link-local, 32 = site-local, 255 = global
_MULTICAST_TTL = 32


class _CoTDatagramProtocol(asyncio.DatagramProtocol):
    """Minimal DatagramProtocol that logs errors and ignores them."""

    def error_received(self, exc: Exception) -> None:
        logger.error("CoT UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.warning("CoT UDP connection lost: %s", exc)


class CoTSender:
    """Async UDP sender that writes CoT XML bytes to the configured endpoint.

    Always use CoTSender.create() — the constructor is not async-safe on its
    own because asyncio.get_event_loop().create_datagram_endpoint() must be
    awaited.
    """

    def __init__(
        self,
        transport: asyncio.DatagramTransport,
        host: str,
        port: int,
    ) -> None:
        self._transport = transport
        self._host = host
        self._port = port

    @classmethod
    async def create(
        cls,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> "CoTSender":
        """Create and connect the UDP sender. Must be awaited."""
        loop = asyncio.get_running_loop()

        # Create a raw UDP socket so we can set multicast options before
        # handing it to asyncio.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, _MULTICAST_TTL)
        # Allow multiple processes to bind the same port (for local TAK testing).
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)

        transport, _ = await loop.create_datagram_endpoint(
            _CoTDatagramProtocol,
            sock=sock,
        )

        instance = cls(transport, host, port)  # type: ignore[arg-type]
        logger.info("CoTSender ready — endpoint %s:%d", host, port)
        return instance

    async def send(self, xml: str) -> None:
        """Send a CoT XML string as UTF-8 bytes to the configured endpoint.

        Fire-and-forget. Errors are logged, not raised, so a bad packet never
        kills the bridge loop.
        """
        try:
            data = xml.encode("utf-8")
            self._transport.sendto(data, (self._host, self._port))
        except Exception as exc:
            logger.error("CoT send failed: %s", exc)

    def close(self) -> None:
        """Close the underlying UDP transport."""
        self._transport.close()
        logger.info("CoTSender closed")

    async def __aenter__(self) -> "CoTSender":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()
