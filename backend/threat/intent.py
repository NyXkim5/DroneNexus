"""
Swarm intent inference from geometry and motion.

Given a swarm and its member tracks, classify the coordinated behavior into one
SwarmIntent. The reasoning is geometric and kinematic, not learned. Rules:

  SATURATION : many members, tightly grouped, most closing on the site together.
  WAVES      : members spread along the approach axis in distinct distance bands.
  DECOY      : a mix where most members loiter or drift while a few close fast.
  PROBE      : a small group closing slowly, scouting rather than committing.
  UNKNOWN    : nothing matches with confidence.

All distances are ENU meters about the site origin. We measure motion relative
to the site position so the rules hold wherever the site sits.
"""
from __future__ import annotations

import math
import statistics
from typing import Dict, List

from csontology import Site, Swarm, SwarmIntent, Track, Vec3

# A member counts as closing when its radial speed toward the site exceeds this.
CLOSING_SPEED_MIN_MS = 1.0
# Saturation needs a group of at least this many members.
SATURATION_MIN_SIZE = 5
# Saturation members must sit within this radius of the centroid to count tight.
SATURATION_SPREAD_MAX_M = 120.0
# Fraction of members that must be closing to read as a committed mass attack.
COMMITTED_FRACTION = 0.7
# Waves needs range bands separated by at least this gap in meters.
WAVE_BAND_GAP_M = 80.0
# Probe stays small. Above this size we stop calling a closing group a probe.
PROBE_MAX_SIZE = 4
# Below this closing speed a probe is scouting, not committing.
PROBE_SLOW_MS = 6.0


def _range_to_site(pos: Vec3, site: Site) -> float:
    """Horizontal distance from an ENU point to the site center."""
    dx = pos[0] - site.position[0]
    dy = pos[1] - site.position[1]
    return math.hypot(dx, dy)


def closing_speed_to_site(track: Track, site: Site) -> float:
    """Return radial speed toward the site in m/s. Positive means closing.

    Projects the track velocity onto the unit vector pointing from the track to
    the site. A track sitting on the site returns 0 to avoid a divide by zero.
    """
    to_site = (
        site.position[0] - track.position[0],
        site.position[1] - track.position[1],
    )
    dist = math.hypot(to_site[0], to_site[1])
    if dist == 0.0:
        return 0.0
    ux, uy = to_site[0] / dist, to_site[1] / dist
    return track.velocity[0] * ux + track.velocity[1] * uy


def _spread_radius(members: List[Track], centroid: Vec3) -> float:
    """Mean horizontal distance of members from their centroid."""
    if not members:
        return 0.0
    dists = [
        math.hypot(m.position[0] - centroid[0], m.position[1] - centroid[1])
        for m in members
    ]
    return statistics.fmean(dists)


def _count_range_bands(ranges: List[float], gap_m: float) -> int:
    """Count distinct distance bands by walking sorted ranges and splitting on gaps."""
    if not ranges:
        return 0
    ordered = sorted(ranges)
    bands = 1
    for prev, cur in zip(ordered, ordered[1:]):
        if cur - prev > gap_m:
            bands += 1
    return bands


def infer_intent(swarm: Swarm, members: List[Track], site: Site) -> SwarmIntent:
    """Classify the swarm's coordinated intent from geometry and motion.

    members must be the Track objects named in swarm.member_track_ids. Order does
    not matter. Returns one SwarmIntent. Falls back to UNKNOWN when ambiguous.
    """
    if not members:
        return SwarmIntent.UNKNOWN

    closing = [closing_speed_to_site(m, site) for m in members]
    closing_count = sum(1 for c in closing if c >= CLOSING_SPEED_MIN_MS)
    fraction_closing = closing_count / len(members)
    spread = _spread_radius(members, swarm.centroid)
    ranges = [_range_to_site(m.position, site) for m in members]
    bands = _count_range_bands(ranges, WAVE_BAND_GAP_M)

    if _is_saturation(len(members), spread, fraction_closing):
        return SwarmIntent.SATURATION
    if _is_waves(bands, fraction_closing):
        return SwarmIntent.WAVES
    if _is_decoy(closing, fraction_closing):
        return SwarmIntent.DECOY
    if _is_probe(len(members), closing, fraction_closing):
        return SwarmIntent.PROBE
    return SwarmIntent.UNKNOWN


def _is_saturation(size: int, spread: float, fraction_closing: float) -> bool:
    """A large, tight mass mostly closing at once."""
    return (
        size >= SATURATION_MIN_SIZE
        and spread <= SATURATION_SPREAD_MAX_M
        and fraction_closing >= COMMITTED_FRACTION
    )


def _is_waves(bands: int, fraction_closing: float) -> bool:
    """Closing members arranged in two or more separated range bands."""
    return bands >= 2 and fraction_closing >= 0.5


def _is_decoy(closing: List[float], fraction_closing: float) -> bool:
    """Most members loiter or drift while a minority commit fast.

    Also triggers when many drones close slowly (all below PROBE_SLOW_MS),
    indicating a swarm deliberately burning defender ammo on cheap targets
    rather than committing to a real attack.
    """
    fast = sum(1 for c in closing if c >= PROBE_SLOW_MS)
    slow_closers = sum(1 for c in closing if CLOSING_SPEED_MIN_MS <= c < PROBE_SLOW_MS)
    if fraction_closing < 0.5 and 0 < fast < len(closing):
        return True
    if len(closing) >= 10 and fast == 0 and slow_closers > len(closing) * 0.6:
        return True
    return False


def _is_probe(size: int, closing: List[float], fraction_closing: float) -> bool:
    """A small group closing slowly, scouting the defenses."""
    if size > PROBE_MAX_SIZE or fraction_closing < 0.5:
        return False
    max_closing = max(closing)
    return 0.0 < max_closing < PROBE_SLOW_MS
