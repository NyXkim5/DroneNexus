"""
Integration tests for visual correlator and ROE wiring in the wargame pipeline.

Verifies that Frame objects include visual_correlations when vision is enabled
and roe_evaluations when engagements occur. Uses fast scenarios with capped
tick counts to keep execution time low.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from wargame.frame import Frame, Metrics
from wargame.runner import WargameRunner
from wargame.scenario import Scenario, load_scenario

pytestmark = pytest.mark.slow


def _fast_scenario(name: str = "skirmish_80", max_ticks: int = 300) -> Scenario:
    """Load a preset and cap its tick count for speed."""
    scenario = load_scenario(name)
    scenario.max_ticks = max_ticks
    return scenario


def _run_scenario(scenario: Scenario) -> list[Frame]:
    """Run a scenario to completion and collect all frames."""
    frames: list[Frame] = []

    async def _collect() -> None:
        runner = WargameRunner(scenario)
        async for frame in runner.run(pace=False):
            frames.append(frame)

    asyncio.run(_collect())
    return frames


# ---------------------------------------------------------------------------
# Frame field presence
# ---------------------------------------------------------------------------


class TestFrameFields:
    """Verify the two new Frame fields serialize correctly."""

    def test_frame_has_visual_correlations_field(self) -> None:
        """Frame dataclass exposes visual_correlations with default empty list."""
        metrics = Metrics(
            tick=1, sim_time_s=1.0, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0, intercept_rate=0.0,
            defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        frame = Frame(metrics=metrics, tracks=[], defenders=[])
        assert frame.visual_correlations == []
        d = frame.to_dict()
        assert "visual_correlations" in d
        assert d["visual_correlations"] == []

    def test_frame_has_roe_evaluations_field(self) -> None:
        """Frame dataclass exposes roe_evaluations with default empty list."""
        metrics = Metrics(
            tick=1, sim_time_s=1.0, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0, intercept_rate=0.0,
            defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        frame = Frame(metrics=metrics, tracks=[], defenders=[])
        assert frame.roe_evaluations == []
        d = frame.to_dict()
        assert "roe_evaluations" in d
        assert d["roe_evaluations"] == []

    def test_frame_serializes_populated_correlations(self) -> None:
        """Populated visual_correlations serialize into to_dict output."""
        metrics = Metrics(
            tick=1, sim_time_s=1.0, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0, intercept_rate=0.0,
            defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        corr = [{"camera_det_id": "c1", "track_id": "t1", "score": 0.95,
                 "range_m": 100.0, "bearing_deg": 45.0}]
        frame = Frame(
            metrics=metrics, tracks=[], defenders=[],
            visual_correlations=corr,
        )
        d = frame.to_dict()
        assert len(d["visual_correlations"]) == 1
        assert d["visual_correlations"][0]["score"] == 0.95

    def test_frame_serializes_populated_roe(self) -> None:
        """Populated roe_evaluations serialize into to_dict output."""
        metrics = Metrics(
            tick=1, sim_time_s=1.0, active_hostiles=0, tracks_held=0,
            leakers=0, engagements_made=0, intercepts=0, intercept_rate=0.0,
            defender_spent=0.0, attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        roe = [{"target_id": "th-1", "rule_name": "DEFENSIVE_STANDARD",
                "authorized": True, "reason": "all conditions met",
                "timestamp": 1.0, "authorization_level": "CO",
                "conditions_met": {"positive_id": True}}]
        frame = Frame(
            metrics=metrics, tracks=[], defenders=[],
            roe_evaluations=roe,
        )
        d = frame.to_dict()
        assert len(d["roe_evaluations"]) == 1
        assert d["roe_evaluations"][0]["authorized"] is True


# ---------------------------------------------------------------------------
# ROE evaluations in the live pipeline
# ---------------------------------------------------------------------------


class TestROEInPipeline:
    """Verify ROE evaluations appear in Frame when engagements happen."""

    def test_roe_evaluations_present_on_engagements(self) -> None:
        """When a scenario produces engagements, roe_evaluations is non-empty."""
        scenario = _fast_scenario("skirmish_80", max_ticks=300)
        frames = _run_scenario(scenario)
        assert len(frames) > 0
        frames_with_engagements = [
            f for f in frames if f.engagements
        ]
        # skirmish_80 should produce at least some engagements
        assert len(frames_with_engagements) > 0, (
            "Expected at least one frame with engagements"
        )
        frames_with_roe = [
            f for f in frames_with_engagements if f.roe_evaluations
        ]
        assert len(frames_with_roe) > 0, (
            "Expected ROE evaluations on frames with engagements"
        )
        # Check structure of first ROE evaluation
        first_roe = frames_with_roe[0].roe_evaluations[0]
        assert "target_id" in first_roe
        assert "rule_name" in first_roe
        assert "authorized" in first_roe
        assert "reason" in first_roe
        assert "conditions_met" in first_roe

    def test_roe_evaluations_empty_when_no_engagements(self) -> None:
        """Frames without engagements have empty roe_evaluations."""
        scenario = _fast_scenario("skirmish_80", max_ticks=300)
        frames = _run_scenario(scenario)
        no_engagement_frames = [f for f in frames if not f.engagements]
        for frame in no_engagement_frames:
            assert frame.roe_evaluations == []

    def test_roe_in_to_dict(self) -> None:
        """ROE evaluations survive Frame.to_dict() serialization."""
        scenario = _fast_scenario("skirmish_80", max_ticks=300)
        frames = _run_scenario(scenario)
        frames_with_roe = [f for f in frames if f.roe_evaluations]
        if not frames_with_roe:
            pytest.skip("No ROE evaluations produced in this run")
        d = frames_with_roe[0].to_dict()
        assert "roe_evaluations" in d
        assert len(d["roe_evaluations"]) > 0


# ---------------------------------------------------------------------------
# Visual correlations in the pipeline (requires target_scenario)
# ---------------------------------------------------------------------------


class TestVisualCorrelationInPipeline:
    """Verify visual correlations appear in Frame on vision-enabled scenarios."""

    def _vision_scenario(self, max_ticks: int = 100) -> Scenario:
        """Build a scenario with target_scenario set for vision fusion."""
        scenario = _fast_scenario("skirmish_80", max_ticks=max_ticks)
        scenario.target_scenario = "ground_strike_base"
        return scenario

    def test_visual_correlations_present(self) -> None:
        """When vision is enabled, some frames carry visual_correlations."""
        scenario = self._vision_scenario(max_ticks=50)
        frames = _run_scenario(scenario)
        assert len(frames) > 0
        frames_with_corr = [f for f in frames if f.visual_correlations]
        assert len(frames_with_corr) > 0, (
            "Expected visual correlations when target_scenario is set"
        )

    def test_visual_correlation_structure(self) -> None:
        """Each visual correlation dict has the expected keys."""
        scenario = self._vision_scenario(max_ticks=50)
        frames = _run_scenario(scenario)
        frames_with_corr = [f for f in frames if f.visual_correlations]
        if not frames_with_corr:
            pytest.skip("No visual correlations produced")
        corr = frames_with_corr[0].visual_correlations[0]
        assert "camera_det_id" in corr
        assert "track_id" in corr
        assert "score" in corr
        assert "range_m" in corr
        assert "bearing_deg" in corr

    def test_correlations_in_to_dict(self) -> None:
        """Visual correlations survive Frame.to_dict() serialization."""
        scenario = self._vision_scenario(max_ticks=50)
        frames = _run_scenario(scenario)
        frames_with_corr = [f for f in frames if f.visual_correlations]
        if not frames_with_corr:
            pytest.skip("No visual correlations produced")
        d = frames_with_corr[0].to_dict()
        assert "visual_correlations" in d
        assert len(d["visual_correlations"]) > 0

    def test_no_correlations_without_vision(self) -> None:
        """When target_scenario is not set, visual_correlations stays empty."""
        scenario = _fast_scenario("skirmish_80", max_ticks=50)
        frames = _run_scenario(scenario)
        for frame in frames:
            assert frame.visual_correlations == []
