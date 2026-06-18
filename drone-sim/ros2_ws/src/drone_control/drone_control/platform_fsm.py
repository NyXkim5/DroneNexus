"""
platform_fsm.py

Declarative Platform State Machine for DroneNexus flight controller.
Inspired by Aerostack2's platform_state_machine pattern.

Pure Python with zero ROS2 dependencies for full unit testability.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PlatformState(Enum):
    """Platform lifecycle states."""
    DISCONNECTED = 'DISCONNECTED'
    DISARMED = 'DISARMED'
    ARMED = 'ARMED'
    TAKING_OFF = 'TAKING_OFF'
    FLYING = 'FLYING'
    LANDING = 'LANDING'
    EMERGENCY = 'EMERGENCY'


class InvalidTransitionError(Exception):
    """Raised when an event is not valid for the current state."""

    def __init__(self, state: PlatformState, event: str) -> None:
        self.state = state
        self.event = event
        super().__init__(
            f'Invalid transition: event "{event}" '
            f'not allowed in state {state.value}'
        )


# Transition table: (from_state, event) -> to_state
_TRANSITIONS: Dict[Tuple[PlatformState, str], PlatformState] = {
    (PlatformState.DISCONNECTED, 'connect'): PlatformState.DISARMED,
    (PlatformState.DISARMED, 'arm'): PlatformState.ARMED,
    (PlatformState.DISARMED, 'disconnect'): PlatformState.DISCONNECTED,
    (PlatformState.ARMED, 'takeoff'): PlatformState.TAKING_OFF,
    (PlatformState.ARMED, 'disarm'): PlatformState.DISARMED,
    (PlatformState.ARMED, 'fault'): PlatformState.EMERGENCY,
    (PlatformState.TAKING_OFF, 'altitude_reached'): PlatformState.FLYING,
    (PlatformState.TAKING_OFF, 'fault'): PlatformState.EMERGENCY,
    (PlatformState.FLYING, 'land'): PlatformState.LANDING,
    (PlatformState.FLYING, 'fault'): PlatformState.EMERGENCY,
    (PlatformState.LANDING, 'ground_contact'): PlatformState.DISARMED,
    (PlatformState.LANDING, 'fault'): PlatformState.EMERGENCY,
    (PlatformState.EMERGENCY, 'reset'): PlatformState.DISARMED,
}

# All known event names
ALL_EVENTS: FrozenSet[str] = frozenset({
    'connect', 'disconnect', 'arm', 'disarm', 'takeoff',
    'altitude_reached', 'land', 'ground_contact', 'fault', 'reset',
})


def _events_for_state(state: PlatformState) -> List[str]:
    """Return sorted list of valid events for a given state."""
    return sorted(
        event for (s, event) in _TRANSITIONS if s is state
    )


class PlatformFSM:
    """Deterministic finite state machine for platform lifecycle management."""

    def __init__(
        self,
        on_transition: Optional[Callable[[PlatformState, PlatformState, str], None]] = None,
    ) -> None:
        self._state = PlatformState.DISCONNECTED
        self._on_transition = on_transition

    @property
    def state(self) -> PlatformState:
        """Return the current platform state."""
        return self._state

    @property
    def events(self) -> List[str]:
        """Return valid events from the current state."""
        return _events_for_state(self._state)

    def can_transition(self, event: str) -> bool:
        """Check whether an event is valid without executing it."""
        return (self._state, event) in _TRANSITIONS

    def transition(self, event: str) -> bool:
        """Attempt a state transition triggered by the given event.

        Returns True if the transition was executed.
        Raises InvalidTransitionError if the event is not valid.
        """
        if not self.can_transition(event):
            raise InvalidTransitionError(self._state, event)

        old_state = self._state
        new_state = _TRANSITIONS[(self._state, event)]
        self._state = new_state

        logger.info(
            'FSM transition: %s -[%s]-> %s',
            old_state.value, event, new_state.value,
        )

        if self._on_transition is not None:
            self._on_transition(old_state, new_state, event)

        return True

    def reset(self) -> None:
        """Force FSM back to DISCONNECTED. For testing and initialization only."""
        old_state = self._state
        self._state = PlatformState.DISCONNECTED
        logger.info(
            'FSM reset: %s -> DISCONNECTED', old_state.value,
        )
