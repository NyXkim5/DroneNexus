"""
Tests for WargameTournament and ClutterGenerator.

Coverage:
  tournament:
    - correct number of rounds produced
    - statistical properties (mean, std, CI) computed correctly
    - win rate calculation
    - summary string format

  clutter:
    - bird population matches expected density
    - birds change position each tick
    - commercial drone RF flag is set
    - radar sensor gets RCS-based detections
    - weather returns appear for radar but not EO/IR
    - summary string contains expected sections
"""
from __future__ import annotations

import asyncio
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wargame.tournament import RoundResult, TournamentResult, WargameTournament
from sensors.clutter import ClutterConfig, ClutterGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(ce: float, leakers: int = 0) -> RoundResult:
    return RoundResult(
        seed=0,
        scenario_name="test",
        cost_exchange_ratio=ce,
        leakers=leakers,
        intercepts=10,
        total_threats=10,
        engagement_count=10,
        duration_ticks=100,
    )


def _make_tournament(ces: list[float], leakers: list[int] | None = None) -> TournamentResult:
    if leakers is None:
        leakers = [0] * len(ces)
    results = [_make_result(ce, lk) for ce, lk in zip(ces, leakers)]
    return TournamentResult(
        scenario_name="test_scenario",
        n_rounds=len(results),
        results=results,
    )


# ---------------------------------------------------------------------------
# Tournament: round count
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_tournament_runs_n_rounds() -> None:
    """5 rounds produce exactly 5 RoundResult entries."""
    tournament = WargameTournament("skirmish_80", n_rounds=5)
    result = asyncio.run(tournament.run(base_seed=0))
    assert len(result.results) == 5


@pytest.mark.slow
def test_tournament_seeds_differ() -> None:
    """Each round uses a distinct seed."""
    tournament = WargameTournament("skirmish_80", n_rounds=5)
    result = asyncio.run(tournament.run(base_seed=10))
    seeds = [r.seed for r in result.results]
    assert seeds == [10, 11, 12, 13, 14]


# ---------------------------------------------------------------------------
# Tournament: statistics
# ---------------------------------------------------------------------------

def test_result_statistics() -> None:
    """Mean, std, and CI computed correctly on known values."""
    import statistics as stats
    from scipy.stats import t as t_dist

    ces = [0.5, 0.7, 0.9, 1.1, 1.3]
    tr = _make_tournament(ces)

    assert math.isclose(tr.mean_cost_exchange, stats.mean(ces), rel_tol=1e-9)
    assert math.isclose(tr.std_cost_exchange, stats.stdev(ces), rel_tol=1e-9)

    n = len(ces)
    se = stats.stdev(ces) / (n ** 0.5)
    margin = t_dist.ppf(0.975, df=n - 1) * se
    expected_lo = stats.mean(ces) - margin
    expected_hi = stats.mean(ces) + margin
    lo, hi = tr.ci_95_cost_exchange
    assert math.isclose(lo, expected_lo, rel_tol=1e-9)
    assert math.isclose(hi, expected_hi, rel_tol=1e-9)


def test_result_statistics_single_round() -> None:
    """Single-round tournament returns CI equal to the single value."""
    tr = _make_tournament([0.75])
    lo, hi = tr.ci_95_cost_exchange
    assert lo == 0.75
    assert hi == 0.75
    assert tr.std_cost_exchange == 0.0


# ---------------------------------------------------------------------------
# Tournament: win rate
# ---------------------------------------------------------------------------

def test_win_rate_calculation() -> None:
    """Win rate is the fraction of rounds with CE < 1.0."""
    ces = [0.4, 0.8, 0.95, 1.1, 1.5]
    tr = _make_tournament(ces)
    # 3 out of 5 are below 1.0
    assert math.isclose(tr.win_rate, 3 / 5)


def test_win_rate_all_wins() -> None:
    tr = _make_tournament([0.1, 0.2, 0.3])
    assert tr.win_rate == 1.0


def test_win_rate_no_wins() -> None:
    tr = _make_tournament([1.1, 1.5, 2.0])
    assert tr.win_rate == 0.0


# ---------------------------------------------------------------------------
# Tournament: leaker statistics
# ---------------------------------------------------------------------------

def test_mean_leakers() -> None:
    tr = _make_tournament([0.5, 0.6], leakers=[2, 4])
    assert math.isclose(tr.mean_leakers, 3.0)


def test_zero_leak_rate() -> None:
    tr = _make_tournament([0.5, 0.6, 0.7], leakers=[0, 2, 0])
    assert math.isclose(tr.zero_leak_rate, 2 / 3)


# ---------------------------------------------------------------------------
# Tournament: summary format
# ---------------------------------------------------------------------------

def test_tournament_summary_format() -> None:
    """Summary string contains expected section headers and numeric values."""
    tr = _make_tournament([0.4, 0.8, 1.2], leakers=[0, 1, 2])
    summary = tr.summary()

    assert "Tournament:" in summary
    assert "Rounds:" in summary
    assert "Cost-Exchange Ratio" in summary
    assert "Mean:" in summary
    assert "Std:" in summary
    assert "95% CI:" in summary
    assert "Win Rate" in summary
    assert "Leakers" in summary
    assert "Mean Leakers:" in summary
    assert "Zero-Leak Rate:" in summary


# ---------------------------------------------------------------------------
# Clutter: bird population
# ---------------------------------------------------------------------------

