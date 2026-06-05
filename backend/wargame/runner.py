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
    DefenderStatus,
    Detection,
    Engagement,
    EngagementStatus,
    Threat,
    Track,
    TrackClass,
    now,
)
from defense import LayeredAllocator, resolve
from sensors.sim_source import SimSensorSource
import threat as threat_module

from wargame.audit import AuditLog
from wargame.degradation import DegradationModel

from wargame.frame import Frame, Metrics, assignment_lines
from wargame.scenario import Scenario
from wargame.world import WorldModel, build_sensor_specs, build_world

logger = logging.getLogger("overwatch.wargame")


class WargameRunner:
    """Drives one counter-swarm wargame from a Scenario to completion."""

    def __init__(
        self, scenario: Scenario, audit: Optional[AuditLog] = None,
    ) -> None:
        self._scenario = scenario
        self._world: WorldModel = build_world(scenario)
        self._source = SimSensorSource(
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
        t = now()
        tracks = self._world.tracks.update(detections, t)
        self._world.last_tracks = tracks
        self._classify_hostiles()
        threats = threat_module.assess(tracks, self._world.site, t)
        engagements = self._engage(threats, t)
        self._audit_tick(t, engagements, threats, tracks)
        self._cool_down()
        return self._build_frame(tracks, threats, engagements)

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
        resolve(
            engagements,
            self._world.defenders,
            threats,
            t,
            ledger=self._world.ledger,
            rng=self._world.rng,
        )
        self._mark_destroyed(engagements, threats)
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

    def _mark_destroyed(
        self, engagements: List[Engagement], threats: List[Threat]
    ) -> None:
        """Kill every attacker drone a HIT neutralized and tally its dollar cost.

        Each HIT carries the threats it actually killed in neutralized_threat_ids,
        so one area shot removes several airframes. We resolve each killed threat
        to an ENU position and flag the nearest live drone as destroyed so it
        leaves the truth feed next tick. We sum drone unit_cost into
        attacker_destroyed so the cost-exchange ratio uses real airframe cost.
        """
        by_threat = {th.id: th for th in threats}
        for eng in engagements:
            if eng.status is not EngagementStatus.HIT:
                continue
            for tid in eng.neutralized_threat_ids:
                threat = by_threat.get(tid)
                if threat is None:
                    continue
                position = self._world.resolve_threat_position(threat)
                self._kill_nearest_drone(position)

    def _kill_nearest_drone(self, position) -> None:
        """Flag the live drone closest to an ENU position as destroyed.

        Adds the drone unit_cost to the world tally of attacker dollars destroyed.
        A None position or no live drone leaves the field unchanged.
        """
        if position is None:
            return
        best = None
        best_d2 = float("inf")
        for drone in self._world.swarm.drones:
            if drone.arrived:
                continue
            dx = drone.position[0] - position[0]
            dy = drone.position[1] - position[1]
            dz = drone.position[2] - position[2]
            d2 = dx * dx + dy * dy + dz * dz
            if d2 < best_d2:
                best_d2 = d2
                best = drone
        if best is not None:
            best.arrived = True
            self._world.attacker_dollars_destroyed += best.unit_cost
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
    ) -> Frame:
        """Compute metrics and bundle a renderable Frame for this tick."""
        metrics = self._compute_metrics(tracks)
        done = metrics.active_hostiles == 0 and self._tick > 1
        return Frame(
            metrics=metrics,
            tracks=tracks,
            defenders=self._world.defenders,
            assignments=assignment_lines(engagements, threats),
            site_enu=self._world.site.position,
            scenario_name=self._scenario.name,
            done=done,
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
        """Record any drone that reached the site as a one-time leaker.

        A leaker is a drone that arrived at the site without being intercepted.
        Killed drones are also flagged arrived, so we only count arrivals that the
        attacker advance loop produced, tracked by drone id distinct from kills.
        """
        for drone in self._world.swarm.drones:
            if drone.arrived and drone.position == self._world.site.position:
                self._world.counted_leakers.add(drone.id)
