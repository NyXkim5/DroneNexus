"""
Threat scoring and prioritization.

Turns tracks and swarms into scored Threat objects and ranks them. A threat is
more dangerous when it reaches the site sooner, closes faster, and endangers
more value. Score is a normalized 0..1 blend of those factors. priority_rank is
the ordering, rank 1 being the most urgent threat.

Only hostile tracks are scored. Friendly and unknown tracks are skipped here so
the allocator never wastes effectors on them. Each track that belongs to a swarm
is scored once as part of that swarm, not twice. Loners are scored on their own.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

from csontology import Site, Swarm, Threat, Track, TrackClass, Vec3

from threat.intent import closing_speed_to_site, infer_intent

logger = logging.getLogger("overwatch.threat")

# Weights for the blended score. They sum to 1.0.
W_TIME_TO_IMPACT = 0.5
W_CLOSING_SPEED = 0.3
W_VALUE_AT_RISK = 0.2
# Time-to-impact at or below this is treated as maximally urgent.
TTI_FLOOR_S = 5.0
# Time-to-impact at or above this contributes almost nothing to urgency.
TTI_CEILING_S = 300.0
# Closing speed at or above this saturates the speed term.
CLOSING_SPEED_FULL_MS = 40.0
# A swarm endangers more value than a lone drone. Scale value by member count.
SWARM_VALUE_PER_MEMBER = 1.0


def _range_to_site(pos: Vec3, site: Site) -> float:
    """Horizontal distance from an ENU point to the site center."""
    dx = pos[0] - site.position[0]
    dy = pos[1] - site.position[1]
    return math.hypot(dx, dy)


def time_to_impact(track: Track, site: Site) -> Optional[float]:
    """Seconds until the track reaches the site at its current closing speed.

    Returns None when the track is not closing. Range is divided by the radial
    closing speed toward the site. A non-closing track has no impact time.
    """
    closing = closing_speed_to_site(track, site)
    if closing <= 0.0:
        return None
    return _range_to_site(track.position, site) / closing


def _tti_urgency(tti_s: Optional[float]) -> float:
    """Map time-to-impact to a 0..1 urgency. Sooner is higher. None is 0."""
    if tti_s is None:
        return 0.0
    if tti_s <= TTI_FLOOR_S:
        return 1.0
    if tti_s >= TTI_CEILING_S:
        return 0.0
    span = TTI_CEILING_S - TTI_FLOOR_S
    return 1.0 - (tti_s - TTI_FLOOR_S) / span


def _speed_urgency(closing_ms: float) -> float:
    """Map closing speed to a 0..1 urgency. Receding speeds clamp to 0."""
    if closing_ms <= 0.0:
        return 0.0
    return min(1.0, closing_ms / CLOSING_SPEED_FULL_MS)


def _value_urgency(value_at_risk: float, site: Site) -> float:
    """Map endangered value to a 0..1 share of total site value."""
    if site.value <= 0.0:
        return 0.0
    return min(1.0, value_at_risk / site.value)


def _blend(tti_u: float, speed_u: float, value_u: float) -> float:
    """Weighted blend of the three urgency terms into a 0..1 score."""
    return (
        W_TIME_TO_IMPACT * tti_u
        + W_CLOSING_SPEED * speed_u
        + W_VALUE_AT_RISK * value_u
    )


def _score_track(track: Track, site: Site) -> Threat:
    """Score one lone hostile track into a Threat (rank filled in later)."""
    tti = time_to_impact(track, site)
    closing = max(0.0, closing_speed_to_site(track, site))
    value_at_risk = site.value
    score = _blend(
        _tti_urgency(tti),
        _speed_urgency(closing),
        _value_urgency(value_at_risk, site),
    )
    return Threat(
        id=f"threat-{track.id}",
        score=score,
        time_to_impact_s=tti,
        value_at_risk=value_at_risk,
        priority_rank=0,
        track_id=track.id,
    )


def _score_swarm(swarm: Swarm, members: List[Track], site: Site) -> Threat:
    """Score a swarm into a Threat using its fastest-closing member.

    The swarm reaches the site when its leading member does, so urgency keys off
    the shortest member time-to-impact and the highest member closing speed. The
    value at risk scales with member count to reflect a mass attack.
    """
    ttis = [t for t in (time_to_impact(m, site) for m in members) if t is not None]
    lead_tti = min(ttis) if ttis else None
    max_closing = max((closing_speed_to_site(m, site) for m in members), default=0.0)
    max_closing = max(0.0, max_closing)
    value_at_risk = site.value * len(members) * SWARM_VALUE_PER_MEMBER
    score = _blend(
        _tti_urgency(lead_tti),
        _speed_urgency(max_closing),
        _value_urgency(value_at_risk, site),
    )
    return Threat(
        id=f"threat-{swarm.id}",
        score=score,
        time_to_impact_s=lead_tti,
        value_at_risk=value_at_risk,
        priority_rank=0,
        swarm_id=swarm.id,
    )


def _rank(threats: List[Threat]) -> List[Threat]:
    """Sort threats by descending urgency and assign 1-based priority_rank.

    Primary key is score. Ties break on sooner time-to-impact. None impact time
    sorts last among ties. The sort is stable, so equal threats keep input order.
    """
    def key(t: Threat) -> tuple:
        tti = t.time_to_impact_s if t.time_to_impact_s is not None else math.inf
        return (-t.score, tti)

    ordered = sorted(threats, key=key)
    for rank, threat in enumerate(ordered, start=1):
        threat.priority_rank = rank
    return ordered
