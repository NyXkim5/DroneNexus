"""
Tests for the CER cost-exchange sensitivity sweep.

These run short bounded sweeps so they finish fast. They assert the harness
returns one aggregated result per grid point with ordered bands, that weakening
HPM effectors actually raises CER so the harness measures sensitivity, and that
the crossover report flags points at or above CER 1.0 once effectors are crippled.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import DefenderKind
from wargame.sweep import (
    SweepResult,
    crossover_report,
    default_param_grid,
    default_seeds,
    format_csv,
    format_table,
    run_sweep,
)

# A mid-size scenario whose whole swarm advances, so kills accumulate fast.
_SCENARIO = "decoy_300"
_SEEDS = (11, 23)
_TICKS = 120
# Spawn ring is 3000 m, so a single-value range axis pushes effectors out far
# enough to engage the swarm from the first ticks. A single-value axis adds no
# cartesian points, it just pins the field for every run.
_REACH = 4000.0


def default_grid_with_reach() -> dict:
    """The default sweep grid plus a single-value range axis to force reach."""
    grid = default_param_grid()
    grid[(DefenderKind.HPM, "range_m")] = [_REACH]
    grid[(DefenderKind.EW, "range_m")] = [_REACH]
    return grid


def _strong_vs_weak_grid() -> dict:
    """Grid spanning a strong and a crippled HPM effector on two params."""
    return {
        (DefenderKind.HPM, "kill_prob"): [0.85, 0.02],
        (DefenderKind.HPM, "effect_radius_m"): [350.0, 5.0],
        # Single-value axes: pin reach so engagements start immediately.
        (DefenderKind.HPM, "range_m"): [_REACH],
        (DefenderKind.EW, "range_m"): [_REACH],
    }


def _crossover_grid() -> dict:
    """Grid that cripples both area effectors so only kinetics can kill.

    With HPM and EW area kills gone, the expensive interceptor carries the fight,
    so defender dollars per attacker dollar killed climbs past 1.0. The strong
    corner keeps healthy area effectors and stays well under 1.0.
    """
    return {
        (DefenderKind.HPM, "kill_prob"): [0.85, 0.01],
        (DefenderKind.EW, "kill_prob"): [0.5, 0.01],
        # Single-value axes: pin reach so engagements start immediately.
        (DefenderKind.HPM, "range_m"): [_REACH],
        (DefenderKind.EW, "range_m"): [_REACH],
    }


def _mean_cer_for(results: list[SweepResult], kill_prob: float, radius: float):
    """Return the mean CER of the point matching a kill_prob and radius pair.

    Matches on the two swept axes only and ignores the pinned range axes.
    """
    want = {
        (DefenderKind.HPM, "kill_prob"): kill_prob,
        (DefenderKind.HPM, "effect_radius_m"): radius,
    }
    for result in results:
        as_map = {key: val for key, val in result.point}
        if all(as_map.get(key) == val for key, val in want.items()):
            return result.cer_mean
    raise AssertionError(f"no swept point for {want}")


def test_sweep_returns_one_result_per_grid_point() -> None:
    results = run_sweep(_SCENARIO, default_grid_with_reach(), _SEEDS, max_ticks=_TICKS)
    # Two two-value params times two single-value range axes = 4 cartesian points.
    assert len(results) == 4
    assert all(isinstance(r, SweepResult) for r in results)
    # Every point ran every seed.
    for result in results:
        assert result.seeds == _SEEDS
        assert len(result.runs) == len(_SEEDS)


def test_default_grid_shape() -> None:
    # The shipped default grid alone is 2 params x 2 values = 4 points.
    assert len(default_param_grid()) == 2
    assert default_seeds() == (11, 23)


def test_bands_are_ordered_around_the_mean() -> None:
    results = run_sweep(_SCENARIO, _strong_vs_weak_grid(), _SEEDS, max_ticks=_TICKS)
    saw_defined = False
    for result in results:
        if result.cer_mean is None:
            continue
        saw_defined = True
        assert result.cer_p10 is not None and result.cer_p90 is not None
        assert result.cer_p10 <= result.cer_mean <= result.cer_p90
    assert saw_defined, "expected at least one point with a defined CER"


def test_weakening_hpm_raises_cer() -> None:
    results = run_sweep(_SCENARIO, _strong_vs_weak_grid(), _SEEDS, max_ticks=_TICKS)
    strong = _mean_cer_for(results, kill_prob=0.85, radius=350.0)
    weak = _mean_cer_for(results, kill_prob=0.02, radius=5.0)
    assert strong is not None and weak is not None
    # A crippled area effector spends more per attacker dollar killed, so CER
    # rises. This proves the harness actually measures effector sensitivity.
    assert weak > strong


def test_crossover_flags_weak_effectors_at_or_above_one() -> None:
    # skirmish_80 is cheap plain drones, so crippling the area layer forces the
    # kinetic interceptor onto $500 targets and the cost ratio climbs past one. It
    # also runs to resolution fast, so the late imminent phase where interceptors
    # actually fire is reached within the tick budget.
    results = run_sweep("skirmish_80", _crossover_grid(), (5,), max_ticks=300)
    report = crossover_report(results)
    # Crippled effectors must push at least one point to CER >= 1.0.
    assert report.crosses()
    assert report.boundary_mean_cer is not None and report.boundary_mean_cer >= 1.0
    for losing in report.losing:
        assert losing.cer_mean is not None and losing.cer_mean >= 1.0
    # The strong corner (healthy HPM and EW) should stay on the winning side.
    winning_maps = [{k: v for k, v in r.point} for r in report.winning]
    assert any(
        m.get((DefenderKind.HPM, "kill_prob")) == 0.85
        and m.get((DefenderKind.EW, "kill_prob")) == 0.5
        for m in winning_maps
    )


def test_crossover_threshold_is_configurable() -> None:
    results = run_sweep(_SCENARIO, _strong_vs_weak_grid(), _SEEDS, max_ticks=_TICKS)
    # A threshold of zero makes every point with a defined CER count as losing.
    report = crossover_report(results, threshold=0.0)
    defined = [r for r in results if r.cer_mean is not None]
    assert len(report.losing) == len(defined)


def test_table_and_csv_render_every_point() -> None:
    results = run_sweep(_SCENARIO, default_grid_with_reach(), _SEEDS, max_ticks=_TICKS)
    table = format_table(results)
    assert "CER mean" in table
    for result in results:
        assert result.label() in table
    csv_text = format_csv(results)
    header = csv_text.splitlines()[0]
    assert header.startswith("param_point,cer_p10,cer_mean,cer_p90")
    # One header row plus one row per point.
    assert len(csv_text.strip().splitlines()) == len(results) + 1
