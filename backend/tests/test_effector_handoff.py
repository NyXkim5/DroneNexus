"""Tests for the effector-to-threat handoff engine."""
from __future__ import annotations

import math

import pytest

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Engagement,
    EngagementStatus,
    Vec3,
)
from defense.effector_handoff import (
    EffectorController,
    HandoffManager,
    SlewCommand,
    compute_bearing,
    compute_elevation,
    compute_lead,
    effector_speed,
    slew_commands_to_dicts,
    _bearing_diff,
)


# ---- Helpers ----

def _make_defender(
    id: str = "d1",
    position: Vec3 = (0.0, 0.0, 0.0),
    kind: DefenderKind = DefenderKind.INTERCEPTOR,
) -> Defender:
    return Defender(
        id=id,
        position=position,
        kind=kind,
        capacity=10,
        range_m=5000.0,
        reload_s=2.0,
        kill_prob=0.8,
        unit_cost=100.0,
    )


def _make_engagement(
    defender_id: str = "d1",
    target_id: str = "t1",
) -> Engagement:
    return Engagement(
        id="eng-test",
        defender_id=defender_id,
        target_threat_id=target_id,
        start_time=100.0,
        status=EngagementStatus.PENDING,
    )


# ---- Bearing computation ----

class TestBearing:
    """Verify compass bearing from origin to target in ENU frame."""

    def test_due_north(self) -> None:
        """Target due north (positive y) should be 0 degrees."""
        bearing = compute_bearing((0, 0, 0), (0, 100, 0))
        assert bearing == pytest.approx(0.0, abs=0.01)

    def test_due_east(self) -> None:
        """Target due east (positive x) should be 90 degrees."""
        bearing = compute_bearing((0, 0, 0), (100, 0, 0))
        assert bearing == pytest.approx(90.0, abs=0.01)

    def test_due_south(self) -> None:
        """Target due south (negative y) should be 180 degrees."""
        bearing = compute_bearing((0, 0, 0), (0, -100, 0))
        assert bearing == pytest.approx(180.0, abs=0.01)

    def test_due_west(self) -> None:
        """Target due west (negative x) should be 270 degrees."""
        bearing = compute_bearing((0, 0, 0), (-100, 0, 0))
        assert bearing == pytest.approx(270.0, abs=0.01)

    def test_northeast_45(self) -> None:
        """Target at 45 degrees (equal east and north)."""
        bearing = compute_bearing((0, 0, 0), (100, 100, 0))
        assert bearing == pytest.approx(45.0, abs=0.01)

    def test_nonzero_origin(self) -> None:
        """Bearing is relative to the origin, not absolute."""
        bearing = compute_bearing((500, 500, 0), (500, 600, 0))
        assert bearing == pytest.approx(0.0, abs=0.01)


# ---- Elevation computation ----

class TestElevation:
    """Verify elevation angle from origin to target."""

    def test_level(self) -> None:
        """Target at same altitude should be 0 degrees elevation."""
        el = compute_elevation((0, 0, 0), (100, 0, 0))
        assert el == pytest.approx(0.0, abs=0.01)

    def test_straight_up(self) -> None:
        """Target directly above should be 90 degrees."""
        el = compute_elevation((0, 0, 0), (0, 0, 100))
        assert el == pytest.approx(90.0, abs=0.01)

    def test_45_degrees(self) -> None:
        """Target at equal horizontal distance and altitude should be 45 degrees."""
        el = compute_elevation((0, 0, 0), (100, 0, 100))
        assert el == pytest.approx(45.0, abs=0.01)

    def test_below_horizon(self) -> None:
        """Target below should give negative elevation."""
        el = compute_elevation((0, 0, 100), (100, 0, 0))
        assert el < 0.0

    def test_coincident_points(self) -> None:
        """Coincident points should return 0."""
        el = compute_elevation((5, 5, 5), (5, 5, 5))
        assert el == pytest.approx(0.0)


# ---- Velocity lead ----

