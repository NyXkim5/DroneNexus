"""
Per-drone mission state machine.
States: IDLE -> ARMED -> TAKING_OFF -> IN_MISSION -> LOITERING -> RETURNING -> LANDED
"""
from typing import Dict, Set, Callable, List
from protocol import MissionState
import logging

logger = logging.getLogger("overwatch.state_machine")

VALID_TRANSITIONS: Dict[MissionState, Set[MissionState]] = {
    MissionState.IDLE: {MissionState.ARMED},
    MissionState.ARMED: {MissionState.TAKING_OFF, MissionState.IDLE},
    MissionState.TAKING_OFF: {MissionState.IN_MISSION, MissionState.IDLE},
    MissionState.IN_MISSION: {MissionState.LOITERING, MissionState.RETURNING, MissionState.IDLE},
    MissionState.LOITERING: {MissionState.IN_MISSION, MissionState.RETURNING, MissionState.IDLE},
    MissionState.RETURNING: {MissionState.LANDED, MissionState.IDLE},
    MissionState.LANDED: {MissionState.IDLE, MissionState.ARMED},
}


class MissionStateMachine:
    """Finite state machine for a single drone's mission lifecycle."""

    def __init__(self, drone_id: str):
        self.drone_id = drone_id
        self.state = MissionState.IDLE
        self._listeners: List[Callable] = []

    def transition(self, target: MissionState) -> bool:
        if isinstance(target, str):
            target = MissionState(target)
        if target in VALID_TRANSITIONS.get(self.state, set()):
            old = self.state
            self.state = target
            logger.info(f"[{self.drone_id}] {old.value} -> {target.value}")
            for cb in self._listeners:
                cb(self.drone_id, old, target)
            return True
        logger.warning(f"[{self.drone_id}] Invalid transition: {self.state.value} -> {target.value}")
        return False

    def on_transition(self, callback: Callable) -> None:
        self._listeners.append(callback)

    def force_idle(self) -> None:
        old = self.state
        self.state = MissionState.IDLE
        for cb in self._listeners:
            cb(self.drone_id, old, MissionState.IDLE)
