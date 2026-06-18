"""Tests for the DetectionHeatmap accumulator."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64
import random

import numpy as np
import pytest

from vision.heatmap import DetectionHeatmap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_heatmap(**kwargs) -> DetectionHeatmap:
    defaults = dict(width=200, height=200, bounds_m=5000.0, decay=0.995)
    defaults.update(kwargs)
    return DetectionHeatmap(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_add_detection_increments_cell():
    hm = _make_heatmap()
    # Grid starts at zero everywhere
    assert hm._grid.sum() == 0.0

    hm.add_detection(0.0, 0.0)
    assert hm._grid.sum() == pytest.approx(1.0)

    # Adding at same location increments further
    hm.add_detection(0.0, 0.0)
    assert hm._grid.sum() == pytest.approx(2.0)


def test_decay_reduces_values():
    hm = _make_heatmap(decay=0.5)
    hm.add_detection(100.0, 200.0, weight=1.0)
    before = hm._grid.max()

    hm.tick()
    after = hm._grid.max()

    assert after < before
    assert after == pytest.approx(before * 0.5)


def test_bounds_clipping():
    hm = _make_heatmap(bounds_m=5000.0)
    # Exactly on the boundary should be clipped (> bounds, not >=)
    hm.add_detection(6000.0, 0.0)   # x too far right
    hm.add_detection(0.0, -6000.0)  # y too far down
    hm.add_detection(-6000.0, 6000.0)

    assert hm._grid.sum() == 0.0, "Out-of-bounds detections must be ignored"


def test_hotspots_returns_peaks():
    hm = _make_heatmap()
    # Concentrate many detections at one spot
    for _ in range(50):
        hm.add_detection(500.0, 500.0)
    # Scatter a few at another location
    hm.add_detection(-1000.0, -1000.0, weight=1.0)

    spots = hm.hotspots(threshold=0.9)
    assert len(spots) >= 1

    # The dominant hotspot should be near (500, 500)
    xs = [s[0] for s in spots]
    ys = [s[1] for s in spots]
    # At least one hotspot within 100 m of the concentrated zone
    near = any(abs(x - 500.0) < 100 and abs(y - 500.0) < 100 for x, y in zip(xs, ys))
    assert near, f"Expected hotspot near (500, 500), got {list(zip(xs, ys))}"


def test_to_image_shape():
    hm = _make_heatmap(width=200, height=200)
    hm.add_detection(0.0, 0.0)
    img = hm.to_image()

    assert img.shape == (200, 200, 4), f"Expected (200, 200, 4), got {img.shape}"
    assert img.dtype == np.uint8


def test_to_dict_has_required_keys():
    hm = _make_heatmap(width=100, height=100, bounds_m=3000.0)
    hm.add_detection(0.0, 0.0)
    data = hm.to_dict()

    assert "image_b64" in data
    assert "bounds_m" in data
    assert "width" in data
    assert "height" in data

    assert data["bounds_m"] == 3000.0
    assert data["width"] == 100
    assert data["height"] == 100

    # Verify it is valid base64 that decodes to a non-empty PNG
    raw = base64.b64decode(data["image_b64"])
    assert raw[:4] == b"\x89PNG", "image_b64 must decode to a PNG"


def test_reset_zeros_grid():
    hm = _make_heatmap()
    hm.add_detection(0.0, 0.0, weight=10.0)
    hm.add_detection(100.0, -200.0, weight=5.0)
    assert hm._grid.sum() > 0.0

    hm.reset()
    assert hm._grid.sum() == 0.0
    assert hm._grid.max() == 0.0


def test_batch_add():
    hm = _make_heatmap()
    rng = random.Random(42)
    positions = [
        (rng.uniform(-4000, 4000), rng.uniform(-4000, 4000))
        for _ in range(100)
    ]
    hm.add_detections(positions)

    # All 100 in-bounds positions (all are within +/-4000 < 5000) should land
    assert hm._grid.sum() == pytest.approx(100.0)


def test_to_image_all_zeros_returns_transparent():
    hm = _make_heatmap()
    img = hm.to_image()
    # When grid is empty, normalized is 0 everywhere -> stop[0] = fully transparent
    assert img[:, :, 3].max() == 0, "Empty grid should produce a fully transparent image"


def test_weight_parameter_respected():
    hm = _make_heatmap()
    hm.add_detection(0.0, 0.0, weight=5.0)
    assert hm._grid.max() == pytest.approx(5.0)


if __name__ == "__main__":
    tests = [
        test_add_detection_increments_cell,
        test_decay_reduces_values,
        test_bounds_clipping,
        test_hotspots_returns_peaks,
        test_to_image_shape,
        test_to_dict_has_required_keys,
        test_reset_zeros_grid,
        test_batch_add,
        test_to_image_all_zeros_returns_transparent,
        test_weight_parameter_respected,
    ]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} tests passed")