class TestVelocityLead:
    """Verify lead computation for moving targets."""

    def test_instant_speed_no_lead(self) -> None:
        """Speed-of-light effectors (None speed) should have zero lead offset."""
        target_pos: Vec3 = (1000, 0, 100)
        target_vel: Vec3 = (0, 50, 0)
        effector_pos: Vec3 = (0, 0, 0)
        lead_b, lead_e = compute_lead(target_pos, target_vel, effector_pos, None)
        direct_b = compute_bearing(effector_pos, target_pos)
        direct_e = compute_elevation(effector_pos, target_pos)
        assert lead_b == pytest.approx(direct_b, abs=0.001)
        assert lead_e == pytest.approx(direct_e, abs=0.001)

    def test_jammer_has_no_lead(self) -> None:
        """JAMMER kind returns None speed, so lead equals direct."""
        assert effector_speed(DefenderKind.JAMMER) is None

    def test_laser_has_no_lead(self) -> None:
        """LASER kind returns None speed, so lead equals direct."""
        assert effector_speed(DefenderKind.LASER) is None

    def test_ew_has_no_lead(self) -> None:
        """EW kind returns None speed, so lead equals direct."""
        assert effector_speed(DefenderKind.EW) is None

    def test_hpm_has_no_lead(self) -> None:
        """HPM kind returns None speed, so lead equals direct."""
        assert effector_speed(DefenderKind.HPM) is None

    def test_interceptor_speed(self) -> None:
        """INTERCEPTOR default speed is 200 m/s."""
        assert effector_speed(DefenderKind.INTERCEPTOR) == 200.0

    def test_net_speed(self) -> None:
        """NET default speed is 50 m/s."""
        assert effector_speed(DefenderKind.NET) == 50.0

    def test_moving_target_lead_offset(self) -> None:
        """A target moving east should produce a lead bearing east of direct.

        Target at (0, 1000, 0) moving east at 50 m/s. Effector at origin with
        200 m/s projectile. Time of flight is 1000/200 = 5s. Predicted position
        is (250, 1000, 0). Direct bearing is 0 deg (north). Lead bearing should
        be atan2(250, 1000) ~ 14 degrees east of north.
        """
        target_pos: Vec3 = (0, 1000, 0)
        target_vel: Vec3 = (50, 0, 0)
        effector_pos: Vec3 = (0, 0, 0)
        lead_b, lead_e = compute_lead(target_pos, target_vel, effector_pos, 200.0)
        direct_b = compute_bearing(effector_pos, target_pos)
        assert direct_b == pytest.approx(0.0, abs=0.01)
        expected_lead = math.degrees(math.atan2(250, 1000))
        assert lead_b == pytest.approx(expected_lead, abs=0.1)

    def test_stationary_target_no_lead(self) -> None:
        """A stationary target should have zero lead offset even with finite speed."""
        target_pos: Vec3 = (500, 500, 100)
        target_vel: Vec3 = (0, 0, 0)
        effector_pos: Vec3 = (0, 0, 0)
        lead_b, lead_e = compute_lead(target_pos, target_vel, effector_pos, 200.0)
        direct_b = compute_bearing(effector_pos, target_pos)
        direct_e = compute_elevation(effector_pos, target_pos)
        assert lead_b == pytest.approx(direct_b, abs=0.001)
        assert lead_e == pytest.approx(direct_e, abs=0.001)

    def test_slow_projectile_more_lead(self) -> None:
        """Slower projectile should produce more lead than faster one."""
        target_pos: Vec3 = (0, 1000, 0)
        target_vel: Vec3 = (50, 0, 0)
        effector_pos: Vec3 = (0, 0, 0)
        lead_fast_b, _ = compute_lead(target_pos, target_vel, effector_pos, 200.0)
        lead_slow_b, _ = compute_lead(target_pos, target_vel, effector_pos, 50.0)
        direct_b = compute_bearing(effector_pos, target_pos)
        fast_offset = abs(lead_fast_b - direct_b)
        slow_offset = abs(lead_slow_b - direct_b)
        assert slow_offset > fast_offset


# ---- EffectorController ----

