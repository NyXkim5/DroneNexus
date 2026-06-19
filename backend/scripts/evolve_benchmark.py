"""
Multi-generation benchmark harness for the BULWARK counter-swarm wargame.

Runs all wargame scenarios, records results as JSON, and tracks improvement
across generations. This is the measurement tool for the optimization loop.

Run from cwd=backend:
  python -m scripts.evolve_benchmark --gen 0
  python -m scripts.evolve_benchmark --gen 1
  python -m scripts.evolve_benchmark --compare 0 1
  python -m scripts.evolve_benchmark --gen 2 --scenarios probe_120,skirmish_80
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import load_scenario

logger = logging.getLogger("overwatch.evolve_benchmark")

ALL_SCENARIOS = [
    "skirmish_80",
    "probe_120",
    "decoy_300",
    "contested_500",
    "saturation_1000",
    "combined_saturation_strike",
]

# Leaker weights per scenario. Small scenarios penalize leakers heavily
# because zero leakers should be achievable. Large saturation raids are
# expected to leak, so they carry lower weight.
LEAKER_WEIGHTS: dict[str, float] = {
    "skirmish_80": 10.0,
    "probe_120": 5.0,
    "decoy_300": 2.0,
    "contested_500": 1.0,
    "saturation_1000": 0.5,
    "combined_saturation_strike": 1.0,
}

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# ANSI color codes
_GREEN = "\033[32m"
_RED = "\033[31m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _gen_path(gen: int) -> Path:
    """Return the JSON file path for a generation number."""
    return MODELS_DIR / f"benchmark_gen{gen}.json"


async def _run_scenario_once(name: str) -> dict:
    """Run one scenario to completion and return a metrics dict."""
    scenario = load_scenario(name)
    runner = WargameRunner(scenario)
    last: Optional[Frame] = None
    async for frame in runner.run(pace=False):
        last = frame
    if last is None:
        raise RuntimeError(f"runner produced no frames for {name}")
    m = last.metrics
    return {
        "leakers": m.leakers,
        "intercepts": m.intercepts,
        "intercept_rate": round(m.intercept_rate, 6),
        "cost_ratio": round(m.cost_exchange_ratio, 6) if m.cost_exchange_ratio is not None else 0.0,
        "defender_spent": round(m.defender_spent, 2),
        "attacker_destroyed": round(m.attacker_destroyed, 2),
        "ticks": m.tick,
        "elapsed_s": round(m.sim_time_s, 2),
    }


async def run_benchmark(scenarios: list[str], gen: int) -> dict:
    """Run all scenarios and return results dict."""
    results: dict = {
        "generation": gen,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scenarios": {},
        "score": 0.0,
    }

    for name in scenarios:
        logger.info("gen %d | running %s ...", gen, name)
        t0 = time.monotonic()
        try:
            metrics = await _run_scenario_once(name)
            wall_s = round(time.monotonic() - t0, 2)
            metrics["wall_s"] = wall_s
            results["scenarios"][name] = metrics
            logger.info(
                "gen %d | %s done in %.1fs  leakers=%d  cost_ratio=%.4f",
                gen, name, wall_s,
                metrics["leakers"], metrics["cost_ratio"],
            )
        except Exception:
            wall_s = round(time.monotonic() - t0, 2)
            err_msg = traceback.format_exc()
            results["scenarios"][name] = {
                "error": err_msg,
                "wall_s": wall_s,
            }
            logger.error("gen %d | %s FAILED after %.1fs:\n%s", gen, name, wall_s, err_msg)

    results["score"] = compute_score(results)

    out_path = _gen_path(gen)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    logger.info("gen %d | results saved to %s", gen, out_path)
    logger.info("gen %d | overall score: %.2f (lower is better)", gen, results["score"])

    return results


def compute_score(results: dict) -> float:
    """Compute a single fitness score from benchmark results.

    score = sum(leakers * weight) + sum(cost_ratio * 100)

    Lower is better. Leakers in small scenarios are penalized more heavily
    because they should be zero.
    """
    total = 0.0
    for name, data in results.get("scenarios", {}).items():
        if "error" in data:
            # Penalize failed scenarios heavily
            total += 10000.0
            continue
        weight = LEAKER_WEIGHTS.get(name, 1.0)
        total += data["leakers"] * weight
        total += data["cost_ratio"] * 100.0
    return round(total, 2)


def _load_gen(gen: int) -> dict:
    """Load a generation JSON file. Raises FileNotFoundError if missing."""
    path = _gen_path(gen)
    if not path.exists():
        raise FileNotFoundError(f"no benchmark file at {path}")
    return json.loads(path.read_text())


def _delta_str(old: float, new: float, lower_is_better: bool = True) -> str:
    """Format a delta value with ANSI color. Green = improvement, red = regression."""
    delta = new - old
    if delta == 0.0:
        return f"{delta:+.4f}"
    improved = (delta < 0) if lower_is_better else (delta > 0)
    color = _GREEN if improved else _RED
    return f"{color}{delta:+.4f}{_RESET}"


def compare_generations(gen_a: int, gen_b: int) -> None:
    """Print a comparison table between two generations."""
    data_a = _load_gen(gen_a)
    data_b = _load_gen(gen_b)

    scenarios_a = data_a.get("scenarios", {})
    scenarios_b = data_b.get("scenarios", {})
    all_names = sorted(set(list(scenarios_a.keys()) + list(scenarios_b.keys())))

    if not all_names:
        logger.error("No scenarios found in either generation.")
        return

    metrics_to_compare = [
        ("leakers", True),
        ("intercepts", False),
        ("intercept_rate", False),
        ("cost_ratio", True),
        ("defender_spent", True),
        ("attacker_destroyed", False),
        ("ticks", True),
    ]

    # Header
    print()
    print(f"{_BOLD}Generation Comparison: gen{gen_a} vs gen{gen_b}{_RESET}")
    print(f"  gen{gen_a}: {data_a.get('timestamp', 'unknown')}")
    print(f"  gen{gen_b}: {data_b.get('timestamp', 'unknown')}")
    print()

    # Per-scenario tables
    for name in all_names:
        sa = scenarios_a.get(name, {})
        sb = scenarios_b.get(name, {})

        if "error" in sa and "error" in sb:
            print(f"  {name}: BOTH FAILED")
            continue
        if "error" in sa:
            print(f"  {name}: gen{gen_a} FAILED, gen{gen_b} ran")
            continue
        if "error" in sb:
            print(f"  {name}: gen{gen_a} ran, gen{gen_b} FAILED")
            continue

        print(f"  {_BOLD}{name}{_RESET}")

        # Build rows
        header = f"    {'Metric':<20s} {'gen' + str(gen_a):>12s} {'gen' + str(gen_b):>12s} {'delta':>16s}"
        print(header)
        print(f"    {'─' * 20} {'─' * 12} {'─' * 12} {'─' * 16}")

        for metric, lower_is_better in metrics_to_compare:
            val_a = sa.get(metric)
            val_b = sb.get(metric)
            if val_a is None or val_b is None:
                continue
            delta = _delta_str(float(val_a), float(val_b), lower_is_better)
            print(f"    {metric:<20s} {val_a:>12.4f} {val_b:>12.4f} {delta:>28s}")

        print()

    # Overall score comparison
    score_a = data_a.get("score", compute_score(data_a))
    score_b = data_b.get("score", compute_score(data_b))
    delta_score = _delta_str(score_a, score_b, lower_is_better=True)

    total_leakers_a = sum(
        s.get("leakers", 0) for s in scenarios_a.values() if "error" not in s
    )
    total_leakers_b = sum(
        s.get("leakers", 0) for s in scenarios_b.values() if "error" not in s
    )
    delta_leakers = _delta_str(total_leakers_a, total_leakers_b, lower_is_better=True)

    print(f"  {_BOLD}Summary{_RESET}")
    print(f"    {'Total leakers':<20s} {total_leakers_a:>12.0f} {total_leakers_b:>12.0f} {delta_leakers:>28s}")
    print(f"    {'Fitness score':<20s} {score_a:>12.2f} {score_b:>12.2f} {delta_score:>28s}")
    print()
    if score_b < score_a:
        print(f"  {_GREEN}gen{gen_b} is an improvement over gen{gen_a}{_RESET}")
    elif score_b > score_a:
        print(f"  {_RED}gen{gen_b} regressed compared to gen{gen_a}{_RESET}")
    else:
        print(f"  No change between gen{gen_a} and gen{gen_b}")
    print()


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Multi-generation benchmark harness for BULWARK wargame optimization"
    )
    parser.add_argument(
        "--gen",
        type=int,
        default=None,
        help="Generation number to run and record",
    )
    parser.add_argument(
        "--compare",
        type=int,
        nargs=2,
        metavar=("GEN_A", "GEN_B"),
        help="Compare two generation results side by side",
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default="all",
        help="Comma-separated scenario names or 'all' (default: all)",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.gen is None and args.compare is None:
        parser.error("provide --gen to run a benchmark or --compare to compare two generations")
        return

    if args.scenarios == "all":
        scenarios = list(ALL_SCENARIOS)
    else:
        scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    if args.compare is not None:
        gen_a, gen_b = args.compare
        try:
            compare_generations(gen_a, gen_b)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        return

    if args.gen is not None:
        t0 = time.monotonic()
        results = asyncio.run(run_benchmark(scenarios, args.gen))
        elapsed = time.monotonic() - t0

        # Print summary table
        print()
        print(f"{_BOLD}Generation {args.gen} Benchmark Results{_RESET}")
        print(f"  Total wall time: {elapsed:.1f}s")
        print()
        header = f"  {'Scenario':<30s} {'Leakers':>8s} {'Intercepts':>10s} {'Rate':>8s} {'Cost Ratio':>11s} {'Ticks':>6s}"
        print(header)
        print(f"  {'─' * 30} {'─' * 8} {'─' * 10} {'─' * 8} {'─' * 11} {'─' * 6}")
        for name in scenarios:
            data = results["scenarios"].get(name, {})
            if "error" in data:
                print(f"  {name:<30s} {'ERROR':>8s}")
                continue
            print(
                f"  {name:<30s} "
                f"{data['leakers']:>8d} "
                f"{data['intercepts']:>10d} "
                f"{data['intercept_rate']:>8.4f} "
                f"{data['cost_ratio']:>11.4f} "
                f"{data['ticks']:>6d}"
            )
        print()
        print(f"  Fitness score: {_BOLD}{results['score']:.2f}{_RESET} (lower is better)")
        print()


if __name__ == "__main__":
    main()
