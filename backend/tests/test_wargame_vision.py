import pytest
from wargame.scenario import Scenario, load_scenario
from wargame.frame import Frame
from vision.cascade import CascadeResult
from decision.models import EngagementOrder


class TestScenarioWithTargets:
    def test_scenario_accepts_target_scenario_name(self):
        s = Scenario(
            name="test_combined",
            swarm_intent="SATURATION",
            swarm_count=10,
            target_scenario="ground_strike_convoy",
        )
        assert s.target_scenario == "ground_strike_convoy"

    def test_existing_scenarios_have_no_targets(self):
        s = load_scenario("saturation_1000")
        assert s.target_scenario is None


class TestFrameWithVision:
    def test_frame_includes_cascade_results(self):
        cr = CascadeResult(
            target_id="t1",
            direct_value=100_000,
            cascade_value=500_000,
            cascade_chain=["t1", "t2"],
            cascade_probability=0.8,
            expected_value=400_000,
            personnel_at_risk=5,
        )
        f = Frame(
            metrics=None,
            tracks=[],
            defenders=[],
            cascade_results=[cr],
        )
        d = f.to_dict()
        assert "cascade_results" in d
        assert len(d["cascade_results"]) == 1
        assert d["cascade_results"][0]["target_id"] == "t1"

    def test_frame_includes_engagement_order(self):
        from decision.models import EngagementMode, EngagementPriority, EngagementOrder
        order = EngagementOrder(
            priorities=[EngagementPriority("t1", "vision", 0.9, 300.0, 5, 2)],
            mode=EngagementMode.AUTO,
            timestamp=1.0,
        )
        f = Frame(
            metrics=None,
            tracks=[],
            defenders=[],
            engagement_order=order,
        )
        d = f.to_dict()
        assert "engagement_order" in d
        assert d["engagement_order"]["mode"] == "auto"

    def test_existing_frame_still_works(self):
        f = Frame(metrics=None, tracks=[], defenders=[])
        d = f.to_dict()
        assert "cascade_results" in d
        assert d["cascade_results"] == []
        assert d["engagement_order"] is None
