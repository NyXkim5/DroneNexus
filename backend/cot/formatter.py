"""
CoT XML formatter for OVERWATCH tracks and threats.

All functions are pure — they take data objects and return XML strings with no
side effects. This makes them trivially testable without any network setup.

CoT type codes used here:
    a-h-A-M-H-Q  hostile air, military, helicopter/multirotor, quadrotor
    a-u-A-M-H-Q  unknown air
    a-f-A-M-H-Q  friendly air
    a-f-G-E-S    friendly ground equipment sensor (OVERWATCH heartbeat)
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, Tuple

from csontology import Track, Threat, TrackClass, Vec3, enu_to_latlon, SwarmIntent

# Seconds a track CoT event remains valid in TAK before going stale.
_TRACK_STALE_S = 60
# Seconds the OVERWATCH sensor heartbeat remains valid.
_HEARTBEAT_STALE_S = 120

_COT_TYPE: dict[TrackClass, str] = {
    TrackClass.HOSTILE: "a-h-A-M-H-Q",
    TrackClass.UNKNOWN: "a-u-A-M-H-Q",
    TrackClass.FRIENDLY: "a-f-A-M-H-Q",
}

_SENSOR_TYPE = "a-f-G-E-S"


def _iso(ts: float) -> str:
    """Format a POSIX timestamp as a CoT-compatible ISO-8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ground_speed(velocity: Vec3) -> float:
    """Compute horizontal ground speed in m/s from an ENU velocity vector."""
    vx, vy, _ = velocity
    return math.sqrt(vx * vx + vy * vy)


def _heading_deg(velocity: Vec3) -> float:
    """Compute true-north heading in degrees [0, 360) from ENU velocity."""
    vx, vy, _ = velocity
    # atan2 in ENU: east=x, north=y. Bearing from north clockwise.
    angle = math.degrees(math.atan2(vx, vy))
    return angle % 360.0


def format_track_cot(
    track: Track,
    site_origin: Optional[Tuple[float, float, float]] = None,
) -> str:
    """Convert a fused Track into a CoT XML string.

    site_origin is unused here because enu_to_latlon already encodes the
    site origin from csontology.ORIGIN_LAT/LON. The parameter is kept in the
    signature for forward compatibility if a multi-site deployment ever needs
    to pass a runtime origin.

    Returns a well-formed CoT XML string ready for UDP transmission.
    """
    now_ts = track.last_update
    stale_ts = now_ts + _TRACK_STALE_S

    lat, lon, alt = enu_to_latlon(*track.position)
    cot_type = _COT_TYPE.get(track.classification, "a-u-A-M-H-Q")

    event = ET.Element("event", {
        "version": "2.0",
        "uid": f"OVERWATCH.{track.id}",
        "type": cot_type,
        "time": _iso(now_ts),
        "start": _iso(now_ts),
        "stale": _iso(stale_ts),
        "how": "m-g",
    })

    ET.SubElement(event, "point", {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "hae": f"{alt:.2f}",
        "ce": "10",
        "le": "10",
    })

    detail = ET.SubElement(event, "detail")

    callsign_prefix = track.classification.value[:1].upper() + track.classification.value[1:].lower()
    ET.SubElement(detail, "contact", {"callsign": f"{callsign_prefix.upper()}-{track.id}"})

    speed = _ground_speed(track.velocity)
    course = _heading_deg(track.velocity)
    ET.SubElement(detail, "track", {
        "speed": f"{speed:.2f}",
        "course": f"{course:.2f}",
    })

    ET.SubElement(detail, "remarks").text = (
        f"OVERWATCH Track {track.id} | Class: {track.classification.value} | "
        f"Conf: {track.confidence:.2f}"
    )

    return ET.tostring(event, encoding="unicode", xml_declaration=False)


def format_threat_cot(
    threat: Threat,
    track: Track,
    site_origin: Optional[Tuple[float, float, float]] = None,
) -> str:
    """Convert a scored Threat (with its backing Track) into a CoT XML string.

    Threats are always emitted as hostile regardless of the track classification
    because a Threat object represents a scored danger, not a tentative contact.
    The remarks field carries threat score, intent, and time-to-impact so TAK
    operators have full situational context without leaving the map.
    """
    now_ts = track.last_update
    stale_ts = now_ts + _TRACK_STALE_S

    lat, lon, alt = enu_to_latlon(*track.position)

    event = ET.Element("event", {
        "version": "2.0",
        "uid": f"OVERWATCH.{track.id}",
        "type": "a-h-A-M-H-Q",
        "time": _iso(now_ts),
        "start": _iso(now_ts),
        "stale": _iso(stale_ts),
        "how": "m-g",
    })

    ET.SubElement(event, "point", {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "hae": f"{alt:.2f}",
        "ce": "10",
        "le": "10",
    })

    detail = ET.SubElement(event, "detail")

    ET.SubElement(detail, "contact", {"callsign": f"HOSTILE-{track.id}"})

    speed = _ground_speed(track.velocity)
    course = _heading_deg(track.velocity)
    ET.SubElement(detail, "track", {
        "speed": f"{speed:.2f}",
        "course": f"{course:.2f}",
    })

    tti = (
        f"{threat.time_to_impact_s:.1f}s"
        if threat.time_to_impact_s is not None
        else "N/A"
    )
    intent: str = threat.intent.value if isinstance(threat.intent, SwarmIntent) else str(threat.intent)
    ET.SubElement(detail, "remarks").text = (
        f"OVERWATCH Track {track.id} | Score: {threat.score:.2f} | "
        f"Intent: {intent} | TTI: {tti}"
    )

    return ET.tostring(event, encoding="unicode", xml_declaration=False)


def format_heartbeat_cot(
    site_lat: float,
    site_lon: float,
    n_tracks: int,
    n_threats: int,
    now_ts: float,
) -> str:
    """Generate the OVERWATCH sensor heartbeat CoT event.

    This event tells ATAK that OVERWATCH-GCS is alive and shows it as a
    friendly ground sensor marker on the map. Broadcast every 30 seconds.
    """
    stale_ts = now_ts + _HEARTBEAT_STALE_S

    event = ET.Element("event", {
        "version": "2.0",
        "uid": "OVERWATCH.SENSOR",
        "type": _SENSOR_TYPE,
        "time": _iso(now_ts),
        "start": _iso(now_ts),
        "stale": _iso(stale_ts),
        "how": "m-g",
    })

    ET.SubElement(event, "point", {
        "lat": f"{site_lat:.7f}",
        "lon": f"{site_lon:.7f}",
        "hae": "0",
        "ce": "1",
        "le": "1",
    })

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": "OVERWATCH-GCS"})
    ET.SubElement(detail, "remarks").text = (
        f"OVERWATCH C2 | Tracks: {n_tracks} | Threats: {n_threats}"
    )

    return ET.tostring(event, encoding="unicode", xml_declaration=False)
