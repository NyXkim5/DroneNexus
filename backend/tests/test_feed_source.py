import pytest
import numpy as np
from vision.models import TargetType
from vision.detector import SimTargetPlacement
from vision.feed_source import FeedSource, SimFeedSource


class TestSimFeedSource:
    def test_returns_frame_and_timestamp(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        source = SimFeedSource(placements=placements, resolution=(640, 480), fps=5.0)
        frame, ts = source.next_frame()
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (480, 640, 3)
        assert isinstance(ts, float)

    def test_resolution_matches(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (0, 0, 0))]
        source = SimFeedSource(placements=placements, resolution=(1280, 720))
        frame, _ = source.next_frame()
        assert frame.shape == (720, 1280, 3)

    def test_timestamps_increment(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (0, 0, 0))]
        source = SimFeedSource(placements=placements, fps=10.0)
        _, ts1 = source.next_frame()
        _, ts2 = source.next_frame()
        assert ts2 > ts1
        assert abs((ts2 - ts1) - 0.1) < 0.01

    def test_frame_not_all_black(self):
        placements = [
            SimTargetPlacement("t1", TargetType.VEHICLE_FUEL_TANKER, (100, 100, 0)),
            SimTargetPlacement("t2", TargetType.INFRA_BUILDING, (300, 300, 0)),
        ]
        source = SimFeedSource(placements=placements, resolution=(640, 480))
        frame, _ = source.next_frame()
        assert frame.sum() > 0

    def test_feed_source_is_abstract(self):
        with pytest.raises(TypeError):
            FeedSource()