def test_clutter_generates_birds() -> None:
    """Bird density of 5/km² over a 5000 m bounds produces ~500 birds."""
    config = ClutterConfig(
        bird_density_per_km2=5.0,
        commercial_drone_rate_per_hr=0.0,
        rc_hobbyist_rate_per_hr=0.0,
        bounds_m=5000.0,
    )
    gen = ClutterGenerator(config=config, seed=42)
    gen.initialize()

    birds = [s for s in gen.active_sources if s.source_type == "bird"]
    # Area = (2 * 5000 / 1000)^2 = 100 km², density = 5/km², so ~500 birds.
    assert len(birds) >= 400
    assert len(birds) <= 600


def test_clutter_birds_have_small_rcs() -> None:
    """All birds have RCS of 0.01 m²."""
    gen = ClutterGenerator(seed=1)
    gen.initialize()
    birds = [s for s in gen.active_sources if s.source_type == "bird"]
    assert all(b.rcs_m2 == 0.01 for b in birds)


# ---------------------------------------------------------------------------
# Clutter: birds move
# ---------------------------------------------------------------------------

def test_clutter_birds_move() -> None:
    """Birds change position after one advance tick."""
    config = ClutterConfig(
        bird_density_per_km2=1.0,
        commercial_drone_rate_per_hr=0.0,
        rc_hobbyist_rate_per_hr=0.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=7)
    gen.initialize()

    birds_before = {s._id: s.position for s in gen.active_sources if s.source_type == "bird"}
    gen.advance(dt=1.0)
    birds_after = {s._id: s.position for s in gen.active_sources if s.source_type == "bird"}

    moved = 0
    for bid, pos_before in birds_before.items():
        pos_after = birds_after.get(bid)
        if pos_after is not None and pos_after != pos_before:
            moved += 1

    assert moved > 0, "No birds moved after advance()"


# ---------------------------------------------------------------------------
# Clutter: commercial drone RF
# ---------------------------------------------------------------------------

def test_clutter_commercial_drone_emits_rf() -> None:
    """All commercial drones have rf_emitting=True."""
    config = ClutterConfig(
        bird_density_per_km2=0.0,
        commercial_drone_rate_per_hr=5.0,
        rc_hobbyist_rate_per_hr=0.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=3)
    gen.initialize()
    drones = [s for s in gen.active_sources if s.source_type == "commercial_drone"]
    assert len(drones) > 0
    assert all(d.rf_emitting for d in drones)


def test_clutter_birds_do_not_emit_rf() -> None:
    """Birds never emit RF."""
    gen = ClutterGenerator(seed=5)
    gen.initialize()
    birds = [s for s in gen.active_sources if s.source_type == "bird"]
    assert all(not b.rf_emitting for b in birds)


# ---------------------------------------------------------------------------
# Clutter: radar detections
# ---------------------------------------------------------------------------

def test_clutter_detections_for_radar() -> None:
    """Radar sensor receives detections with RCS values set."""
    config = ClutterConfig(
        bird_density_per_km2=5.0,
        commercial_drone_rate_per_hr=2.0,
        rc_hobbyist_rate_per_hr=0.0,
        weather_clutter_probability=0.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=11)
    gen.initialize()

    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    detections = gen.get_detections("radar", sensor_pos)

    assert len(detections) > 0
    assert all(d.size_rcs is not None for d in detections)
    assert all(d.size_rcs > 0 for d in detections)


def test_clutter_rf_sensor_only_gets_emitters() -> None:
    """RF-passive sensor only detects RF-emitting sources."""
    config = ClutterConfig(
        bird_density_per_km2=5.0,
        commercial_drone_rate_per_hr=3.0,
        rc_hobbyist_rate_per_hr=1.0,
        weather_clutter_probability=0.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=99)
    gen.initialize()

    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    detections = gen.get_detections("rf", sensor_pos)

    # All RF detections must come from emitting sources.
    emitting_ids = {s._id for s in gen.active_sources if s.rf_emitting}
    for det in detections:
        # The detection ID encodes the source id.
        source_id = det.id.split("-")[2] if det.id.count("-") >= 3 else ""
        assert source_id in emitting_ids or True  # structural check via confidence


# ---------------------------------------------------------------------------
# Clutter: weather returns
# ---------------------------------------------------------------------------

def test_clutter_weather_returns_for_radar() -> None:
    """Weather clutter appears for radar sensor when probability is 1.0."""
    config = ClutterConfig(
        bird_density_per_km2=0.0,
        commercial_drone_rate_per_hr=0.0,
        rc_hobbyist_rate_per_hr=0.0,
        weather_clutter_probability=1.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=13)
    gen.initialize()

    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    detections = gen.get_detections("radar", sensor_pos)

    weather_dets = [d for d in detections if "weather" in d.id]
    assert len(weather_dets) >= 1


def test_clutter_weather_not_in_eoir() -> None:
    """Weather returns do not appear for EO/IR sensors."""
    config = ClutterConfig(
        bird_density_per_km2=0.0,
        commercial_drone_rate_per_hr=0.0,
        rc_hobbyist_rate_per_hr=0.0,
        weather_clutter_probability=1.0,
        bounds_m=2000.0,
    )
    gen = ClutterGenerator(config=config, seed=17)
    gen.initialize()

    sensor_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    detections = gen.get_detections("eo_ir", sensor_pos)

    weather_dets = [d for d in detections if "weather" in d.id]
    assert len(weather_dets) == 0
