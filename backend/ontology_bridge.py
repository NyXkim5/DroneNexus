"""
Ontology bridge between BULWARK counter-swarm and OVERWATCH wire protocol.

BULWARK (csontology.py) and OVERWATCH (protocol.py) were built as two parallel
world models with no shared types. This module is the seam. It holds pure
mapping functions so a BULWARK Track, Threat, Defender, or Engagement can be
expressed in the existing OVERWATCH vocabulary and surface on the same map,
roster, and event log.

Design choices where OVERWATCH lacks a direct concept
-----------------------------------------------------
- No Observation class exists in protocol.py. The OVERWATCH way to push
  sensor-collected intel onto the map is an OVERLAY_UPDATE message carrying an
  OverlayType. So threat_to_observation builds an OVERLAY_UPDATE dict keyed by
  the OverlayType that best matches the source swarm formation.
- No Asset dataclass exists. The OVERWATCH roster vocabulary is AssetStatePacket
  with AssetClassification, OperationalStatus, and Formation. defender_to_asset
  builds a minimal valid AssetStatePacket so a defender shows in the roster.
- Positions in BULWARK are local ENU meters. OVERWATCH Position is geodetic
  lat/lon, so we convert through csontology.enu_to_latlon.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import csontology as cs
import protocol as ow

logger = logging.getLogger("overwatch.bridge")


# ---- Static enum mappings ----

_TRACKCLASS_TO_ASSET = {
    cs.TrackClass.HOSTILE: ow.AssetClassification.PRIMARY,
    cs.TrackClass.FRIENDLY: ow.AssetClassification.ESCORT,
    cs.TrackClass.UNKNOWN: ow.AssetClassification.ISR,
}

_FORMATION_TO_OVERLAY = {
    "V_FORMATION": ow.OverlayType.V_FORMATION,
    "LINE_ABREAST": ow.OverlayType.LINE_ABREAST,
    "COLUMN": ow.OverlayType.COLUMN,
    "DIAMOND": ow.OverlayType.DIAMOND,
    "ORBIT": ow.OverlayType.ORBIT,
    "SCATTER": ow.OverlayType.SCATTER,
}

_DEFENDERSTATUS_TO_OPSTATUS = {
    cs.DefenderStatus.READY: ow.OperationalStatus.NOMINAL,
    cs.DefenderStatus.ENGAGING: ow.OperationalStatus.NOMINAL,
    cs.DefenderStatus.RELOADING: ow.OperationalStatus.DEGRADED,
    cs.DefenderStatus.DEPLETED: ow.OperationalStatus.RTB,
    cs.DefenderStatus.OFFLINE: ow.OperationalStatus.OFFLINE,
}


def track_to_asset_classification(track: cs.Track) -> ow.AssetClassification:
    """Map a BULWARK Track hostility class to an OVERWATCH AssetClassification.

    HOSTILE maps to PRIMARY (the priority object on the picture), FRIENDLY to
    ESCORT, and UNKNOWN to ISR. Unknown classes fall back to ISR.
    """
    return _TRACKCLASS_TO_ASSET.get(track.classification, ow.AssetClassification.ISR)


def asset_classification_to_track_class(
    classification: ow.AssetClassification,
) -> cs.TrackClass:
    """Inverse of track_to_asset_classification.

    PRIMARY and ESCORT round-trip to HOSTILE and FRIENDLY. Every other
    OVERWATCH classification collapses to UNKNOWN since BULWARK only models
    three hostility states.
    """
    inverse = {
        ow.AssetClassification.PRIMARY: cs.TrackClass.HOSTILE,
        ow.AssetClassification.ESCORT: cs.TrackClass.FRIENDLY,
    }
    return inverse.get(classification, cs.TrackClass.UNKNOWN)


def _enu_to_position(position: cs.Vec3) -> ow.Position:
    """Build an OVERWATCH Position from a BULWARK ENU position."""
    lat, lon, alt = cs.enu_to_latlon(*position)
    return ow.Position(lat=lat, lon=lon, alt_msl=alt, alt_agl=alt)


def _now_iso() -> str:
    """Return current UTC time as an ISO 8601 string for wire timestamps."""
    return datetime.now(timezone.utc).isoformat()


def threat_to_observation(
    threat: cs.Threat,
    track: cs.Track,
    swarm: Optional[cs.Swarm] = None,
) -> dict:
    """Surface a BULWARK Threat as an OVERWATCH OVERLAY_UPDATE observation.

    OVERWATCH has no Observation class. Its map-intel channel is the
    OVERLAY_UPDATE message carrying an OverlayType. We pick the OverlayType from
    the source swarm formation, default SCATTER for a lone track, and attach the
    threat score, position, and source ids so the HUD can render it.
    """
    overlay = ow.OverlayType.SCATTER
    if swarm is not None:
        overlay = _FORMATION_TO_OVERLAY.get(swarm.formation, ow.OverlayType.SCATTER)
    pos = _enu_to_position(track.position)
    return {
        "type": ow.MessageType.OVERLAY_UPDATE.value,
        "overlay_type": overlay.value,
        "threat_id": threat.id,
        "track_id": track.id,
        "swarm_id": threat.swarm_id,
        "classification": track_to_asset_classification(track).value,
        "score": threat.score,
        "priority_rank": threat.priority_rank,
        "time_to_impact_s": threat.time_to_impact_s,
        "value_at_risk": threat.value_at_risk,
        "position": pos.model_dump(),
        "timestamp": _now_iso(),
    }


def _engagement_severity(engagement: cs.Engagement) -> ow.AlertSeverity:
    """Map an engagement status to an OVERWATCH alert severity."""
    if engagement.status == cs.EngagementStatus.LEAK:
        return ow.AlertSeverity.CRITICAL
    if engagement.status == cs.EngagementStatus.MISS:
        return ow.AlertSeverity.WARNING
    return ow.AlertSeverity.INFO


def engagement_to_event(engagement: cs.Engagement) -> dict:
    """Convert a BULWARK Engagement into an OVERWATCH event row.

    The shape matches db.OverwatchDB.log_event arguments (drone_id, severity,
    message, data). The defender id stands in as drone_id so the engagement
    threads through the existing events table and HUD activity feed.
    """
    severity = _engagement_severity(engagement)
    message = f"Engagement {engagement.id} {engagement.status.value}"
    return {
        "drone_id": engagement.defender_id,
        "severity": severity.value,
        "message": message,
        "data": {
            "engagement_id": engagement.id,
            "defender_id": engagement.defender_id,
            "target_threat_id": engagement.target_threat_id,
            "status": engagement.status.value,
            "start_time": engagement.start_time,
            "cost": engagement.cost,
        },
        "timestamp": _now_iso(),
    }


def _defender_formation(defender: cs.Defender) -> ow.Formation:
    """Build an OVERWATCH Formation block for a defender asset."""
    return ow.Formation(
        role=ow.AssetClassification.OVERWATCH,
        offset_vector=ow.OffsetVector(dx=0.0, dy=0.0),
        cohesion=1.0,
    )


def defender_to_asset(defender: cs.Defender) -> ow.AssetStatePacket:
    """Map a BULWARK Defender into the OVERWATCH roster as an AssetStatePacket.

    OVERWATCH has no Asset dataclass, so the AssetStatePacket is the roster
    vocabulary. A defender is a friendly effector, so it takes the OVERWATCH
    classification. Status maps from DefenderStatus. Telemetry sub-blocks get
    nominal placeholder values since a ground effector has no flight telemetry.
    """
    pos = _enu_to_position(defender.position)
    status = _DEFENDERSTATUS_TO_OPSTATUS.get(
        defender.status, ow.OperationalStatus.OFFLINE
    )
    return ow.AssetStatePacket(
        drone_id=defender.id,
        timestamp=_now_iso(),
        seq=0,
        position=pos,
        attitude=ow.Attitude(roll=0.0, pitch=0.0, yaw=0.0),
        velocity=ow.Velocity(ground_speed=0.0, vertical_speed=0.0, heading=0.0),
        battery=ow.Battery(voltage=0.0, current=0.0, remaining_pct=100.0),
        gps=ow.GPS(satellites=0, hdop=0.0),
        link=ow.Link(rssi=0, quality=100, latency_ms=0),
        status=status,
        formation=_defender_formation(defender),
    )
