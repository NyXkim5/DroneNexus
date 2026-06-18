"""
WargameTournament — multi-round statistical wargame analysis.

Runs N independent rounds of one scenario, each seeded differently, and
aggregates the results into a TournamentResult. The statistical summary gives
confidence intervals (95%, t-distribution) on cost-exchange ratio, win rate,
leaker rate, and zero-leak rate. This is the tool for establishing whether a
loadout is robust across the randomness in the swarm and sensor layer, not just
lucky on one seed.

Usage:
    tournament = WargameTournament("skirmish_80", n_rounds=50)
    result = asyncio.run(tournament.run(base_seed=0))
    print(result.summary())
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from scipy.stats import t as t_dist

from wargame.runner import WargameRunner
from wargame.scenario import Scenario, load_scenario

logger = logging.getLogger("overwatch.wargame.tournament")


@dataclass
class RoundResult:
    """Metrics extracted from a single wargame round."""

    seed: int
    scenario_name: str
    cost_exchange_ratio: float
    leakers: int
    intercepts: int
    total_threats: int
    engagement_count: int
    duration_ticks: int
    cascade_value_destroyed: float = 0.0


@dataclass
class TournamentResult:
    """Aggregated statistics across all rounds of a tournament."""

    scenario_name: str
    n_rounds: int
    results: List[RoundResult]

    @property
    def mean_cost_exchange(self) -> float:
        """Mean cost-exchange ratio across all rounds."""
        ratios = [r.cost_exchange_ratio for r in self.results]
        return statistics.mean(ratios) if ratios else 0.0

    @property
    def std_cost_exchange(self) -> float:
        """Sample standard deviation of cost-exchange ratio."""
        ratios = [r.cost_exchange_ratio for r in self.results]
        return statistics.stdev(ratios) if len(ratios) > 1 else 0.0

    @property
    def ci_95_cost_exchange(self) -> Tuple[float, float]:
        """95% confidence interval on cost-exchange ratio using t-distribution."""
        ratios = [r.cost_exchange_ratio for r in self.results]
        n = len(ratios)
        if n < 2:
            mean = ratios[0] if ratios else 0.0
            return (mean, mean)
        mean = statistics.mean(ratios)
        std = statistics.stdev(ratios)
        se = std / (n ** 0.5)
        margin = t_dist.ppf(0.975, df=n - 1) * se
        return (mean - margin, mean + margin)

    @property
    def win_rate(self) -> float:
        """Fraction of rounds where cost_exchange_ratio < 1.0 (defense wins)."""
        if not self.results:
            return 0.0
        wins = sum(1 for r in self.results if r.cost_exchange_ratio < 1.0)
        return wins / len(self.results)

    @property
    def mean_leakers(self) -> float:
        """Mean leaker count across all rounds."""
        leakers = [r.leakers for r in self.results]
        return statistics.mean(leakers) if leakers else 0.0

    @property
    def zero_leak_rate(self) -> float:
        """Fraction of rounds with zero leakers."""
        if not self.results:
            return 0.0
        zero = sum(1 for r in self.results if r.leakers == 0)
        return zero / len(self.results)

    def summary(self) -> str:
        """Formatted statistical summary of the tournament."""
        lo, hi = self.ci_95_cost_exchange
        lines = [
            f"Tournament: {self.scenario_name}",
            f"Rounds: {self.n_rounds}",
            "",
            "Cost-Exchange Ratio",
            f"  Mean:   {self.mean_cost_exchange:.4f}",
            f"  Std:    {self.std_cost_exchange:.4f}",
            f"  95% CI: [{lo:.4f}, {hi:.4f}]",
            f"  Win Rate (CE < 1.0): {self.win_rate:.1%}",
            "",
            "Leakers",
            f"  Mean Leakers:   {self.mean_leakers:.2f}",
            f"  Zero-Leak Rate: {self.zero_leak_rate:.1%}",
        ]
        return "\n".join(lines)


def _patch_scenario_seed(scenario: Scenario, seed: int) -> Scenario:
    """Return a copy of the scenario with a different seed.

    Scenario is a dataclass so we rebuild it field by field to avoid mutating
    the shared original. Only the seed changes — all loadout, swarm, and sensor
    config stays identical across rounds.
    """
    import dataclasses

    return dataclasses.replace(scenario, seed=seed)


class WargameTournament:
    """Run multiple wargame rounds with varied seeds for statistical confidence."""

    def __init__(self, scenario_name: str, n_rounds: int = 100) -> None:
        self._scenario_name = scenario_name
        self._n_rounds = n_rounds

    async def run(self, base_seed: int = 0) -> TournamentResult:
        """Run n_rounds of the scenario, each with a different seed.

        Seeds are base_seed, base_seed+1, ..., base_seed+n_rounds-1. Each round
        uses the same scenario config but a different RNG seed so the swarm
        starting positions, sensor noise, and engagement outcomes vary. The final
        frame of each round carries the accumulated metrics.
        """
        base_scenario = load_scenario(self._scenario_name)
        round_results: List[RoundResult] = []

        for i in range(self._n_rounds):
            seed = base_seed + i
            scenario = _patch_scenario_seed(base_scenario, seed)
            result = await self._run_one(scenario, seed)
            round_results.append(result)
            logger.debug(
                "round %d/%d seed=%d CE=%.4f leakers=%d",
                i + 1,
                self._n_rounds,
                seed,
                result.cost_exchange_ratio,
                result.leakers,
            )

        return TournamentResult(
            scenario_name=self._scenario_name,
            n_rounds=self._n_rounds,
            results=round_results,
        )

    async def _run_one(self, scenario: Scenario, seed: int) -> RoundResult:
        """Run one full wargame round and return its metrics."""
        runner = WargameRunner(scenario)
        final_frame = None
        async for frame in runner.run(pace=False):
            final_frame = frame
        if final_frame is None:
            raise RuntimeError(f"scenario {scenario.name} produced no frames (seed={seed})")
        return self._extract_result(final_frame.metrics.__dict__ | {"scenario_name": scenario.name}, seed)

    def _extract_result(self, final_frame: dict, seed: int) -> RoundResult:
        """Extract metrics from the final wargame frame dict."""
        ce = final_frame.get("cost_exchange_ratio")
        if ce is None or not isinstance(ce, (int, float)):
            ce = 999.0
        return RoundResult(
            seed=seed,
            scenario_name=final_frame.get("scenario_name", self._scenario_name),
            cost_exchange_ratio=float(ce),
            leakers=int(final_frame.get("leakers", 0)),
            intercepts=int(final_frame.get("intercepts", 0)),
            total_threats=int(final_frame.get("tracks_held", 0)),
            engagement_count=int(final_frame.get("engagements_made", 0)),
            duration_ticks=int(final_frame.get("tick", 0)),
            cascade_value_destroyed=float(final_frame.get("attacker_destroyed", 0.0)),
        )
