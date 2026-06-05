"""
Swarm-aware threat classifier and prioritizer — the public entry point.

assess(tracks, site, now) is the one call the integration layer makes each tick.
It clusters hostile tracks into swarms, infers each swarm's intent, scores every
swarm and every lone hostile track into Threat objects, and returns them ranked
by priority. Friendly and unknown tracks are filtered out before scoring.

Pipeline:
  1. keep only HOSTILE tracks
  2. cluster them into swarms (threat.clustering)
  3. infer intent per swarm (threat.intent)
  4. score swarms and remaining loners (threat.scoring)
  5. rank all threats and stamp priority_rank
"""
from __future__ import annotations

import logging
from typing import Dict, List

from csontology import Site, Swarm, Threat, Track, TrackClass

from threat.clustering import (
    DEFAULT_CLUSTER_RADIUS_M,
    MIN_SWARM_SIZE,
    cluster_tracks,
)
from threat.intent import infer_intent
from threat.scoring import _rank, _score_track

logger = logging.getLogger("overwatch.threat")


def _hostile_tracks(tracks: List[Track]) -> List[Track]:
    """Return only the tracks classified HOSTILE."""
    return [t for t in tracks if t.classification == TrackClass.HOSTILE]


def detect_swarms(
    tracks: List[Track],
    site: Site,
    timestamp: float,
    radius_m: float = DEFAULT_CLUSTER_RADIUS_M,
    min_size: int = MIN_SWARM_SIZE,
) -> List[Swarm]:
    """Cluster hostile tracks into swarms and fill in each swarm's intent.

    Returns swarms with intent inferred from member geometry and motion. Tracks
    not classified HOSTILE never form a swarm.
    """
    hostiles = _hostile_tracks(tracks)
    by_id: Dict[str, Track] = {t.id: t for t in hostiles}
    swarms = cluster_tracks(hostiles, timestamp, radius_m=radius_m, min_size=min_size)
    for swarm in swarms:
        members = [by_id[mid] for mid in swarm.member_track_ids if mid in by_id]
        swarm.intent = infer_intent(swarm, members, site)
    return swarms


def assess(
    tracks: List[Track],
    site: Site,
    timestamp: float,
    radius_m: float = DEFAULT_CLUSTER_RADIUS_M,
    min_size: int = MIN_SWARM_SIZE,
) -> List[Threat]:
    """Classify and prioritize hostile tracks into ranked Threat objects.

    Each hostile track becomes one Threat so effectors can be assigned to
    individual airframes, which is what an area effect needs and what keeps the
    detection-to-engagement lineage intact. Swarms and their intent are still
    inferred for situational awareness and stamped onto each member threat as
    swarm_id context. The returned list is ordered most urgent first, with a
    1-based priority_rank. track_id is always set, swarm_id is optional context.
    """
    hostiles = _hostile_tracks(tracks)
    if not hostiles:
        return []

    swarms = detect_swarms(tracks, site, timestamp, radius_m=radius_m, min_size=min_size)
    swarm_of: Dict[str, str] = {}
    for swarm in swarms:
        for mid in swarm.member_track_ids:
            swarm_of[mid] = swarm.id

    threats: List[Threat] = []
    for track in hostiles:
        threat = _score_track(track, site)
        threat.swarm_id = swarm_of.get(track.id)
        threats.append(threat)

    ranked = _rank(threats)
    logger.info(
        "assessed %d hostile tracks into %d threats (%d swarms for SA)",
        len(hostiles),
        len(ranked),
        len(swarms),
    )
    return ranked
