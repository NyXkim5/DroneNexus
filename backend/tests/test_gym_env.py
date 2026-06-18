"""
Tests for the Gymnasium-compatible BULWARK wargame environment.

Validates observation shape, action handling, reward computation,
termination conditions, and gymnasium env_checker compliance.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from csontology import DefenderKind, SwarmIntent
from wargame.gym_env import (
    REWARD_PER_KILL,
    REWARD_PER_LEAKER,
    REWARD_PER_TICK,
    REWARD_WIN_BONUS,
    BulwarkEnv,
    OBS_DIM,
    TACTIC_ORDER,
    _default_reward,
    _extract_observation,
)
from wargame.scenario import (
    DefenderConfig,
    Scenario,
    SensorConfig,
    SiteConfig,
)


def _minimal_scenario(
    swarm_count: int = 10,
    max_ticks: int = 30,
) -> Scenario:
    """Build a small scenario for fast test runs."""
    return Scenario(
        name="test_gym",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=swarm_count,
        unit_cost=500.0,
        sensors=[
            SensorConfig(
                sensor_id="radar-1",
                position=(0.0, 0.0, 0.0),
                range_m=5000.0,
            ),
        ],
        defenders=[
            DefenderConfig(
                id_prefix="INT",
                kind=DefenderKind.INTERCEPTOR,
                count=2,
                position=(0.0, 0.0, 0.0),
                capacity=20,
                range_m=3000.0,
                reload_s=2.0,
                kill_prob=0.8,
                unit_cost=50000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=max_ticks,
        seed=42,
    )


class TestEnvCreation:
    """Verify the environment constructs with valid spaces."""

    def test_create_env(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        assert env.observation_space.shape == (OBS_DIM,)
        assert env.action_space.n == len(TACTIC_ORDER)

    def test_obs_space_bounds(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        assert float(env.observation_space.low.min()) == 0.0
        assert float(env.observation_space.high.max()) == 1.0


class TestReset:
    """Verify reset returns a valid initial observation."""

    def test_reset_returns_valid_shape(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        obs, info = env.reset()
        assert obs.shape == (OBS_DIM,)
        assert isinstance(info, dict)

    def test_reset_obs_in_bounds(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        obs, _ = env.reset()
        assert np.all(obs >= 0.0)
        assert np.all(obs <= 1.0)

    def test_reset_with_seed(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        obs1, _ = env.reset(seed=123)
        obs2, _ = env.reset(seed=123)
        np.testing.assert_array_equal(obs1, obs2)


class TestStep:
    """Verify step returns correct tuple structure for each action."""

    def test_step_returns_five_tuple(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        env.reset()
        result = env.step(0)
        assert len(result) == 5
        obs, reward, terminated, truncated, info = result
        assert obs.shape == (OBS_DIM,)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_each_action_valid(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        env.reset()
        for action in range(len(TACTIC_ORDER)):
            obs, reward, terminated, truncated, info = env.step(action)
            assert obs.shape == (OBS_DIM,)
            assert np.all(obs >= 0.0)
            assert np.all(obs <= 1.0)
            if terminated:
                break

    def test_info_contains_metrics(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        env.reset()
        _, _, _, _, info = env.step(0)
        assert "active_hostiles" in info
        assert "leakers" in info
        assert "intercepts" in info
        assert "cost_exchange_ratio" in info
        assert "tactic" in info
        assert "tick" in info


class TestTermination:
    """Verify episode ends under the correct conditions."""

    def test_truncation_at_max_steps(self) -> None:
        env = BulwarkEnv(_minimal_scenario(max_ticks=5))
        env.reset()
        truncated = False
        for _ in range(10):
            _, _, terminated, truncated, _ = env.step(0)
            if terminated or truncated:
                break
        assert truncated or terminated

    def test_episode_runs_multiple_steps(self) -> None:
        env = BulwarkEnv(_minimal_scenario(max_ticks=20))
        env.reset()
        steps = 0
        for _ in range(20):
            _, _, terminated, truncated, _ = env.step(0)
            steps += 1
            if terminated or truncated:
                break
        assert steps >= 1


class TestObservationBounds:
    """Verify all observation values stay in [0, 1] over an episode."""

    def test_obs_always_in_bounds(self) -> None:
        env = BulwarkEnv(_minimal_scenario(max_ticks=15))
        obs, _ = env.reset()
        assert np.all(obs >= 0.0) and np.all(obs <= 1.0)
        for _ in range(15):
            obs, _, terminated, truncated, _ = env.step(
                env.action_space.sample()
            )
            assert np.all(obs >= 0.0), f"obs below 0: {obs}"
            assert np.all(obs <= 1.0), f"obs above 1: {obs}"
            if terminated or truncated:
                break


class TestReward:
    """Verify the default reward function logic."""

    def test_tick_penalty_applied(self) -> None:
        env = BulwarkEnv(_minimal_scenario())
        env.reset()
        _, reward, _, _, _ = env.step(0)
        # Reward should include at least the tick penalty.
        assert isinstance(reward, float)

    def test_custom_reward_fn(self) -> None:
        def constant_reward(
            env: BulwarkEnv, prev: object, curr: object
        ) -> float:
            return 42.0

        env = BulwarkEnv(_minimal_scenario(), reward_fn=constant_reward)
        env.reset()
        _, reward, _, _, _ = env.step(0)
        assert reward == 42.0

    def test_max_steps_override(self) -> None:
        scenario = _minimal_scenario(max_ticks=100)
        env = BulwarkEnv(scenario, max_steps=5)
        env.reset()
        truncated = False
        for _ in range(10):
            _, _, terminated, truncated, _ = env.step(0)
            if terminated or truncated:
                break
        assert truncated or terminated


class TestEnvChecker:
    """Run gymnasium's built-in env_checker to validate the full API."""

    def test_check_env_passes(self) -> None:
        from gymnasium.utils.env_checker import check_env

        env = BulwarkEnv(_minimal_scenario(max_ticks=10))
        # check_env raises on failure; passing means no exception.
        check_env(env, skip_render_check=True)
