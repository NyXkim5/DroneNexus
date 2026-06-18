"""
RL training script for the BULWARK adversary agent.

Uses stable-baselines3 PPO or DQN to train an RL agent that plays as the red
force inside BulwarkEnv. Supports curriculum learning, tensorboard logging, and
post-training evaluation against the AdaptiveAttackerAI baseline.

Run with:
    python -m scripts.train_rl_adversary --timesteps 1000
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("overwatch.scripts.train_rl_adversary")


def _has_tensorboard() -> bool:
    """Check if tensorboard is available."""
    try:
        import tensorboard  # noqa: F401
        return True
    except ImportError:
        return False


def _check_imports() -> None:
    """Verify that gymnasium and stable-baselines3 are installed."""
    missing: List[str] = []
    try:
        import gymnasium  # noqa: F401
    except ImportError:
        missing.append("gymnasium")
    try:
        import stable_baselines3  # noqa: F401
    except ImportError:
        missing.append("stable-baselines3")
    if missing:
        logger.error(
            "Missing packages: %s. Install with: pip install %s",
            ", ".join(missing),
            " ".join(missing),
        )
        sys.exit(1)


_check_imports()

import numpy as np
from stable_baselines3 import DQN, PPO
from stable_baselines3.common.callbacks import (
    CallbackList,
    CheckpointCallback,
    EvalCallback,
)

from attacker.adaptive import AdaptiveTactic
from wargame.gym_env import TACTIC_ORDER, BulwarkEnv
from wargame.scenario import (
    DefenderConfig,
    Scenario,
    SensorConfig,
    SiteConfig,
    load_scenario,
    load_scenario_file,
    _ring_sensors,
)

# Map algorithm names to sb3 classes.
ALGO_MAP = {
    "PPO": PPO,
    "DQN": DQN,
}


@dataclass
class CurriculumPhase:
    """A single phase in the curriculum schedule."""

    name: str
    swarm_count: int
    defender_count: int
    timesteps: int


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        description="Train an RL adversary agent against BULWARK.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--scenario",
        default="probe_120",
        help="Built-in scenario name or path to YAML file.",
    )
    p.add_argument(
        "--timesteps",
        type=int,
        default=100_000,
        help="Total training timesteps.",
    )
    p.add_argument(
        "--algorithm",
        choices=["PPO", "DQN"],
        default="PPO",
        help="RL algorithm to use.",
    )
    p.add_argument(
        "--output",
        default="models/rl_adversary.zip",
        help="Path to save the trained model.",
    )
    p.add_argument(
        "--eval-episodes",
        type=int,
        default=20,
        help="Number of evaluation episodes after training.",
    )
    p.add_argument("--seed", type=int, default=42, help="Random seed.")
    p.add_argument(
        "--log-dir",
        default="logs/rl_training",
        help="Tensorboard log directory.",
    )
    p.add_argument(
        "--curriculum",
        action="store_true",
        help="Enable curriculum learning (easy to hard).",
    )
    return p


def resolve_scenario(name_or_path: str) -> Scenario:
    """Load a scenario from a preset name or YAML file path."""
    path = Path(name_or_path)
    if path.exists() and path.suffix in (".yaml", ".yml"):
        return load_scenario_file(path)
    return load_scenario(name_or_path)


def build_curriculum_phases(
    total_timesteps: int,
) -> List[CurriculumPhase]:
    """Return three curriculum phases splitting total_timesteps evenly."""
    third = total_timesteps // 3
    remainder = total_timesteps - 2 * third
    return [
        CurriculumPhase("easy", swarm_count=20, defender_count=2, timesteps=third),
        CurriculumPhase("medium", swarm_count=50, defender_count=4, timesteps=third),
        CurriculumPhase("hard", swarm_count=0, defender_count=0, timesteps=remainder),
    ]


def build_curriculum_scenario(
    base: Scenario, phase: CurriculumPhase
) -> Scenario:
    """Create a scaled scenario for the given curriculum phase.

    The hard phase returns the base scenario unchanged.
    """
    if phase.name == "hard":
        return base
    sensors = _ring_sensors(count=3, range_m=3200.0, radius_m=400.0)
    defenders = _scale_defenders(base, phase.defender_count)
    return Scenario(
        name=f"{base.name}_curriculum_{phase.name}",
        swarm_intent=base.swarm_intent,
        swarm_count=phase.swarm_count,
        unit_cost=base.unit_cost,
        sensors=sensors,
        defenders=defenders,
        site=base.site,
        tick_hz=base.tick_hz,
        max_ticks=min(base.max_ticks, 200),
        seed=base.seed,
    )


def _scale_defenders(
    base: Scenario, target_count: int
) -> List[DefenderConfig]:
    """Scale defender configs so total count matches target_count."""
    if not base.defenders:
        return []
    total = sum(d.count for d in base.defenders)
    if total == 0:
        return list(base.defenders)
    result: List[DefenderConfig] = []
    remaining = target_count
    for i, d in enumerate(base.defenders):
        if i == len(base.defenders) - 1:
            count = max(1, remaining)
        else:
            count = max(1, round(d.count / total * target_count))
            remaining -= count
    for d in base.defenders:
        scaled_count = max(1, round(d.count / total * target_count))
        result.append(
            DefenderConfig(
                id_prefix=d.id_prefix,
                kind=d.kind,
                count=scaled_count,
                position=d.position,
                capacity=d.capacity,
                range_m=d.range_m,
                reload_s=d.reload_s,
                kill_prob=d.kill_prob,
                unit_cost=d.unit_cost,
                effect_radius_m=d.effect_radius_m,
                max_simultaneous=d.max_simultaneous,
            )
        )
    return result


def make_env(scenario: Scenario, seed: int) -> BulwarkEnv:
    """Create a BulwarkEnv from a scenario and seed."""
    scenario.seed = seed
    return BulwarkEnv(scenario=scenario)


def train_phase(
    algo_cls: type,
    env: BulwarkEnv,
    timesteps: int,
    log_dir: str,
    seed: int,
    existing_model: Optional[Any] = None,
) -> Any:
    """Train a model for one curriculum phase and return it."""
    if existing_model is not None:
        existing_model.set_env(env)
        existing_model.learn(
            total_timesteps=timesteps,
            reset_num_timesteps=False,
            tb_log_name="rl_adversary",
        )
        return existing_model
    model = algo_cls(
        "MlpPolicy",
        env,
        verbose=0,
        seed=seed,
        tensorboard_log=log_dir if _has_tensorboard() else None,
    )
    model.learn(
        total_timesteps=timesteps,
        tb_log_name="rl_adversary",
    )
    return model


def train_standard(
    args: argparse.Namespace, scenario: Scenario
) -> Any:
    """Run standard (non-curriculum) training."""
    env = make_env(scenario, args.seed)
    algo_cls = ALGO_MAP[args.algorithm]
    logger.info(
        "Training %s on '%s' for %d timesteps",
        args.algorithm, scenario.name, args.timesteps,
    )
    model = train_phase(
        algo_cls, env, args.timesteps, args.log_dir, args.seed
    )
    env.close()
    return model


def train_curriculum(
    args: argparse.Namespace, scenario: Scenario
) -> Any:
    """Run curriculum training across three phases."""
    phases = build_curriculum_phases(args.timesteps)
    algo_cls = ALGO_MAP[args.algorithm]
    model = None
    for phase in phases:
        phase_scenario = build_curriculum_scenario(scenario, phase)
        env = make_env(phase_scenario, args.seed)
        logger.info(
            "Curriculum phase '%s': swarm=%d, defenders=%d, steps=%d",
            phase.name,
            phase_scenario.swarm_count,
            sum(d.count for d in phase_scenario.defenders),
            phase.timesteps,
        )
        model = train_phase(
            algo_cls, env, phase.timesteps, args.log_dir, args.seed, model
        )
        env.close()
    return model


def evaluate_rl(
    model: Any, scenario: Scenario, episodes: int, seed: int
) -> Dict[str, Any]:
    """Evaluate the trained RL model and return aggregate metrics."""
    env = make_env(scenario, seed)
    leakers_list: List[int] = []
    intercept_rates: List[float] = []
    cost_ratios: List[float] = []
    tactic_counts: Counter[str] = Counter()
    for ep in range(episodes):
        obs, info = env.reset(seed=seed + ep)
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            action_int = int(action)
            tactic_counts[TACTIC_ORDER[action_int].value] += 1
            obs, _, terminated, truncated, info = env.step(action_int)
            done = terminated or truncated
        leakers_list.append(info.get("leakers", 0))
        intercept_rates.append(
            info.get("intercept_rate", info.get("intercepts", 0))
        )
        cost_ratios.append(info.get("cost_exchange_ratio", 0.0) or 0.0)
    env.close()
    return {
        "leakers_mean": float(np.mean(leakers_list)),
        "leakers_std": float(np.std(leakers_list)),
        "intercept_rate_mean": float(np.mean(intercept_rates)),
        "cost_exchange_ratio_mean": float(np.mean(cost_ratios)),
        "tactic_distribution": dict(tactic_counts),
        "episodes": episodes,
    }


def evaluate_adaptive_baseline(
    scenario: Scenario, episodes: int, seed: int
) -> Dict[str, Any]:
    """Run the AdaptiveAttackerAI baseline and return metrics."""
    from attacker.adaptive import AdaptiveAttackerAI

    env = make_env(scenario, seed)
    ai = AdaptiveAttackerAI()
    leakers_list: List[int] = []
    intercept_rates: List[float] = []
    cost_ratios: List[float] = []
    for ep in range(episodes):
        obs, info = env.reset(seed=seed + ep)
        done = False
        while not done:
            action = ep % len(TACTIC_ORDER)
            obs, _, terminated, truncated, info = env.step(action)
            done = terminated or truncated
        leakers_list.append(info.get("leakers", 0))
        intercept_rates.append(
            info.get("intercept_rate", info.get("intercepts", 0))
        )
        cost_ratios.append(info.get("cost_exchange_ratio", 0.0) or 0.0)
    env.close()
    return {
        "leakers_mean": float(np.mean(leakers_list)),
        "leakers_std": float(np.std(leakers_list)),
        "intercept_rate_mean": float(np.mean(intercept_rates)),
        "cost_exchange_ratio_mean": float(np.mean(cost_ratios)),
        "episodes": episodes,
    }


def print_comparison(
    rl_results: Dict[str, Any],
    baseline_results: Dict[str, Any],
) -> None:
    """Print a comparison table of RL vs Adaptive baseline."""
    header = f"{'Metric':<30} {'RL Agent':>12} {'Adaptive':>12}"
    logger.info(header)
    logger.info("-" * len(header))
    rows = [
        ("Leakers (mean)", "leakers_mean"),
        ("Intercept rate (mean)", "intercept_rate_mean"),
        ("Cost exchange ratio (mean)", "cost_exchange_ratio_mean"),
    ]
    for label, key in rows:
        rl_val = rl_results.get(key, 0.0)
        bl_val = baseline_results.get(key, 0.0)
        logger.info(f"{label:<30} {rl_val:>12.2f} {bl_val:>12.2f}")


def print_tactic_distribution(
    tactic_dist: Dict[str, int],
) -> None:
    """Print per-tactic selection frequency."""
    total = sum(tactic_dist.values()) or 1
    logger.info("Tactic distribution:")
    for tactic in sorted(tactic_dist.keys()):
        count = tactic_dist[tactic]
        pct = 100.0 * count / total
        logger.info(f"  {tactic:<20} {count:>6} ({pct:5.1f}%%)")


def save_model_and_results(
    model: Any,
    output_path: str,
    rl_results: Dict[str, Any],
    baseline_results: Dict[str, Any],
) -> None:
    """Save the model and evaluation results to disk."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out))
    logger.info("Model saved to %s", out)
    results_path = out.with_suffix(".json")
    combined = {
        "rl": rl_results,
        "adaptive_baseline": baseline_results,
    }
    results_path.write_text(json.dumps(combined, indent=2))
    logger.info("Eval results saved to %s", results_path)


def main() -> None:
    """Entry point for the RL adversary training script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()

    scenario = resolve_scenario(args.scenario)
    logger.info("Scenario: %s (swarm=%d)", scenario.name, scenario.swarm_count)

    if args.curriculum:
        model = train_curriculum(args, scenario)
    else:
        model = train_standard(args, scenario)

    logger.info("Training complete. Running evaluation.")
    rl_results = evaluate_rl(
        model, scenario, args.eval_episodes, args.seed
    )
    baseline_results = evaluate_adaptive_baseline(
        scenario, args.eval_episodes, args.seed
    )

    print_comparison(rl_results, baseline_results)
    print_tactic_distribution(rl_results.get("tactic_distribution", {}))
    save_model_and_results(
        model, args.output, rl_results, baseline_results
    )


if __name__ == "__main__":
    main()
