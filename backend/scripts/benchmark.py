"""
Multi-scenario benchmarking CLI for the BULWARK counter-swarm wargame.

Runs multiple wargame scenarios N times each, collects key metrics, computes
statistics, and prints a side-by-side comparison table. Supports concurrent
execution via asyncio, JSON/CSV output, and config comparison.

Run from cwd=backend:
  python -m scripts.benchmark --scenarios saturation_1000,contested_500 --runs 3
  python -m scripts.benchmark --all --format csv
  python -m scripts.benchmark --scenarios skirmish_80 --compare "default,use_flocking=true"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import SCENARIOS_DIR, Scenario, list_scenarios, load_scenario

logger = logging.getLogger("overwatch.benchmark")

METRIC_KEYS = (
    "intercept_rate",
    "cost_exchange_ratio",
    "leakers",
    "engagements_made",
    "sim_time_s",
)

METRIC_LABELS = {
    "intercept_rate": "Intercept Rate",
    "cost_exchange_ratio": "Cost Ratio",
    "leakers": "Leakers",
    "engagements_made": "Engagements",
    "sim_time_s": "Sim Time (s)",
}


@dataclass(frozen=True)
class RunMetrics:
    """Extracted metrics from one completed wargame run."""

    intercept_rate: float
    cost_exchange_ratio: Optional[float]
    leakers: int
    engagements_made: int
    sim_time_s: float


@dataclass(frozen=True)
class MetricStats:
    """Aggregated statistics for one metric across multiple runs."""

    mean: float
    std: float
    min_val: float
    max_val: float


@dataclass
class ScenarioResult:
    """Complete benchmark result for one scenario."""

    scenario_name: str
    config_label: str
    runs: List[RunMetrics] = field(default_factory=list)
    stats: Dict[str, MetricStats] = field(default_factory=dict)


def _extract_metrics(frame: Frame) -> RunMetrics:
    """Pull the five key metrics from a completed wargame frame."""
    m = frame.metrics
    return RunMetrics(
        intercept_rate=m.intercept_rate,
        cost_exchange_ratio=m.cost_exchange_ratio,
        leakers=m.leakers,
        engagements_made=m.engagements_made,
        sim_time_s=m.sim_time_s,
    )


async def _run_scenario_once(scenario: Scenario) -> RunMetrics:
    """Run one scenario to completion and return its final metrics."""
    runner = WargameRunner(scenario)
    last: Optional[Frame] = None
    async for frame in runner.run(pace=False):
        last = frame
    if last is None:
        raise RuntimeError(f"runner produced no frames for {scenario.name}")
    return _extract_metrics(last)


def _compute_stats(values: Sequence[float]) -> MetricStats:
    """Compute mean, std, min, max for a sequence of floats."""
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return MetricStats(
        mean=mean,
        std=math.sqrt(variance),
        min_val=min(values),
        max_val=max(values),
    )


def _aggregate_runs(runs: List[RunMetrics]) -> Dict[str, MetricStats]:
    """Compute per-metric statistics across all runs."""
    stats: Dict[str, MetricStats] = {}
    for key in METRIC_KEYS:
        raw = [getattr(r, key) for r in runs]
        values = [v if v is not None else 0.0 for v in raw]
        stats[key] = _compute_stats(values)
    return stats


def validate_scenario_names(names: List[str]) -> List[str]:
    """Validate scenario names against known presets. Raises on unknown."""
    known = list_scenarios()
    bad = [n for n in names if n not in known]
    if bad:
        raise ValueError(
            f"unknown scenario(s): {bad}, available: {known}"
        )
    return names


def _apply_config_overrides(scenario: Scenario, overrides: str) -> None:
    """Apply comma-separated key=value overrides to a scenario."""
    for token in overrides.split(","):
        token = token.strip()
        if "=" not in token:
            continue
        key, val = token.split("=", 1)
        key = key.strip()
        if not hasattr(scenario, key):
            raise ValueError(f"scenario has no field '{key}'")
        current = getattr(scenario, key)
        if isinstance(current, bool):
            setattr(scenario, key, val.lower() in ("true", "1", "yes"))
        elif isinstance(current, int):
            setattr(scenario, key, int(val))
        elif isinstance(current, float):
            setattr(scenario, key, float(val))
        else:
            setattr(scenario, key, val)


async def _bench_scenario_sequential(
    name: str,
    num_runs: int,
    config_label: str = "default",
    overrides: str = "",
) -> ScenarioResult:
    """Benchmark one scenario sequentially for num_runs."""
    result = ScenarioResult(scenario_name=name, config_label=config_label)
    for i in range(num_runs):
        logger.info("Running scenario: %s (run %d/%d) [%s]", name, i + 1, num_runs, config_label)
        scenario = load_scenario(name)
        scenario.seed = scenario.seed + i
        if overrides:
            _apply_config_overrides(scenario, overrides)
        metrics = await _run_scenario_once(scenario)
        result.runs.append(metrics)
    result.stats = _aggregate_runs(result.runs)
    return result


async def run_benchmark(
    scenario_names: List[str],
    num_runs: int = 3,
    parallel: bool = False,
    configs: Optional[List[str]] = None,
) -> List[ScenarioResult]:
    """Run the full benchmark across scenarios and optional configs."""
    configs = configs or ["default"]
    tasks: List[asyncio.coroutine] = []
    for name in scenario_names:
        for cfg in configs:
            overrides = "" if cfg == "default" else cfg
            tasks.append(
                _bench_scenario_sequential(name, num_runs, cfg, overrides)
            )
    if parallel:
        results = await asyncio.gather(*tasks)
    else:
        results = [await t for t in tasks]
    return list(results)


def _fmt_metric(stats: MetricStats, key: str) -> str:
    """Format one metric cell as 'mean +/- std'."""
    if key in ("leakers", "engagements_made"):
        return f"{stats.mean:.0f} +/- {stats.std:.0f}"
    return f"{stats.mean:.2f} +/- {stats.std:.2f}"


def format_table(results: List[ScenarioResult]) -> str:
    """Render results as a padded ASCII comparison table."""
    display_keys = list(METRIC_KEYS[:4])
    headers = ["Scenario"] + [METRIC_LABELS[k] for k in display_keys]
    rows: List[List[str]] = []
    for r in results:
        label = r.scenario_name
        if r.config_label != "default":
            label = f"{r.scenario_name} [{r.config_label}]"
        row = [label]
        for key in display_keys:
            row.append(_fmt_metric(r.stats[key], key))
        rows.append(row)
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    sep = " | "
    header_line = sep.join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    divider = "-+-".join("-" * w for w in col_widths)
    lines = [header_line, divider]
    for row in rows:
        lines.append(sep.join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def format_csv(results: List[ScenarioResult]) -> str:
    """Render results as CSV rows."""
    header = "scenario,config,metric,mean,std,min,max"
    lines = [header]
    for r in results:
        for key in METRIC_KEYS:
            s = r.stats[key]
            lines.append(
                f"{r.scenario_name},{r.config_label},{key},"
                f"{s.mean:.4f},{s.std:.4f},{s.min_val:.4f},{s.max_val:.4f}"
            )
    return "\n".join(lines)


def results_to_json(results: List[ScenarioResult]) -> dict:
    """Serialize all results to a JSON-ready dict."""
    out: Dict[str, object] = {"scenarios": []}
    for r in results:
        entry: Dict[str, object] = {
            "scenario_name": r.scenario_name,
            "config_label": r.config_label,
            "num_runs": len(r.runs),
            "stats": {},
            "runs": [],
        }
        for key in METRIC_KEYS:
            s = r.stats[key]
            entry["stats"][key] = {
                "mean": round(s.mean, 4),
                "std": round(s.std, 4),
                "min": round(s.min_val, 4),
                "max": round(s.max_val, 4),
            }
        for run in r.runs:
            entry["runs"].append({
                "intercept_rate": run.intercept_rate,
                "cost_exchange_ratio": run.cost_exchange_ratio,
                "leakers": run.leakers,
                "engagements_made": run.engagements_made,
                "sim_time_s": run.sim_time_s,
            })
        out["scenarios"].append(entry)
    return out


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Multi-scenario wargame benchmark CLI"
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default="",
        help="Comma-separated scenario names to benchmark",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every scenario in SCENARIOS_DIR",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs per scenario (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to save full results as JSON",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run scenarios concurrently with asyncio.gather",
    )
    parser.add_argument(
        "--compare",
        type=str,
        default="",
        help='Compare configs, e.g. "default,use_flocking=true"',
    )
    return parser


def main() -> None:
    """CLI entry point for the benchmark."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.all:
        names = list_scenarios()
    elif args.scenarios:
        names = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    else:
        parser.error("provide --scenarios or --all")
        return

    names = validate_scenario_names(names)

    configs: Optional[List[str]] = None
    if args.compare:
        configs = [c.strip() for c in args.compare.split(",") if c.strip()]

    start = time.monotonic()
    results = asyncio.run(
        run_benchmark(names, args.runs, args.parallel, configs)
    )
    elapsed = time.monotonic() - start
    logger.info("Benchmark complete in %.1f s", elapsed)

    fmt = args.format
    if fmt == "table":
        print(format_table(results))
    elif fmt == "csv":
        print(format_csv(results))
    elif fmt == "json":
        print(json.dumps(results_to_json(results), indent=2))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(results_to_json(results), indent=2))
        logger.info("Results saved to %s", out_path)


if __name__ == "__main__":
    main()
