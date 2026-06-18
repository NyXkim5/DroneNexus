"""Integration tests: DetectionHeatmap wired into Frame and WargameRunner."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64
import pytest

from vision.heatmap import DetectionHeatmap
from wargame.frame import Frame, Metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_metrics() -> Metrics:
    return Metrics(
        tick=1,
        sim_time_s=0.2,
        active_hostiles=3,
        tracks_held=3,
        leakers=0,
        engagements_made=0,
        intercepts=0,
        intercept_rate=0.0,
        defender_spent=0.0,
        attacker_destroyed=0.0,
        cost_exchange_ratio=None,
    )


def _minimal_frame(**overrides) -> Frame:
    defaults = dict(
        metrics=_minimal_metrics(),
        tracks=[],
        defenders=[],
    )
    defaults.update(overrides)
    return Frame(**defaults)


# ---------------------------------------------------------------------------
# test_frame_includes_heatmap
# ---------------------------------------------------------------------------

def test_frame_includes_heatmap():
    """Frame with heatmap_data serializes the dict under 'heatmap_data'."""
    hm = DetectionHeatmap(width=50, height=50, bounds_m=1000.0)
    hm.add_detection(0.0, 0.0, weight=10.0)
    heatmap_dict = hm.to_dict()

    frame = _minimal_frame(heatmap_data=heatmap_dict)
    serialized = frame.to_dict()

    assert "heatmap_data" in serialized
    hd = serialized["heatmap_data"]
    assert hd is not None
    assert "image_b64" in hd
    assert "bounds_m" in hd
    assert "width" in hd
    assert "height" in hd
    assert hd["bounds_m"] == 1000.0
    assert hd["width"] == 50
    assert hd["height"] == 50

    # Confirm base64 decodes to a valid PNG
    raw = base64.b64decode(hd["image_b64"])
    assert raw[:4] == b"\x89PNG", "image_b64 must decode to a PNG"


# ---------------------------------------------------------------------------
# test_frame_without_heatmap
# ---------------------------------------------------------------------------

def test_frame_without_heatmap():
    """Default Frame has heatmap_data=None; serializes to null — backward compatible."""
    frame = _minimal_frame()
    assert frame.heatmap_data is None

    serialized = frame.to_dict()
    assert "heatmap_data" in serialized
    assert serialized["heatmap_data"] is None

    # Core fields still present — no regression.
    assert serialized["type"] == "WARGAME_FRAME"
    assert "metrics" in serialized
    assert "tracks" in serialized
    assert "defenders" in serialized


# ---------------------------------------------------------------------------
# test_heatmap_detections_accumulate
# ---------------------------------------------------------------------------

def test_heatmap_detections_accumulate():
    """Feeding detections causes hotspots to appear at the expected location."""
    hm = DetectionHeatmap(width=100, height=100, bounds_m=2000.0)

    # Concentrate many detections at one location.
    for _ in range(40):
        hm.add_detection(500.0, 500.0)

    # Add a lighter scatter elsewhere.
    hm.add_detection(-800.0, -800.0, weight=2.0)

    spots = hm.hotspots(threshold=0.8)

    assert len(spots) >= 1, "Expected at least one hotspot after concentrated detections"

    xs = [s[0] for s in spots]
    ys = [s[1] for s in spots]
    near = any(abs(x - 500.0) < 150 and abs(y - 500.0) < 150 for x, y in zip(xs, ys))
    assert near, f"Expected a hotspot near (500, 500). Got: {list(zip(xs, ys))}"

    # Verify the hotspot intensity is in [0, 1].
    intensities = [s[2] for s in spots]
    assert all(0.0 <= i <= 1.0 for i in intensities), f"Intensities out of range: {intensities}"


# ---------------------------------------------------------------------------
# test_heatmap_decay_applied_each_tick
# ---------------------------------------------------------------------------

def test_heatmap_decay_applied_each_tick():
    """tick() reduces accumulated intensity — simulating the runner's per-step decay."""
    hm = DetectionHeatmap(width=50, height=50, bounds_m=1000.0, decay=0.5)
    hm.add_detection(0.0, 0.0, weight=8.0)

    before = hm._grid.max()
    hm.tick()
    after = hm._grid.max()

    assert after == pytest.approx(before * 0.5)


# ---------------------------------------------------------------------------
# test_heatmap_serialized_in_frame_every_fifth_tick (runner cadence simulation)
# ---------------------------------------------------------------------------

def test_heatmap_only_on_fifth_tick():
    """Frames on non-5th ticks carry None; the 5th tick carries a dict.

    This mirrors the runner's 'if self._tick % 5 == 0' guard and ensures the
    Frame field stays backward compatible between heatmap emissions.
    """
    hm = DetectionHeatmap(width=50, height=50, bounds_m=1000.0)
    hm.add_detection(100.0, 100.0)

    for tick in range(1, 11):
        heatmap_data = hm.to_dict() if tick % 5 == 0 else None
        # Patch metrics tick number for realism.
        metrics = Metrics(
            tick=tick,
            sim_time_s=tick * 0.2,
            active_hostiles=1,
            tracks_held=1,
            leakers=0,
            engagements_made=0,
            intercepts=0,
            intercept_rate=0.0,
            defender_spent=0.0,
            attacker_destroyed=0.0,
            cost_exchange_ratio=None,
        )
        frame = Frame(metrics=metrics, tracks=[], defenders=[], heatmap_data=heatmap_data)
        serialized = frame.to_dict()

        if tick % 5 == 0:
            assert serialized["heatmap_data"] is not None, f"tick {tick}: expected heatmap_data"
            assert "image_b64" in serialized["heatmap_data"]
        else:
            assert serialized["heatmap_data"] is None, f"tick {tick}: expected None heatmap_data"
