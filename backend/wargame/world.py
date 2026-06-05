"""
World model for one wargame run.

WorldModel holds the live counter-swarm state the runner mutates each tick: the
attacker swarm, the fusion track manager, the defender roster with reload timers,
the running cost ledger, and the site. It builds itself from a Scenario so the
runner stays focused on the tick loop.

It also owns the small pieces of glue the modules need from each other: a truth
callable for the sensor layer and a threat-to-position resolver for the
allocator. Both read live state so they reflect the current tick.

All positions and velocities are ENU meters and m/s about the site origin.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from csontology import (
    Defender,
    DefenderStatus,
    Site,
    Threat,
    Track,
    Vec3,
)
from defense import CostLedger
from sensors.sim_source import SimSensorSpec, TruthTarget
from attacker.hostile_swarm import HostileSwarm
from fusion import FusionConfig, TrackManager

from wargame.scenario import DefenderConfig, Scenario

logger = logging.getLogger("overwatch.wargame")


@dataclass
class WorldModel:
    """Live state for one wargame run, assembled from a Scenario."""

    scenario: Scenario
    swarm: HostileSwarm
    tracks: TrackManager
    defenders: List[Defender]
    site: Site
    ledger: CostLedger = field(default_factory=CostLedger)
    rng: random.Random = field(default_factory=random.Random)
    # Reload countdown per defender id, in seconds remaining. Zero means ready.
    reload_left: Dict[str, float] = field(default_factory=dict)
    # Track ids already counted as leakers, so each leaker counts once.
    counted_leakers: set[str] = field(default_factory=set)
    last_tracks: List[Track] = field(default_factory=list)
    # Real attacker airframe dollars destroyed, summed from killed drone cost.
    # This drives the headline cost-exchange ratio, distinct from the ledger
    # value_at_risk figure which reflects endangered asset value, not airframes.
    attacker_dollars_destroyed: float = 0.0
    drones_killed: int = 0

    def truth_targets(self) -> List[TruthTarget]:
        """Snapshot the attacker as TruthTargets for the sensor layer.

        Arrived drones are dropped so a struck or impacted airframe stops
        producing detections. The sensor never imports attacker internals, so
        this adapter bridges the two.
        """
        out: List[TruthTarget] = []
        for d in self.swarm.get_truth():
            out.append(
                TruthTarget(id=d.id, position=d.position, velocity=d.velocity)
            )
        return out

    def resolve_threat_position(self, threat: Threat) -> Optional[Vec3]:
        """Map a Threat back to an ENU position for the allocator range gate.

        A track threat resolves to its track position. A swarm threat has no
        single track on the Threat, so it resolves to the centroid of all current
        hostile tracks, a representative point for range gating. Returns None when
        no track is available.
        """
        by_id = {t.id: t for t in self.last_tracks}
        if threat.track_id is not None:
            track = by_id.get(threat.track_id)
            return track.position if track else None
        return self._hostile_centroid()

    def _hostile_centroid(self) -> Optional[Vec3]:
        """Centroid of all current tracks, the swarm-threat range-gate proxy."""
        if not self.last_tracks:
            return None
        n = float(len(self.last_tracks))
        sx = sum(t.position[0] for t in self.last_tracks) / n
        sy = sum(t.position[1] for t in self.last_tracks) / n
        sz = sum(t.position[2] for t in self.last_tracks) / n
        return sx, sy, sz


def build_world(scenario: Scenario) -> WorldModel:
    """Assemble a WorldModel from a Scenario, wiring every module together."""
    rng = random.Random(scenario.seed)
    swarm = HostileSwarm(
        intent=scenario.swarm_intent,
        count=scenario.swarm_count,
        site_position=scenario.site.position,
        unit_cost=scenario.unit_cost,
        seed=scenario.seed,
    )
    tracks = TrackManager(FusionConfig())
    defenders = _build_defenders(scenario.defenders)
    site = Site(
        id=scenario.site.id,
        position=scenario.site.position,
        protected_assets=list(scenario.site.protected_assets),
        value=scenario.site.value,
    )
    reload_left = {d.id: 0.0 for d in defenders}
    return WorldModel(
        scenario=scenario,
        swarm=swarm,
        tracks=tracks,
        defenders=defenders,
        site=site,
        rng=rng,
        reload_left=reload_left,
    )


def _build_defenders(configs: List[DefenderConfig]) -> List[Defender]:
    """Expand each DefenderConfig count into individual Defender objects."""
    defenders: List[Defender] = []
    for cfg in configs:
        for i in range(cfg.count):
            defenders.append(
                Defender(
                    id=f"{cfg.id_prefix}-{i + 1:03d}",
                    position=cfg.position,
                    kind=cfg.kind,
                    capacity=cfg.capacity,
                    range_m=cfg.range_m,
                    reload_s=cfg.reload_s,
                    kill_prob=cfg.kill_prob,
                    unit_cost=cfg.unit_cost,
                    status=DefenderStatus.READY,
                )
            )
    return defenders


def build_sensor_specs(scenario: Scenario) -> List[SimSensorSpec]:
    """Map a scenario sensor layout onto SimSensorSpec objects for the source."""
    return [
        SimSensorSpec(
            sensor_id=s.sensor_id,
            position=s.position,
            range_m=s.range_m,
            fov_deg=s.fov_deg,
            bearing_deg=s.bearing_deg,
            detection_prob=s.detection_prob,
            pos_noise_m=s.pos_noise_m,
            vel_noise_ms=s.vel_noise_ms,
        )
        for s in scenario.sensors
    ]
