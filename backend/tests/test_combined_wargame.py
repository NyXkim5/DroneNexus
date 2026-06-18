import pytest
from wargame.scenario import Scenario, load_scenario


@pytest.mark.slow
class TestCombinedWargame:
    def test_combined_scenario_loads(self):
        s = load_scenario("combined_saturation_strike")
        assert s.target_scenario == "ground_strike_base"
        assert s.swarm_count >= 100

    @pytest.mark.asyncio
    async def test_combined_scenario_runs(self):
        from wargame.runner import WargameRunner
        s = load_scenario("combined_saturation_strike")
        runner = WargameRunner(s)
        frame_count = 0
        has_cascade = False
        has_engagement_order = False

        async for frame in runner.run(pace=False):
            frame_count += 1
            if frame.cascade_results:
                has_cascade = True
            if frame.engagement_order:
                has_engagement_order = True
            if frame_count > 20:
                break

        assert frame_count > 0
        assert has_cascade
        assert has_engagement_order

    @pytest.mark.asyncio
    async def test_existing_scenario_unaffected(self):
        from wargame.runner import WargameRunner
        s = load_scenario("skirmish_80")
        runner = WargameRunner(s)
        frame_count = 0
        async for frame in runner.run(pace=False):
            frame_count += 1
            assert frame.cascade_results == []
            assert frame.engagement_order is None
            if frame_count > 10:
                break
        assert frame_count > 0
