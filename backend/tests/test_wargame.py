"""
Tests for the BULWARK wargame integration loop.

These assert the runner wires every module together and produces coherent frames
and metrics. They run a short scenario with asyncio.run so they need no plugin
config. Scenarios are seeded so runs are deterministic enough to bound metrics.
"""
import asyncio
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Full end-to-end wargame runs. Deselect for a fast loop with -m "not slow".
pytestmark = pytest.mark.slow

from csontology import SwarmIntent, Threat, Track, TrackClass
from wargame.frame import Frame, Metrics
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
    frames = _run_scenario("probe_120", max_ticks=160)
    last = frames[-1].metrics
    swarm_size = load_scenario("probe_120").swarm_count
    # Engagements should have happened once drones reach defender range.
    assert last.engagements_made > 0
    # Intercepts are real kills, capped by the swarm size. Area effectors can
    # neutralize several drones per engagement, so intercepts may exceed the
    # engagement count, which is the point of a cheap area effect.
    assert 0 <= last.intercepts <= swarm_size
    # Killed drones reduce the live hostile count below the swarm size.
    assert last.active_hostiles < swarm_size
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


# ---- Frame serialization (fast, built directly, no scenario run) ----


def _make_track(track_id: str) -> Track:
    """Build a minimal confirmed track for serialization tests."""
    return Track(
        id=track_id,
        position=(120.0, -40.0, 60.0),
        velocity=(-5.0, 3.0, 0.0),
        covariance=(2.0, 2.0, 1.0),
        last_update=1.0,
        classification=TrackClass.HOSTILE,
        confidence=0.8,
    )


def _make_metrics(ratio) -> Metrics:
    """Build a scoreboard with a chosen cost-exchange ratio for tests."""
    return Metrics(
        tick=3,
        sim_time_s=0.3,
        active_hostiles=4,
        tracks_held=2,
        leakers=1,
        engagements_made=5,
        intercepts=3,
        intercept_rate=0.6,
        defender_spent=1500.0,
        attacker_destroyed=3000.0,
        cost_exchange_ratio=ratio,
    )


def test_frame_to_dict_surfaces_intent_and_impact() -> None:
    track = _make_track("T1")
    threat = Threat(
        id="X1",
        score=0.91,
        time_to_impact_s=12.5,
        value_at_risk=500.0,
        priority_rank=1,
        track_id="T1",
        swarm_id="S1",
        intent=SwarmIntent.SATURATION,
    )
    frame = Frame(
        metrics=_make_metrics(0.5),
        tracks=[track],
        defenders=[],
        threats=[threat],
    )
    payload = frame.to_dict()
    # json.dumps must succeed end to end.
    json.dumps(payload)
    t = payload["tracks"][0]
    assert t["intent"] == "SATURATION"
    assert isinstance(t["intent"], str)
    assert t["time_to_impact_s"] == 12.5
    assert t["swarm_id"] == "S1"
    assert t["threat_score"] == 0.91


def test_frame_to_dict_defaults_intent_when_unscored() -> None:
    frame = Frame(
        metrics=_make_metrics(None),
        tracks=[_make_track("T9")],
        defenders=[],
    )
    payload = frame.to_dict()
    json.dumps(payload)
    t = payload["tracks"][0]
    assert t["intent"] == "UNKNOWN"
    assert t["time_to_impact_s"] is None
    assert t["swarm_id"] is None


def test_metrics_to_dict_scoreboard_keys_present() -> None:
    payload = _make_metrics(0.5).to_dict()
    for key in ("leakers", "intercepts", "cost_exchange_ratio"):
        assert key in payload
    assert payload["leakers"] == 1
    assert payload["intercepts"] == 3
    assert isinstance(payload["cost_exchange_ratio"], float)
    assert payload["cost_exchange_win"] is True


def test_metrics_to_dict_ratio_null_when_undefined() -> None:
    payload = _make_metrics(None).to_dict()
    assert payload["cost_exchange_ratio"] is None
    assert payload["cost_exchange_win"] is None
    json.dumps(payload)


def test_metrics_to_dict_ratio_null_when_not_finite() -> None:
    # NaN and inf are not JSON, so they must serialize as null.
    for bad in (float("nan"), float("inf"), float("-inf")):
        payload = _make_metrics(bad).to_dict()
        assert payload["cost_exchange_ratio"] is None
        assert payload["cost_exchange_win"] is None
        text = json.dumps(payload)
        assert "NaN" not in text and "Infinity" not in text


def test_frame_to_dict_keeps_existing_keys() -> None:
    frame = Frame(
        metrics=_make_metrics(0.5),
        tracks=[_make_track("T1")],
        defenders=[],
        threats=[],
    )
    payload = frame.to_dict()
    for key in ("type", "scenario", "done", "metrics", "site",
                "tracks", "defenders", "assignments"):
        assert key in payload
    assert payload["type"] == "WARGAME_FRAME"
    track = payload["tracks"][0]
    for key in ("id", "enu", "lat", "lon", "velocity",
                "classification", "confidence"):
        assert key in track