class TestEffectorController:
    """Verify the controller produces correct SlewCommands."""

    def test_basic_slew_command(self) -> None:
        """Controller should produce a valid SlewCommand with correct fields."""
        ctrl = EffectorController()
        defender = _make_defender(position=(0, 0, 0))
        cmd = ctrl.compute_slew(
            defender=defender,
            target_position=(0, 1000, 100),
            target_id="threat-1",
            priority=1,
            timestamp=100.0,
        )
        assert cmd.defender_id == "d1"
        assert cmd.target_id == "threat-1"
        assert cmd.bearing_deg == pytest.approx(0.0, abs=0.01)
        assert cmd.elevation_deg > 0.0
        assert cmd.range_m == pytest.approx(
            math.sqrt(1000**2 + 100**2), abs=0.1,
        )
        assert cmd.priority == 1
        assert cmd.timestamp == 100.0

    def test_jammer_slew_no_lead(self) -> None:
        """A JAMMER should have lead equal to direct bearing."""
        ctrl = EffectorController()
        defender = _make_defender(kind=DefenderKind.JAMMER)
        cmd = ctrl.compute_slew(
            defender=defender,
            target_position=(1000, 0, 50),
            target_velocity=(0, 100, 0),
            target_id="t1",
            timestamp=10.0,
        )
        assert cmd.bearing_deg == pytest.approx(cmd.lead_bearing_deg, abs=0.001)
        assert cmd.elevation_deg == pytest.approx(cmd.lead_elevation_deg, abs=0.001)

    def test_interceptor_slew_with_lead(self) -> None:
        """An INTERCEPTOR against a moving target should have lead != direct."""
        ctrl = EffectorController()
        defender = _make_defender(kind=DefenderKind.INTERCEPTOR)
        cmd = ctrl.compute_slew(
            defender=defender,
            target_position=(0, 1000, 0),
            target_velocity=(50, 0, 0),
            target_id="t1",
            timestamp=10.0,
        )
        assert cmd.lead_bearing_deg != pytest.approx(cmd.bearing_deg, abs=0.1)

    def test_to_dict_serialization(self) -> None:
        """SlewCommand.to_dict() should produce all expected keys."""
        ctrl = EffectorController()
        defender = _make_defender()
        cmd = ctrl.compute_slew(
            defender=defender,
            target_position=(500, 500, 100),
            target_id="t1",
            timestamp=50.0,
        )
        d = cmd.to_dict()
        expected_keys = {
            "defender_id", "target_id", "bearing_deg", "elevation_deg",
            "range_m", "lead_bearing_deg", "lead_elevation_deg",
            "priority", "timestamp",
        }
        assert set(d.keys()) == expected_keys


# ---- HandoffManager deconfliction ----

class TestHandoffManager:
    """Verify handoff queue management and bearing deconfliction."""

    def test_single_engagement(self) -> None:
        """One engagement should produce one slew command."""
        mgr = HandoffManager()
        defender = _make_defender()
        eng = _make_engagement()
        cmds = mgr.update(
            engagements=[eng],
            defenders={"d1": defender},
            target_positions={"t1": (0, 1000, 100)},
            target_velocities={"t1": (0, 0, 0)},
            now=100.0,
        )
        assert len(cmds) == 1
        assert cmds[0].target_id == "t1"

    def test_two_targets_different_bearing(self) -> None:
        """Two targets at different bearings should both survive deconfliction."""
        mgr = HandoffManager()
        defender = _make_defender()
        eng1 = _make_engagement(target_id="t1")
        eng2 = _make_engagement(target_id="t2")
        cmds = mgr.update(
            engagements=[eng1, eng2],
            defenders={"d1": defender},
            target_positions={
                "t1": (0, 1000, 100),
                "t2": (1000, 0, 100),
            },
            target_velocities={
                "t1": (0, 0, 0),
                "t2": (0, 0, 0),
            },
            now=100.0,
        )
        assert len(cmds) == 2

    def test_deconflict_same_bearing(self) -> None:
        """Two targets at nearly the same bearing from the same defender: keep closer.

        Both targets are due north (bearing ~0). The closer one should survive.
        """
        mgr = HandoffManager()
        defender = _make_defender()
        eng1 = _make_engagement(target_id="t_far")
        eng2 = _make_engagement(target_id="t_near")
        cmds = mgr.update(
            engagements=[eng1, eng2],
            defenders={"d1": defender},
            target_positions={
                "t_far": (0, 2000, 100),
                "t_near": (0, 500, 50),
            },
            target_velocities={
                "t_far": (0, 0, 0),
                "t_near": (0, 0, 0),
            },
            now=100.0,
        )
        assert len(cmds) == 1
        assert cmds[0].target_id == "t_near"

    def test_deconflict_different_defenders(self) -> None:
        """Same bearing but different defenders should not conflict."""
        mgr = HandoffManager()
        d1 = _make_defender(id="d1")
        d2 = _make_defender(id="d2", position=(100, 0, 0))
        eng1 = _make_engagement(defender_id="d1", target_id="t1")
        eng2 = _make_engagement(defender_id="d2", target_id="t2")
        cmds = mgr.update(
            engagements=[eng1, eng2],
            defenders={"d1": d1, "d2": d2},
            target_positions={
                "t1": (0, 1000, 100),
                "t2": (100, 1000, 100),
            },
            target_velocities={
                "t1": (0, 0, 0),
                "t2": (0, 0, 0),
            },
            now=100.0,
        )
        assert len(cmds) == 2

    def test_get_active_commands(self) -> None:
        """get_active_commands returns the last computed set."""
        mgr = HandoffManager()
        assert mgr.get_active_commands() == []
        defender = _make_defender()
        eng = _make_engagement()
        mgr.update(
            engagements=[eng],
            defenders={"d1": defender},
            target_positions={"t1": (0, 1000, 100)},
            target_velocities={"t1": (0, 0, 0)},
            now=100.0,
        )
        assert len(mgr.get_active_commands()) == 1

    def test_missing_defender_skipped(self) -> None:
        """Engagement with unknown defender_id should be silently skipped."""
        mgr = HandoffManager()
        eng = _make_engagement(defender_id="unknown")
        cmds = mgr.update(
            engagements=[eng],
            defenders={},
            target_positions={"t1": (0, 1000, 100)},
            target_velocities={"t1": (0, 0, 0)},
            now=100.0,
        )
        assert len(cmds) == 0

    def test_missing_target_position_skipped(self) -> None:
        """Engagement with unknown target position should be silently skipped."""
        mgr = HandoffManager()
        defender = _make_defender()
        eng = _make_engagement(target_id="unknown")
        cmds = mgr.update(
            engagements=[eng],
            defenders={"d1": defender},
            target_positions={},
            target_velocities={},
            now=100.0,
        )
        assert len(cmds) == 0


