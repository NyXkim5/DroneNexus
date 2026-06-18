"""
Gymnasium-compatible environment wrapping the BULWARK WargameRunner.

The RL agent plays as the ATTACKER (red force), choosing an AdaptiveTactic
each tick to defeat the layered defense. The observation is a normalized
feature vector extracted from the Frame metrics and world state. The action
space maps to the six AdaptiveTactic enum values.

The runner's individual _collect_tick() and _step() methods are synchronous.
This env calls them directly, avoiding asyncio inside the Gym loop. The
async source.start() is handled once in reset() via asyncio.run().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Tuple

import gymnasium
import numpy as np
from gymnasium import spaces

from attacker.adaptive import AdaptiveTactic, AttackerObservation
from wargame.frame import Frame, Metrics
from wargame.runner import WargameRunner
from wargame.scenario import Scenario

logger = logging.getLogger("overwatch.wargame.gym")

# Ordered list of tactics matching Discrete(6) action indices.
TACTIC_ORDER: Tuple[AdaptiveTactic, ...] = (
    AdaptiveTactic.SATURATE,
    AdaptiveTactic.FEINT_AND_STRIKE,
    AdaptiveTactic.SPLIT_AND_FLANK,
    AdaptiveTactic.LOW_AND_SLOW,
    AdaptiveTactic.SACRIFICE_PROBE,
    AdaptiveTactic.SWARM_REFORM,
)

# Number of features in the observation vector.
OBS_DIM = 8

# Default reward constants.
REWARD_PER_LEAKER = 10.0
REWARD_PER_KILL = -0.5
REWARD_PER_TICK = -0.01
REWARD_WIN_BONUS = 100.0

RewardFn = Callable[["BulwarkEnv", Frame, Frame], float]


def _default_reward(
    env: "BulwarkEnv", prev_frame: Frame, curr_frame: Frame
) -> float:
    """Compute the default attacker reward for one tick transition."""
    new_leakers = curr_frame.metrics.leakers - prev_frame.metrics.leakers
    new_kills = curr_frame.metrics.intercepts - prev_frame.metrics.intercepts
    reward = (
        REWARD_PER_LEAKER * new_leakers
        + REWARD_PER_KILL * new_kills
        + REWARD_PER_TICK
    )
    if curr_frame.done:
        ratio = curr_frame.metrics.cost_exchange_ratio
        if ratio is not None and ratio < 1.0:
            reward += REWARD_WIN_BONUS
    return reward


def _extract_observation(
    frame: Frame,
    initial_count: int,
    max_ticks: int,
    total_effectors: int,
) -> np.ndarray:
    """Build a normalized observation vector from a Frame."""
    m = frame.metrics
    destroyed = m.intercepts
    remaining = initial_count - destroyed - m.leakers
    obs = np.array(
        [
            max(0.0, remaining) / max(1, initial_count),
            destroyed / max(1, initial_count),
            m.leakers / max(1, initial_count),
            min(1.0, m.intercept_rate / 1.0),
            _active_effectors_ratio(frame, total_effectors),
            _coverage_gap_norm(frame),
            m.tick / max(1, max_ticks),
            min(1.0, (m.cost_exchange_ratio or 2.0) / 2.0),
        ],
        dtype=np.float32,
    )
    return np.clip(obs, 0.0, 1.0)


def _active_effectors_ratio(frame: Frame, total: int) -> float:
    """Fraction of defenders still in READY or RELOADING status."""
    if total == 0:
        return 0.0
    active = sum(
        1
        for d in frame.defenders
        if d.status.value in ("READY", "RELOADING")
    )
    return active / total


def _coverage_gap_norm(frame: Frame) -> float:
    """Normalized coverage gap count. Placeholder returns 0 when unavailable."""
    return 0.0


class BulwarkEnv(gymnasium.Env):
    """Gymnasium environment for RL adversary training against BULWARK.

    The agent selects an AdaptiveTactic each tick. The environment advances
    the wargame one tick and returns a normalized observation, a scalar
    reward, and termination signals.
    """

    metadata: Dict[str, Any] = {"render_modes": []}

    def __init__(
        self,
        scenario: Scenario,
        reward_fn: Optional[RewardFn] = None,
        max_steps: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._scenario = scenario
        self._reward_fn = reward_fn or _default_reward
        self._max_steps = max_steps or scenario.max_ticks

        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(OBS_DIM,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(len(TACTIC_ORDER))

        self._runner: Optional[WargameRunner] = None
        self._initial_count: int = scenario.swarm_count
        self._total_effectors: int = sum(
            d.count for d in scenario.defenders
        )
        self._prev_frame: Optional[Frame] = None
        self._current_frame: Optional[Frame] = None
        self._tick: int = 0

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Create a fresh runner and return the initial observation."""
        super().reset(seed=seed)
        if seed is not None:
            self._scenario.seed = seed
        self._runner = WargameRunner(self._scenario)
        self._tick = 0
        _start_source(self._runner)
        initial_frame = self._advance_one_tick()
        self._prev_frame = initial_frame
        self._current_frame = initial_frame
        obs = self._observe()
        return obs, self._build_info()

    def step(
        self, action: int
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Apply a tactic and advance the wargame one tick."""
        assert self._runner is not None, "call reset() before step()"
        tactic = TACTIC_ORDER[action]
        self._inject_tactic(tactic)
        self._prev_frame = self._current_frame
        self._current_frame = self._advance_one_tick()
        obs = self._observe()
        reward = self._reward_fn(
            self, self._prev_frame, self._current_frame
        )
        terminated = self._current_frame.done
        truncated = self._tick >= self._max_steps
        return obs, reward, terminated, truncated, self._build_info()

    def _advance_one_tick(self) -> Frame:
        """Run one synchronous tick of the wargame and return the Frame."""
        assert self._runner is not None
        detections = self._runner._collect_tick()
        frame = self._runner._step(detections)
        self._tick += 1
        return frame

    def _inject_tactic(self, tactic: AdaptiveTactic) -> None:
        """Force the swarm toward the chosen tactic's waypoints.

        Uses the AdaptiveAttackerAI.apply_tactic to compute per-drone
        waypoints and writes them into the swarm's drone target positions.
        """
        assert self._runner is not None
        swarm = self._runner.world.swarm
        positions = [d.position for d in swarm.drones if not d.arrived]
        velocities = [d.velocity for d in swarm.drones if not d.arrived]
        site = self._runner.world.site.position
        if not positions:
            return
        from attacker.adaptive import AdaptiveAttackerAI

        ai = AdaptiveAttackerAI()
        waypoints = ai.apply_tactic(
            tactic, positions, velocities, site
        )
        alive_drones = [d for d in swarm.drones if not d.arrived]
        for drone, wp in zip(alive_drones, waypoints):
            drone.target = wp

    def _observe(self) -> np.ndarray:
        """Extract observation vector from the current frame."""
        assert self._current_frame is not None
        return _extract_observation(
            self._current_frame,
            self._initial_count,
            self._max_steps,
            self._total_effectors,
        )

    def _build_info(self) -> Dict[str, Any]:
        """Build the info dict from current frame metrics."""
        assert self._current_frame is not None
        m = self._current_frame.metrics
        tactic_name = "unknown"
        if self._tick > 0:
            tactic_name = TACTIC_ORDER[0].value
        return {
            "active_hostiles": m.active_hostiles,
            "leakers": m.leakers,
            "intercepts": m.intercepts,
            "cost_exchange_ratio": m.cost_exchange_ratio,
            "tactic": tactic_name,
            "tick": m.tick,
        }


def _start_source(runner: WargameRunner) -> None:
    """Start the runner's sensor source, bridging async to sync."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(
                asyncio.run, runner._source.start()
            ).result()
    else:
        asyncio.run(runner._source.start())
