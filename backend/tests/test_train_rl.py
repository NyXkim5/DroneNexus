"""
Smoke tests for the RL adversary training pipeline.

These tests verify that BulwarkEnv integrates with sb3, that curriculum
phase generation works, and that the eval loop runs with a random policy.
"""
from __future__ import annotations

import pytest

pytest.importorskip("stable_baselines3", reason="sb3 required for RL tests")
pytest.importorskip("gymnasium", reason="gymnasium required for RL tests")

from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env

from scripts.train_rl_adversary import (
    build_curriculum_phases,
    build_curriculum_scenario,
    evaluate_rl,
    make_env,
)
from wargame.gym_env import BulwarkEnv, TACTIC_ORDER
from wargame.scenario import load_scenario


@pytest.fixture()
def small_scenario():
    """Return the smallest built-in scenario for fast tests."""
    return load_scenario("skirmish_80")


class TestBulwarkEnvSb3:
    """Verify BulwarkEnv works with sb3 PPO."""

    def test_ppo_trains_100_steps(self, small_scenario):
        """PPO can train for 100 timesteps without crashing."""
        env = make_env(small_scenario, seed=42)
        model = PPO("MlpPolicy", env, verbose=0, seed=42, n_steps=32)
        model.learn(total_timesteps=100)
        env.close()

    def test_env_reset_and_step(self, small_scenario):
        """BulwarkEnv reset and step produce correct shapes."""
        env = make_env(small_scenario, seed=42)
        obs, info = env.reset()
        assert obs.shape == (8,)
        assert isinstance(info, dict)
        action = env.action_space.sample()
        obs2, reward, terminated, truncated, info2 = env.step(action)
        assert obs2.shape == (8,)
        assert isinstance(reward, float)
        env.close()


class TestCurriculum:
    """Verify curriculum phase generation."""

    def test_phases_sum_to_total(self):
        """Curriculum phases should sum to the total timesteps."""
        total = 9000
        phases = build_curriculum_phases(total)
        assert len(phases) == 3
        assert sum(p.timesteps for p in phases) == total

    def test_phases_difficulty_increases(self):
        """Swarm count should increase across phases."""
        phases = build_curriculum_phases(3000)
        assert phases[0].swarm_count < phases[1].swarm_count

    def test_build_curriculum_scenario(self, small_scenario):
        """Curriculum scenario builder returns valid Scenario objects."""
        phases = build_curriculum_phases(3000)
        easy = build_curriculum_scenario(small_scenario, phases[0])
        assert easy.swarm_count == 20
        hard = build_curriculum_scenario(small_scenario, phases[2])
        assert hard.swarm_count == small_scenario.swarm_count


class TestEvalLoop:
    """Verify evaluation with a random policy."""

    def test_eval_random_policy(self, small_scenario):
        """Eval loop runs with a freshly initialized (random) PPO model."""
        env = make_env(small_scenario, seed=42)
        model = PPO("MlpPolicy", env, verbose=0, seed=42, n_steps=32)
        results = evaluate_rl(model, small_scenario, episodes=2, seed=42)
        assert "leakers_mean" in results
        assert "tactic_distribution" in results
        assert results["episodes"] == 2
        env.close()
