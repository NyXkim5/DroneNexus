"""
Tests for the BULWARK wargame integration loop.

These assert the runner wires every module together and produces coherent frames
and metrics. They run a short scenario with asyncio.run so they need no plugin
config. Scenarios are seeded so runs are deterministic enough to bound metrics.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from csontology import SwarmIntent
from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import (
    Scenario,
    list_scenarios,
    load_scenario,
    load_scenario_file,
)
from wargame.world import build_world
from pathlib import Path


def _run_scenario(name: str, max_ticks: int) -> list[Frame]:
    """Run a preset for a bounded number of ticks and return all frames."""
    scenario = load_scenario(name)
    scenario.max_ticks = max_ticks
    runner = WargameRunner(scenario)

    async def go() -> list[Frame]:
        frames: list[Frame] = []
        async for frame in runner.run():
            frames.append(frame)
        return frames

    return asyncio.run(go())


def test_presets_include_required_scenarios() -> None:
    names = list_scenarios()
    assert "saturation_1000" in names
    assert len(names) >= 2


def test_saturation_preset_is_1000_drones() -> None:
    scenario = load_scenario("saturation_1000")
    assert scenario.swarm_count == 1000
    assert scenario.swarm_intent is SwarmIntent.SATURATION


def test_scenario_validates_swarm_count() -> None:
    with pytest.raises(ValueError):
        Scenario(
            name="bad",
            swarm_intent=SwarmIntent.PROBE,
            swarm_count=5,
            sensors=load_scenario("probe_120").sensors,
            defenders=load_scenario("probe_120").defenders,
        )


def test_runner_emits_one_frame_per_tick() -> None:
    frames = _run_scenario("probe_120", max_ticks=12)
    assert len(frames) == 12
    assert all(isinstance(f, Frame) for f in frames)
    ticks = [f.metrics.tick for f in frames]
    assert ticks == list(range(1, 13))


def test_frame_serializes_to_json_shape() -> None:
    frames = _run_scenario("probe_120", max_ticks=8)
    payload = frames[-1].to_dict()
    assert payload["type"] == "WARGAME_FRAME"
    assert "metrics" in payload and "tracks" in payload
    assert "defenders" in payload and "assignments" in payload
    assert "cost_exchange_ratio" in payload["metrics"]
    for track in payload["tracks"]:
        assert "lat" in track and "lon" in track and "enu" in track


def test_metrics_are_coherent_after_engagements() -> None:
    frames = _run_scenario("probe_120", max_ticks=30)
    last = frames[-1].metrics
    # Engagements should have happened and intercepts cannot exceed them.
    assert last.engagements_made > 0
    assert last.intercepts <= last.engagements_made
    # Killed drones reduce the live hostile count below the swarm size.
    assert last.active_hostiles < load_scenario("probe_120").swarm_count
    # Cost figures are non-negative and the ratio is real once anything dies.
    assert last.defender_spent >= 0.0
    assert last.attacker_destroyed >= 0.0
    if last.attacker_destroyed > 0.0:
        assert last.cost_exchange_ratio is not None


def test_build_world_expands_defender_counts() -> None:
    scenario = load_scenario("probe_120")
    world = build_world(scenario)
    expected = sum(d.count for d in scenario.defenders)
    assert len(world.defenders) == expected
    # Every defender starts ready with a reload entry.
    assert all(d.id in world.reload_left for d in world.defenders)


def test_yaml_preset_round_trips() -> None:
    path = Path(__file__).parent.parent / "wargame" / "scenarios" / "probe_120.yaml"
    scenario = load_scenario_file(path)
    assert scenario.name == "probe_120"
    assert scenario.swarm_intent is SwarmIntent.PROBE
    assert scenario.swarm_count == 120
    assert len(scenario.sensors) == 3


def test_unknown_scenario_raises() -> None:
    with pytest.raises(KeyError):
        load_scenario("does_not_exist")
