"""
Prometheus metrics for the OVERWATCH ISR Platform.

Exposes counters, gauges, and histograms for WebSocket connections, commands,
wargame ticks, engagements, and cost exchange ratio. The /metrics endpoint
returns Prometheus text format for scraping.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger("overwatch.metrics")

# Dedicated registry so tests can create isolated instances without colliding
# with the default global registry.
REGISTRY = CollectorRegistry()

# -- WebSocket --
WS_CONNECTIONS_TOTAL = Counter(
    "overwatch_ws_connections_total",
    "Total WebSocket connections accepted",
    registry=REGISTRY,
)

WS_CONNECTIONS_ACTIVE = Gauge(
    "overwatch_ws_connections_active",
    "Current active WebSocket connections",
    registry=REGISTRY,
)

# -- Commands --
COMMANDS_TOTAL = Counter(
    "overwatch_commands_total",
    "Commands received via WebSocket",
    ["command", "success"],
    registry=REGISTRY,
)

# -- Wargame --
WARGAME_TICKS_TOTAL = Counter(
    "overwatch_wargame_ticks_total",
    "Wargame ticks processed",
    registry=REGISTRY,
)

WARGAME_FRAME_DURATION = Histogram(
    "overwatch_wargame_frame_duration_seconds",
    "Time spent processing a single wargame frame",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)

# -- Engagements --
ENGAGEMENTS_TOTAL = Counter(
    "overwatch_engagements_total",
    "Engagement outcomes by status",
    ["status"],
    registry=REGISTRY,
)

# -- Cost --
COST_EXCHANGE_RATIO = Gauge(
    "overwatch_cost_exchange_ratio",
    "Current defender-to-attacker cost exchange ratio",
    registry=REGISTRY,
)

# -- HTTP request duration (optional instrumentation) --
HTTP_REQUEST_DURATION = Histogram(
    "overwatch_http_request_duration_seconds",
    "HTTP request processing time",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    registry=REGISTRY,
)

metrics_router = APIRouter(tags=["Metrics"])


@metrics_router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
)
async def prometheus_metrics() -> Response:
    """Return all registered metrics in Prometheus text exposition format."""
    body = generate_latest(REGISTRY)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
