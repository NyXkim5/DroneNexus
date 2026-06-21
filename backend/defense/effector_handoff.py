"""
Effector-to-threat handoff engine for BULWARK.

Computes physical slew commands that direct jammers, interceptors, nets, and
lasers to visually detected drone targets. Each SlewCommand carries the bearing,
elevation, range, and velocity-lead-corrected aim point from a defender to a
target. A HandoffManager maintains a priority queue of active slew commands per
tick, deconflicts overlapping bearing sectors, and exposes the queue for the HUD.

Velocity lead is a simple linear prediction: where will the target be when the
effector effect reaches it? Speed-of-light effectors (JAMMER, EW, HPM, LASER)
have zero lead. Kinetic effectors predict based on their projectile speed.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from csontology import Defender, DefenderKind, Engagement, Vec3

logger = logging.getLogger("overwatch.defense.handoff")

# Default effector speeds in m/s by kind. Speed-of-light effectors use None
# (instant). Kinetic and net effectors use finite projectile speeds.
_EFFECTOR_SPEED: Dict[DefenderKind, Optional[float]] = {
    DefenderKind.JAMMER: None,
    DefenderKind.EW: None,
    DefenderKind.HPM: None,
    DefenderKind.LASER: None,
    DefenderKind.INTERCEPTOR: 200.0,
    DefenderKind.NET: 50.0,
}

# Bearing sector width in degrees for deconfliction. Two slew commands pointing
# within this many degrees of each other are in the same sector.
_DECONFLICT_SECTOR_DEG = 10.0


@dataclass
class SlewCommand:
    """Physical aim directive from a defender to a target."""

    defender_id: str
    target_id: str
    bearing_deg: float
    elevation_deg: float
    range_m: float
    lead_bearing_deg: float
    lead_elevation_deg: float
    priority: int
    timestamp: float

    def to_dict(self) -> dict:
        """Serialize to a JSON-ready dict for the websocket."""
        return {
            "defender_id": self.defender_id,
            "target_id": self.target_id,
            "bearing_deg": round(self.bearing_deg, 2),
            "elevation_deg": round(self.elevation_deg, 2),
            "range_m": round(self.range_m, 2),
            "lead_bearing_deg": round(self.lead_bearing_deg, 2),
            "lead_elevation_deg": round(self.lead_elevation_deg, 2),
            "priority": self.priority,
            "timestamp": round(self.timestamp, 3),
        }


def _distance(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two ENU points in meters."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_bearing(origin: Vec3, target: Vec3) -> float:
    """Compass bearing in degrees from origin to target in the ENU frame.

    ENU convention: x=East, y=North. Bearing is clockwise from North.
    Returns 0..360.
    """
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    bearing = math.degrees(math.atan2(dx, dy)) % 360.0
    return bearing


def compute_elevation(origin: Vec3, target: Vec3) -> float:
    """Elevation angle in degrees from origin to target.

    Positive is above the horizon, negative is below. Horizontal range is
    the distance projected onto the ground plane (x, y).
    """
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    dz = target[2] - origin[2]
    horiz = math.sqrt(dx * dx + dy * dy)
    if horiz < 1e-9 and abs(dz) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(dz, horiz))


def effector_speed(kind: DefenderKind) -> Optional[float]:
    """Return projectile speed in m/s for the effector kind, None if instant."""
    return _EFFECTOR_SPEED.get(kind)


def compute_lead(
    target_pos: Vec3,
    target_vel: Vec3,
    effector_pos: Vec3,
    effector_speed_ms: Optional[float],
) -> tuple:
    """Predict lead bearing and elevation for a moving target.

    Uses simple linear extrapolation: time of flight is range / effector speed.
    The target moves linearly during that interval. Speed-of-light effectors
    (effector_speed_ms is None) return the direct bearing and elevation with no
    lead correction.

    Returns (lead_bearing_deg, lead_elevation_deg).
    """
    if effector_speed_ms is None or effector_speed_ms <= 0.0:
        return (
            compute_bearing(effector_pos, target_pos),
            compute_elevation(effector_pos, target_pos),
        )
    dist = _distance(effector_pos, target_pos)
    if dist < 1e-6:
        return (
            compute_bearing(effector_pos, target_pos),
            compute_elevation(effector_pos, target_pos),
        )
    tof = dist / effector_speed_ms
    predicted = (
        target_pos[0] + target_vel[0] * tof,
        target_pos[1] + target_vel[1] * tof,
        target_pos[2] + target_vel[2] * tof,
    )
    return (
        compute_bearing(effector_pos, predicted),
        compute_elevation(effector_pos, predicted),
    )


class EffectorController:
    """Computes physical slew commands from engagement assignments."""

    def compute_slew(
        self,
        defender: Defender,
        target_position: Vec3,
        target_velocity: Vec3 = (0.0, 0.0, 0.0),
        target_id: str = "",
        priority: int = 1,
        timestamp: Optional[float] = None,
    ) -> SlewCommand:
        """Build a SlewCommand from a defender to a target position.

        Bearing and elevation are the direct line of sight. Lead bearing and
        elevation account for target motion and effector projectile speed.
        """
        ts = timestamp if timestamp is not None else time.time()
        bearing = compute_bearing(defender.position, target_position)
        elevation = compute_elevation(defender.position, target_position)
        rng = _distance(defender.position, target_position)
        speed = effector_speed(defender.kind)
        lead_b, lead_e = compute_lead(
            target_position, target_velocity, defender.position, speed,
        )
        return SlewCommand(
            defender_id=defender.id,
            target_id=target_id,
            bearing_deg=bearing,
            elevation_deg=elevation,
            range_m=rng,
            lead_bearing_deg=lead_b,
            lead_elevation_deg=lead_e,
            priority=priority,
            timestamp=ts,
        )


def _bearing_diff(a: float, b: float) -> float:
    """Smallest angular difference between two bearings in degrees."""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


@dataclass
class HandoffManager:
    """Priority queue of active slew commands with bearing deconfliction.

    Each tick the caller feeds new engagements. The manager computes slew
    commands, deconflicts overlapping bearing sectors, and exposes the active
    queue for the HUD and effector drivers.
    """

    _controller: EffectorController = field(default_factory=EffectorController)
    _active: List[SlewCommand] = field(default_factory=list)
    _sector_deg: float = _DECONFLICT_SECTOR_DEG

    def update(
        self,
        engagements: List[Engagement],
        defenders: Dict[str, Defender],
        target_positions: Dict[str, Vec3],
        target_velocities: Dict[str, Vec3],
        now: float,
    ) -> List[SlewCommand]:
        """Compute slew commands for new engagements and deconflict.

        Returns the active slew command list for this tick.
        """
        raw = self._compute_commands(
            engagements, defenders, target_positions, target_velocities, now,
        )
        self._active = self._deconflict(raw)
        return list(self._active)

    def get_active_commands(self) -> List[SlewCommand]:
        """Return the current active slew commands for the HUD."""
        return list(self._active)

    def _compute_commands(
        self,
        engagements: List[Engagement],
        defenders: Dict[str, Defender],
        target_positions: Dict[str, Vec3],
        target_velocities: Dict[str, Vec3],
        now: float,
    ) -> List[SlewCommand]:
        """Build one SlewCommand per engagement that has resolvable geometry."""
        commands: List[SlewCommand] = []
        for i, eng in enumerate(engagements):
            defender = defenders.get(eng.defender_id)
            pos = target_positions.get(eng.target_threat_id)
            if defender is None or pos is None:
                continue
            vel = target_velocities.get(eng.target_threat_id, (0.0, 0.0, 0.0))
            cmd = self._controller.compute_slew(
                defender=defender,
                target_position=pos,
                target_velocity=vel,
                target_id=eng.target_threat_id,
                priority=i + 1,
                timestamp=now,
            )
            commands.append(cmd)
        return commands

    def _deconflict(self, commands: List[SlewCommand]) -> List[SlewCommand]:
        """Remove lower-priority commands in the same bearing sector.

        When two commands from the same defender point within sector_deg of
        each other, only the one targeting the closer range survives. This
        prevents two effectors on the same mount from trying to slew to
        nearly the same azimuth simultaneously.
        """
        sorted_cmds = sorted(commands, key=lambda c: c.range_m)
        keep: List[SlewCommand] = []
        for cmd in sorted_cmds:
            if self._sector_conflict(cmd, keep):
                continue
            keep.append(cmd)
        keep.sort(key=lambda c: c.priority)
        return keep

    def _sector_conflict(
        self, cmd: SlewCommand, existing: List[SlewCommand],
    ) -> bool:
        """True if cmd conflicts with an already-kept command from the same defender."""
        for kept in existing:
            if kept.defender_id != cmd.defender_id:
                continue
            if _bearing_diff(kept.lead_bearing_deg, cmd.lead_bearing_deg) < self._sector_deg:
                return True
        return False


def slew_commands_to_dicts(commands: List[SlewCommand]) -> List[dict]:
    """Serialize a list of SlewCommands for inclusion in a WARGAME_FRAME.

    Integration note: add a "slew_commands" key to Frame.to_dict() that calls
    this function with the tick's active commands. The Frame dataclass would
    gain a field:

        slew_commands: List[dict] = field(default_factory=list)

    And to_dict() would include:

        "slew_commands": self.slew_commands,

    The HandoffManager.update() output feeds this field each tick.
    """
    return [cmd.to_dict() for cmd in commands]
