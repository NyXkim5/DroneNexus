"""
Tests for the multi-scenario wargame benchmarking CLI.

Uses the smallest, fastest scenario (skirmish_80) so tests complete quickly.
Validates metric extraction, multi-run averaging, JSON schema, scenario name
validation, and output formatting.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import math

import pytest

pytestmark = pytest.mark.slow

from scripts.benchmark import (
    METRIC_KEYS,
    MetricStats,
    RunMetrics,
    ScenarioResult,
    _aggregate_runs,
    _compute_stats,
    format_csv,
    format_table,
    results_to_json,
    run_benchmark,
    validate_scenario_names,
)

_FAST_SCENARIO = "skirmish_80"


# ---------------------------------------------------------------------------
# Scenario name validation
# ---------------------------------------------------------------------------


def test_validate_known_scenario() -> None:
    """Known scenario names pass validation."""
    result = validate_scenario_names(["skirmish_80"])
    assert result == ["skirmish_80"]


def test_validate_multiple_known_scenarios() -> None:
    """Multiple known names all pass."""
    result = validate_scenario_names(["skirmish_80", "probe_120"])
    assert result == ["skirmish_80", "probe_120"]


def test_validate_unknown_scenario_raises() -> None:
    """Unknown scenario names raise ValueError."""
    with pytest.raises(ValueError, match="unknown scenario"):
        validate_scenario_names(["does_not_exist"])


def test_validate_mixed_known_unknown_raises() -> None:
    """A mix of known and unknown names raises for the unknown ones."""
    with pytest.raises(ValueError, match="does_not_exist"):
        validate_scenario_names(["skirmish_80", "does_not_exist"])


# ---------------------------------------------------------------------------
# Stats computation (pure, no wargame needed)
# ---------------------------------------------------------------------------


def test_compute_stats_basic() -> None:
    """Stats computation returns correct mean, std, min, max."""
    values = [10.0, 20.0, 30.0]
    stats = _compute_stats(values)
    assert stats.mean == pytest.approx(20.0)
    expected_std = math.sqrt((100 + 0 + 100) / 3)
    assert stats.std == pytest.approx(expected_std, abs=0.01)
    assert stats.min_val == 10.0
    assert stats.max_val == 30.0


def test_compute_stats_single_value() -> None:
    """Single-value stats have zero std."""
    stats = _compute_stats([42.0])
    assert stats.mean == 42.0
    assert stats.std == 0.0
    assert stats.min_val == 42.0
    assert stats.max_val == 42.0


def test_aggregate_runs_covers_all_metrics() -> None:
    """Aggregation produces stats for every metric key."""
    runs = [
        RunMetrics(
            intercept_rate=0.8,
            cost_exchange_ratio=1.2,
            leakers=10,
            engagements_made=50,
            sim_time_s=30.0,
        ),
        RunMetrics(
            intercept_rate=0.9,
            cost_exchange_ratio=0.9,
            leakers=5,
            engagements_made=60,
            sim_time_s=32.0,
        ),
    ]
    stats = _aggregate_runs(runs)
    for key in METRIC_KEYS:
        assert key in stats
        assert isinstance(stats[key], MetricStats)


def test_aggregate_runs_handles_none_cost_ratio() -> None:
    """None cost_exchange_ratio is treated as 0.0 for stats."""
    runs = [
        RunMetrics(
            intercept_rate=0.5,
            cost_exchange_ratio=None,
            leakers=3,
            engagements_made=10,
            sim_time_s=20.0,
        ),
    ]
    stats = _aggregate_runs(runs)
    assert stats["cost_exchange_ratio"].mean == 0.0


# ---------------------------------------------------------------------------
# Formatting (pure, no wargame needed)
# ---------------------------------------------------------------------------


def test_format_table_has_header_and_rows() -> None:
    """Table format includes header, divider, and one row per result."""
    result = ScenarioResult(
        scenario_name="test_scenario",
        config_label="default",
        runs=[
            RunMetrics(
                intercept_rate=0.85,
                cost_exchange_ratio=1.1,
                leakers=12,
                engagements_made=100,
                sim_time_s=25.0,
            ),
        ],
    )
    result.stats = _aggregate_runs(result.runs)
    table = format_table([result])
    lines = table.strip().split("\n")
    assert len(lines) == 3
    assert "Scenario" in lines[0]
    assert "Intercept Rate" in lines[0]
    assert "test_scenario" in lines[2]


def test_format_table_multiple_results() -> None:
    """Table with two results has header, divider, and two data rows."""
    mk = RunMetrics(
        intercept_rate=0.85,
        cost_exchange_ratio=1.1,
        leakers=12,
        engagements_made=100,
        sim_time_s=25.0,
    )
    r1 = ScenarioResult(scenario_name="alpha", config_label="default", runs=[mk])
    r1.stats = _aggregate_runs(r1.runs)
    r2 = ScenarioResult(scenario_name="beta", config_label="default", runs=[mk])
    r2.stats = _aggregate_runs(r2.runs)
    table = format_table([r1, r2])
    lines = table.strip().split("\n")
    assert len(lines) == 4
    assert "alpha" in lines[2]
    assert "beta" in lines[3]


def test_format_csv_has_correct_columns() -> None:
    """CSV format has header and one row per scenario per metric."""
    result = ScenarioResult(
        scenario_name="test_scenario",
        config_label="default",
        runs=[
            RunMetrics(
                intercept_rate=0.85,
                cost_exchange_ratio=1.1,
                leakers=12,
                engagements_made=100,
                sim_time_s=25.0,
            ),
        ],
    )
    result.stats = _aggregate_runs(result.runs)
    csv_out = format_csv([result])
    lines = csv_out.strip().split("\n")
    assert lines[0] == "scenario,config,metric,mean,std,min,max"
    assert len(lines) == 1 + len(METRIC_KEYS)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


def test_results_json_schema_synthetic() -> None:
    """JSON schema has required fields from synthetic data."""
    result = ScenarioResult(
        scenario_name="test_scenario",
        config_label="default",
        runs=[
            RunMetrics(
                intercept_rate=0.85,
                cost_exchange_ratio=1.1,
                leakers=12,
                engagements_made=100,
                sim_time_s=25.0,
            ),
        ],
    )
    result.stats = _aggregate_runs(result.runs)
    data = results_to_json([result])
    assert "scenarios" in data
    entry = data["scenarios"][0]
    assert entry["scenario_name"] == "test_scenario"
    assert entry["num_runs"] == 1
    for key in METRIC_KEYS:
        assert key in entry["stats"]
        for f in ("mean", "std", "min", "max"):
            assert f in entry["stats"][key]
    run_entry = entry["runs"][0]
    for key in METRIC_KEYS:
        assert key in run_entry


# ---------------------------------------------------------------------------
# Integration: actual wargame runs (slow)
# ---------------------------------------------------------------------------


def test_single_scenario_benchmark_produces_valid_metrics() -> None:
    """A single-scenario, single-run benchmark returns valid metrics."""
    results = asyncio.run(
        run_benchmark([_FAST_SCENARIO], num_runs=1, parallel=False)
    )
    assert len(results) == 1
    r = results[0]
    assert r.scenario_name == _FAST_SCENARIO
    assert len(r.runs) == 1
    m = r.runs[0]
    assert 0.0 <= m.intercept_rate <= 1.0
    assert m.leakers >= 0
    assert m.engagements_made >= 0
    assert m.sim_time_s > 0.0


def test_multi_run_averaging() -> None:
    """Multiple runs produce averaged stats with plausible values."""
    results = asyncio.run(
        run_benchmark([_FAST_SCENARIO], num_runs=2, parallel=False)
    )
    r = results[0]
    assert len(r.runs) == 2
    stats = r.stats
    for key in METRIC_KEYS:
        assert key in stats
        assert stats[key].min_val <= stats[key].mean <= stats[key].max_val


def test_results_json_schema_from_run() -> None:
    """JSON output from an actual run matches the expected schema."""
    results = asyncio.run(
        run_benchmark([_FAST_SCENARIO], num_runs=1, parallel=False)
    )
    data = results_to_json(results)
    assert "scenarios" in data
    assert len(data["scenarios"]) == 1
    entry = data["scenarios"][0]
    assert entry["scenario_name"] == _FAST_SCENARIO
    assert entry["num_runs"] == 1
    for key in METRIC_KEYS:
        assert key in entry["stats"]
    assert len(entry["runs"]) == 1


def test_parallel_flag_produces_same_structure() -> None:
    """Parallel execution returns the same result structure as sequential."""
    results = asyncio.run(
        run_benchmark([_FAST_SCENARIO], num_runs=1, parallel=True)
    )
    assert len(results) == 1
    assert results[0].scenario_name == _FAST_SCENARIO
    assert len(results[0].runs) == 1
