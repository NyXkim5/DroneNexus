"""
WargameRunner — the end-to-end counter-swarm tick loop.

Each tick runs the full pipeline in order:
  attacker.advance -> sensors emit detections -> fusion.update -> threat.assess
  -> defense.allocate + resolve -> update world model -> emit a Frame.

The runner drives a SimSensorSource for the sensor layer but owns the clock. It
collects all detections the source produced over one tick interval, folds them
into the fusion engine, classifies confirmed tracks as hostile so the threat
layer can score them, prioritizes threats, allocates ready defenders, resolves
outcomes against the cost ledger, and snapshots a Frame.

The loop is an async generator of Frame so both the CLI and the websocket consume
it the same way. It stops when max_ticks is reached or every hostile is gone.

All positions and velocities are ENU meters and m/s about the site origin.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, List, Optional

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Detection,
    Engagement,
    EngagementStatus,
    Threat,
    Track,
    TrackClass,
    Vec3,
)
from defense import LayeredAllocator
from sensors.base import SensorSource
from sensors.sim_source import SimSensorSource
import threat as threat_module

from vision.detector import SimDetector
from vision.feed_source import SimFeedSource
from vision.cascade import CascadeEngine, DependencyEdge
from decision.engine import DecisionEngine
from decision.models import EngagementMode

from wargame.audit import AuditLog
from wargame.degradation import DegradationModel

from wargame.frame import Frame, Metrics, assignment_lines
from wargame.scenario import Scenario
from wargame.world import WorldModel, build_sensor_specs, build_world

logger = logging.getLogger("overwatch.wargame")

# Lethal radius in meters for a kinetic point effector. A drone must be within
# this of the threat position for the shot to connect, so a shot at empty
# airspace kills nothing and lets the drone leak.
_POINT_KILL_RADIUS_M = 80.0


def _resistance(kind: DefenderKind, drone) -> float:
    """How well a target survives one effector kind, as a kill-probability scale.

    Jam-resistant drones defeat EW and other soft-kill RF entirely. Hardened
    drones largely shrug off high-power microwave. Kinetic effectors, including
    interceptors, nets, and lasers, work regardless, which is why the expensive
    kinetic layer is the answer to a hardened, jam-resistant raid.
    """
    if kind in (DefenderKind.EW, DefenderKind.JAMMER) and drone.ew_resistant:
        return 0.0
    if kind is DefenderKind.HPM and drone.hardened:
        return 0.15
    return 1.0


class WargameRunner:
    """Drives one counter-swarm wargame from a Scenario to completion."""

    def __init__(
        self,
        scenario: Scenario,
        audit: Optional[AuditLog] = None,
        events_db: Optional[object] = None,
        source: Optional[SensorSource] = None,
    ) -> None:
        self._scenario = scenario
        self._events_db = events_db
        self._pending_events: List[tuple] = []
        self._world: WorldModel = build_world(scenario)
        # The decision engine consumes any SensorSource. The default is the
        # simulator, but a deployment injects a RealSensorSource here to drive the
        # same engine from a recorded capture or a live feed, with no other change.
        self._source = source or SimSensorSource(
            sensors=build_sensor_specs(scenario),
            truth_fn=self._world.truth_targets,
            rate_hz=scenario.tick_hz,
            rng=self._world.rng,
        )
        self._allocator = LayeredAllocator(
            resolve_position=self._world.resolve_threat_position,
            attacker_cost_ref=scenario.unit_cost,
        )
        self._audit = audit
        self._degradation = DegradationModel(
            jam_fraction=scenario.jam_fraction,
            blackout_windows=list(scenario.blackout_windows),
        )
        self._dt = 1.0 / scenario.tick_hz
        self._tick = 0
        self._engagements_made = 0

        self._vision_detector: Optional[SimDetector] = None
        self._vision_feed: Optional[SimFeedSource] = None
        self._cascade_dependencies: List[DependencyEdge] = []
        self._decision_engine = DecisionEngine(mode=EngagementMode.AUTO)

        if scenario.target_scenario:
            from vision.scenarios import load_target_scenario
            ts = load_target_scenario(scenario.target_scenario)
            self._vision_detector = SimDetector(
                placements=ts.placements, noise_sigma_m=2.0, false_positive_rate=0.02, seed=scenario.seed
            )
            self._vision_feed = SimFeedSource(placements=ts.placements, resolution=(1280, 720))
            self._cascade_dependencies = ts.dependencies

    @property
    def world(self) -> WorldModel:
        """The live world model, exposed for inspection and tests."""
        return self._world

    async def run(self, pace: bool = True) -> AsyncIterator[Frame]:
        """Yield one Frame per tick until the scenario terminates.

        The runner owns the clock. Each tick advances the red force, samples the
        sensor source once, runs the pipeline, and yields a Frame. When pace is
        true it awaits the tick interval so a live consumer like the websocket
        sees real-time pacing. Set pace false for a fast batch run with no sleep.
        """
        await self._source.start()
        try:
            while self._tick < self._scenario.max_ticks:
                detections = self._collect_tick()
                frame = self._step(detections)
                await self._flush_events()
                yield frame
                if frame.done:
                    break
                if pace:
                    await asyncio.sleep(self._dt)
        finally:
            await self._source.stop()
            if self._audit is not None:
                self._audit.close()

    def _collect_tick(self) -> List[Detection]:
        """Advance the attacker, then sample one tick of detections.

        We advance the red force first so this tick samples fresh truth, then read
        one synchronous burst from the source. The source owns no clock here, the
        runner does, so detections and the pipeline stay in lockstep per tick.
        """
        self._world.swarm.advance(self._dt)
        detections = self._source.sample_once()
        return self._degradation.apply(detections, self._tick, self._world.rng)

    def _step(self, detections: List[Detection]) -> Frame:
        """Run fusion, threat, defense for one tick and snapshot a Frame."""
        self._tick += 1
        # Use a deterministic simulation clock, not wall-clock. Fusion coasting
        # and time-to-impact depend on the time delta between ticks, so a
        # wall-clock base would make outcomes vary with CPU load and break
        # reproducibility. Sim time is exactly tick count times the tick interval.
        t = self._tick * self._dt
        if self._audit is not None:
            self._audit.record_detections(self._tick, t, detections)
        tracks = self._world.tracks.update(detections, t)
        self._world.last_tracks = tracks
        if self._audit is not None:
            self._audit.link_tracks(self._tick, tracks)
        self._classify_hostiles()
        threats = threat_module.assess(tracks, self._world.site, t)
        cascade_results = []
        engagement_order = None
        if self._vision_detector and self._vision_feed:
            frame_img, frame_ts = self._vision_feed.next_frame()
            visual_targets = self._vision_detector.detect(frame_img, frame_ts)
            cascade_engine = CascadeEngine(targets=visual_targets, dependencies=self._cascade_dependencies)
            cascade_results = cascade_engine.score_all()
            engagement_order = self._decision_engine.merge(
                threats=threats, cascade_results=cascade_results, now=t
            )
        engagements = self._engage(threats, t)
        self._audit_tick(t, engagements, threats, tracks)
        self._queue_events(engagements, threats, tracks)
        self._cool_down()
        return self._build_frame(tracks, threats, engagements, cascade_results, engagement_order)

    def _queue_events(
        self,
        engagements: List[Engagement],
        threats: List[Threat],
        tracks: List[Track],
    ) -> None:
        """Stage engagement events with lineage for emission to the OVERWATCH DB.

        Emission is async and the per-tick step is sync, so we stage the data here
        and the async run loop flushes it. Each entry carries the threat and the
        detection lineage so the surfaced event is fully explainable.
        """
        if self._events_db is None or not engagements:
            return
        by_threat = {th.id: th for th in threats}
        by_track = {tr.id: tr for tr in tracks}
        for eng in engagements:
            threat = by_threat.get(eng.target_threat_id)
            lineage = self._event_lineage(eng, by_threat, by_track)
            self._pending_events.append((eng, threat, lineage))

    def _event_lineage(
        self, eng: Engagement, by_threat: dict, by_track: dict,
    ) -> dict:
        """Map each targeted threat to its source detection ids for an event."""
        chain: dict = {}
        for tid in eng.neutralized_threat_ids or [eng.target_threat_id]:
            threat = by_threat.get(tid)
            track = by_track.get(threat.track_id) if threat else None
            chain[tid] = list(track.source_detection_ids) if track else []
        return chain

    async def _flush_events(self) -> None:
        """Emit staged engagement events to the OVERWATCH events DB."""
        if self._events_db is None or not self._pending_events:
            return
        from ontology_bridge import emit_engagement_event

        pending = self._pending_events
        self._pending_events = []
        for eng, threat, lineage in pending:
            await emit_engagement_event(self._events_db, eng, threat, lineage)

    def _audit_tick(
        self,
        t: float,
        engagements: List[Engagement],
        threats: List[Threat],
        tracks: List[Track],
    ) -> None:
        """Record this tick's fire decisions with lineage when auditing is on."""
        if self._audit is None or not engagements:
            return
        self._audit.record_tick(
            self._tick,
            t,
            engagements,
            {th.id: th for th in threats},
            {tr.id: tr for tr in tracks},
        )

    def _classify_hostiles(self) -> None:
        """Mark every confirmed track hostile so the threat layer scores it.

        In this wargame every real airframe is an attacker, so a confirmed track
        is hostile by construction. The threat module then clusters and scores.
        """
        for track in self._world.tracks.confirmed_tracks():
            if track.classification is not TrackClass.HOSTILE:
                self._world.tracks.classify_track(track.id, TrackClass.HOSTILE)

    def _engage(self, threats: List[Threat], t: float) -> List[Engagement]:
        """Allocate ready defenders to threats and resolve outcomes.

        The allocator does not mutate shared Defender objects, so we decrement
        capacity and start reload timers for the defenders it committed, then
        resolve the engagements against the cost ledger.
        """
        ready = [d for d in self._world.defenders if d.status is DefenderStatus.READY]
        engagements = self._allocator.allocate(threats, ready, t)
        self._commit_capacity(engagements)
        self._resolve_engagements(engagements, threats)
        self._engagements_made += len(engagements)
        return engagements

    def _commit_capacity(self, engagements: List[Engagement]) -> None:
        """Spend capacity and arm reload for each defender the allocator used."""
        by_id = {d.id: d for d in self._world.defenders}
        for eng in engagements:
            defender = by_id.get(eng.defender_id)
            if defender is None:
                continue
            defender.capacity = max(0, defender.capacity - 1)
            defender.status = (
                DefenderStatus.DEPLETED
                if defender.capacity == 0
                else DefenderStatus.RELOADING
            )
            if defender.status is DefenderStatus.RELOADING:
                self._world.reload_left[defender.id] = defender.reload_s

    def _resolve_engagements(
        self, engagements: List[Engagement], threats: List[Threat],
    ) -> None:
        """Resolve fire decisions into real kills, gated by radius and hardness.

        Each engagement charges its cost once. For every targeted threat we find
        the nearest live drone within the effector lethal radius. A shot at empty
        airspace kills nothing, so misses let drones leak. The kill probability is
        the effector base probability scaled by how the target resists that
        effector, so a jam-resistant drone shrugs off EW and a hardened drone
        shrugs off HPM, forcing the defense onto kinetic interceptors.
        """
        by_def = {d.id: d for d in self._world.defenders}
        by_threat = {th.id: th for th in threats}
        ledger = self._world.ledger
        kill_positions: List[Vec3] = []
        for eng in engagements:
            ledger.record_spend(eng.cost)
            defender = by_def.get(eng.defender_id)
            if defender is None:
                eng.status = EngagementStatus.LEAK
                eng.neutralized_threat_ids = []
                continue
            killed = self._apply_effect(defender, eng, by_threat, kill_positions)
            eng.neutralized_threat_ids = killed
            eng.status = EngagementStatus.HIT if killed else EngagementStatus.MISS
        if kill_positions:
            self._world.swarm.register_losses(kill_positions)

    def _apply_effect(
        self,
        defender: Defender,
        eng: Engagement,
        by_threat: dict,
        kill_positions: List[Vec3],
    ) -> List[str]:
        """Apply one effector to its targets and return the threats it killed.

        Killed drone positions are appended to kill_positions so the runner can
        tell the swarm where it took losses, which makes nearby survivors react.
        """
        area = defender.effect_radius_m > 0.0
        radius = defender.effect_radius_m if area else _POINT_KILL_RADIUS_M
        targets = eng.neutralized_threat_ids or [eng.target_threat_id]
        killed: List[str] = []
        for tid in targets:
            threat = by_threat.get(tid)
            if threat is None:
                continue
            position = self._world.resolve_threat_position(threat)
            drone = self._nearest_live_drone(position, radius)
            if drone is None:
                continue
            kill_prob = defender.kill_prob * _resistance(defender.kind, drone)
            if self._world.rng.random() < kill_prob:
                kill_positions.append(drone.position)
                self._destroy(drone)
                self._world.ledger.record_outcome(
                    EngagementStatus.HIT, drone.unit_cost,
                )
                killed.append(tid)
        return killed

    def _nearest_live_drone(self, position, radius: float):
        """Return the nearest unarrived drone within radius of position, or None."""
        if position is None:
            return None
        best = None
        best_d2 = radius * radius
        for drone in self._world.swarm.drones:
            if drone.arrived:
                continue
            dx = drone.position[0] - position[0]
            dy = drone.position[1] - position[1]
            dz = drone.position[2] - position[2]
            d2 = dx * dx + dy * dy + dz * dz
            if d2 <= best_d2:
                best_d2 = d2
                best = drone
        return best

    def _destroy(self, drone) -> None:
        """Flag a drone killed and credit its real airframe cost destroyed."""
        drone.killed = True
        drone.arrived = True
        drone.velocity = (0.0, 0.0, 0.0)
        self._world.attacker_dollars_destroyed += drone.unit_cost
        self._world.drones_killed += 1

    def _cool_down(self) -> None:
        """Tick down reload timers and return finished defenders to READY."""
        for defender in self._world.defenders:
            if defender.status is not DefenderStatus.RELOADING:
                continue
            left = self._world.reload_left.get(defender.id, 0.0) - self._dt
            if left <= 0.0:
                defender.status = DefenderStatus.READY
                self._world.reload_left[defender.id] = 0.0
            else:
                self._world.reload_left[defender.id] = left

    def _build_frame(
        self,
        tracks: List[Track],
        threats: List[Threat],
        engagements: List[Engagement],
        cascade_results: Optional[list] = None,
        engagement_order: Optional[object] = None,
    ) -> Frame:
        """Compute metrics and bundle a renderable Frame for this tick."""
        metrics = self._compute_metrics(tracks)
        done = metrics.active_hostiles == 0 and self._tick > 1
        return Frame(
            metrics=metrics,
            tracks=tracks,
            defenders=self._world.defenders,
            threats=threats,
            assignments=assignment_lines(engagements, threats),
            site_enu=self._world.site.position,
            scenario_name=self._scenario.name,
            done=done,
            cascade_results=cascade_results or [],
            engagement_order=engagement_order,
        )

    def _compute_metrics(self, tracks: List[Track]) -> Metrics:
        """Recompute the live scoreboard from current world state.

        Attacker dollars destroyed and the cost-exchange ratio use the real
        airframe cost of killed drones, not the ledger value_at_risk figure. The
        ratio is defender dollars spent per attacker dollar of airframe killed.
        """
        self._count_leakers()
        ledger = self._world.ledger
        active = self._active_hostiles()
        intercepts = self._world.drones_killed
        rate = intercepts / self._engagements_made if self._engagements_made else 0.0
        destroyed = self._world.attacker_dollars_destroyed
        ratio = ledger.defender_spent / destroyed if destroyed > 0.0 else None
        return Metrics(
            tick=self._tick,
            sim_time_s=self._tick * self._dt,
            active_hostiles=active,
            tracks_held=len(tracks),
            leakers=len(self._world.counted_leakers),
            engagements_made=self._engagements_made,
            intercepts=intercepts,
            intercept_rate=rate,
            defender_spent=ledger.defender_spent,
            attacker_destroyed=destroyed,
            cost_exchange_ratio=ratio,
        )

    def _active_hostiles(self) -> int:
        """Count attacker drones still flying and not yet arrived or killed."""
        return sum(1 for d in self._world.swarm.drones if not d.arrived)

    def _count_leakers(self) -> None:
        """Record any drone that reached the site without being killed.

        A leaker is a drone that arrived at the site and was not intercepted. The
        kill path sets killed True, the attacker arrival path does not, so the
        killed flag cleanly separates a leaker from a kill. Each leaker is counted
        once by id.
        """
        for drone in self._world.swarm.drones:
            if drone.arrived and not drone.killed:
                self._world.counted_leakers.add(drone.id)
