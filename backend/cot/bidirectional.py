"""
Bi-directional CoT bridge for OVERWATCH/BULWARK.

Combines the outbound CoTBridge (tracks/threats -> TAK) with the inbound
CoTReceiver (TAK -> OVERWATCH) into a single lifecycle object. Incoming
ATAK events are converted into OVERWATCH world-model objects so the fusion
pipeline and IFF system can act on operator intelligence without any manual
translation step.

Usage
-----
    from cot.bridge import CoTBridge
    from cot.receiver import CoTReceiver
    from cot.bidirectional import BiDirectionalCoTBridge

    sender = CoTBridge()
    receiver = CoTReceiver()
    bridge = BiDirectionalCoTBridge(sender, receiver)
    await bridge.start()
    # ... run loop ...
    await bridge.stop()
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Dict, Optional

from csontology import Track, TrackClass, Vec3, latlon_to_enu
from cot.receiver import CoTReceiver, IncomingCoTEvent

if TYPE_CHECKING:
    from cot.bridge import CoTBridge

logger = logging.getLogger(__name__)

# Default covariance assigned to TAK-reported positions. ATAK GPS accuracy is
# roughly 3-5 m CEP; we use a conservative 10 m sigma on x/y, 15 m on z.
_TAK_POSITION_SIGMA: Vec3 = (10.0, 10.0, 15.0)


class BiDirectionalCoTBridge:
    """Full TAK integration — sends track/threat CoT events and receives them.

    Incoming hostile reports become Tracks in the OVERWATCH fusion pipeline.
    Incoming friendly positions update a registry used for IFF deconfliction.
    Incoming map markers become waypoint/objective dicts consumable by mission
    planning.
    """

    def __init__(self, sender: "CoTBridge", receiver: CoTReceiver) -> None:
        self._sender = sender
        self._receiver = receiver
        self._friendly_registry: Dict[str, dict] = {}

    async def start(self) -> None:
        """Start both the outbound sender and the inbound receiver."""
        await self._sender.start()
        await self._receiver.start()
        logger.info("BiDirectionalCoTBridge started")

    async def stop(self) -> None:
        """Stop both the outbound sender and the inbound receiver."""
        await self._sender.stop()
        await self._receiver.stop()
        logger.info("BiDirectionalCoTBridge stopped")

    def inject_hostile_report(self, event: IncomingCoTEvent) -> Optional[Track]:
        """Convert an incoming ATAK hostile report into an OVERWATCH Track.

        Converts the incoming geodetic position to the local ENU frame using
        latlon_to_enu() from csontology. The resulting Track is suitable for
        insertion into the fusion pipeline's track manager.

        Returns None if the event is not a hostile type.
        """
        if not event.event_type.startswith("a-h-"):
            logger.debug(
                "inject_hostile_report: ignoring non-hostile type %s", event.event_type
            )
            return None

        enu_pos: Vec3 = latlon_to_enu(event.lat, event.lon, event.altitude)
        track_id = f"TAK.{event.uid}"

        track = Track(
            id=track_id,
            position=enu_pos,
            velocity=(0.0, 0.0, 0.0),
            covariance=_TAK_POSITION_SIGMA,
            last_update=event.timestamp,
            age=0.0,
            classification=TrackClass.HOSTILE,
            confidence=0.75,
        )

        logger.info(
            "inject_hostile_report: created Track %s from ATAK callsign=%s pos=(%s)",
            track_id, event.callsign, enu_pos,
        )
        return track

    def inject_friendly_position(self, event: IncomingCoTEvent) -> None:
        """Store an incoming friendly unit position for IFF/deconfliction use.

        Converts the geodetic position to ENU and writes it into the internal
        friendly registry keyed by UID. Any component holding a reference to
        this bridge can read friendly_registry to get current positions.
        """
        if not event.event_type.startswith("a-f-"):
            logger.debug(
                "inject_friendly_position: ignoring non-friendly type %s", event.event_type
            )
            return

        enu_pos: Vec3 = latlon_to_enu(event.lat, event.lon, event.altitude)
        self._friendly_registry[event.uid] = {
            "uid": event.uid,
            "callsign": event.callsign,
            "position": enu_pos,
            "lat": event.lat,
            "lon": event.lon,
            "altitude": event.altitude,
            "last_update": event.timestamp,
        }
        logger.debug(
            "inject_friendly_position: updated %s callsign=%s enu=%s",
            event.uid, event.callsign, enu_pos,
        )

    def inject_marker(self, event: IncomingCoTEvent) -> dict:
        """Convert an ATAK map marker into an OVERWATCH waypoint/objective dict.

        The returned dict is intentionally schema-loose so mission planning
        modules can consume it without depending on a specific dataclass. Keys:
          id, uid, callsign, lat, lon, altitude, enu_position,
          remarks, timestamp, cot_type
        """
        enu_pos: Vec3 = latlon_to_enu(event.lat, event.lon, event.altitude)
        waypoint = {
            "id": str(uuid.uuid4()),
            "uid": event.uid,
            "callsign": event.callsign,
            "lat": event.lat,
            "lon": event.lon,
            "altitude": event.altitude,
            "enu_position": enu_pos,
            "remarks": event.remarks,
            "timestamp": event.timestamp,
            "cot_type": event.event_type,
        }
        logger.info(
            "inject_marker: waypoint from %s at enu=%s remarks=%r",
            event.callsign or event.uid, enu_pos, event.remarks,
        )
        return waypoint

    @property
    def friendly_registry(self) -> Dict[str, dict]:
        """Read-only view of known friendly positions keyed by CoT UID."""
        return dict(self._friendly_registry)
