import pytest
import numpy as np
from vision.models import TargetType
from vision.detector import SimDetector
from vision.feed_source import SimFeedSource
from vision.cascade import CascadeEngine
from vision.scenarios import load_target_scenario
from decision.engine import DecisionEngine
from decision.models import EngagementMode


class TestFullPipeline:
    def test_convoy_pipeline(self):
        scenario = load_target_scenario("ground_strike_convoy")
        feed = SimFeedSource(placements=scenario.placements, resolution=(640, 480))
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)

        frame, ts = feed.next_frame()
        targets = detector.detect(frame, ts)
        assert len(targets) == len(scenario.placements)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()
        assert len(results) == len(targets)
        assert results[0].expected_value > results[-1].expected_value

        decision = DecisionEngine(mode=EngagementMode.AUTO)
        order = decision.merge(threats=[], cascade_results=results, now=ts)
        assert len(order.priorities) == len(targets)
        assert order.mode == EngagementMode.AUTO

    def test_base_scenario_generator_has_high_cascade(self):
        scenario = load_target_scenario("ground_strike_base")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()

        gen_result = next(r for r in results if r.target_id == "generator")
        assert len(gen_result.cascade_chain) >= 3
        assert gen_result.cascade_value > gen_result.direct_value

    def test_dispersed_no_cascades(self):
        scenario = load_target_scenario("ground_strike_dispersed")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()

        for r in results:
            assert len(r.cascade_chain) == 1
            assert r.cascade_value == r.direct_value

    def test_pipeline_with_noise_still_works(self):
        scenario = load_target_scenario("ground_strike_convoy")
        detector = SimDetector(
            placements=scenario.placements,
            noise_sigma_m=5.0,
            false_positive_rate=0.05,
            seed=42,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)
        assert len(targets) >= len(scenario.placements)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()
        assert len(results) > 0

    def test_combined_defensive_and_offensive(self):
        from csontology import Threat, SwarmIntent

        scenario = load_target_scenario("ground_strike_convoy")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        cascade_engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        cascade_results = cascade_engine.score_all()

        threats = [
            Threat(id="swarm-1", score=0.95, time_to_impact_s=8.0,
                   value_at_risk=2_000_000, priority_rank=1,
                   track_id="track-1", intent=SwarmIntent.SATURATION),
            Threat(id="swarm-2", score=0.6, time_to_impact_s=45.0,
                   value_at_risk=500_000, priority_rank=2,
                   track_id="track-2", intent=SwarmIntent.PROBE),
        ]

        decision = DecisionEngine(mode=EngagementMode.ADVISORY)
        order = decision.merge(threats=threats, cascade_results=cascade_results, now=0.0)

        sources = {p.source for p in order.priorities}
        assert "bulwark" in sources
        assert "vision" in sources
        assert order.priorities[0].source == "bulwark"
        assert order.priorities[0].target_id == "swarm-1"

        d = order.to_dict()
        assert d["mode"] == "advisory"
        assert len(d["priorities"]) == len(threats) + len(cascade_results)
