"""
test_preflight.py

Comprehensive unit tests for the preflight check system.
No ROS2 dependency required.
"""

from __future__ import annotations

import pytest

from drone_control.preflight import (
    PreflightCheck,
    PreflightCheckResult,
    PreflightChecker,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _full_passing_state() -> dict:
    """Return a state dict that passes all checks."""
    return {
        "battery_pct": 85.0,
        "battery_voltage": 16.2,
        "gps_satellites": 12,
        "gps_hdop": 0.8,
        "gps_fix_type": "FIX_3D",
        "connected": True,
        "armed": False,
        "expected_armed": False,
        "lat": 37.7749,
        "lon": -122.4194,
        "alt": 0.0,
        "flight_mode": "GUIDED",
        "accel_bias": 0.01,
        "gyro_bias": 0.001,
    }


# ── PreflightCheckResult enum tests ─────────────────────────────────────────

class TestPreflightCheckResult:

    def test_severity_ordering(self):
        assert PreflightCheckResult.PASS.severity == 0
        assert PreflightCheckResult.WARNING.severity == 10
        assert PreflightCheckResult.RUNNING.severity == 20
        assert PreflightCheckResult.SOFT_FAILURE.severity == 30
        assert PreflightCheckResult.FAILURE.severity == 40

    def test_severity_increases_with_badness(self):
        ordered = [
            PreflightCheckResult.PASS,
            PreflightCheckResult.WARNING,
            PreflightCheckResult.RUNNING,
            PreflightCheckResult.SOFT_FAILURE,
            PreflightCheckResult.FAILURE,
        ]
        for i in range(len(ordered) - 1):
            assert ordered[i].severity < ordered[i + 1].severity

    def test_passed_true_for_pass_and_warning(self):
        assert PreflightCheckResult.PASS.passed is True
        assert PreflightCheckResult.WARNING.passed is True

    def test_passed_false_for_failures(self):
        assert PreflightCheckResult.FAILURE.passed is False
        assert PreflightCheckResult.SOFT_FAILURE.passed is False
        assert PreflightCheckResult.RUNNING.passed is False

    def test_skip_severity_is_zero(self):
        assert PreflightCheckResult.SKIP.severity == 0
        assert PreflightCheckResult.SKIP.passed is False


# ── Battery check tests ─────────────────────────────────────────────────────

class TestCheckBattery:

    def test_pass_good_battery(self):
        checker = PreflightChecker()
        result = checker.check_battery(16.0, 80.0)
        assert result.result is PreflightCheckResult.PASS

    def test_warning_marginal_battery(self):
        checker = PreflightChecker()
        result = checker.check_battery(15.0, 25.0)
        assert result.result is PreflightCheckResult.WARNING

    def test_failure_low_battery(self):
        checker = PreflightChecker()
        result = checker.check_battery(14.0, 15.0)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_zero_battery(self):
        checker = PreflightChecker()
        result = checker.check_battery(0.0, 0.0)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_none_percentage(self):
        checker = PreflightChecker()
        result = checker.check_battery(16.0, None)
        assert result.result is PreflightCheckResult.FAILURE

    def test_custom_thresholds(self):
        checker = PreflightChecker(min_battery_pct=30.0, warn_battery_pct=50.0)
        assert checker.check_battery(15.0, 25.0).result is PreflightCheckResult.FAILURE
        assert checker.check_battery(15.0, 40.0).result is PreflightCheckResult.WARNING
        assert checker.check_battery(16.0, 60.0).result is PreflightCheckResult.PASS

    def test_exact_threshold_boundary(self):
        checker = PreflightChecker()
        # Exactly at min_battery_pct (20.0) should pass (not less than)
        result = checker.check_battery(15.0, 20.0)
        assert result.result is PreflightCheckResult.WARNING

    def test_exact_warning_boundary(self):
        checker = PreflightChecker()
        # Exactly at warn_battery_pct (30.0) should pass
        result = checker.check_battery(16.0, 30.0)
        assert result.result is PreflightCheckResult.PASS


# ── GPS check tests ─────────────────────────────────────────────────────────

class TestCheckGPS:

    def test_pass_good_gps(self):
        checker = PreflightChecker()
        result = checker.check_gps(10, 0.9, "FIX_3D")
        assert result.result is PreflightCheckResult.PASS

    def test_failure_no_fix(self):
        checker = PreflightChecker()
        result = checker.check_gps(10, 0.9, "NO_FIX")
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_none_fix_type(self):
        checker = PreflightChecker()
        result = checker.check_gps(10, 0.9, None)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_too_few_satellites(self):
        checker = PreflightChecker()
        result = checker.check_gps(3, 1.0, "FIX_3D")
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_none_satellites(self):
        checker = PreflightChecker()
        result = checker.check_gps(None, 1.0, "FIX_3D")
        assert result.result is PreflightCheckResult.FAILURE

    def test_warning_low_satellites(self):
        checker = PreflightChecker()
        result = checker.check_gps(5, 1.0, "FIX_3D")
        assert result.result is PreflightCheckResult.WARNING

    def test_warning_high_hdop(self):
        checker = PreflightChecker()
        result = checker.check_gps(10, 3.0, "FIX_3D")
        assert result.result is PreflightCheckResult.WARNING

    def test_warning_combined(self):
        checker = PreflightChecker()
        result = checker.check_gps(5, 3.0, "FIX_3D")
        assert result.result is PreflightCheckResult.WARNING
        assert "low satellite" in result.message
        assert "HDOP" in result.message


# ── Connection check tests ──────────────────────────────────────────────────

class TestCheckConnection:

    def test_pass_connected(self):
        checker = PreflightChecker()
        result = checker.check_connection(True)
        assert result.result is PreflightCheckResult.PASS

    def test_failure_disconnected(self):
        checker = PreflightChecker()
        result = checker.check_connection(False)
        assert result.result is PreflightCheckResult.FAILURE


# ── Armed state check tests ─────────────────────────────────────────────────

class TestCheckArmedState:

    def test_pass_matching_disarmed(self):
        checker = PreflightChecker()
        result = checker.check_armed_state(False, False)
        assert result.result is PreflightCheckResult.PASS

    def test_pass_matching_armed(self):
        checker = PreflightChecker()
        result = checker.check_armed_state(True, True)
        assert result.result is PreflightCheckResult.PASS

    def test_failure_mismatch(self):
        checker = PreflightChecker()
        result = checker.check_armed_state(True, False)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_reverse_mismatch(self):
        checker = PreflightChecker()
        result = checker.check_armed_state(False, True)
        assert result.result is PreflightCheckResult.FAILURE


# ── Geofence check tests ────────────────────────────────────────────────────

class TestCheckGeofence:

    SQUARE_FENCE = [
        (37.0, -123.0),
        (38.0, -123.0),
        (38.0, -122.0),
        (37.0, -122.0),
    ]

    def test_pass_inside_geofence(self):
        checker = PreflightChecker()
        result = checker.check_geofence(37.5, -122.5, 10.0, self.SQUARE_FENCE, 120.0)
        assert result.result is PreflightCheckResult.PASS

    def test_failure_outside_geofence(self):
        checker = PreflightChecker()
        result = checker.check_geofence(36.0, -122.5, 10.0, self.SQUARE_FENCE, 120.0)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_above_max_altitude(self):
        checker = PreflightChecker()
        result = checker.check_geofence(37.5, -122.5, 200.0, self.SQUARE_FENCE, 120.0)
        assert result.result is PreflightCheckResult.FAILURE

    def test_failure_no_position(self):
        checker = PreflightChecker()
        result = checker.check_geofence(None, None, 0.0, self.SQUARE_FENCE, 120.0)
        assert result.result is PreflightCheckResult.FAILURE

    def test_pass_none_altitude(self):
        checker = PreflightChecker()
        result = checker.check_geofence(37.5, -122.5, None, self.SQUARE_FENCE, 120.0)
        assert result.result is PreflightCheckResult.PASS


# ── Mode check tests ────────────────────────────────────────────────────────

class TestCheckMode:

    def test_skip_no_required_mode(self):
        checker = PreflightChecker()
        result = checker.check_mode("GUIDED", None)
        assert result.result is PreflightCheckResult.SKIP

    def test_pass_matching_mode(self):
        checker = PreflightChecker()
        result = checker.check_mode("GUIDED", "GUIDED")
        assert result.result is PreflightCheckResult.PASS

    def test_warning_mismatched_mode(self):
        checker = PreflightChecker()
        result = checker.check_mode("STABILIZE", "GUIDED")
        assert result.result is PreflightCheckResult.WARNING

    def test_warning_none_current_mode(self):
        checker = PreflightChecker()
        result = checker.check_mode(None, "GUIDED")
        assert result.result is PreflightCheckResult.WARNING


# ── IMU check tests ─────────────────────────────────────────────────────────

class TestCheckIMU:

    def test_pass_good_imu(self):
        checker = PreflightChecker()
        result = checker.check_imu(0.01, 0.001)
        assert result.result is PreflightCheckResult.PASS

    def test_skip_no_data(self):
        checker = PreflightChecker()
        result = checker.check_imu(None, None)
        assert result.result is PreflightCheckResult.SKIP

    def test_warning_high_accel_bias(self):
        checker = PreflightChecker()
        result = checker.check_imu(1.0, 0.001)
        assert result.result is PreflightCheckResult.WARNING

    def test_warning_high_gyro_bias(self):
        checker = PreflightChecker()
        result = checker.check_imu(0.01, 0.1)
        assert result.result is PreflightCheckResult.WARNING


# ── Full run_all tests ──────────────────────────────────────────────────────

class TestRunAll:

    def test_full_passing_state(self):
        checker = PreflightChecker()
        results = checker.run_all(_full_passing_state())
        assert checker.passed is True
        assert checker.summary is PreflightCheckResult.PASS

    def test_one_failure_fails_suite(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["battery_pct"] = 5.0
        results = checker.run_all(state)
        assert checker.passed is False
        assert checker.summary is PreflightCheckResult.FAILURE

    def test_warning_still_passes(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["battery_pct"] = 25.0  # Warning range
        results = checker.run_all(state)
        assert checker.passed is True
        assert checker.summary is PreflightCheckResult.WARNING

    def test_summary_returns_worst_result(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["connected"] = False  # FAILURE
        state["gps_satellites"] = 5  # WARNING
        checker.run_all(state)
        assert checker.summary is PreflightCheckResult.FAILURE

    def test_all_results_have_timestamps(self):
        checker = PreflightChecker()
        results = checker.run_all(_full_passing_state())
        for check in results:
            assert check.timestamp > 0

    def test_all_results_have_names(self):
        checker = PreflightChecker()
        results = checker.run_all(_full_passing_state())
        names = {r.name for r in results}
        assert "battery" in names
        assert "gps" in names
        assert "connection" in names
        assert "armed_state" in names

    def test_disconnected_state(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["connected"] = False
        checker.run_all(state)
        assert checker.passed is False

    def test_geofence_included_when_vertices_present(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["geofence_vertices"] = [
            (37.0, -123.0),
            (38.0, -123.0),
            (38.0, -122.0),
            (37.0, -122.0),
        ]
        results = checker.run_all(state)
        names = {r.name for r in results}
        assert "geofence" in names

    def test_geofence_excluded_when_no_vertices(self):
        checker = PreflightChecker()
        results = checker.run_all(_full_passing_state())
        names = {r.name for r in results}
        assert "geofence" not in names

    def test_missing_gps_data(self):
        checker = PreflightChecker()
        state = _full_passing_state()
        state["gps_fix_type"] = None
        state["gps_satellites"] = None
        checker.run_all(state)
        assert checker.passed is False


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_state_dict(self):
        checker = PreflightChecker()
        results = checker.run_all({})
        assert checker.passed is False

    def test_summary_with_no_checks(self):
        checker = PreflightChecker()
        assert checker.summary is PreflightCheckResult.PASS

    def test_passed_with_no_checks(self):
        checker = PreflightChecker()
        assert checker.passed is True

    def test_check_returns_preflightcheck_dataclass(self):
        checker = PreflightChecker()
        result = checker.check_connection(True)
        assert isinstance(result, PreflightCheck)
        assert isinstance(result.result, PreflightCheckResult)
        assert isinstance(result.name, str)
        assert isinstance(result.message, str)
        assert isinstance(result.timestamp, float)
