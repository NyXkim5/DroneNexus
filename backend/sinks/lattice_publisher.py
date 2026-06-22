"""
Lattice-compatible entity publisher for OVERWATCH.

Publishes tracks, defenders, and engagements as Lattice entity JSON. Follows the
DragonSync LatticeSink pattern but maps BULWARK domain objects instead of raw
RemoteID detections. Each entity carries ontology, disposition, relationships,
and sensor provenance so Lattice consumers render the full counter-swarm picture.

Rate limits: tracks at 2 Hz, defenders at 1 Hz, engagements on every event.

Usage
-----
    publisher = LatticePublisher(source_name="OVERWATCH-BULWARK")
    entities = publisher.publish_frame_to_lattice(frame)
    # entities is a list of Lattice-compatible JSON dicts
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Engagement,
    EngagementStatus,
    SwarmIntent,
    Threat,
    Track,
    TrackClass,
    enu_to_latlon,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _disposition_for_track(track: Track) -> str:
    """Map track classification to Lattice disposition string."""
    mapping = {
        TrackClass.HOSTILE: "DISPOSITION_HOSTILE",
        TrackClass.FRIENDLY: "DISPOSITION_FRIENDLY",
        TrackClass.UNKNOWN: "DISPOSITION_UNKNOWN",
    }
    return mapping.get(track.classification, "DISPOSITION_UNKNOWN")


def _intent_label(threat: Optional[Threat]) -> Optional[str]:
    """Return a human-readable intent label or None."""
    if threat is None:
        return None
    return threat.intent.value


def _defender_disposition(status: DefenderStatus) -> str:
    """All defenders are friendly assets."""
    return "DISPOSITION_FRIENDLY"


def _defender_platform_detail(kind: DefenderKind) -> str:
    """Map defender kind to a readable platform detail string."""
    mapping = {
        DefenderKind.INTERCEPTOR: "INTERCEPTOR",
        DefenderKind.NET: "NET_GUN",
        DefenderKind.JAMMER: "RF_JAMMER",
        DefenderKind.EW: "EW_SYSTEM",
        DefenderKind.HPM: "HPM_EMITTER",
        DefenderKind.LASER: "DIRECTED_ENERGY",
    }
    return mapping.get(kind, "COUNTER_UAS")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Per-key rate limiter using monotonic time."""

    def __init__(self, default_period_s: float = 1.0) -> None:
        self._periods: Dict[str, float] = {}
        self._last: Dict[str, float] = {}
        self._default = default_period_s

    def set_period(self, key: str, period_s: float) -> None:
        self._periods[key] = period_s

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        period = self._periods.get(key, self._default)
        last = self._last.get(key, 0.0)
        if now - last >= period:
            self._last[key] = now
            return True
        return False

    def reset(self) -> None:
        self._last.clear()


# ---------------------------------------------------------------------------
# Entity builders
# ---------------------------------------------------------------------------

def build_track_entity(
    track: Track,
    threat: Optional[Threat],
    source_name: str,
    timestamp: str,
) -> Dict[str, Any]:
    """Build a Lattice-compatible entity dict for one hostile track."""
    lat, lon, alt = enu_to_latlon(*track.position)
    entity: Dict[str, Any] = {
        "entity_id": f"overwatch-track-{track.id}",
        "entity_type": "TRACK",
        "location": {"lat": lat, "lon": lon, "alt": round(alt, 1)},
        "timestamp": timestamp,
        "ontology": {
            "template": "TEMPLATE_TRACK",
            "platform_type": "PLATFORM_TYPE_UAV",
        },
        "disposition": _disposition_for_track(track),
        "confidence": round(track.confidence, 4),
        "sensors": [],
        "relationships": [],
        "provenance": {
            "integration_name": source_name,
            "data_type": "overwatch-track",
        },
        "velocity": {
            "east_mps": round(track.velocity[0], 2),
            "north_mps": round(track.velocity[1], 2),
            "up_mps": round(track.velocity[2], 2),
        },
    }
    if threat is not None:
        entity["threat_score"] = round(threat.score, 4)
        entity["intent"] = _intent_label(threat)
        entity["time_to_impact_s"] = threat.time_to_impact_s
    return entity


def build_defender_entity(
    defender: Defender,
    source_name: str,
    timestamp: str,
) -> Dict[str, Any]:
    """Build a Lattice-compatible entity dict for one defender asset."""
    lat, lon, alt = enu_to_latlon(*defender.position)
    return {
        "entity_id": f"overwatch-defender-{defender.id}",
        "entity_type": "ASSET",
        "location": {"lat": lat, "lon": lon, "alt": round(alt, 1)},
        "timestamp": timestamp,
        "ontology": {
            "template": "TEMPLATE_ASSET",
            "platform_type": "PLATFORM_TYPE_COUNTER_UAS",
        },
        "disposition": _defender_disposition(defender.status),
        "confidence": 1.0,
        "sensors": [],
        "relationships": [],
        "provenance": {
            "integration_name": source_name,
            "data_type": "overwatch-defender",
        },
        "effector": {
            "kind": defender.kind.value,
            "detail": _defender_platform_detail(defender.kind),
            "status": defender.status.value,
            "capacity": defender.capacity,
            "range_m": round(defender.range_m, 1),
        },
    }


