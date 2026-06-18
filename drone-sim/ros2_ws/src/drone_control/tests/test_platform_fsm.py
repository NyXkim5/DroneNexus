"""
test_platform_fsm.py

Comprehensive unit tests for the PlatformFSM.
No ROS2 dependency. Tests only the pure Python state machine.
"""

from __future__ import annotations

import pytest

from drone_control.platform_fsm import (
    InvalidTransitionError,
    PlatformFSM,
    PlatformState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fsm_at(state: PlatformState) -> PlatformFSM:
    """Create an FSM and advance it to the requested state."""
    fsm = PlatformFSM()
    paths = {
        PlatformState.DISCONNECTED: [],
        PlatformState.DISARMED: ['connect'],
        PlatformState.ARMED: ['connect', 'arm'],
        PlatformState.TAKING_OFF: ['connect', 'arm', 'takeoff'],
        PlatformState.FLYING: ['connect', 'arm', 'takeoff', 'altitude_reached'],
        PlatformState.LANDING: [
            'connect', 'arm', 'takeoff', 'altitude_reached', 'land',
        ],
        PlatformState.EMERGENCY: ['connect', 'arm', 'fault'],
    }
    for event in paths[state]:
        fsm.transition(event)
    return fsm


# ── Initial state ────────────────────────────────────────────────────────────


class TestInitialState:
    def test_starts_disconnected(self) -> None:
        fsm = PlatformFSM()
        assert fsm.state is PlatformState.DISCONNECTED


# ── Valid transitions ────────────────────────────────────────────────────────


class TestValidTransitions:
    def test_disconnected_to_disarmed(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISCONNECTED)
        assert fsm.transition('connect') is True
        assert fsm.state is PlatformState.DISARMED

    def test_disarmed_to_armed(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISARMED)
        assert fsm.transition('arm') is True
        assert fsm.state is PlatformState.ARMED

    def test_disarmed_to_disconnected(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISARMED)
        assert fsm.transition('disconnect') is True
        assert fsm.state is PlatformState.DISCONNECTED

    def test_armed_to_taking_off(self) -> None:
        fsm = _make_fsm_at(PlatformState.ARMED)
        assert fsm.transition('takeoff') is True
        assert fsm.state is PlatformState.TAKING_OFF

    def test_armed_to_disarmed(self) -> None:
        fsm = _make_fsm_at(PlatformState.ARMED)
        assert fsm.transition('disarm') is True
        assert fsm.state is PlatformState.DISARMED

    def test_armed_to_emergency(self) -> None:
        fsm = _make_fsm_at(PlatformState.ARMED)
        assert fsm.transition('fault') is True
        assert fsm.state is PlatformState.EMERGENCY

    def test_taking_off_to_flying(self) -> None:
        fsm = _make_fsm_at(PlatformState.TAKING_OFF)
        assert fsm.transition('altitude_reached') is True
        assert fsm.state is PlatformState.FLYING

    def test_taking_off_to_emergency(self) -> None:
        fsm = _make_fsm_at(PlatformState.TAKING_OFF)
        assert fsm.transition('fault') is True
        assert fsm.state is PlatformState.EMERGENCY

    def test_flying_to_landing(self) -> None:
        fsm = _make_fsm_at(PlatformState.FLYING)
        assert fsm.transition('land') is True
        assert fsm.state is PlatformState.LANDING

    def test_flying_to_emergency(self) -> None:
        fsm = _make_fsm_at(PlatformState.FLYING)
        assert fsm.transition('fault') is True
        assert fsm.state is PlatformState.EMERGENCY

    def test_landing_to_disarmed(self) -> None:
        fsm = _make_fsm_at(PlatformState.LANDING)
        assert fsm.transition('ground_contact') is True
        assert fsm.state is PlatformState.DISARMED

    def test_landing_to_emergency(self) -> None:
        fsm = _make_fsm_at(PlatformState.LANDING)
        assert fsm.transition('fault') is True
        assert fsm.state is PlatformState.EMERGENCY

    def test_emergency_to_disarmed(self) -> None:
        fsm = _make_fsm_at(PlatformState.EMERGENCY)
        assert fsm.transition('reset') is True
        assert fsm.state is PlatformState.DISARMED


# ── Invalid transitions ─────────────────────────────────────────────────────


class TestInvalidTransitions:
    @pytest.mark.parametrize('event', [
        'disconnect', 'arm', 'disarm', 'takeoff',
        'altitude_reached', 'land', 'ground_contact', 'fault', 'reset',
    ])
    def test_disconnected_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.DISCONNECTED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'takeoff', 'altitude_reached',
        'land', 'ground_contact', 'fault', 'reset',
    ])
    def test_disarmed_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.DISARMED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'disconnect', 'arm',
        'altitude_reached', 'land', 'ground_contact', 'reset',
    ])
    def test_armed_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.ARMED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'disconnect', 'arm', 'disarm',
        'takeoff', 'land', 'ground_contact', 'reset',
    ])
    def test_taking_off_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.TAKING_OFF)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'disconnect', 'arm', 'disarm',
        'takeoff', 'altitude_reached', 'ground_contact', 'reset',
    ])
    def test_flying_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.FLYING)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'disconnect', 'arm', 'disarm',
        'takeoff', 'altitude_reached', 'land', 'reset',
    ])
    def test_landing_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.LANDING)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)

    @pytest.mark.parametrize('event', [
        'connect', 'disconnect', 'arm', 'disarm',
        'takeoff', 'altitude_reached', 'land', 'ground_contact', 'fault',
    ])
    def test_emergency_rejects_invalid(self, event: str) -> None:
        fsm = _make_fsm_at(PlatformState.EMERGENCY)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(event)


