"""
Hostile swarm generator — the red force that drives the whole pipeline.

This module spawns N attacker drones in the shared ENU frame and flies them
toward the defended Site each tick. It mirrors the integration style of
simulation/mock_drone.py but works in ENU meters from csontology rather than
lat/lon, so the sensor layer can sample ground truth directly.

The swarm exposes ground truth through get_truth(). The sensor layer reads that,
adds noise, and emits Detection events. The attacker never touches sensors,
tracks, or threats. It only knows positions, velocities, and cost.

Behaviors
---------
SATURATION  every drone converges on the site from all axes at once.
WAVES       drones split into staggered groups that launch on a delay.
DECOY       a mix of cheap decoys and a smaller set of real, costly threats.
PROBE       a small leading element advances while the rest hold and observe.

Adversary realism
-----------------
Drones do not fly straight. Each one jinks with a deterministic serpentine
lateral acceleration and varies altitude as it approaches, so the track layer
sees a maneuvering target. When nearby drones are destroyed, survivors react.
They sprint, spread their jink wider, and the wave timing presses forward while
attrition stays low. All randomness comes from the injected RNG so a fixed seed
reproduces every run.

All positions and velocities are ENU meters and m/s about the site origin.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Set

from csontology import SwarmIntent, Vec3, now

logger = logging.getLogger("overwatch.attacker")

# Default per-drone cost in dollars. A real attacker is cheap by design.
DEFAULT_UNIT_COST = 500.0
# A decoy is an order of magnitude cheaper than a real threat.
DECOY_UNIT_COST = 50.0
# Hardened and jam-resistant airframes are not cheap FPV drones. They carry
# shielding, autonomy, or fiber-optic control, so they cost far more. This is the
# real raid economy: the cheap mass is killed cheaply by area effects, and the
# few drones that force an expensive kinetic interceptor are themselves expensive,
# so the cost exchange stays defensible instead of a free win or a free loss.
EW_RESISTANT_UNIT_COST = 6000.0
HARDENED_UNIT_COST = 9000.0
DUAL_RESISTANT_UNIT_COST = 14000.0
# Nominal cruise speed of an attacker drone in m/s.
CRUISE_SPEED_MPS = 26.0
# Ring radius the swarm spawns on, in meters from the site.
SPAWN_RADIUS_M = 2200.0
# Distance from the site at which a drone is considered to have arrived.
ARRIVAL_RADIUS_M = 25.0
# Fraction of a DECOY swarm that are real threats. The rest are decoys.
REAL_THREAT_FRACTION = 0.25
# Fraction of a PROBE swarm that forms the leading element.
PROBE_LEAD_FRACTION = 0.15
# Seconds between successive WAVES launches.
WAVE_INTERVAL_S = 8.0
# Number of staggered groups in a WAVES attack.
WAVE_GROUP_COUNT = 4

# ---- evasion tuning ----
# Peak sideways jink acceleration in m/s^2 under nominal approach.
JINK_ACCEL_MPS2 = 9.0
# Serpentine angular rate in radians per second. Higher means tighter weave.
JINK_RATE_RPS = 0.9
# Peak altitude bob amplitude in meters for terrain-following profiles.
ALT_BOB_M = 35.0
# Altitude bob angular rate in radians per second.
ALT_BOB_RATE_RPS = 0.4
# Fraction of the swarm that flies low terrain-following profiles.
TERRAIN_FOLLOW_FRACTION = 0.5

# ---- reactive tuning ----
# Survivors within this radius of a fresh kill react, in meters.
REACTION_RADIUS_M = 600.0
# Speed multiplier a reacting survivor sprints at.
SPRINT_MULTIPLIER = 1.5
# Extra jink amplitude multiplier while reacting.
REACT_JINK_MULTIPLIER = 1.8
# Seconds a survivor keeps sprinting after a nearby kill.
REACT_DECAY_S = 6.0

# ---- pulsing tuning ----
# Attrition fraction below which the swarm presses and pulls waves forward.
PRESS_ATTRITION_THRESHOLD = 0.2
# Seconds each press pulls an unlaunched wave forward by.
PRESS_PULL_S = WAVE_INTERVAL_S * 0.5


@dataclass
class AttackerDrone:
    """One hostile drone with ENU kinematics and a dollar cost.

    is_decoy marks cheap throwaway airframes used to draw fire. launch_time is
    the wall-clock second this drone starts moving. Before it launches the drone
    holds its spawn position with zero velocity.

    The evasion fields drive deterministic maneuvering. jink_phase and jink_sign
    set the serpentine weave, alt_phase and alt_amp set the altitude bob, and
    react_until is the wall-clock second a sprint reaction expires.
    """
    id: str
    position: Vec3
    velocity: Vec3
    unit_cost: float
    is_decoy: bool = False
    launch_time: float = 0.0
    arrived: bool = False
    killed: bool = False
    speed_mps: float = CRUISE_SPEED_MPS
    jink_phase: float = 0.0
    jink_sign: float = 1.0
    jink_rate: float = JINK_RATE_RPS
    alt_phase: float = 0.0
    alt_amp: float = 0.0
    cruise_alt: float = 100.0
    react_until: float = 0.0
    # ew_resistant drones fly autonomous or fiber-optic links that defeat jamming,
    # so EW and soft-kill effectors do nothing to them. hardened drones shrug off
    # high-power microwave. Both force the defense onto kinetic interceptors, which
    # is where the attacker tries to win the cost war.
    ew_resistant: bool = False
    hardened: bool = False


@dataclass
class TruthDrone:
    """A read-only snapshot of one drone's ground truth for the sensor layer."""
    id: str
    position: Vec3
    velocity: Vec3
    is_decoy: bool
    unit_cost: float
    ew_resistant: bool = False
    hardened: bool = False


