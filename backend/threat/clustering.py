"""
Spatial clustering of hostile tracks into swarms.

Groups tracks that sit close together in the ENU plane into one Swarm object.
The method is single-link distance clustering with a fixed neighbor radius. It
is the swarm-side inverse of the friendly formation logic in swarm/formations.py.
We do not need fast big-data clustering here. Site scale is a few hundred tracks.
"""
from __future__ import annotations

import math
from typing import Dict, List

from csontology import Swarm, SwarmIntent, Track, Vec3

# Tracks within this horizontal distance link into the same swarm.
DEFAULT_CLUSTER_RADIUS_M = 60.0
# A swarm needs at least this many members. Smaller groups stay singletons.
MIN_SWARM_SIZE = 2


def horizontal_distance(a: Vec3, b: Vec3) -> float:
    """Return the ENU horizontal (x, y) distance in meters between two points."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.hypot(dx, dy)


def centroid_of(positions: List[Vec3]) -> Vec3:
    """Return the mean position of a non-empty list of ENU points."""
    n = float(len(positions))
    sx = sum(p[0] for p in positions)
    sy = sum(p[1] for p in positions)
    sz = sum(p[2] for p in positions)
    return (sx / n, sy / n, sz / n)


def _link_indices(tracks: List[Track], radius_m: float) -> List[List[int]]:
    """Group track indices by single-link connectivity within radius_m.

    Uses union-find so a chain of nearby tracks forms one cluster. A horizontal
    spatial-hash grid keyed on the link radius restricts each track to candidates
    in its own and adjacent cells, so connectivity is found in near-linear time
    rather than the all-pairs product. Any two tracks within radius_m fall in
    adjacent cells, so this yields the identical clusters as the dense scan.
    """
    parent = list(range(len(tracks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    cell = max(1.0, radius_m)
    buckets: Dict[tuple, List[int]] = {}
    keys: List[tuple] = []
    for i, track in enumerate(tracks):
        key = (int(track.position[0] // cell), int(track.position[1] // cell))
        keys.append(key)
        buckets.setdefault(key, []).append(i)

    for i, track in enumerate(tracks):
        ix, iy = keys[i]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((ix + dx, iy + dy), ()):
                    if j <= i:
                        continue
                    if horizontal_distance(track.position, tracks[j].position) <= radius_m:
                        union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(len(tracks)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def cluster_tracks(
    tracks: List[Track],
    timestamp: float,
    radius_m: float = DEFAULT_CLUSTER_RADIUS_M,
    min_size: int = MIN_SWARM_SIZE,
) -> List[Swarm]:
    """Cluster hostile tracks into Swarm objects by spatial proximity.

    Only groups meeting min_size become swarms. Singletons are left out and the
    caller scores them as individual track threats. Intent starts UNKNOWN here.
    The intent module fills it in from geometry and motion.
    """
    if not tracks:
        return []

    swarms: List[Swarm] = []
    for group in _link_indices(tracks, radius_m):
        if len(group) < min_size:
            continue
        members = [tracks[i] for i in group]
        positions = [m.position for m in members]
        first_seen = min(m.last_update - m.age for m in members)
        swarms.append(
            Swarm(
                id=_swarm_id(members),
                member_track_ids=[m.id for m in members],
                centroid=centroid_of(positions),
                formation="UNKNOWN",
                intent=SwarmIntent.UNKNOWN,
                size=len(members),
                first_seen=first_seen,
            )
        )
    return swarms


def _swarm_id(members: List[Track]) -> str:
    """Build a stable swarm id from its sorted member ids."""
    return "swarm-" + "-".join(sorted(m.id for m in members))
