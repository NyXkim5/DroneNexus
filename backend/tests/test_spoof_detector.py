"""Tests for GPS spoofing detection module."""
from __future__ import annotations

import pytest

from csontology import Detection
from sensors.spoof_detector import SpoofAlert, SpoofDetector


def _det(
    drone_id: str = "UAV-1",
    ts: float = 0.0,
    pos: tuple = (0.0, 0.0, 50.0),
    vel: tuple = (5.0, 0.0, 0.0),
    sensor: str = "rid-1",
) -> Detection:
    return Detection(
        id=drone_id, timestamp=ts,
        position=pos, velocity=vel,
        confidence=0.9, sensor_id=sensor,
    )


class TestVelocityViolation:
    def test_normal_speed_no_alert(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(20, 0, 50)))
        assert not any(a.alert_type == "VELOCITY" for a in alerts)

    def test_excessive_speed_triggers(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(200, 0, 50)))
        vel_alerts = [a for a in alerts if a.alert_type == "VELOCITY"]
        assert len(vel_alerts) == 1
        assert vel_alerts[0].confidence > 0


class TestAltitudeViolation:
    def test_normal_altitude_rate(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(0, 0, 80)))
        assert not any(a.alert_type == "ALTITUDE" for a in alerts)

    def test_impossible_altitude_rate(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(0, 0, 200)))
        alt_alerts = [a for a in alerts if a.alert_type == "ALTITUDE"]
        assert len(alt_alerts) == 1
        assert "150.0" in alt_alerts[0].details


class TestTeleportation:
    def test_small_jump_ok(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=10.0, pos=(100, 0, 50)))
        assert not any(a.alert_type == "TELEPORT" for a in alerts)

    def test_large_jump_triggers(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(800, 0, 50)))
        tp_alerts = [a for a in alerts if a.alert_type == "TELEPORT"]
        assert len(tp_alerts) == 1
        assert tp_alerts[0].confidence > 0.5


class TestDuplicateID:
    def test_close_positions_no_alert(self):
        sd = SpoofDetector()
        sd.check(_det(ts=1.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.5, pos=(10, 0, 50)))
        assert not any(a.alert_type == "DUPLICATE" for a in alerts)

    def test_far_apart_same_instant(self):
        sd = SpoofDetector()
        sd.check(_det(ts=1.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.5, pos=(500, 0, 50)))
        dup_alerts = [a for a in alerts if a.alert_type == "DUPLICATE"]
        assert len(dup_alerts) == 1

    def test_far_apart_long_interval_no_duplicate(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=5.0, pos=(500, 0, 50)))
        assert not any(a.alert_type == "DUPLICATE" for a in alerts)


class TestVelocityConsistency:
    def test_consistent_velocity_ok(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50), vel=(10, 0, 0)))
        alerts = sd.check(_det(ts=1.0, pos=(10, 0, 50), vel=(10, 0, 0)))
        assert not any(a.alert_type == "INCONSISTENT" for a in alerts)

    def test_mismatched_velocity(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50), vel=(50, 0, 0)))
        # Reports 50 m/s but only moved 5m in 1s
        alerts = sd.check(_det(ts=1.0, pos=(5, 0, 50), vel=(50, 0, 0)))
        inc_alerts = [a for a in alerts if a.alert_type == "INCONSISTENT"]
        assert len(inc_alerts) == 1


class TestConfidence:
    def test_confidence_capped_at_one(self):
        sd = SpoofDetector()
        sd.check(_det(ts=0.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=0.1, pos=(50000, 0, 50)))
        for a in alerts:
            assert a.confidence <= 1.0

    def test_confidence_scales_with_severity(self):
        sd1 = SpoofDetector()
        sd1.check(_det(ts=0.0, pos=(0, 0, 50)))
        mild = sd1.check(_det(ts=1.0, pos=(600, 0, 50)))

        sd2 = SpoofDetector()
        sd2.check(_det(ts=0.0, pos=(0, 0, 50)))
        severe = sd2.check(_det(ts=1.0, pos=(5000, 0, 50)))

        tp_mild = [a for a in mild if a.alert_type == "TELEPORT"]
        tp_severe = [a for a in severe if a.alert_type == "TELEPORT"]
        assert tp_severe[0].confidence > tp_mild[0].confidence


class TestBatch:
    def test_check_batch(self):
        sd = SpoofDetector()
        dets = [
            _det(ts=0.0, pos=(0, 0, 50)),
            _det(ts=1.0, pos=(2000, 0, 50)),
        ]
        alerts = sd.check_batch(dets)
        assert len(alerts) > 0
        types = {a.alert_type for a in alerts}
        assert "TELEPORT" in types


class TestEdgeCases:
    def test_first_detection_no_alerts(self):
        sd = SpoofDetector()
        alerts = sd.check(_det(ts=0.0, pos=(100, 200, 50)))
        assert alerts == []

    def test_zero_dt_skipped(self):
        sd = SpoofDetector()
        sd.check(_det(ts=1.0, pos=(0, 0, 50)))
        alerts = sd.check(_det(ts=1.0, pos=(1000, 0, 50)))
        # dt=0 so _compare is skipped, only duplicate check runs
        assert not any(a.alert_type == "VELOCITY" for a in alerts)

    def test_different_drone_ids_independent(self):
        sd = SpoofDetector()
        sd.check(_det(drone_id="A", ts=0.0, pos=(0, 0, 50)))
        sd.check(_det(drone_id="B", ts=0.0, pos=(5000, 0, 50)))
        alerts = sd.check(_det(drone_id="A", ts=1.0, pos=(10, 0, 50)))
        assert not any(a.alert_type == "TELEPORT" for a in alerts)
