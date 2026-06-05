"""
TrackManager — the multi-sensor track fusion core.

It ingests Detection events from any number of sensors and maintains a stable
set of Track objects. Each update tick does three things in order: predict every
track forward to the current time, associate incoming detections to tracks by
gated nearest-neighbor matching, then correct matched tracks, spawn tracks for
unmatched detections, and coast or expire tracks that no sensor confirmed.

Association
-----------
We use global nearest-neighbor with a chi-square gate. Each track predicts a
position and a covariance. The squared Mahalanobis distance between a detection
and a track is the cost. Pairs beyond the gate are forbidden. We then take the
cheapest valid pairs greedily, one detection per track and one track per
detection. Greedy global-nearest-neighbor is near-optimal for well-separated
contacts and stays fast at swarm scale. A coarse spatial pre-filter with numpy
keeps the cost matrix sparse so we never score every track against every
detection.

Lifecycle
---------
A new detection that matches nothing seeds a tentative track. A track that keeps
getting hits gains confidence and is confirmed. A track that misses updates
coasts on its predicted state with growing covariance. After a coast timeout the
track expires and is dropped. This gives identity stability under noise and
graceful behavior under sensor dropout.

Designed for 1000-plus simultaneous contacts. Association cost is bounded by the
spatial gate, not by the full track-by-detection product.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from csontology import Detection, Track, TrackClass, Vec3
from fusion.kalman import ConstantVelocityKalman

logger = logging.getLogger("overwatch.fusion")

# Cap on the position-history ring stored on each Track for the HUD trail.
_MAX_HISTORY = 64


@dataclass(frozen=True)
class FusionConfig:
    """Tuning knobs for the fusion engine.

    gate_chi2 is the chi-square gate on squared Mahalanobis distance with three
    degrees of freedom. The default 11.345 is the 0.99 quantile, so a true
    measurement falls inside the gate 99 percent of the time. coast_timeout_s is
    how long a track survives with no confirming detection. confirm_hits is how
    many associations promote a tentative track to confirmed. meas_sigma_m is the
    assumed sensor position noise in meters when a Detection carries none.
    """

    gate_chi2: float = 11.345
    coast_timeout_s: float = 3.0
    confirm_hits: int = 3
    meas_sigma_m: float = 5.0
    init_pos_sigma_m: float = 25.0
    init_vel_sigma_m: float = 15.0
    process_noise: float = 4.0
    gate_radius_m: float = 150.0
    confidence_gain: float = 0.4
    confidence_decay: float = 0.3


@dataclass
class _TrackEntry:
    """Internal bookkeeping that wraps a public Track with its filter."""

    track: Track
    kalman: ConstantVelocityKalman
    hits: int = 0
    confirmed: bool = False
    last_seen: float = 0.0
    history: List[Vec3] = field(default_factory=list)


class TrackManager:
    """Maintains fused Track objects from multi-sensor Detection streams.

    Thread-model: single-owner. One caller drives update() and predict() in
    sequence. The manager holds no async state of its own, so it slots behind any
    SensorSource pump loop. All positions and velocities are ENU meters and m/s.
    """

    def __init__(self, config: Optional[FusionConfig] = None) -> None:
        self._config = config or FusionConfig()
        self._entries: Dict[str, _TrackEntry] = {}
        self._id_counter = itertools.count(1)
        self._last_now: Optional[float] = None

    @property
    def config(self) -> FusionConfig:
        """The active tuning configuration."""
        return self._config

    def tracks(self) -> List[Track]:
        """Return the current confirmed and tentative tracks."""
        return [e.track for e in self._entries.values()]

    def confirmed_tracks(self) -> List[Track]:
        """Return only tracks that have passed the confirmation threshold."""
        return [e.track for e in self._entries.values() if e.confirmed]

    def predict(self, now: float) -> List[Track]:
        """Advance every track to time now without consuming detections.

        Use this between sensor updates to keep a live picture. It moves each
        track along its velocity and grows its covariance. It does not expire
        tracks. Call update() to fold in new detections and run lifecycle.
        """
        self._advance_all(now)
        return self.tracks()

    def update(self, detections: List[Detection], now: float) -> List[Track]:
        """Fold detections into the track set at time now and return all tracks.

        Steps: predict tracks to now, associate detections by gated nearest
        neighbor, correct matched tracks, spawn tracks for the rest, then coast
        or expire unconfirmed tracks. Returns the full current track list.
        """
        self._advance_all(now)
        matches, unmatched = self._associate(detections)
        self._apply_matches(matches, detections, now)
        self._spawn_tracks([detections[i] for i in unmatched], now)
        self._expire(now)
        self._last_now = now
        return self.tracks()

    def _advance_all(self, now: float) -> None:
        """Predict every track filter forward to now and refresh its Track view."""
        for entry in self._entries.values():
            dt = self._step_dt(entry.last_seen, now)
            entry.kalman.predict(dt)
            self._sync_track(entry, now)

    def _step_dt(self, last_seen: float, now: float) -> float:
        """Non-negative time delta since a track was last advanced."""
        if self._last_now is not None:
            base = self._last_now
        else:
            base = last_seen
        return max(0.0, now - base)

    def _associate(
        self, detections: List[Detection],
    ) -> Tuple[List[Tuple[str, int]], List[int]]:
        """Match detections to tracks by gated greedy nearest neighbor.

        Returns a list of (track_id, detection_index) matches and the indices of
        unmatched detections. The spatial pre-filter restricts each detection to
        nearby tracks so the Mahalanobis scoring stays cheap at scale.
        """
        if not detections:
            return [], []
        entries = list(self._entries.values())
        if not entries:
            return [], list(range(len(detections)))
        candidates = self._gate_pairs(detections, entries)
        return self._resolve_greedy(candidates, entries, len(detections))

    def _gate_pairs(
        self, detections: List[Detection], entries: List[_TrackEntry],
    ) -> List[Tuple[float, str, int]]:
        """Build scored (cost, track_id, det_index) pairs that pass both gates.

        First a vectorized radius pre-filter prunes far pairs. Then the survivors
        get an exact Mahalanobis score and the chi-square gate. This two-stage
        gate is what keeps association near linear in contact count.
        """
        track_pos = np.array(
            [e.kalman.position for e in entries], dtype=np.float64,
        )
        det_pos = np.array([d.position for d in detections], dtype=np.float64)
        radius_sq = self._config.gate_radius_m**2
        pairs: List[Tuple[float, str, int]] = []
        for di in range(det_pos.shape[0]):
            diff = track_pos - det_pos[di]
            dist_sq = np.einsum("ij,ij->i", diff, diff)
            near = np.nonzero(dist_sq <= radius_sq)[0]
            self._score_near(near, entries, detections[di], di, pairs)
        return pairs

    def _score_near(
        self,
        near: np.ndarray,
        entries: List[_TrackEntry],
        detection: Detection,
        det_index: int,
        out: List[Tuple[float, str, int]],
    ) -> None:
        """Score gated track candidates for one detection and append to out."""
        sigma = self._meas_sigma(detection)
        for ti in near:
            entry = entries[int(ti)]
            cost = entry.kalman.mahalanobis_sq(detection.position, sigma)
            if cost <= self._config.gate_chi2:
                out.append((cost, entry.track.id, det_index))

    def _resolve_greedy(
        self,
        candidates: List[Tuple[float, str, int]],
        entries: List[_TrackEntry],
        n_detections: int,
    ) -> Tuple[List[Tuple[str, int]], List[int]]:
        """Pick cheapest non-conflicting pairs greedily, one per track and det."""
        candidates.sort(key=lambda c: c[0])
        used_tracks: set[str] = set()
        used_dets: set[int] = set()
        matches: List[Tuple[str, int]] = []
        for _cost, track_id, det_index in candidates:
            if track_id in used_tracks or det_index in used_dets:
                continue
            used_tracks.add(track_id)
            used_dets.add(det_index)
            matches.append((track_id, det_index))
        unmatched = [i for i in range(n_detections) if i not in used_dets]
        return matches, unmatched

    def _apply_matches(
        self,
        matches: List[Tuple[str, int]],
        detections: List[Detection],
        now: float,
    ) -> None:
        """Correct each matched track with its detection and bump confidence."""
        for track_id, det_index in matches:
            entry = self._entries[track_id]
            detection = detections[det_index]
            entry.kalman.update(detection.position, self._meas_sigma(detection))
            entry.hits += 1
            entry.last_seen = now
            if entry.hits >= self._config.confirm_hits:
                entry.confirmed = True
            self._record_source(entry, detection)
            self._raise_confidence(entry)
            self._sync_track(entry, now)

    def _spawn_tracks(self, detections: List[Detection], now: float) -> None:
        """Seed a tentative track for each unmatched detection."""
        for detection in detections:
            track_id = f"trk-{next(self._id_counter)}"
            kalman = ConstantVelocityKalman(
                position=detection.position,
                velocity=detection.velocity,
                pos_sigma=self._config.init_pos_sigma_m,
                vel_sigma=self._config.init_vel_sigma_m,
                process_noise=self._config.process_noise,
            )
            track = Track(
                id=track_id,
                position=detection.position,
                velocity=detection.velocity,
                covariance=kalman.position_sigma,
                last_update=now,
                confidence=detection.confidence * self._config.confidence_gain,
                source_detection_ids=[detection.id],
            )
            entry = _TrackEntry(
                track=track, kalman=kalman, hits=1, last_seen=now,
            )
            entry.history.append(detection.position)
            track.history = list(entry.history)
            self._entries[track_id] = entry

    def _expire(self, now: float) -> None:
        """Drop tracks that have coasted past the timeout with no confirmation."""
        timeout = self._config.coast_timeout_s
        dead = [
            tid for tid, e in self._entries.items()
            if (now - e.last_seen) > timeout
        ]
        for tid in dead:
            self._decay_to_death(self._entries[tid])
            del self._entries[tid]
        if dead:
            logger.debug("expired %d coasted tracks", len(dead))

    def _decay_to_death(self, entry: _TrackEntry) -> None:
        """Final confidence floor before a track is removed."""
        entry.track.confidence = 0.0

    def _record_source(self, entry: _TrackEntry, detection: Detection) -> None:
        """Append a contributing detection id, bounded to recent sources."""
        ids = entry.track.source_detection_ids
        ids.append(detection.id)
        if len(ids) > _MAX_HISTORY:
            del ids[:-_MAX_HISTORY]

    def _raise_confidence(self, entry: _TrackEntry) -> None:
        """Move track confidence toward 1.0 on a confirming hit."""
        gain = self._config.confidence_gain
        entry.track.confidence = min(
            1.0, entry.track.confidence + gain * (1.0 - entry.track.confidence),
        )

    def _sync_track(self, entry: _TrackEntry, now: float) -> None:
        """Refresh the public Track from the filter and decay if coasting."""
        track = entry.track
        track.position = entry.kalman.position
        track.velocity = entry.kalman.velocity
        track.covariance = entry.kalman.position_sigma
        track.age = now - self._birth_time(entry)
        if entry.last_seen < now:
            self._decay_confidence(entry, now)
        track.last_update = entry.last_seen
        self._append_history(entry)

    def _decay_confidence(self, entry: _TrackEntry, now: float) -> None:
        """Lower confidence in proportion to coast time since last hit."""
        coast = now - entry.last_seen
        factor = self._config.confidence_decay * coast
        entry.track.confidence = max(0.0, entry.track.confidence - factor)

    def _append_history(self, entry: _TrackEntry) -> None:
        """Keep a bounded position trail on the track for the HUD."""
        entry.history.append(entry.kalman.position)
        if len(entry.history) > _MAX_HISTORY:
            del entry.history[:-_MAX_HISTORY]
        entry.track.history = list(entry.history)

    def _birth_time(self, entry: _TrackEntry) -> float:
        """Earliest known time for a track, used to compute age."""
        return entry.track.last_update - entry.track.age

    def _meas_sigma(self, detection: Detection) -> float:
        """Measurement stddev for a detection, scaled by its confidence.

        A high-confidence contact gets a tighter sigma so it pulls the estimate
        harder. We clamp confidence away from zero to avoid an infinite sigma.
        """
        conf = max(0.05, min(1.0, detection.confidence))
        return self._config.meas_sigma_m / conf

    def classify_track(self, track_id: str, label: TrackClass) -> None:
        """Set the hostility classification on a track the threat layer owns.

        Fusion does not decide hostility. It exposes this hook so the threat
        classifier can stamp a class onto a fused track by id. Raises KeyError if
        the track is unknown.
        """
        self._entries[track_id].track.classification = label
