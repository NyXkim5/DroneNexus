"""Tests for Prometheus metrics and the /metrics endpoint."""
from __future__ import annotations

import pytest
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


@pytest.fixture()
def fresh_registry() -> CollectorRegistry:
    """Return an isolated registry so tests do not collide with global state."""
    return CollectorRegistry()


class TestPrometheusCounters:
    """Verify counters increment and appear in text output."""

    def test_counter_increments(self, fresh_registry: CollectorRegistry) -> None:
        counter = Counter(
            "test_ws_connections_total",
            "Test counter",
            registry=fresh_registry,
        )
        counter.inc()
        counter.inc()
        output = generate_latest(fresh_registry).decode()
        assert "test_ws_connections_total" in output
        assert "2.0" in output

    def test_labeled_counter(self, fresh_registry: CollectorRegistry) -> None:
        counter = Counter(
            "test_commands_total",
            "Test labeled counter",
            ["command", "success"],
            registry=fresh_registry,
        )
        counter.labels(command="ARM", success="True").inc()
        counter.labels(command="ARM", success="True").inc()
        counter.labels(command="LAND", success="False").inc()
        output = generate_latest(fresh_registry).decode()
        assert 'command="ARM"' in output
        assert 'command="LAND"' in output


class TestPrometheusGauge:
    """Verify gauge tracks current value."""

    def test_gauge_inc_dec(self, fresh_registry: CollectorRegistry) -> None:
        gauge = Gauge(
            "test_ws_active",
            "Test gauge",
            registry=fresh_registry,
        )
        gauge.inc()
        gauge.inc()
        gauge.dec()
        output = generate_latest(fresh_registry).decode()
        assert "test_ws_active" in output
        assert "1.0" in output

    def test_gauge_set(self, fresh_registry: CollectorRegistry) -> None:
        gauge = Gauge(
            "test_cost_ratio",
            "Test gauge set",
            registry=fresh_registry,
        )
        gauge.set(2.5)
        output = generate_latest(fresh_registry).decode()
        assert "2.5" in output


class TestPrometheusHistogram:
    """Verify histogram records observations."""

    def test_histogram_observe(self, fresh_registry: CollectorRegistry) -> None:
        hist = Histogram(
            "test_frame_duration",
            "Test histogram",
            buckets=(0.01, 0.05, 0.1),
            registry=fresh_registry,
        )
        hist.observe(0.03)
        hist.observe(0.07)
        output = generate_latest(fresh_registry).decode()
        assert "test_frame_duration_count" in output
        assert "2.0" in output


class TestMetricsModule:
    """Verify the shared metrics module objects exist and function."""

    def test_registry_has_ws_counter(self) -> None:
        from api.metrics import REGISTRY, WS_CONNECTIONS_TOTAL
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_ws_connections_total" in output

    def test_registry_has_active_gauge(self) -> None:
        from api.metrics import REGISTRY, WS_CONNECTIONS_ACTIVE
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_ws_connections_active" in output

    def test_registry_has_commands_counter(self) -> None:
        from api.metrics import REGISTRY, COMMANDS_TOTAL
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_commands_total" in output

    def test_registry_has_wargame_ticks(self) -> None:
        from api.metrics import REGISTRY, WARGAME_TICKS_TOTAL
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_wargame_ticks_total" in output

    def test_registry_has_frame_duration(self) -> None:
        from api.metrics import REGISTRY, WARGAME_FRAME_DURATION
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_wargame_frame_duration_seconds" in output

    def test_registry_has_engagements(self) -> None:
        from api.metrics import REGISTRY, ENGAGEMENTS_TOTAL
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_engagements_total" in output

    def test_registry_has_cost_ratio(self) -> None:
        from api.metrics import REGISTRY, COST_EXCHANGE_RATIO
        output = generate_latest(REGISTRY).decode()
        assert "overwatch_cost_exchange_ratio" in output


class TestMetricsEndpoint:
    """Verify the /metrics FastAPI route returns valid Prometheus text."""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from api.metrics import metrics_router
        from fastapi import FastAPI

        test_app = FastAPI()
        test_app.include_router(metrics_router)
        return TestClient(test_app)

    def test_metrics_endpoint_returns_200(self, client) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type(self, client) -> None:
        resp = client.get("/metrics")
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct or "text/openmetrics" in ct

    def test_metrics_body_contains_overwatch(self, client) -> None:
        resp = client.get("/metrics")
        assert "overwatch_" in resp.text


class TestLoggingConfig:
    """Verify structured logging configuration."""

    def test_configure_logging_text(self) -> None:
        import os
        os.environ.pop("OVERWATCH_LOG_FORMAT", None)
        from api.logging_config import configure_logging
        configure_logging()
        import logging
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_configure_logging_json(self) -> None:
        import os
        os.environ["OVERWATCH_LOG_FORMAT"] = "json"
        try:
            from api.logging_config import configure_logging, JSONFormatter
            configure_logging()
            import logging
            root = logging.getLogger()
            handler = root.handlers[0]
            assert isinstance(handler.formatter, JSONFormatter)
        finally:
            os.environ.pop("OVERWATCH_LOG_FORMAT", None)

    def test_json_formatter_output(self) -> None:
        import json as json_mod
        import logging
        from api.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="overwatch.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json_mod.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "overwatch.test"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
