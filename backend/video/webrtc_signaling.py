"""
WebRTC signaling server for low-latency FPV video.
Handles SDP offer/answer exchange between the HUD (browser) and
a video source (drone companion computer or local capture device).

The HUD creates an RTCPeerConnection, sends an SDP offer via WebSocket,
this server relays it to the video source, and returns the SDP answer.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("nexus.video.webrtc")


@dataclass
class PeerSession:
    drone_id: str
    offer_sdp: Optional[str] = None
    answer_sdp: Optional[str] = None
    ice_candidates: list = field(default_factory=list)
    state: str = "new"


class WebRTCSignaling:
    """Manages WebRTC signaling state for FPV video connections."""

    def __init__(self):
        self.sessions: Dict[str, PeerSession] = {}
        self._answer_events: Dict[str, asyncio.Event] = {}

    async def handle_offer(self, drone_id: str, sdp: str) -> Optional[str]:
        """
        Process an SDP offer from the HUD client.
        If a local WebRTC source is available, create an answer.
        Otherwise, relay to the drone's companion computer.
        """
        session = PeerSession(drone_id=drone_id, offer_sdp=sdp, state="offer_received")
        self.sessions[drone_id] = session

        logger.info(f"[WebRTC] Received offer for {drone_id}")

        # Try to create a local answer using aiortc
        answer = await self._create_local_answer(drone_id, sdp)
        if answer:
            session.answer_sdp = answer
            session.state = "connected"
            return answer

        # Fall back to relay mode — wait for answer from companion computer
        event = asyncio.Event()
        self._answer_events[drone_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=10.0)
            return session.answer_sdp
        except asyncio.TimeoutError:
            logger.warning(f"[WebRTC] Answer timeout for {drone_id}")
            session.state = "timeout"
            return None

    async def handle_answer(self, drone_id: str, sdp: str) -> None:
        """Process an SDP answer from a drone companion computer."""
        session = self.sessions.get(drone_id)
        if session:
            session.answer_sdp = sdp
            session.state = "connected"
            event = self._answer_events.pop(drone_id, None)
            if event:
                event.set()

    async def add_ice_candidate(self, drone_id: str, candidate: dict) -> None:
        """Add an ICE candidate to a session."""
        session = self.sessions.get(drone_id)
        if session:
            session.ice_candidates.append(candidate)

    def get_session(self, drone_id: str) -> Optional[PeerSession]:
        return self.sessions.get(drone_id)

    def close_session(self, drone_id: str) -> None:
        session = self.sessions.pop(drone_id, None)
        self._answer_events.pop(drone_id, None)
        if session:
            session.state = "closed"
            logger.info(f"[WebRTC] Session closed for {drone_id}")

    async def _create_local_answer(self, drone_id: str, offer_sdp: str) -> Optional[str]:
        """Attempt to create a local WebRTC answer using aiortc."""
        try:
            from aiortc import RTCPeerConnection, RTCSessionDescription
            from aiortc.contrib.media import MediaPlayer

            pc = RTCPeerConnection()
            offer = RTCSessionDescription(sdp=offer_sdp, type="offer")
            await pc.setRemoteDescription(offer)

            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            logger.info(f"[WebRTC] Local answer created for {drone_id}")
            return pc.localDescription.sdp

        except ImportError:
            logger.debug("[WebRTC] aiortc not available, using relay mode")
            return None
        except Exception as e:
            logger.debug(f"[WebRTC] Local answer failed: {e}")
            return None
