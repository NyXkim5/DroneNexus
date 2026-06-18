"""
CoTBridge — subscribes to OVERWATCH track/threat state and emits CoT XML.

The bridge runs as an asyncio task. On each tick it receives the current
snapshot of tracks and threats, formats them into CoT XML via formatter.py,
and sends each event via CoTSender. A heartbeat event for the OVERWATCH sensor
itself is emitted every 30 seconds.

Usage
-----
    bridge = CoTBridge(host="239.2.3.1", port=6969)
    await bridge.start()

    # On each update cycle:
    await bridge.publish(tracks=track_list, threats=threat_list)

    # On shutdown:
    await bridge.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from csontology import (
    Defender,
    Engagement,
    ORIGIN_LAT,
    ORIGIN_LON,
    Threat,
    Track,
)
from cot.formatter import (
    format_defender_cot,
    format_engagement_cot,
    format_heartbeat_cot,
    format_swarm_cluster_cot,
    format_threat_cot,
    format_track_cot,
)
from cot.sender import CoTSender, DEFAULT_HOST, DEFAULT_PORT

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_S = 30.0


class CoTBridge:
    """Converts OVERWATCH track/threat snapshots into CoT XML and sends them.

    Each call to publish() formats and sends one CoT event per track. Threats
    override the type code and remarks on their backing track's event so TAK
    operators see threat scoring without duplicate markers.

    The bridge manages its own CoTSender lifecycle. Call start() before the
    first publish() and stop() on shutdown.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        site_lat: float = ORIGIN_LAT,
        site_lon: float = ORIGIN_LON,
    ) -> None:
        self._host = host
        self._port = port
        self._site_lat = site_lat
        self._site_lon = site_lon
        self._sender: Optional[CoTSender] = None
        self._last_heartbeat: float = 0.0
        self._heartbeat_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Open the UDP socket and schedule the heartbeat loop."""
        self._sender = await CoTSender.create(host=self._host, port=self._port)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "CoTBridge started — %s:%d site=(%.4f, %.4f)",
            self._host, self._port, self._site_lat, self._site_lon,
        )

    async def stop(self) -> None:
        """Cancel the heartbeat loop and close the UDP sender."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._sender:
            self._sender.close()
        logger.info("CoTBridge stopped")

    async def publish(
        self,
        tracks: List[Track],
        threats: List[Threat],
    ) -> None:
        """Format and send CoT events for all current tracks and threats.

        Threats are matched to their backing track by track_id. When a match
        is found the threat formatter is used (hostile type, score in remarks).
        Tracks with no associated threat use the standard track formatter.
        """
        if not self._sender:
            logger.warning("CoTBridge.publish called before start() — skipping")
            return

        # Build a lookup from track_id to Threat for O(1) matching.
        threat_by_track: Dict[str, Threat] = {
            t.track_id: t for t in threats if t.track_id is not None
        }

        xml_events: List[str] = []
        for track in tracks:
            threat = threat_by_track.get(track.id)
            if threat is not None:
                xml_events.append(format_threat_cot(threat, track))
            else:
                xml_events.append(format_track_cot(track))

        for xml in xml_events:
            await self._sender.send(xml)

        logger.debug(
            "CoTBridge published %d events (%d threats)",
            len(xml_events), len(threat_by_track),
        )

    def format_batch(
        self,
        tracks: List[Track],
        threats: List[Threat],
    ) -> List[str]:
        """Return CoT XML strings for all tracks without sending.

        Useful for testing and for callers that need the XML for inspection
        before transmitting.
        """
        threat_by_track: Dict[str, Threat] = {
            t.track_id: t for t in threats if t.track_id is not None
        }

        result: List[str] = []
        for track in tracks:
            threat = threat_by_track.get(track.id)
            if threat is not None:
                result.append(format_threat_cot(threat, track))
            else:
                result.append(format_track_cot(track))
        return result

    async def publish_full_picture(
        self,
        tracks: List[Track],
        threats: List[Threat],
        defenders: List[Defender],
        engagements: List[Engagement],
        swarm_clusters: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Send the complete BULWARK operational picture to TAK.

        Extends publish() with defender status, engagement results, and
        swarm clusters so operators see the full picture.
        """
        if not self._sender:
            logger.warning("publish_full_picture called before start()")
            return

        await self.publish(tracks, threats)

        for defender in defenders:
            xml = format_defender_cot(defender)
            await self._sender.send(xml)

        defender_by_id: Dict[str, Defender] = {d.id: d for d in defenders}
        threat_by_id: Dict[str, Threat] = {t.id: t for t in threats}
        track_by_id: Dict[str, Track] = {t.id: t for t in tracks}

        for eng in engagements:
            defender = defender_by_id.get(eng.defender_id)
            threat = threat_by_id.get(eng.target_threat_id)
            if defender is None or threat is None:
                continue
            track = track_by_id.get(threat.track_id or "")
            if track is None:
                continue
            xml = format_engagement_cot(eng, defender, threat, track)
            await self._sender.send(xml)

        if swarm_clusters:
            for cluster in swarm_clusters:
                xml = format_swarm_cluster_cot(
                    cluster_id=cluster["cluster_id"],
                    center=cluster["center"],
                    member_count=cluster["member_count"],
                    radius_m=cluster["radius_m"],
                    intent=cluster["intent"],
                    timestamp=cluster["timestamp"],
                )
                await self._sender.send(xml)

        logger.debug(
            "Full picture: %d tracks, %d threats, %d defenders, %d engagements, %d clusters",
            len(tracks), len(threats), len(defenders), len(engagements),
            len(swarm_clusters) if swarm_clusters else 0,
        )

    async def _heartbeat_loop(self) -> None:
        """Emit a sensor heartbeat every _HEARTBEAT_INTERVAL_S seconds."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            await self._send_heartbeat(n_tracks=0, n_threats=0)

    async def _send_heartbeat(self, n_tracks: int, n_threats: int) -> None:
        if not self._sender:
            return
        xml = format_heartbeat_cot(
            site_lat=self._site_lat,
            site_lon=self._site_lon,
            n_tracks=n_tracks,
            n_threats=n_threats,
            now_ts=time.time(),
        )
        await self._sender.send(xml)
        logger.debug("CoTBridge heartbeat sent — tracks=%d threats=%d", n_tracks, n_threats)
