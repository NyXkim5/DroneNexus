"""
Cost-exchange sensitivity sweep for the BULWARK counter-swarm wargame.

The headline metric is the cost-exchange ratio (CER), defender dollars spent per
attacker dollar of airframe destroyed. Below 1.0 means defense wins on cost. The
prior panel flagged that CER ships as a single optimistic number with no bands
and is an artifact of hand-set HPM and EW effector constants. This module runs
the wargame across a grid of effector parameters and seeds and reports CER as a
distribution with p10 and p90 bands, plus the crossover boundary where mean CER
reaches 1.0 and defense stops winning on cost.

A param grid maps an effector kind (HPM or EW) and a DefenderConfig field name
(kill_prob, effect_radius_m, max_simultaneous, capacity) to a list of values.
The sweep takes the cartesian product of every grid axis and every seed, builds
the scenario fresh per run, overrides the matching DefenderConfig fields, runs
the WargameRunner to completion with no real-time pacing, and records the final
CER, leakers, intercepts, and defender_spent.

Run from cwd=backend:
  python -m wargame.sweep --scenario saturation_1000
"""
from __future__ import annotations

import argparse
import asyncio
import io
import itertools
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from csontology import DefenderKind
from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import DefenderConfig, Scenario, load_scenario

logger = logging.getLogger("overwatch.wargame.sweep")

# A grid key names one effector parameter to sweep: (kind, field_name).
GridKey = Tuple[DefenderKind, str]
# A param grid maps each grid key to the list of values to sweep over it.
ParamGrid = Dict[GridKey, List[float]]
# A point is one frozen combination of (grid_key, value) overrides.
ParamPoint = Tuple[Tuple[GridKey, float], ...]

# Effector fields a sweep may override on a DefenderConfig. The headline four are
# kill_prob, effect_radius_m, max_simultaneous, and capacity. range_m and
# unit_cost are allowed too since they shape the same cost-exchange tradeoff.
SWEEPABLE_FIELDS = frozenset(
    {
        "kill_prob",
        "effect_radius_m",
        "max_simultaneous",
        "capacity",
        "range_m",
        "unit_cost",
    }
)
# Integer-valued fields get cast back to int after a float grid value.
_INT_FIELDS = frozenset({"max_simultaneous", "capacity"})


@dataclass(frozen=True)
class RunResult:
    """One completed run at a fixed param point and seed."""

    point: ParamPoint
    seed: int
    cost_exchange_ratio: Optional[float]
    leakers: int
    intercepts: int
    defender_spent: float


@dataclass(frozen=True)
class SweepResult:
    """Aggregated CER distribution at one param point across all seeds.

    cer_mean is the mean over seeds that produced a defined ratio. cer_p10 and
    cer_p90 are the lower and upper bands. A point where every seed destroyed
    nothing has no defined ratio and reports None for all three.
    """

    point: ParamPoint
    seeds: Tuple[int, ...]
    cer_mean: Optional[float]
    cer_p10: Optional[float]
    cer_p90: Optional[float]
    leakers_mean: float
    intercepts_mean: float
    defender_spent_mean: float
    runs: Tuple[RunResult, ...] = field(default_factory=tuple)

    def label(self) -> str:
        """Render the param point as a short stable label like HPM.kill_prob=0.2."""
        return ", ".join(
            f"{kind.value}.{fname}={_fmt_num(val)}" for (kind, fname), val in self.point
        )


def default_param_grid() -> ParamGrid:
    """Return the small default grid: two values across two HPM params.

    Two values times two params times two seeds keeps a test run fast while still
    spanning a strong and a weak effector setting so sensitivity is measurable.
    """
    return {
        (DefenderKind.HPM, "kill_prob"): [0.8, 0.2],
        (DefenderKind.HPM, "effect_radius_m"): [350.0, 80.0],
    }


def default_seeds() -> Tuple[int, ...]:
    """Return the small default seed set used for the bands."""
    return (11, 23)


