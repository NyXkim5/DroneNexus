"""Multi-site federated defense network for OVERWATCH.

Coordinates defense across independent OVERWATCH instances so tracks, threat
warnings, and defender reinforcements flow between sites in near real time.
"""
from __future__ import annotations

import logging
import math
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from csontology import Defender, DefenderStatus, Track, Vec3

logger = logging.getLogger("overwatch.federation")

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"

DEFAULT_COVERAGE_M = 3000.0
MAX_LEND_FRACTION = 0.25


def _distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


@dataclass
class SiteNode:
    """One defended site in the federated network."""
    site_id: str
    position: Vec3
    defenders: List[Defender]
    tracks: List[Track] = field(default_factory=list)
    threat_level: str = GREEN
    last_update: float = 0.0
    coverage_m: float = DEFAULT_COVERAGE_M


@dataclass
class SharedTrack:
    track: Track
    source_site: str
    shared_at: float


@dataclass
class ReinforcementRequest:
    requesting_site: str
    threat_level: str
    timestamp: float
    fulfilled_by: Optional[str] = None
    defenders_sent: List[str] = field(default_factory=list)


@dataclass
class FederatedPicture:
    sites: Dict[str, SiteNode]
    merged_tracks: List[SharedTrack]
    active_handoffs: List[Tuple[str, str, str]]
    timestamp: float


class FederatedDefenseNetwork:
    """Coordinates multiple OVERWATCH sites into a unified defense picture."""

    def __init__(self) -> None:
        self.sites: Dict[str, SiteNode] = {}
        self._shared_tracks: Dict[str, SharedTrack] = {}
        self._handoff_log: List[Tuple[str, str, str]] = []
        self._reinforcement_log: List[ReinforcementRequest] = []

    def add_site(
        self, site_id: str, position: Vec3, defenders: List[Defender],
        coverage_m: float = DEFAULT_COVERAGE_M,
    ) -> None:
        """Register a new defended site."""
        self.sites[site_id] = SiteNode(
            site_id=site_id, position=position,
            defenders=list(defenders), coverage_m=coverage_m,
            last_update=_time.time(),
        )

    def remove_site(self, site_id: str) -> bool:
        """Remove a site. Returns True if it existed."""
        return self.sites.pop(site_id, None) is not None

    def share_track(self, source_site: str, track: Track) -> None:
        """Share a track from one site to all others."""
        if source_site not in self.sites:
            return
        self._shared_tracks[track.id] = SharedTrack(
            track=track, source_site=source_site, shared_at=_time.time(),
        )
        for sid, site in self.sites.items():
            if sid == source_site:
                continue
            if track.id not in {t.id for t in site.tracks}:
                site.tracks.append(track)

    def handoff_track(self, track: Track) -> Optional[Tuple[str, str]]:
        """Hand off tracking when a track crosses coverage. Returns (from, to) or None."""
        shared = self._shared_tracks.get(track.id)
        if shared is None:
            return None
        owner = shared.source_site
        owner_site = self.sites.get(owner)
        if owner_site is None:
            return None
        best_site, best_dist = owner, _distance(track.position, owner_site.position)
        for sid, site in self.sites.items():
            if sid == owner:
                continue
            d = _distance(track.position, site.position)
            if d < best_dist and d <= site.coverage_m:
                best_dist, best_site = d, sid
        if best_site != owner:
            self._shared_tracks[track.id] = SharedTrack(
                track=track, source_site=best_site, shared_at=_time.time(),
            )
            entry = (track.id, owner, best_site)
            self._handoff_log.append(entry)
            return owner, best_site
        return None

    def request_reinforcement(
        self, requesting_site: str, threat_level: str,
    ) -> Optional[ReinforcementRequest]:
        """Request defenders from the nearest GREEN neighbor."""
        req_site = self.sites.get(requesting_site)
        if req_site is None:
            return None
        req_site.threat_level = threat_level
        request = ReinforcementRequest(
            requesting_site=requesting_site,
            threat_level=threat_level, timestamp=_time.time(),
        )
        candidates = [
            (sid, s) for sid, s in self.sites.items()
            if sid != requesting_site and s.threat_level == GREEN
        ]
        if not candidates:
            self._reinforcement_log.append(request)
            return request
        candidates.sort(
            key=lambda p: _distance(p[1].position, req_site.position),
        )
        donor_id, donor = candidates[0]
        ready = [
            d for d in donor.defenders
            if d.status is DefenderStatus.READY and d.capacity > 0
        ]
        send_count = max(1, int(len(ready) * MAX_LEND_FRACTION))
        sent = ready[:send_count]
        if not sent:
            self._reinforcement_log.append(request)
            return request
        for d in sent:
            donor.defenders.remove(d)
            req_site.defenders.append(d)
        request.fulfilled_by = donor_id
        request.defenders_sent = [d.id for d in sent]
        self._reinforcement_log.append(request)
        return request

    def alert_site(
        self, alerting_site: str, target_site: str, track: Track,
    ) -> bool:
        """Alert a site about an inbound threat. Pushes track and elevates threat level."""
        target = self.sites.get(target_site)
        if target is None:
            return False
        self.share_track(alerting_site, track)
        if target.threat_level == GREEN:
            target.threat_level = YELLOW
        return True

    def get_common_operating_picture(self) -> FederatedPicture:
        """Merge all site tracks into one unified picture."""
        merged: Dict[str, SharedTrack] = {}
        now = _time.time()
        for tid, st in self._shared_tracks.items():
            merged[tid] = st
        for sid, site in self.sites.items():
            site.last_update = now
            for track in site.tracks:
                if track.id not in merged:
                    merged[track.id] = SharedTrack(
                        track=track, source_site=sid, shared_at=now,
                    )
        return FederatedPicture(
            sites=dict(self.sites),
            merged_tracks=list(merged.values()),
            active_handoffs=list(self._handoff_log),
            timestamp=now,
        )
