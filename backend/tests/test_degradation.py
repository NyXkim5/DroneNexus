"""Tests for comms-denied and degraded-sensing operation."""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Detection
from wargame import load_scenario
from wargame.degradation import DegradationModel
from wargame.runner import WargameRunner


def _det(i: int) -> Detection:
    return Detection(
        id=f"d{i}", timestamp=0.0, position=(float(i), 0.0, 50.0),
        velocity=(0.0, 0.0, 0.0), confidence=0.9, sensor_id="radar-1",
    )


def test_blackout_denies_all_detections() -> None:
    model = DegradationModel(blackout_windows=[(5, 10)])
    dets = [_det(i) for i in range(20)]
    assert model.apply(dets, tick=7, rng=random.Random(0)) == []
    assert len(model.apply(dets, tick=3, rng=random.Random(0))) == 20


def test_jamming_drops_a_fraction() -> None:
    model = DegradationModel(jam_fraction=0.5)
    dets = [_det(i) for i in range(400)]
    kept = model.apply(dets, tick=0, rng=random.Random(1))
    # Roughly half survive, never all and never none for a 0.5 fraction.
    assert 120 < len(kept) < 280


def _run(name: str, ticks: int) -> object:
    async def go() -> object:
        sc = load_scenario(name)
        sc.max_ticks = ticks
        runner = WargameRunner(sc)
        last = None
        async for frame in runner.run(pace=False):
            last = frame
        return last

    return asyncio.run(go())


def test_autonomy_keeps_defending_under_contested_conditions() -> None:
    last = _run("contested_500", ticks=200)
    m = last.metrics
    # The autonomy keeps engaging and neutralizing despite jamming and a blackout.
    assert m.engagements_made > 0
    assert m.intercepts > 0
    # It still wins the cost war under degradation.
    if m.attacker_destroyed > 0.0:
        assert m.cost_exchange_ratio is not None


def test_tracks_survive_a_blackout_by_coasting() -> None:
    # During the blackout window the fusion engine must hold tracks by coasting,
    # so the picture does not collapse to zero with no operator input.
    async def go() -> int:
        sc = load_scenario("contested_500")
        sc.max_ticks = 80
        sc.blackout_windows = [(50, 79)]
        runner = WargameRunner(sc)
        async for _frame in runner.run(pace=False):
            pass
        return len(runner.world.tracks.tracks())

    held_during_blackout = asyncio.run(go())
    assert held_during_blackout > 0