@dataclass
class HostileSwarm:
    """A coordinated red force of attacker drones converging on a Site.

    Construct with a behavior intent, a member count, and the site position in
    ENU meters. Call advance(dt) each tick to fly the swarm toward the site.
    Call get_truth() to read ground-truth kinematics for the sensor layer.

    The swarm maneuvers and reacts to attrition on its own. The runner may call
    register_losses(positions) to signal kills explicitly, but it does not need
    to. Kills are also inferred from drones flagged arrived away from the site,
    so the existing runner keeps working unchanged.
    """

    intent: SwarmIntent
    count: int
    site_position: Vec3 = (0.0, 0.0, 0.0)
    unit_cost: float = DEFAULT_UNIT_COST
    seed: Optional[int] = None
    swarm_id: str = "RED-1"
    spawn_radius_m: float = SPAWN_RADIUS_M
    evasive: bool = True
    jam_resistant_fraction: float = 0.0
    hardened_fraction: float = 0.0
    drones: List[AttackerDrone] = field(default_factory=list)
    first_seen: float = field(default_factory=now)
    _rng: random.Random = field(init=False, repr=False)
    _known_arrived: Set[str] = field(init=False, default_factory=set, repr=False)
    _pressed: bool = field(init=False, default=False, repr=False)
    _sim_time: float = field(init=False, default=0.0, repr=False)

    def __post_init__(self) -> None:
        if not 10 <= self.count <= 1000:
            raise ValueError(f"count must be in 10..1000, got {self.count}")
        self._rng = random.Random(self.seed)
        self._spawn()

    # ---- spawning ----

    def _spawn(self) -> None:
        """Build the member drones for the configured behavior."""
        builders = {
            SwarmIntent.SATURATION: self._spawn_saturation,
            SwarmIntent.WAVES: self._spawn_waves,
            SwarmIntent.DECOY: self._spawn_decoy,
            SwarmIntent.PROBE: self._spawn_probe,
        }
        builder = builders.get(self.intent)
        if builder is None:
            raise ValueError(f"unsupported intent: {self.intent}")
        self.drones = builder()
        logger.info(
            "Spawned %s hostile drones, intent=%s, id=%s, evasive=%s",
            len(self.drones), self.intent.value, self.swarm_id, self.evasive,
        )

    def _ring_position(self, index: int) -> Vec3:
        """Place a drone on a spawn ring around the site at a spread bearing."""
        bearing = (index / max(1, self.count)) * 2.0 * math.pi
        bearing += self._rng.uniform(-0.05, 0.05)
        radius = self.spawn_radius_m * self._rng.uniform(0.9, 1.1)
        sx, sy, sz = self.site_position
        x = sx + radius * math.cos(bearing)
        y = sy + radius * math.sin(bearing)
        z = self._rng.uniform(40.0, 160.0)
        return x, y, z

    def _airframe_cost(
        self, base_cost: float, is_decoy: bool, ew_resistant: bool, hardened: bool,
    ) -> float:
        """Price an airframe by its survivability, the real raid economy.

        A decoy keeps its throwaway price. A plain drone keeps the base cost.
        Resistance to EW or HPM marks an expensive shielded or fiber-optic drone,
        and a drone resistant to both is the costliest, so a kinetic interceptor
        spent on it is a fair trade rather than a runaway loss.
        """
        if is_decoy:
            return base_cost
        if ew_resistant and hardened:
            return DUAL_RESISTANT_UNIT_COST
        if hardened:
            return HARDENED_UNIT_COST
        if ew_resistant:
            return EW_RESISTANT_UNIT_COST
        return base_cost

    def _make_drone(
        self, index: int, launch_time: float, is_decoy: bool, cost: float,
    ) -> AttackerDrone:
        """Construct one attacker drone holding at its spawn ring position.

        Each drone gets a deterministic evasion profile drawn from the RNG so a
        fixed seed reproduces every weave and altitude bob.
        """
        position = self._ring_position(index)
        terrain_follow = self._rng.random() < TERRAIN_FOLLOW_FRACTION
        ew_resistant = self._rng.random() < self.jam_resistant_fraction
        hardened = self._rng.random() < self.hardened_fraction
        cost = self._airframe_cost(cost, is_decoy, ew_resistant, hardened)
        return AttackerDrone(
            id=f"{self.swarm_id}-{index:04d}",
            position=position,
            velocity=(0.0, 0.0, 0.0),
            unit_cost=cost,
            is_decoy=is_decoy,
            launch_time=launch_time,
            jink_phase=self._rng.uniform(0.0, 2.0 * math.pi),
            jink_sign=self._rng.choice((-1.0, 1.0)),
            jink_rate=JINK_RATE_RPS * self._rng.uniform(0.7, 1.3),
            alt_phase=self._rng.uniform(0.0, 2.0 * math.pi),
            alt_amp=ALT_BOB_M if terrain_follow else ALT_BOB_M * 0.25,
            cruise_alt=position[2],
            ew_resistant=ew_resistant,
            hardened=hardened,
        )

    def _spawn_saturation(self) -> List[AttackerDrone]:
        """All drones launch at once from every bearing."""
        start = 0.0  # sim-time base so launch timing is deterministic, not wall-clock
        return [
            self._make_drone(i, start, is_decoy=False, cost=self.unit_cost)
            for i in range(self.count)
        ]

    def _spawn_waves(self) -> List[AttackerDrone]:
        """Drones split into groups that launch on staggered delays."""
        start = 0.0  # sim-time base so launch timing is deterministic, not wall-clock
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            group = i % WAVE_GROUP_COUNT
            launch = start + group * WAVE_INTERVAL_S
            drones.append(
                self._make_drone(i, launch, is_decoy=False, cost=self.unit_cost)
            )
        return drones

    def _spawn_decoy(self) -> List[AttackerDrone]:
        """A small set of real threats hidden among many cheap decoys."""
        start = 0.0  # sim-time base so launch timing is deterministic, not wall-clock
        real_count = max(1, round(self.count * REAL_THREAT_FRACTION))
        real_indices = set(self._rng.sample(range(self.count), real_count))
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            is_decoy = i not in real_indices
            cost = DECOY_UNIT_COST if is_decoy else self.unit_cost
            drones.append(self._make_drone(i, start, is_decoy=is_decoy, cost=cost))
        return drones

    def _spawn_probe(self) -> List[AttackerDrone]:
        """A small lead element advances while the rest hold and observe.

        Holding drones get a launch time far in the future so advance() leaves
        them parked on the spawn ring while the lead element closes the site.
        """
        start = 0.0  # sim-time base so launch timing is deterministic, not wall-clock
        lead_count = max(1, round(self.count * PROBE_LEAD_FRACTION))
        hold_launch = start + 1e9
        drones: List[AttackerDrone] = []
        for i in range(self.count):
            launch = start if i < lead_count else hold_launch
            drones.append(
                self._make_drone(i, launch, is_decoy=False, cost=self.unit_cost)
            )
        return drones

    # ---- loss signaling and inference ----

    def register_losses(self, positions: List[Vec3]) -> None:
        """Tell the swarm where drones were just destroyed so survivors react.

        This is an optional hook for a runner that knows its kills directly. The
        runner is not required to call it. Survivors within REACTION_RADIUS_M of
        any position start sprinting and widen their weave. Calling it with an
        empty list is a no-op.
        """
        for kill_pos in positions:
            self._trigger_reactions(kill_pos)

    def _infer_losses(self) -> List[Vec3]:
        """Find drones newly flagged arrived away from the site, return positions.

        The runner kills a drone by flagging arrived while it is still in the
        field, whereas a drone that reaches the site snaps to the site position.
        We treat any new arrived drone that is not at the site as a fresh kill so
        survivors react without the runner calling register_losses.
        """
        kills: List[Vec3] = []
        for drone in self.drones:
            if not drone.arrived or drone.id in self._known_arrived:
                continue
            self._known_arrived.add(drone.id)
            if _magnitude(self._vector_to_site(drone.position)) > ARRIVAL_RADIUS_M:
                kills.append(drone.position)
        return kills

    def _trigger_reactions(self, kill_pos: Vec3) -> None:
        """Make live survivors near a kill sprint and widen their weave.

        react_until is on the internal sim-clock so reactions stay deterministic
        across runs regardless of wall-clock time.
        """
        for drone in self.drones:
            if drone.arrived:
                continue
            offset = _subtract(drone.position, kill_pos)
            if _magnitude(offset) <= REACTION_RADIUS_M:
                drone.react_until = self._sim_time + REACT_DECAY_S

    def _press_if_holding(self) -> None:
        """Pull unlaunched waves forward while attrition stays low.

        A confident attacker presses the attack when it is barely losing drones.
        We pull every future launch time forward once, the first tick attrition
        sits below the press threshold, so later waves arrive sooner.
        """
        if self._pressed:
            return
        attrition = self._attrition_fraction()
        if attrition > PRESS_ATTRITION_THRESHOLD:
            return
        t = self._sim_time
        for drone in self.drones:
            if not drone.arrived and drone.launch_time > t:
                drone.launch_time = max(t, drone.launch_time - PRESS_PULL_S)
        self._pressed = True
        logger.info("Red force presses, attrition=%.2f", attrition)

    def _attrition_fraction(self) -> float:
        """Return the fraction of the force that has been killed in the field."""
        killed = sum(
            1 for d in self.drones
            if d.arrived
            and _magnitude(self._vector_to_site(d.position)) > ARRIVAL_RADIUS_M
        )
        return killed / max(1, len(self.drones))

    # ---- per-tick advance ----

    def advance(self, dt: float) -> None:
        """Fly every launched, unarrived drone toward the site for dt seconds.

        First infer fresh kills so survivors react, then maybe press the attack,
        then move each drone with evasion. Evasion runs on an internal sim-clock
        so trajectories stay deterministic under a fixed seed. The public
        signature is unchanged.
        """
        for kill_pos in self._infer_losses():
            self._trigger_reactions(kill_pos)
        self._sim_time += dt
        self._press_if_holding()
        t = self._sim_time
        for drone in self.drones:
            self._advance_one(drone, dt, t)

    def _advance_one(self, drone: AttackerDrone, dt: float, t: float) -> None:
        """Move one drone toward the site with evasion, or hold it if not launched."""
        if drone.arrived or t < drone.launch_time:
            drone.velocity = (0.0, 0.0, 0.0)
            return
        to_site = self._vector_to_site(drone.position)
        dist = _magnitude(to_site)
        if dist <= ARRIVAL_RADIUS_M:
            drone.arrived = True
            self._known_arrived.add(drone.id)
            drone.position = self.site_position
            drone.velocity = (0.0, 0.0, 0.0)
            return
        velocity = self._evasive_velocity(drone, to_site, dist)
        drone.velocity = velocity
        step = _scale(velocity, dt)
        if _magnitude(step) > dist:
            step = to_site
        drone.position = _add(drone.position, step)

    def _evasive_velocity(
        self, drone: AttackerDrone, to_site: Vec3, dist: float,
    ) -> Vec3:
        """Steer a constant-speed velocity that weaves toward the site.

        Pursuit, serpentine jink, and altitude bob combine into a steering
        direction that is renormalized back to the drone speed. The drone keeps
        cruise speed but deviates laterally from a straight line, so the track
        layer sees a maneuvering target. Reacting survivors sprint and weave
        harder. With evasion disabled this collapses to straight-line pursuit.
        """
        speed = drone.speed_mps
        reacting = self._sim_time < drone.react_until
        if reacting:
            speed *= SPRINT_MULTIPLIER
        if not self.evasive:
            return _scale(to_site, speed / dist)
        forward = (to_site[0] / dist, to_site[1] / dist, to_site[2] / dist)
        lateral = self._lateral_jink(drone, to_site, dist, reacting)
        vertical = self._altitude_bob(drone)
        steer = _add(_add(forward, lateral), vertical)
        mag = _magnitude(steer)
        if mag <= 0.0:
            return _scale(to_site, speed / dist)
        return _scale(steer, speed / mag)

    def _lateral_jink(
        self, drone: AttackerDrone, to_site: Vec3, dist: float, reacting: bool,
    ) -> Vec3:
        """Return a unit-scaled sideways serpentine steering, perpendicular to approach."""
        nx, ny = to_site[0] / dist, to_site[1] / dist
        perp = (-ny, nx, 0.0)
        amp = JINK_ACCEL_MPS2 / CRUISE_SPEED_MPS
        if reacting:
            amp *= REACT_JINK_MULTIPLIER
        phase = drone.jink_phase + drone.jink_rate * self._sim_time
        weave = drone.jink_sign * amp * math.sin(phase)
        return _scale(perp, weave)

    def _altitude_bob(self, drone: AttackerDrone) -> Vec3:
        """Return a unit-scaled vertical steering toward a bobbing target altitude."""
        target = drone.cruise_alt + drone.alt_amp * math.sin(
            drone.alt_phase + ALT_BOB_RATE_RPS * self._sim_time
        )
        error = (target - drone.position[2]) / CRUISE_SPEED_MPS
        return (0.0, 0.0, max(-0.4, min(0.4, error)))

    def _vector_to_site(self, position: Vec3) -> Vec3:
        """Return the ENU vector from a position to the site center."""
        return _subtract(self.site_position, position)

    # ---- ground-truth readout ----

    def get_truth(self) -> List[TruthDrone]:
        """Return ground-truth snapshots for the sensor layer to sample."""
        return [
            TruthDrone(
                id=d.id,
                position=d.position,
                velocity=d.velocity,
                is_decoy=d.is_decoy,
                unit_cost=d.unit_cost,
            )
            for d in self.drones
        ]

    def centroid(self) -> Vec3:
        """Return the mean position of all member drones in ENU meters."""
        if not self.drones:
            return self.site_position
        n = float(len(self.drones))
        sx = sum(d.position[0] for d in self.drones) / n
        sy = sum(d.position[1] for d in self.drones) / n
        sz = sum(d.position[2] for d in self.drones) / n
        return sx, sy, sz

    def total_cost(self) -> float:
        """Return the summed dollar cost of the whole red force."""
        return sum(d.unit_cost for d in self.drones)

    def arrived_count(self) -> int:
        """Return how many drones have reached the site."""
        return sum(1 for d in self.drones if d.arrived)


# ---- Vec3 math helpers ----

def _add(a: Vec3, b: Vec3) -> Vec3:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _scale(a: Vec3, k: float) -> Vec3:
    return a[0] * k, a[1] * k, a[2] * k


def _magnitude(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