# ── can_transition ───────────────────────────────────────────────────────────


class TestCanTransition:
    def test_returns_true_for_valid(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISCONNECTED)
        assert fsm.can_transition('connect') is True

    def test_returns_false_for_invalid(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISCONNECTED)
        assert fsm.can_transition('arm') is False

    def test_does_not_change_state(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISARMED)
        fsm.can_transition('arm')
        assert fsm.state is PlatformState.DISARMED


# ── on_transition callback ──────────────────────────────────────────────────


class TestOnTransitionCallback:
    def test_callback_fires_with_old_and_new_state(self) -> None:
        captured: list[tuple[PlatformState, PlatformState, str]] = []

        def cb(
            old: PlatformState,
            new: PlatformState,
            event: str,
        ) -> None:
            captured.append((old, new, event))

        fsm = PlatformFSM(on_transition=cb)
        fsm.transition('connect')

        assert len(captured) == 1
        assert captured[0] == (
            PlatformState.DISCONNECTED,
            PlatformState.DISARMED,
            'connect',
        )

    def test_callback_not_fired_on_invalid(self) -> None:
        captured: list[tuple[PlatformState, PlatformState, str]] = []

        def cb(
            old: PlatformState,
            new: PlatformState,
            event: str,
        ) -> None:
            captured.append((old, new, event))

        fsm = PlatformFSM(on_transition=cb)
        with pytest.raises(InvalidTransitionError):
            fsm.transition('arm')

        assert len(captured) == 0


# ── Full lifecycle ───────────────────────────────────────────────────────────


class TestFullLifecycle:
    def test_full_happy_path(self) -> None:
        fsm = PlatformFSM()

        fsm.transition('connect')
        assert fsm.state is PlatformState.DISARMED

        fsm.transition('arm')
        assert fsm.state is PlatformState.ARMED

        fsm.transition('takeoff')
        assert fsm.state is PlatformState.TAKING_OFF

        fsm.transition('altitude_reached')
        assert fsm.state is PlatformState.FLYING

        fsm.transition('land')
        assert fsm.state is PlatformState.LANDING

        fsm.transition('ground_contact')
        assert fsm.state is PlatformState.DISARMED


# ── Emergency ────────────────────────────────────────────────────────────────


class TestEmergency:
    @pytest.mark.parametrize('airborne_state', [
        PlatformState.ARMED,
        PlatformState.TAKING_OFF,
        PlatformState.FLYING,
        PlatformState.LANDING,
    ])
    def test_fault_from_airborne_states(
        self, airborne_state: PlatformState,
    ) -> None:
        fsm = _make_fsm_at(airborne_state)
        fsm.transition('fault')
        assert fsm.state is PlatformState.EMERGENCY

    def test_emergency_recovery(self) -> None:
        fsm = _make_fsm_at(PlatformState.EMERGENCY)
        fsm.transition('reset')
        assert fsm.state is PlatformState.DISARMED


# ── Events property ─────────────────────────────────────────────────────────


class TestEventsProperty:
    def test_disconnected_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISCONNECTED)
        assert fsm.events == ['connect']

    def test_disarmed_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.DISARMED)
        assert fsm.events == ['arm', 'disconnect']

    def test_armed_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.ARMED)
        assert fsm.events == ['disarm', 'fault', 'takeoff']

    def test_taking_off_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.TAKING_OFF)
        assert fsm.events == ['altitude_reached', 'fault']

    def test_flying_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.FLYING)
        assert fsm.events == ['fault', 'land']

    def test_landing_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.LANDING)
        assert fsm.events == ['fault', 'ground_contact']

    def test_emergency_events(self) -> None:
        fsm = _make_fsm_at(PlatformState.EMERGENCY)
        assert fsm.events == ['reset']


# ── Reset ────────────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_from_any_state(self) -> None:
        for state in PlatformState:
            fsm = _make_fsm_at(state)
            fsm.reset()
            assert fsm.state is PlatformState.DISCONNECTED