# ---- Priority ordering ----

class TestPriorityOrdering:
    """Verify priority assignment follows engagement order."""

    def test_priority_matches_engagement_order(self) -> None:
        """First engagement gets priority 1, second gets priority 2."""
        mgr = HandoffManager()
        d1 = _make_defender(id="d1")
        d2 = _make_defender(id="d2", position=(500, 0, 0))
        eng1 = _make_engagement(defender_id="d1", target_id="t1")
        eng2 = _make_engagement(defender_id="d2", target_id="t2")
        cmds = mgr.update(
            engagements=[eng1, eng2],
            defenders={"d1": d1, "d2": d2},
            target_positions={
                "t1": (0, 1000, 100),
                "t2": (1000, 0, 100),
            },
            target_velocities={
                "t1": (0, 0, 0),
                "t2": (0, 0, 0),
            },
            now=100.0,
        )
        priorities = [c.priority for c in cmds]
        assert priorities == [1, 2]


# ---- Serialization ----

class TestSerialization:
    """Verify slew_commands_to_dicts round-trips the data."""

    def test_slew_commands_to_dicts(self) -> None:
        """slew_commands_to_dicts should produce a list of dicts."""
        cmd = SlewCommand(
            defender_id="d1",
            target_id="t1",
            bearing_deg=45.123,
            elevation_deg=10.456,
            range_m=1234.567,
            lead_bearing_deg=46.0,
            lead_elevation_deg=10.5,
            priority=1,
            timestamp=100.0,
        )
        result = slew_commands_to_dicts([cmd])
        assert len(result) == 1
        assert result[0]["bearing_deg"] == 45.12
        assert result[0]["range_m"] == 1234.57


# ---- Bearing diff utility ----

class TestBearingDiff:
    """Verify angular difference wraps correctly."""

    def test_same_bearing(self) -> None:
        assert _bearing_diff(0.0, 0.0) == pytest.approx(0.0)

    def test_small_diff(self) -> None:
        assert _bearing_diff(10.0, 15.0) == pytest.approx(5.0)

    def test_wrap_around(self) -> None:
        assert _bearing_diff(355.0, 5.0) == pytest.approx(10.0)

    def test_opposite(self) -> None:
        assert _bearing_diff(0.0, 180.0) == pytest.approx(180.0)
