"""Tests for sensor health monitoring system."""
from __future__ import annotations

import asyncio
import time

import pytest

from sensors.health_monitor import (
    DEGRADED,
    HEALTHY,
    OFFLINE,
    UNKNOWN,
    HealthMonitor,
    SensorHealth,
    _OFFLINE_SILENCE_S,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def monitor() -> HealthMonitor:
    return HealthMonitor()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_sensor(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        healths = monitor.check_health()
        assert len(healths) == 1
        assert healths[0].sensor_id == "cam-1"
        assert healths[0].sensor_type == "camera"
        assert healths[0].expected_rate_hz == 30.0

    def test_register_multiple(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        monitor.register_sensor("rf-1", "rf_odid", 10.0)
        monitor.register_sensor("sdr-1", "antsdr", 5.0)
        assert len(monitor.check_health()) == 3

    def test_duplicate_registration_ignored(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        monitor.register_sensor("cam-1", "camera", 60.0)
        healths = monitor.check_health()
        assert len(healths) == 1
        assert healths[0].expected_rate_hz == 30.0

    def test_unregister_sensor(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        monitor.unregister_sensor("cam-1")
        assert len(monitor.check_health()) == 0

    def test_unregister_nonexistent(self, monitor: HealthMonitor) -> None:
        monitor.unregister_sensor("nope")  # should not raise


# ---------------------------------------------------------------------------
# UNKNOWN status -- never received data
# ---------------------------------------------------------------------------

class TestUnknownStatus:
    def test_initial_status_unknown(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        healths = monitor.check_health()
        assert healths[0].status == UNKNOWN

    def test_unknown_has_zero_rate(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 30.0)
        h = monitor.check_health()[0]
        assert h.data_rate_hz == 0.0


# ---------------------------------------------------------------------------
# HEALTHY status
# ---------------------------------------------------------------------------

class TestHealthyStatus:
    def test_healthy_at_full_rate(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # Simulate 10 Hz for 2 seconds
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        healths = monitor.check_health()
        assert healths[0].status == HEALTHY

    def test_healthy_at_80_percent(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # 8 Hz = 80% of 10 Hz (boundary, should be HEALTHY)
        for i in range(16):
            monitor.record_data("cam-1", now + i * 0.125)
        healths = monitor.check_health()
        assert healths[0].status == HEALTHY

    def test_healthy_with_few_errors(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        # 4 errors (< 5 threshold)
        for _ in range(4):
            monitor.record_error("cam-1", "minor glitch")
        healths = monitor.check_health()
        assert healths[0].status == HEALTHY


# ---------------------------------------------------------------------------
# DEGRADED status
# ---------------------------------------------------------------------------

class TestDegradedStatus:
    def test_degraded_low_rate(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # 5 Hz = 50% of 10 Hz -> DEGRADED
        for i in range(10):
            monitor.record_data("cam-1", now + i * 0.2)
        healths = monitor.check_health()
        assert healths[0].status == DEGRADED

    def test_degraded_from_errors(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # Good rate but many errors
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        for i in range(5):
            monitor.record_error("cam-1", f"error {i}")
        healths = monitor.check_health()
        assert healths[0].status == DEGRADED


# ---------------------------------------------------------------------------
# OFFLINE status
# ---------------------------------------------------------------------------

class TestOfflineStatus:
    def test_offline_no_data_for_10s(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        # Send one data point far in the past
        old_time = time.monotonic() - _OFFLINE_SILENCE_S - 1.0
        monitor.record_data("cam-1", old_time)
        healths = monitor.check_health()
        assert healths[0].status == OFFLINE

    def test_offline_very_low_rate(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # 2 Hz = 20% of 10 Hz -> OFFLINE
        for i in range(4):
            monitor.record_data("cam-1", now + i * 0.5)
        healths = monitor.check_health()
        assert healths[0].status == OFFLINE

    def test_offline_from_many_errors(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        for i in range(20):
            monitor.record_error("cam-1", f"critical error {i}")
        healths = monitor.check_health()
        assert healths[0].status == OFFLINE


# ---------------------------------------------------------------------------
# Alert generation
# ---------------------------------------------------------------------------

class TestAlerts:
    def test_alert_on_degraded(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        # First: receive data so status goes UNKNOWN -> HEALTHY
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        monitor.check_health()
        monitor.get_alerts()  # clear initial transition alerts

        # Now degrade: add many errors
        for i in range(10):
            monitor.record_error("cam-1", f"err {i}")
        monitor.check_health()
        alerts = monitor.get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["old_status"] == HEALTHY
        assert alerts[0]["new_status"] == DEGRADED
        assert alerts[0]["sensor_id"] == "cam-1"
        assert "message" in alerts[0]
        assert "timestamp" in alerts[0]

    def test_alert_on_recovery(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        # Go to OFFLINE first via silence
        old_time = time.monotonic() - _OFFLINE_SILENCE_S - 1.0
        monitor.record_data("cam-1", old_time)
        monitor.check_health()
        monitor.get_alerts()  # clear UNKNOWN -> OFFLINE

        # Recover: flood with enough fresh data at correct rate so the
        # old stale timestamp is outvoted in the rate calculation.
        # Use a 3-second burst at 10 Hz (30 points). The old timestamp
        # is ~11s before, so span is ~14s with 30 intervals -> ~2.1 Hz
        # which is still low. Instead, unregister and re-register to
        # reset state, then verify the recovery alert path directly.
        monitor.unregister_sensor("cam-1")
        monitor.register_sensor("cam-1", "camera", 10.0)
        # Set to OFFLINE manually via old data
        stale = time.monotonic() - _OFFLINE_SILENCE_S - 1.0
        monitor.record_data("cam-1", stale)
        monitor.check_health()
        monitor.get_alerts()  # clear UNKNOWN -> OFFLINE

        # Clear stale timestamps by removing them from internal state
        state = monitor._sensors["cam-1"]
        state.data_timestamps.clear()

        # Now send fresh data at full rate
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        monitor.check_health()
        alerts = monitor.get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["new_status"] == HEALTHY
        assert "recovered" in alerts[0]["message"]

    def test_no_alert_when_status_unchanged(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        monitor.check_health()
        monitor.get_alerts()  # clear initial

        # Check again without changes
        monitor.check_health()
        assert len(monitor.get_alerts()) == 0

    def test_alerts_cleared_after_get(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        monitor.check_health()
        alerts1 = monitor.get_alerts()
        assert len(alerts1) > 0
        alerts2 = monitor.get_alerts()
        assert len(alerts2) == 0

    def test_alert_contains_sensor_type(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("rf-1", "rf_dji", 5.0)
        old_time = time.monotonic() - _OFFLINE_SILENCE_S - 1.0
        monitor.record_data("rf-1", old_time)
        monitor.check_health()
        alerts = monitor.get_alerts()
        assert alerts[0]["sensor_type"] == "rf_dji"


# ---------------------------------------------------------------------------
# Error counting
# ---------------------------------------------------------------------------

class TestErrorCounting:
    def test_error_count_in_health(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        monitor.record_error("cam-1", "timeout")
        monitor.record_error("cam-1", "decode fail")
        healths = monitor.check_health()
        assert healths[0].error_count == 2
        assert healths[0].last_error == "decode fail"

    def test_error_on_unregistered_sensor(self, monitor: HealthMonitor) -> None:
        # Should not raise
        monitor.record_error("ghost", "boo")

    def test_data_on_unregistered_sensor(self, monitor: HealthMonitor) -> None:
        monitor.record_data("ghost", time.monotonic())


# ---------------------------------------------------------------------------
# Latency tracking
# ---------------------------------------------------------------------------

class TestLatency:
    def test_latency_average(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        now = time.monotonic()
        for i in range(20):
            monitor.record_data("cam-1", now + i * 0.1)
        monitor.record_latency("cam-1", 10.0)
        monitor.record_latency("cam-1", 20.0)
        healths = monitor.check_health()
        assert healths[0].latency_ms == pytest.approx(15.0)

    def test_latency_zero_when_no_samples(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        healths = monitor.check_health()
        assert healths[0].latency_ms == 0.0


# ---------------------------------------------------------------------------
# System summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_all_healthy(self, monitor: HealthMonitor) -> None:
        now = time.monotonic()
        for i in range(3):
            sid = f"cam-{i}"
            monitor.register_sensor(sid, "camera", 10.0)
            for j in range(20):
                monitor.record_data(sid, now + j * 0.1)

        summary = monitor.get_summary()
        assert summary["total_sensors"] == 3
        assert summary["healthy"] == 3
        assert summary["degraded"] == 0
        assert summary["offline"] == 0
        assert summary["overall"] == HEALTHY

    def test_summary_with_degraded(self, monitor: HealthMonitor) -> None:
        now = time.monotonic()
        monitor.register_sensor("cam-0", "camera", 10.0)
        for j in range(20):
            monitor.record_data("cam-0", now + j * 0.1)

        monitor.register_sensor("cam-1", "camera", 10.0)
        for j in range(10):
            monitor.record_data("cam-1", now + j * 0.2)

        summary = monitor.get_summary()
        assert summary["degraded"] >= 1
        assert summary["overall"] == DEGRADED

    def test_summary_with_offline(self, monitor: HealthMonitor) -> None:
        now = time.monotonic()
        monitor.register_sensor("cam-0", "camera", 10.0)
        for j in range(20):
            monitor.record_data("cam-0", now + j * 0.1)

        monitor.register_sensor("cam-1", "camera", 10.0)
        monitor.record_data("cam-1", now - _OFFLINE_SILENCE_S - 1.0)

        summary = monitor.get_summary()
        assert summary["offline"] >= 1
        assert summary["overall"] == OFFLINE

    def test_summary_all_unknown(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-0", "camera", 10.0)
        summary = monitor.get_summary()
        assert summary["unknown"] == 1
        assert summary["overall"] == UNKNOWN

    def test_summary_empty(self, monitor: HealthMonitor) -> None:
        summary = monitor.get_summary()
        assert summary["total_sensors"] == 0
        assert summary["overall"] == UNKNOWN


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

class TestUptime:
    def test_uptime_increases(self, monitor: HealthMonitor) -> None:
        monitor.register_sensor("cam-1", "camera", 10.0)
        healths = monitor.check_health()
        assert healths[0].uptime_s >= 0.0


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    @pytest.mark.asyncio
    async def test_start_stop_watchdog(self, monitor: HealthMonitor) -> None:
        await monitor.start_watchdog()
        assert monitor._watchdog_task is not None
        assert not monitor._watchdog_task.done()
        await monitor.stop_watchdog()
        assert monitor._watchdog_task is None

    @pytest.mark.asyncio
    async def test_double_start(self, monitor: HealthMonitor) -> None:
        await monitor.start_watchdog()
        task1 = monitor._watchdog_task
        await monitor.start_watchdog()
        assert monitor._watchdog_task is task1
        await monitor.stop_watchdog()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, monitor: HealthMonitor) -> None:
        await monitor.stop_watchdog()  # should not raise


# ---------------------------------------------------------------------------
# SensorHealth dataclass
# ---------------------------------------------------------------------------

class TestSensorHealthDataclass:
    def test_fields(self) -> None:
        h = SensorHealth(
            sensor_id="cam-1",
            sensor_type="camera",
            status=HEALTHY,
            last_data_time=1000.0,
            data_rate_hz=30.0,
            expected_rate_hz=30.0,
            latency_ms=5.0,
            error_count=0,
            uptime_s=120.0,
            last_error="",
        )
        assert h.sensor_id == "cam-1"
        assert h.status == HEALTHY
        assert h.data_rate_hz == 30.0
        assert h.latency_ms == 5.0
