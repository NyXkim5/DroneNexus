"""Tests for predictor and swarm-counter integration in the wargame pipeline.

Verifies that Frame includes predictions, early_warnings, and intercept_orders
after wargame ticks. Runs short scenarios to keep tests fast.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import load_scenario


pytestmark = pytest.mark.slow


def _run_scenario(name: str, max_ticks: int) -> list[Frame]:
    """Run a preset for a bounded number of ticks and return all frames."""
    scenario = load_scenario(name)
    scenario.max_ticks = max_ticks
    runner = WargameRunner(scenario)

    async def go() -> list[Frame]:
        frames: list[Frame] = []
        async for frame in runner.run(pace=False):
            frames.append(frame)
        return frames

    return asyncio.run(go())


class TestFramePredictionFields:
    """Frame dataclass has prediction, early_warning, and intercept_order fields."""

    def test_frame_defaults_are_empty_lists(self) -> None:
        """New fields default to empty lists so old callers are unaffected."""
        from wargame.frame import Metrics

        metrics = Metrics(
            tick=1, sim_time_s=0.2, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0,
            intercept_rate=0.0, defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        frame = Frame(metrics=metrics, tracks=[], defenders=[])
        assert frame.predictions == []
        assert frame.early_warnings == []
        assert frame.intercept_orders == []

    def test_to_dict_includes_new_keys(self) -> None:
        """to_dict output includes predictions, early_warnings, intercept_orders."""
        from wargame.frame import Metrics

        metrics = Metrics(
            tick=1, sim_time_s=0.2, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0,
            intercept_rate=0.0, defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        frame = Frame(metrics=metrics, tracks=[], defenders=[])
        d = frame.to_dict()
        assert "predictions" in d
        assert "early_warnings" in d
        assert "intercept_orders" in d
        assert d["predictions"] == []
        assert d["early_warnings"] == []
        assert d["intercept_orders"] == []


class TestPredictorIntegration:
    """ThreatPredictor runs inside the wargame tick and populates frames."""

    def test_predictions_appear_in_frames(self) -> None:
        """After enough ticks for tracks to form, predictions should be present."""
        frames = _run_scenario("probe_120", max_ticks=30)
        has_predictions = any(len(f.predictions) > 0 for f in frames)
        assert has_predictions, "No predictions found in any frame"

    def test_prediction_dict_shape(self) -> None:
        """Each prediction dict has the required keys."""
        frames = _run_scenario("probe_120", max_ticks=30)
        preds = []
        for f in frames:
            preds.extend(f.predictions)
        assert len(preds) > 0
        for p in preds:
            assert "track_id" in p
            assert "likely_target" in p
            assert "impact_probability" in p
            assert "eta_s" in p
            assert isinstance(p["impact_probability"], float)
            assert isinstance(p["eta_s"], (int, float))


class TestEarlyWarnings:
    """Early warnings appear when threats are imminent."""

    def test_warnings_when_threats_close(self) -> None:
        """Run long enough for drones to approach the site and trigger warnings."""
        frames = _run_scenario("probe_120", max_ticks=160)
        all_warnings = []
        for f in frames:
            all_warnings.extend(f.early_warnings)
        assert len(all_warnings) > 0, "No early warnings generated"

    def test_warning_strings_are_readable(self) -> None:
        """Warnings should be non-empty strings."""
        frames = _run_scenario("probe_120", max_ticks=160)
        for f in frames:
            for w in f.early_warnings:
                assert isinstance(w, str)
                assert len(w) > 0


class TestInterceptOrders:
    """SwarmCounterPlanner produces intercept orders during wargame ticks."""

    def test_intercept_orders_generated(self) -> None:
        """After engagements start, intercept orders should appear."""
        frames = _run_scenario("probe_120", max_ticks=80)
        has_orders = any(len(f.intercept_orders) > 0 for f in frames)
        assert has_orders, "No intercept orders generated"

    def test_intercept_order_dict_shape(self) -> None:
        """Each intercept order has defender_id, target_id, lat, lon, eta_s."""
        frames = _run_scenario("probe_120", max_ticks=80)
        orders = []
        for f in frames:
            orders.extend(f.intercept_orders)
        assert len(orders) > 0
        for o in orders:
            assert "defender_id" in o
            assert "target_id" in o
            assert "intercept_lat" in o
            assert "intercept_lon" in o
            assert "eta_s" in o
            assert isinstance(o["eta_s"], (int, float))

    def test_serialized_frame_includes_intercept_orders(self) -> None:
        """to_dict output includes intercept_orders from a live run."""
        frames = _run_scenario("probe_120", max_ticks=80)
        frames_with_orders = [f for f in frames if len(f.intercept_orders) > 0]
        assert len(frames_with_orders) > 0
        d = frames_with_orders[0].to_dict()
        assert len(d["intercept_orders"]) > 0
