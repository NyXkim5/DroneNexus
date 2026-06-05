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
from typing import Dict, List, Set

from csontology import Site, Swarm, Threat, Track, TrackClass

from threat.clustering import (
    DEFAULT_CLUSTER_RADIUS_M,
    MIN_SWARM_SIZE,
    cluster_tracks,
)
from threat.intent import infer_intent
from threat.scoring import _rank, _score_swarm, _score_track

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

    timestamp is the shared wall-clock from csontology.now(). The returned list
    is ordered most urgent first, and each Threat carries its 1-based
    priority_rank. Exactly one of track_id or swarm_id is set per Threat.
    """
    hostiles = _hostile_tracks(tracks)
    if not hostiles:
        return []

    swarms = detect_swarms(tracks, site, timestamp, radius_m=radius_m, min_size=min_size)
    by_id: Dict[str, Track] = {t.id: t for t in hostiles}
    clustered_ids: Set[str] = set()

    threats: List[Threat] = []
    for swarm in swarms:
        members = [by_id[mid] for mid in swarm.member_track_ids if mid in by_id]
        clustered_ids.update(m.id for m in members)
        threats.append(_score_swarm(swarm, members, site))

    for track in hostiles:
        if track.id in clustered_ids:
            continue
        threats.append(_score_track(track, site))

    ranked = _rank(threats)
    logger.info(
        "assessed %d hostile tracks into %d threats (%d swarms)",
        len(hostiles),
        len(ranked),
        len(swarms),
    )
    return ranked
