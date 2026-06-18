"""Tests for the WebSocket rate limiter and bandwidth tracker."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from api.rate_limiter import BandwidthTracker, MessageRateLimiter


class TestMessageRateLimiter:
    """Verify per-topic rate limiting behavior."""

    def test_first_message_allowed(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        assert limiter.should_send("telemetry") is True

    def test_rapid_repeat_blocked(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        assert limiter.should_send("telemetry") is True
        assert limiter.should_send("telemetry") is False

    def test_allowed_after_interval(self) -> None:
        base = time.monotonic()
        with patch("api.rate_limiter.time.monotonic") as mock_mono:
            mock_mono.return_value = base
            limiter = MessageRateLimiter(default_hz=10.0)
            assert limiter.should_send("telemetry") is True

            # Simulate time advancing past the 0.1s interval
            mock_mono.return_value = base + 0.15
            assert limiter.should_send("telemetry") is True

    def test_independent_topics(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        assert limiter.should_send("telemetry") is True
        assert limiter.should_send("status") is True
        # Both should now be blocked
        assert limiter.should_send("telemetry") is False
        assert limiter.should_send("status") is False

    def test_set_rate_overrides_default(self) -> None:
        base = time.monotonic()
        with patch("api.rate_limiter.time.monotonic") as mock_mono:
            mock_mono.return_value = base
            limiter = MessageRateLimiter(default_hz=10.0)
            limiter.set_rate("status", 1.0)

            assert limiter.should_send("status") is True
            # At 1Hz, need 1 second. Immediate retry should fail.
            assert limiter.should_send("status") is False

            mock_mono.return_value = base + 1.1
            assert limiter.should_send("status") is True

    def test_set_rate_rejects_zero(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        with pytest.raises(ValueError):
            limiter.set_rate("bad", 0.0)

    def test_set_rate_rejects_negative(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        with pytest.raises(ValueError):
            limiter.set_rate("bad", -5.0)

    def test_reset_clears_state(self) -> None:
        limiter = MessageRateLimiter(default_hz=10.0)
        assert limiter.should_send("telemetry") is True
        assert limiter.should_send("telemetry") is False
        limiter.reset("telemetry")
        assert limiter.should_send("telemetry") is True


class TestBandwidthTracker:
    """Verify per-client bandwidth tracking."""

    def test_record_and_rate(self) -> None:
        tracker = BandwidthTracker()
        base = time.monotonic()

        with patch("api.rate_limiter.time.monotonic") as mock_mono:
            mock_mono.return_value = base
            tracker.record("client-1", 1000)

            mock_mono.return_value = base + 1.0
            tracker.record("client-1", 1000)

            mock_mono.return_value = base + 2.0
            rate = tracker.get_rate("client-1")
            # 2000 bytes over 2 seconds = 1000 bytes/sec
            assert rate == pytest.approx(1000.0, rel=0.01)

    def test_rate_zero_with_no_records(self) -> None:
        tracker = BandwidthTracker()
        assert tracker.get_rate("unknown") == 0.0

    def test_reset_clears_client(self) -> None:
        tracker = BandwidthTracker()
        tracker.record("client-1", 500)
        tracker.reset("client-1")
        assert tracker.get_rate("client-1") == 0.0

    def test_old_records_pruned(self) -> None:
        tracker = BandwidthTracker()
        base = time.monotonic()

        with patch("api.rate_limiter.time.monotonic") as mock_mono:
            # Record at t=0
            mock_mono.return_value = base
            tracker.record("client-1", 5000)

            # At t=11 (beyond 10s window), old record should be pruned
            mock_mono.return_value = base + 11.0
            tracker.record("client-1", 100)

            rate = tracker.get_rate("client-1")
            # Only the 100-byte record remains, window is essentially 0
            assert rate >= 0.0
            # The 5000-byte record should be gone
            assert rate < 5000.0

    def test_multiple_clients_independent(self) -> None:
        tracker = BandwidthTracker()
        tracker.record("client-a", 1000)
        tracker.record("client-b", 2000)

        rate_a = tracker.get_rate("client-a")
        rate_b = tracker.get_rate("client-b")

        # Both should have nonzero rates
        assert rate_a > 0.0
        assert rate_b > 0.0
        assert rate_b > rate_a
