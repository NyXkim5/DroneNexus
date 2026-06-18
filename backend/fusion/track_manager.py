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
position and a covariance. The cost blends the squared Mahalanobis position
distance with a velocity-mismatch term so a target keeps its identity through a
crossing. Pairs beyond the gate are forbidden. We then take the cheapest valid
pairs greedily, one detection per track and one track per detection. Greedy
global-nearest-neighbor is near-optimal for well-separated contacts and stays
fast at swarm scale. A coarse spatial pre-filter built on a plain Python spatial
hash (see _SpatialGrid) keeps the candidate set sparse so we never score every
track against every detection.

Lifecycle
---------
A new detection that matches nothing seeds a tentative track. We keep a sliding
window of recent association outcomes per track and confirm on N hits out of the
last M ticks, so steady real targets confirm and one-off clutter never does. A
track that misses updates coasts on its predicted state with growing covariance.
A confirmed track that stops associating is demoted, and after a coast timeout it
expires and is dropped. This gives identity stability under noise, rejects false
tracks from clutter, and behaves gracefully under sensor dropout.

Designed for 1000-plus simultaneous contacts. Association cost is bounded by the
spatial gate, not by the full track-by-detection product.
"""
from __future__ import annotations

import itertools
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import floor
from typing import Deque, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from csontology import Detection, Track, TrackClass, Vec3
from fusion.kalman import ConstantVelocityKalman

logger = logging.getLogger("overwatch.fusion")

# Cap on the position-history ring stored on each Track for the HUD trail.
_MAX_HISTORY = 64


class _SpatialGrid:
    """Uniform spatial hash over 3D positions for near-linear neighbor lookup.

    Cells are cubes of side cell_m. Insert points by index, then query the 27
    cells around a position to get candidate neighbors. This replaces the dense
    detection-by-track scan so gating and clustering stay linear at swarm scale.
    """

    def __init__(self, cell_m: float) -> None:
        self._cell = max(1.0, cell_m)
        self._buckets: Dict[Tuple[int, int, int], List[int]] = defaultdict(list)
        self._points: List[Vec3] = []

    def insert(self, index: int, pos: Vec3) -> None:
        """Store a point index under its cell key."""
        self._points.append(pos)
        self._buckets[self._key(pos)].append(index)

    def neighbors(self, pos: Vec3) -> Iterable[int]:
        """Yield indices in the 27 cells around pos."""
        cx, cy, cz = self._key(pos)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    yield from self._buckets.get((cx + dx, cy + dy, cz + dz), ())

    def _key(self, pos: Vec3) -> Tuple[int, int, int]:
        """Integer cell coordinates for a position."""
        c = self._cell
        return (int(floor(pos[0] / c)), int(floor(pos[1] / c)), int(floor(pos[2] / c)))


@dataclass
class _Measurement:
    """One fused observation built from co-located detections this tick.

    Multiple sensors that see the same physical object produce detections within
    a small radius. We collapse those into a single measurement before track
    association so one object yields one track, not one track per sensor.
    """

    position: Vec3
    velocity: Vec3
    confidence: float
    source_ids: List[str]


@dataclass(frozen=True)
class FusionConfig:
    """Tuning knobs for the fusion engine.

    gate_chi2 is the chi-square gate on squared Mahalanobis distance with three
    degrees of freedom. The default 11.345 is the 0.99 quantile, so a true
    measurement falls inside the gate 99 percent of the time. coast_timeout_s is
    how long a track survives with no confirming detection. meas_sigma_m is the
    assumed sensor position noise in meters when a Detection carries none.

    Confirmation uses N-of-M. confirm_window is M, the number of recent ticks we
    remember per track. confirm_hits is N, the hits within that window that
    promote a tentative track. A confirmed track that drops below N hits in the
    window is demoted, so clutter that flickers once never sticks.

    Merge fires only on true duplicates. merge_radius_m is a tight co-location
    radius (about two measurement sigmas) and merge_overlap_ticks is how many
    consecutive ticks two tracks must stay co-located before we collapse them.
    velocity_cost_w weights the velocity-mismatch term in the association cost so
    a target keeps its identity through a crossing.
    """

    gate_chi2: float = 11.345
    coast_timeout_s: float = 3.0
    confirm_hits: int = 3
    confirm_window: int = 5
    meas_sigma_m: float = 5.0
    init_pos_sigma_m: float = 25.0
    init_vel_sigma_m: float = 15.0
    process_noise: float = 4.0
    gate_radius_m: float = 150.0
    confidence_gain: float = 0.4
    confidence_decay: float = 0.3
    cluster_radius_m: float = 60.0
    dup_radius_m: float = 50.0
    merge_radius_m: float = 10.0
    merge_vel_ms: float = 10.0
    merge_overlap_ticks: int = 3
    velocity_cost_w: float = 0.05


@dataclass
class _TrackEntry:
    """Internal bookkeeping that wraps a public Track with its filter.

    window holds the recent per-tick association outcomes, newest last, capped at
    confirm_window. hits is the lifetime association count used only as a merge
    tie-breaker. overlaps counts consecutive ticks this track stayed co-located
    with another track, keyed by that track id, and drives the merge decision.
    """

    track: Track
    kalman: ConstantVelocityKalman
    hits: int = 0
    confirmed: bool = False
    last_seen: float = 0.0
    history: List[Vec3] = field(default_factory=list)
    window: Deque[bool] = field(default_factory=deque)
    overlaps: Dict[str, int] = field(default_factory=dict)


class TrackManager:
    """Maintains fused Track objects from multi-sensor Detection streams.

    Thread-model: single-owner. One caller drives update() and predict() in
    sequence. The manager holds no async state of its own, so it slots behind any
    SensorSource pump loop. All positions and velocities are ENU meters and m/s.
    """

    def __init__(
        self,
        config: Optional[FusionConfig] = None,
        association_method: str = "gnn",
    ) -> None:
        if association_method not in ("gnn", "hungarian"):
            raise ValueError(
                f"association_method must be 'gnn' or 'hungarian', got {association_method!r}"
            )
        self._config = config or FusionConfig()
        self._association_method = association_method
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
        measurements = self._fuse_detections(detections)
        matches, unmatched = self._associate(measurements)
        matched_ids = {track_id for track_id, _ in matches}
        self._apply_matches(matches, measurements, now)
        self._record_outcomes(matched_ids)
        fresh = self._reject_duplicates(measurements, unmatched)
        self._spawn_tracks(fresh, now)
        self._merge_duplicates()
        self._expire(now)
        self._last_now = now
        return self.tracks()

    def _record_outcomes(self, matched_ids: set[str]) -> None:
        """Push each existing track's hit or miss this tick and re-confirm.

        Every track that lived into this tick gets one window entry: a hit if it
        associated, a miss otherwise. Tracks spawned later this tick are not yet
        present, so they record their seed hit in _spawn_tracks instead. A track
        is confirmed exactly when it has N hits in the last M ticks.
        """
        for track_id, entry in self._entries.items():
            self._push_window(entry, track_id in matched_ids)
            self._reevaluate_confirm(entry)

    def _push_window(self, entry: _TrackEntry, hit: bool) -> None:
        """Append one association outcome, capped to the confirm window length."""
        entry.window.append(hit)
        while len(entry.window) > self._config.confirm_window:
            entry.window.popleft()

    def _reevaluate_confirm(self, entry: _TrackEntry) -> None:
        """Confirm or demote a track on N hits within the last M ticks."""
        entry.confirmed = sum(entry.window) >= self._config.confirm_hits

    def _merge_duplicates(self) -> None:
        """Collapse only true-duplicate track pairs, never tight formations.

        One physical object can briefly seed two tracks before suppression kicks
        in. A real second drone in formation is a distinct object even at close
        spacing, so proximity alone must not merge. We require both very close
        co-location, within merge_radius_m (about two measurement sigmas, well
        under formation spacing), and persistent overlap across merge_overlap_ticks
        consecutive ticks. Only then do we keep the better-supported track and
        fold the other one's lineage into it.
        """
        entries = list(self._entries.items())
        if len(entries) < 2:
            return
        grid = _SpatialGrid(self._config.merge_radius_m)
        positions = [e.kalman.position for _tid, e in entries]
        for idx, pos in enumerate(positions):
            grid.insert(idx, pos)
        radius_sq = self._config.merge_radius_m**2
        seen_pairs = self._tally_overlaps(entries, positions, grid, radius_sq)
        dead: set[str] = set()
        for i, (tid_i, _ent_i) in enumerate(entries):
            if tid_i in dead:
                continue
            self._merge_neighbors(i, entries, positions, grid, radius_sq, dead)
        self._prune_overlaps(entries, seen_pairs, dead)
        for tid in dead:
            del self._entries[tid]

    def _tally_overlaps(
        self,
        entries: List[Tuple[str, _TrackEntry]],
        positions: List[Vec3],
        grid: _SpatialGrid,
        radius_sq: float,
    ) -> set:
        """Bump the consecutive-overlap counter for every co-located pair.

        Returns the set of track-id pairs that overlap this tick so callers can
        reset stale counters for pairs that drifted apart.
        """
        seen: set = set()
        for i, (tid_i, ent_i) in enumerate(entries):
            for j in set(grid.neighbors(positions[i])):
                if j <= i:
                    continue
                if _dist_sq(positions[i], positions[j]) > radius_sq:
                    continue
                tid_j = entries[j][0]
                ent_i.overlaps[tid_j] = ent_i.overlaps.get(tid_j, 0) + 1
                seen.add((tid_i, tid_j))
        return seen

    def _prune_overlaps(
        self,
        entries: List[Tuple[str, _TrackEntry]],
        seen_pairs: set,
        dead: set,
    ) -> None:
        """Reset overlap counters for pairs that did not co-locate this tick."""
        for tid_i, ent_i in entries:
            stale = [
                other for other in ent_i.overlaps
                if (tid_i, other) not in seen_pairs or other in dead
            ]
            for other in stale:
                del ent_i.overlaps[other]

    def _merge_neighbors(
        self,
        i: int,
        entries: List[Tuple[str, _TrackEntry]],
        positions: List[Vec3],
        grid: _SpatialGrid,
        radius_sq: float,
        dead: set,
    ) -> None:
        """Merge any later track that truly duplicates entry i into the survivor."""
        need = self._config.merge_overlap_ticks
        for j in set(grid.neighbors(positions[i])):
            if j <= i:
                continue
            tid_j, ent_j = entries[j]
            if tid_j in dead or entries[i][0] in dead:
                continue
            if _dist_sq(positions[i], positions[j]) > radius_sq:
                continue
            if _vel_diff(entries[i][1].kalman.velocity, ent_j.kalman.velocity) > self._config.merge_vel_ms:
                continue
            if entries[i][1].overlaps.get(tid_j, 0) < need:
                continue
            self._fold_track(entries[i][1], ent_j, dead, tid_j, entries[i][0])

    def _fold_track(
        self, a: _TrackEntry, b: _TrackEntry, dead: set, tid_b: str, tid_a: str,
    ) -> None:
        """Keep the better-supported of two tracks and retire the other."""
        keep, drop, drop_id = (a, b, tid_b) if a.hits >= b.hits else (b, a, tid_a)
        keep.track.source_detection_ids.extend(drop.track.source_detection_ids)
        if len(keep.track.source_detection_ids) > _MAX_HISTORY:
            del keep.track.source_detection_ids[:-_MAX_HISTORY]
        keep.hits = max(keep.hits, drop.hits)
        dead.add(drop_id)

    def _reject_duplicates(
        self, measurements: List[_Measurement], unmatched: List[int],
    ) -> List[_Measurement]:
        """Drop unmatched measurements that sit on top of an existing track.

        Per-tick clustering is imperfect, so one object can yield a main cluster
        plus a stray that lost the greedy match. Without this guard each stray
        seeds a duplicate track. A measurement within dup_radius_m of any current
        track is treated as a duplicate of a tracked object and not spawned.
        """
        if not unmatched or not self._entries:
            return [measurements[i] for i in unmatched]
        grid = _SpatialGrid(self._config.dup_radius_m)
        positions = [e.kalman.position for e in self._entries.values()]
        for ti, pos in enumerate(positions):
            grid.insert(ti, pos)
        radius_sq = self._config.dup_radius_m**2
        fresh: List[_Measurement] = []
        for i in unmatched:
            meas = measurements[i]
            near = any(
                _dist_sq(positions[ti], meas.position) <= radius_sq
                for ti in set(grid.neighbors(meas.position))
            )
            if not near:
                fresh.append(meas)
        return fresh

    def _fuse_detections(self, detections: List[Detection]) -> List[_Measurement]:
        """Collapse co-located detections into one measurement per object.

        Sensors overlap, so the same drone is seen several times per tick. We
        single-link cluster detections within cluster_radius_m using a spatial
        grid, then fuse each cluster into one confidence-weighted measurement.
        This is what stops one object from spawning one track per sensor.
        """
        if not detections:
            return []
        clusters = self._cluster_detections(detections)
        return [self._fuse_cluster([detections[i] for i in idxs]) for idxs in clusters]

    def _cluster_detections(self, detections: List[Detection]) -> List[List[int]]:
        """Group cross-sensor detections of one object, keep distinct objects apart.

        A single sensor reports each physical object at most once per tick, so two
        detections from the same sensor are always two different objects and must
        never share a cluster. We grow each cluster around a seed detection,
        admitting nearby detections only from sensors the cluster has not used.
        This separates distinct drones even when they fall inside one radius, as
        long as a sensor resolves them, while still fusing genuine duplicates.
        """
        grid = _SpatialGrid(self._config.cluster_radius_m)
        seeds: List[Vec3] = []
        members: List[List[int]] = []
        used_sensors: List[set] = []
        radius_sq = self._config.cluster_radius_m**2
        order = sorted(
            range(len(detections)), key=lambda i: -detections[i].confidence,
        )
        for i in order:
            det = detections[i]
            ci = self._best_cluster(det, grid, seeds, used_sensors, radius_sq)
            if ci is None:
                grid.insert(len(seeds), det.position)
                seeds.append(det.position)
                members.append([i])
                used_sensors.append({det.sensor_id})
            else:
                members[ci].append(i)
                used_sensors[ci].add(det.sensor_id)
        return members

    def _best_cluster(
        self,
        det: Detection,
        grid: _SpatialGrid,
        seeds: List[Vec3],
        used_sensors: List[set],
        radius_sq: float,
    ) -> Optional[int]:
        """Nearest seeded cluster within radius that has not used this sensor."""
        best: Optional[int] = None
        best_d2 = radius_sq
        for ci in set(grid.neighbors(det.position)):
            if det.sensor_id in used_sensors[ci]:
                continue
            d2 = _dist_sq(seeds[ci], det.position)
            if d2 <= best_d2:
                best_d2 = d2
                best = ci
        return best

    def _fuse_cluster(self, group: List[Detection]) -> _Measurement:
        """Confidence-weighted fusion of one cluster into a single measurement.

        Position and velocity are weighted by detection confidence so a sharp
        sensor pulls harder. Fused confidence is a probabilistic OR across the
        independent looks, so more sensors seeing an object raise certainty.
        """
        weights = [max(0.05, d.confidence) for d in group]
        total = sum(weights)
        pos = _weighted_vec([d.position for d in group], weights, total)
        vel = _weighted_vec([d.velocity for d in group], weights, total)
        miss = 1.0
        for d in group:
            miss *= 1.0 - max(0.0, min(1.0, d.confidence))
        return _Measurement(
            position=pos,
            velocity=vel,
            confidence=1.0 - miss,
            source_ids=[d.id for d in group],
        )

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
        self, measurements: List[_Measurement],
    ) -> Tuple[List[Tuple[str, int]], List[int]]:
        """Match fused measurements to tracks by the configured association method.

        Returns a list of (track_id, measurement_index) matches and the indices
        of unmatched measurements. Dispatches to either gated greedy nearest
        neighbor (gnn) or globally optimal Hungarian assignment (hungarian)
        depending on self._association_method.
        """
        if not measurements:
            return [], []
        entries = list(self._entries.values())
        if not entries:
            return [], list(range(len(measurements)))
        if self._association_method == "hungarian":
            return self._hungarian_associate(measurements, entries)
        candidates = self._gate_pairs(measurements, entries)
        return self._resolve_greedy(candidates, len(measurements))

    def _gate_pairs(
        self, measurements: List[_Measurement], entries: List[_TrackEntry],
    ) -> List[Tuple[float, str, int]]:
        """Build scored (cost, track_id, meas_index) pairs that pass both gates.

        A spatial grid keyed on the gate radius prunes far pairs in near-linear
        time. Survivors get an exact Mahalanobis score on the nominal sensor
        sigma and the chi-square gate. We gate on the nominal sigma, not the
        confidence-inflated one, so low-confidence clutter cannot widen its own
        gate and steal a real measurement. Confidence scaling stays in the Kalman
        update. The reported cost adds a velocity-mismatch term so a target keeps
        its identity through a crossing where two positions nearly coincide.
        """
        grid = _SpatialGrid(self._config.gate_radius_m)
        gate_sigma = self._config.meas_sigma_m
        # Precompute each track's predicted position and inverse innovation
        # covariance once. Both are measurement-independent at the nominal gate
        # sigma, so this turns a per-pair linear solve into one inverse per track
        # plus a cheap matrix product per candidate. Each entry sits in exactly
        # one grid cell, so grid.neighbors yields it at most once and no dedupe is
        # needed. The candidate scan order is unchanged, so the greedy resolver
        # breaks ties identically and the matches are the same as before.
        positions: List[Vec3] = []
        inv_covs: List[np.ndarray] = []
        for ei, entry in enumerate(entries):
            pos = entry.kalman.position
            positions.append(pos)
            inv_covs.append(entry.kalman.gate_inverse(gate_sigma))
            grid.insert(ei, pos)
        radius_sq = self._config.gate_radius_m**2
        cand_e, cand_m = self._candidate_pairs(measurements, positions, grid, radius_sq)
        if not cand_e:
            return []
        return self._score_candidates(measurements, entries, positions, inv_covs, cand_e, cand_m)

    def _candidate_pairs(
        self,
        measurements: List[_Measurement],
        positions: List[Vec3],
        grid: "_SpatialGrid",
        radius_sq: float,
    ) -> Tuple[List[int], List[int]]:
        """Collect (entry_index, meas_index) pairs surviving the radius prefilter.

        Scan order is measurement-major then grid-neighbor, the same order the
        per-pair loop used, so downstream tie-breaking is unchanged.
        """
        cand_e: List[int] = []
        cand_m: List[int] = []
        for mi, meas in enumerate(measurements):
            mpos = meas.position
            for ei in grid.neighbors(mpos):
                if _dist_sq(positions[ei], mpos) <= radius_sq:
                    cand_e.append(ei)
                    cand_m.append(mi)
        return cand_e, cand_m

    def _score_candidates(
        self,
        measurements: List[_Measurement],
        entries: List[_TrackEntry],
        positions: List[Vec3],
        inv_covs: List[np.ndarray],
        cand_e: List[int],
        cand_m: List[int],
    ) -> List[Tuple[float, str, int]]:
        """Batch-score candidate pairs and emit those inside the chi-square gate.

        The Mahalanobis gate and the velocity-mismatch term are computed as
        vectorized numpy ops over all candidate pairs at once, which removes the
        per-pair Python and numpy-call overhead. Survivors are emitted in the
        original scan order so the greedy resolver breaks ties identically and the
        matches match the per-pair implementation.
        """
        ce = np.asarray(cand_e, dtype=np.intp)
        cm = np.asarray(cand_m, dtype=np.intp)
        pos_arr = np.asarray(positions, dtype=np.float64)
        sinv_arr = np.asarray(inv_covs, dtype=np.float64)
        meas_pos = np.array([m.position for m in measurements], dtype=np.float64)
        meas_vel = np.array([m.velocity for m in measurements], dtype=np.float64)
        ent_vel = np.array([e.kalman.velocity for e in entries], dtype=np.float64)
        innov = meas_pos[cm] - pos_arr[ce]
        gate_cost = np.einsum("ki,kij,kj->k", innov, sinv_arr[ce], innov)
        vel_err = np.einsum("ki,ki->k", meas_vel[cm] - ent_vel[ce], meas_vel[cm] - ent_vel[ce])
        cost = gate_cost + self._config.velocity_cost_w * vel_err
        survivors = np.nonzero(gate_cost <= self._config.gate_chi2)[0]
        pairs: List[Tuple[float, str, int]] = []
        for k in survivors:
            pairs.append((float(cost[k]), entries[ce[k]].track.id, int(cm[k])))
        return pairs

    def _resolve_greedy(
        self,
        candidates: List[Tuple[float, str, int]],
        n_measurements: int,
    ) -> Tuple[List[Tuple[str, int]], List[int]]:
        """Pick cheapest non-conflicting pairs greedily, one per track and meas."""
        candidates.sort(key=lambda c: c[0])
        used_tracks: set[str] = set()
        used_meas: set[int] = set()
        matches: List[Tuple[str, int]] = []
        for _cost, track_id, meas_index in candidates:
            if track_id in used_tracks or meas_index in used_meas:
                continue
            used_tracks.add(track_id)
            used_meas.add(meas_index)
            matches.append((track_id, meas_index))
        unmatched = [i for i in range(n_measurements) if i not in used_meas]
        return matches, unmatched

    def _hungarian_associate(
        self,
        measurements: List[_Measurement],
        entries: List[_TrackEntry],
    ) -> Tuple[List[Tuple[str, int]], List[int]]:
        """Match measurements to tracks via globally optimal Hungarian assignment.

        Builds a full (n_tracks x n_measurements) cost matrix of blended
        Mahalanobis-plus-velocity cost. Entries that fail the chi-square gate are
        set to a large sentinel (1e9) so the solver never selects them. After
        linear_sum_assignment resolves the optimal permutation we discard any
        assigned pair whose cost still equals the sentinel, which happens when a
        track has no gated candidate at all. The return signature matches
        _resolve_greedy: matched pairs and a list of unmatched measurement indices.
        """
        _SENTINEL = 1e9
        gate_chi2 = self._config.gate_chi2
        gate_sigma = self._config.meas_sigma_m
        vel_w = self._config.velocity_cost_w

        n_tracks = len(entries)
        n_meas = len(measurements)

        meas_pos = np.array([m.position for m in measurements], dtype=np.float64)
        meas_vel = np.array([m.velocity for m in measurements], dtype=np.float64)

        cost_matrix = np.full((n_tracks, n_meas), _SENTINEL, dtype=np.float64)

        for ti, entry in enumerate(entries):
            inv_cov = entry.kalman.gate_inverse(gate_sigma)
            track_pos = np.array(entry.kalman.position, dtype=np.float64)
            track_vel = np.array(entry.kalman.velocity, dtype=np.float64)
            innov = meas_pos - track_pos
            gate_cost = np.einsum("mi,ij,mj->m", innov, inv_cov, innov)
            vel_err = np.sum((meas_vel - track_vel) ** 2, axis=1)
            total_cost = gate_cost + vel_w * vel_err
            mask = gate_cost <= gate_chi2
            cost_matrix[ti, mask] = total_cost[mask]

        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        matches: List[Tuple[str, int]] = []
        matched_meas: set[int] = set()
        for ti, mi in zip(row_ind, col_ind):
            if cost_matrix[ti, mi] >= _SENTINEL:
                continue
            matches.append((entries[ti].track.id, int(mi)))
            matched_meas.add(int(mi))

        unmatched = [i for i in range(n_meas) if i not in matched_meas]
        return matches, unmatched

    def _apply_matches(
        self,
        matches: List[Tuple[str, int]],
        measurements: List[_Measurement],
        now: float,
    ) -> None:
        """Correct each matched track with its measurement and bump confidence.

        Confirmation is decided later in _record_outcomes from the N-of-M window,
        so here we only fold the measurement, bump the lifetime hit count used as
        a merge tie-breaker, and raise confidence.
        """
        for track_id, meas_index in matches:
            entry = self._entries[track_id]
            meas = measurements[meas_index]
            entry.kalman.update(meas.position, self._meas_sigma(meas))
            entry.hits += 1
            entry.last_seen = now
            self._record_source(entry, meas)
            self._raise_confidence(entry)
            self._sync_track(entry, now)

    def _spawn_tracks(self, measurements: List[_Measurement], now: float) -> None:
        """Seed a tentative track for each unmatched measurement."""
        for meas in measurements:
            track_id = f"trk-{next(self._id_counter)}"
            kalman = ConstantVelocityKalman(
                position=meas.position,
                velocity=meas.velocity,
                pos_sigma=self._config.init_pos_sigma_m,
                vel_sigma=self._config.init_vel_sigma_m,
                process_noise=self._config.process_noise,
            )
            track = Track(
                id=track_id,
                position=meas.position,
                velocity=meas.velocity,
                covariance=kalman.position_sigma,
                last_update=now,
                confidence=meas.confidence * self._config.confidence_gain,
                source_detection_ids=list(meas.source_ids),
            )
            entry = _TrackEntry(
                track=track, kalman=kalman, hits=1, last_seen=now,
                window=deque([True]),
            )
            entry.history.append(meas.position)
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

    def _record_source(self, entry: _TrackEntry, meas: _Measurement) -> None:
        """Append the fused contributing detection ids, bounded to recent."""
        ids = entry.track.source_detection_ids
        ids.extend(meas.source_ids)
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

    def _meas_sigma(self, meas: _Measurement) -> float:
        """Measurement stddev for a fused measurement, scaled by its confidence.

        A high-confidence contact gets a tighter sigma so it pulls the estimate
        harder. We clamp confidence away from zero to avoid an infinite sigma.
        """
        conf = max(0.05, min(1.0, meas.confidence))
        return self._config.meas_sigma_m / conf

    def classify_track(self, track_id: str, label: TrackClass) -> None:
        """Set the hostility classification on a track the threat layer owns.

        Fusion does not decide hostility. It exposes this hook so the threat
        classifier can stamp a class onto a fused track by id. Raises KeyError if
        the track is unknown.
        """
        self._entries[track_id].track.classification = label


def _dist_sq(a: Vec3, b: Vec3) -> float:
    """Squared Euclidean distance between two ENU points."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return dx * dx + dy * dy + dz * dz


def _vel_diff(a: Vec3, b: Vec3) -> float:
    """Euclidean speed difference between two velocity vectors in m/s."""
    return _dist_sq(a, b) ** 0.5


def _weighted_vec(vecs: List[Vec3], weights: List[float], total: float) -> Vec3:
    """Confidence-weighted average of 3D vectors with a guarded denominator."""
    if total <= 0.0:
        total = float(len(vecs)) or 1.0
        weights = [1.0] * len(vecs)
    sx = sum(v[0] * w for v, w in zip(vecs, weights))
    sy = sum(v[1] * w for v, w in zip(vecs, weights))
    sz = sum(v[2] * w for v, w in zip(vecs, weights))
    return (sx / total, sy / total, sz / total)