def run_sweep(
    scenario_name: str,
    param_grid: Optional[ParamGrid] = None,
    seeds: Optional[Tuple[int, ...]] = None,
    max_ticks: int = 120,
) -> List[SweepResult]:
    """Run the wargame across the param grid and seeds and aggregate CER bands.

    For each combination of effector parameters and each seed it builds the
    scenario, overrides the matching DefenderConfig fields, runs the runner to
    completion with no pacing, and records the final metrics. Results aggregate
    across seeds into mean and p10 and p90 bands, one SweepResult per grid point.
    """
    grid = param_grid if param_grid is not None else default_param_grid()
    seed_tuple = tuple(seeds) if seeds is not None else default_seeds()
    points = _expand_points(grid)
    results: List[SweepResult] = []
    for point in points:
        runs = [
            _run_one(scenario_name, point, seed, max_ticks) for seed in seed_tuple
        ]
        results.append(_aggregate(point, seed_tuple, runs))
    logger.info("Sweep done: %d points x %d seeds", len(points), len(seed_tuple))
    return results


def _expand_points(grid: ParamGrid) -> List[ParamPoint]:
    """Take the cartesian product of every grid axis into frozen param points."""
    keys = list(grid.keys())
    for key in keys:
        if key[1] not in SWEEPABLE_FIELDS:
            raise ValueError(f"field '{key[1]}' is not sweepable, use {SWEEPABLE_FIELDS}")
    value_lists = [grid[key] for key in keys]
    points: List[ParamPoint] = []
    for combo in itertools.product(*value_lists):
        points.append(tuple(zip(keys, combo)))
    return points


def _run_one(
    scenario_name: str, point: ParamPoint, seed: int, max_ticks: int
) -> RunResult:
    """Build, override, and run one scenario to completion for one seed."""
    scenario = load_scenario(scenario_name)
    scenario.seed = seed
    scenario.max_ticks = max_ticks
    _apply_overrides(scenario, point)
    last = _drive(scenario)
    metrics = last.metrics
    return RunResult(
        point=point,
        seed=seed,
        cost_exchange_ratio=metrics.cost_exchange_ratio,
        leakers=metrics.leakers,
        intercepts=metrics.intercepts,
        defender_spent=metrics.defender_spent,
    )


def _apply_overrides(scenario: Scenario, point: ParamPoint) -> None:
    """Override matching DefenderConfig fields for every override in the point."""
    for (kind, fname), value in point:
        applied = _override_kind(scenario.defenders, kind, fname, value)
        if not applied:
            raise ValueError(f"no {kind.value} defender to override {fname}")


def _override_kind(
    defenders: List[DefenderConfig], kind: DefenderKind, fname: str, value: float
) -> bool:
    """Set one field on every defender of a kind. Return True if any matched."""
    cast = int(value) if fname in _INT_FIELDS else float(value)
    matched = False
    for config in defenders:
        if config.kind is kind:
            setattr(config, fname, cast)
            matched = True
    return matched


def _drive(scenario: Scenario) -> Frame:
    """Run the scenario to completion with no pacing and return the last frame."""

    async def go() -> Frame:
        runner = WargameRunner(scenario)
        last: Optional[Frame] = None
        async for frame in runner.run(pace=False):
            last = frame
        if last is None:
            raise RuntimeError("runner produced no frames")
        return last

    return asyncio.run(go())


def _aggregate(
    point: ParamPoint, seeds: Tuple[int, ...], runs: List[RunResult]
) -> SweepResult:
    """Reduce per-seed runs into mean and p10 and p90 CER bands for one point."""
    ratios = [r.cost_exchange_ratio for r in runs if r.cost_exchange_ratio is not None]
    return SweepResult(
        point=point,
        seeds=seeds,
        cer_mean=_mean(ratios),
        cer_p10=_percentile(ratios, 0.10),
        cer_p90=_percentile(ratios, 0.90),
        leakers_mean=_mean([float(r.leakers) for r in runs]) or 0.0,
        intercepts_mean=_mean([float(r.intercepts) for r in runs]) or 0.0,
        defender_spent_mean=_mean([r.defender_spent for r in runs]) or 0.0,
        runs=tuple(runs),
    )