def build_engagement_entity(
    engagement: Engagement,
    defender: Defender,
    threat: Threat,
    track: Track,
    source_name: str,
    timestamp: str,
) -> Dict[str, Any]:
    """Build a Lattice-compatible entity for an engagement event."""
    lat, lon, alt = enu_to_latlon(*track.position)
    return {
        "entity_id": f"overwatch-engagement-{engagement.id}",
        "entity_type": "ENGAGEMENT",
        "location": {"lat": lat, "lon": lon, "alt": round(alt, 1)},
        "timestamp": timestamp,
        "ontology": {
            "template": "TEMPLATE_ENGAGEMENT",
            "platform_type": "PLATFORM_TYPE_ENGAGEMENT",
        },
        "disposition": "DISPOSITION_FRIENDLY",
        "confidence": 1.0,
        "sensors": [],
        "relationships": [
            {
                "related_entity_id": f"overwatch-defender-{defender.id}",
                "relationship_type": "ENGAGED_BY",
            },
            {
                "related_entity_id": f"overwatch-track-{track.id}",
                "relationship_type": "ENGAGED_TARGET",
            },
        ],
        "provenance": {
            "integration_name": source_name,
            "data_type": "overwatch-engagement",
        },
        "engagement_detail": {
            "status": engagement.status.value,
            "cost": round(engagement.cost, 2),
            "neutralized_count": len(engagement.neutralized_threat_ids),
        },
    }


# ---------------------------------------------------------------------------
# LatticePublisher
# ---------------------------------------------------------------------------

class LatticePublisher:
    """Publishes OVERWATCH entities in Lattice-compatible JSON format.

    Rate limits per entity type: tracks at 2 Hz, defenders at 1 Hz,
    engagements pass through on every event with no throttle.
    """

    def __init__(
        self,
        *,
        source_name: str = "OVERWATCH-BULWARK",
        track_hz: float = 2.0,
        defender_hz: float = 1.0,
    ) -> None:
        self.source_name = source_name
        self._rate = _RateLimiter()
        self._rate.set_period("track", 1.0 / max(track_hz, 1e-6))
        self._rate.set_period("defender", 1.0 / max(defender_hz, 1e-6))

    def publish_tracks(
        self,
        tracks: List[Track],
        threats: List[Threat],
    ) -> List[Dict[str, Any]]:
        """Convert tracks to Lattice entities, rate-limited at track_hz."""
        if not self._rate.allow("track"):
            return []
        ts = _iso_now()
        threat_by_track = _index_threats(threats)
        return [
            build_track_entity(t, threat_by_track.get(t.id), self.source_name, ts)
            for t in tracks
        ]

    def publish_defenders(
        self,
        defenders: List[Defender],
    ) -> List[Dict[str, Any]]:
        """Convert defenders to Lattice entities, rate-limited at defender_hz."""
        if not self._rate.allow("defender"):
            return []
        ts = _iso_now()
        return [
            build_defender_entity(d, self.source_name, ts)
            for d in defenders
        ]

    def publish_engagements(
        self,
        engagements: List[Engagement],
        defenders: List[Defender],
        threats: List[Threat],
        tracks: List[Track],
    ) -> List[Dict[str, Any]]:
        """Convert engagements to Lattice entities. No rate limit (on-event)."""
        if not engagements:
            return []
        ts = _iso_now()
        by_defender = {d.id: d for d in defenders}
        by_threat = {t.id: t for t in threats}
        by_track = {t.id: t for t in tracks}
        return _build_engagement_batch(
            engagements, by_defender, by_threat, by_track, self.source_name, ts,
        )

    def publish_frame_to_lattice(self, frame: Any) -> List[Dict[str, Any]]:
        """Publish all entities from a wargame Frame in one call.

        Returns the combined list of Lattice entity dicts for tracks,
        defenders, and engagements. Callers can forward these to a Lattice
        API endpoint, write them to a file, or broadcast via websocket.
        """
        entities: List[Dict[str, Any]] = []
        entities.extend(self.publish_tracks(frame.tracks, frame.threats))
        entities.extend(self.publish_defenders(frame.defenders))
        entities.extend(
            self.publish_engagements(
                frame.engagements, frame.defenders, frame.threats, frame.tracks,
            )
        )
        logger.debug(
            "Lattice publish: %d entities from frame tick %s",
            len(entities),
            getattr(frame.metrics, "tick", "?"),
        )
        return entities

    def reset_rate_limits(self) -> None:
        """Clear rate limit state. Useful for tests."""
        self._rate.reset()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _index_threats(threats: List[Threat]) -> Dict[str, Threat]:
    """Index threats by track_id, keeping the highest score per track."""
    by_track: Dict[str, Threat] = {}
    for threat in threats:
        if threat.track_id is None:
            continue
        existing = by_track.get(threat.track_id)
        if existing is None or threat.score > existing.score:
            by_track[threat.track_id] = threat
    return by_track


def _build_engagement_batch(
    engagements: List[Engagement],
    by_defender: Dict[str, Defender],
    by_threat: Dict[str, Threat],
    by_track: Dict[str, Track],
    source_name: str,
    timestamp: str,
) -> List[Dict[str, Any]]:
    """Build engagement entities, skipping any with missing references."""
    results: List[Dict[str, Any]] = []
    for eng in engagements:
        defender = by_defender.get(eng.defender_id)
        threat = by_threat.get(eng.target_threat_id)
        if defender is None or threat is None:
            continue
        track = by_track.get(threat.track_id or "")
        if track is None:
            continue
        results.append(
            build_engagement_entity(eng, defender, threat, track, source_name, timestamp)
        )
    return results