def _mean(values: List[float]) -> Optional[float]:
    """Arithmetic mean, or None for an empty list."""
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: List[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile of values, or None when empty.

    q is a fraction in 0..1. Matches the common linear interpolation method so a
    two-seed sample gives a sensible spread between its low and high values.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = q * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


@dataclass(frozen=True)
class CrossoverReport:
    """Where mean CER crosses 1.0 and defense stops winning on cost.

    losing holds every point whose mean CER is at or above 1.0. winning holds
    the rest. boundary_mean_cer is the lowest losing mean CER, the nearest point
    to the crossover from the losing side, or None when defense wins everywhere.
    """

    winning: Tuple[SweepResult, ...]
    losing: Tuple[SweepResult, ...]
    boundary_mean_cer: Optional[float]

    def crosses(self) -> bool:
        """True when at least one swept point reaches or passes CER 1.0."""
        return bool(self.losing)


def crossover_report(results: List[SweepResult], threshold: float = 1.0) -> CrossoverReport:
    """Split swept points into cost-winning and cost-losing at the threshold.

    A point is losing when its mean CER is defined and at or above the threshold,
    meaning defense spends at least as much as it destroys. The boundary is the
    lowest losing mean CER, the closest approach to the crossover from above.
    """
    winning: List[SweepResult] = []
    losing: List[SweepResult] = []
    for result in results:
        if result.cer_mean is not None and result.cer_mean >= threshold:
            losing.append(result)
        else:
            winning.append(result)
    boundary = min((r.cer_mean for r in losing if r.cer_mean is not None), default=None)
    return CrossoverReport(
        winning=tuple(winning), losing=tuple(losing), boundary_mean_cer=boundary
    )


def _fmt_num(value: float) -> str:
    """Format a grid value compactly, dropping a trailing .0 on whole numbers."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _fmt_cer(value: Optional[float]) -> str:
    """Format a CER value, or n/a when undefined."""
    return "n/a" if value is None else f"{value:.3f}"


def format_table(results: List[SweepResult]) -> str:
    """Render the sweep as a readable fixed-width table with CER bands."""
    lines = [
        f"{'param point':<40} {'CER p10':>9} {'CER mean':>9} {'CER p90':>9} "
        f"{'leak':>6} {'kills':>7} {'def $':>12}"
    ]
    lines.append("-" * 96)
    for result in results:
        lines.append(
            f"{result.label():<40} "
            f"{_fmt_cer(result.cer_p10):>9} "
            f"{_fmt_cer(result.cer_mean):>9} "
            f"{_fmt_cer(result.cer_p90):>9} "
            f"{result.leakers_mean:>6.1f} "
            f"{result.intercepts_mean:>7.1f} "
            f"{result.defender_spent_mean:>12,.0f}"
        )
    return "\n".join(lines)


def format_csv(results: List[SweepResult]) -> str:
    """Render the sweep as CSV text with one row per param point."""
    out = io.StringIO()
    out.write(
        "param_point,cer_p10,cer_mean,cer_p90,leakers_mean,"
        "intercepts_mean,defender_spent_mean\n"
    )
    for result in results:
        out.write(
            f"{result.label()},"
            f"{_csv_num(result.cer_p10)},"
            f"{_csv_num(result.cer_mean)},"
            f"{_csv_num(result.cer_p90)},"
            f"{result.leakers_mean:.4f},"
            f"{result.intercepts_mean:.4f},"
            f"{result.defender_spent_mean:.4f}\n"
        )
    return out.getvalue()


def _csv_num(value: Optional[float]) -> str:
    """Format a number for CSV, empty for None."""
    return "" if value is None else f"{value:.6f}"


def format_crossover(report: CrossoverReport) -> str:
    """Render the crossover report as a short readable block."""
    lines = ["CROSSOVER (mean CER >= 1.0 means defense loses on cost):"]
    if not report.crosses():
        lines.append("  defense wins on cost at every swept point")
    else:
        lines.append(f"  boundary mean CER {report.boundary_mean_cer:.3f}")
        lines.append("  cost-losing points:")
        for result in report.losing:
            lines.append(f"    {result.label()}  mean CER {_fmt_cer(result.cer_mean)}")
    lines.append(f"  winning points: {len(report.winning)}  losing: {len(report.losing)}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the sweep entrypoint."""
    parser = argparse.ArgumentParser(description="BULWARK CER sensitivity sweep")
    parser.add_argument("--scenario", required=True, help="preset scenario name")
    parser.add_argument(
        "--max-ticks", type=int, default=120, help="per-run tick bound"
    )
    parser.add_argument(
        "--seeds", type=int, nargs="+", help="override the default seed set"
    )
    parser.add_argument("--csv", action="store_true", help="emit CSV instead of a table")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint: run the default grid and print bands and crossover."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = _parse_args()
    seeds = tuple(args.seeds) if args.seeds else None
    results = run_sweep(args.scenario, seeds=seeds, max_ticks=args.max_ticks)
    if args.csv:
        print(format_csv(results))
        return
    print(format_table(results))
    print()
    print(format_crossover(crossover_report(results)))


if __name__ == "__main__":
    main()
