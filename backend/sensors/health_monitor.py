"""
Sensor health monitoring for OVERWATCH.

Tracks data rates, latency, and error counts for all registered sensors
(cameras, RF decoders, SDR sources). Detects degradation and outages,
generates alerts on status transitions, and provides a system-wide summary.

No external dependencies beyond the standard library.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List

logger = logging.getLogger("overwatch.health_monitor")

# Status constants
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
OFFLINE = "OFFLINE"
UNKNOWN = "UNKNOWN"

# Thresholds
_HEALTHY_RATE_RATIO = 0.80
_DEGRADED_RATE_RATIO = 0.30
_OFFLINE_SILENCE_S = 10.0
_ERROR_DEGRADED_THRESHOLD = 5
_ERROR_OFFLINE_THRESHOLD = 20
_ERROR_WINDOW_S = 60.0
_WATCHDOG_INTERVAL_S = 5.0


@dataclass
class SensorHealth:
    """Snapshot of a single sensor's health state."""

    sensor_id: str
    sensor_type: str
    status: str
    last_data_time: float
    data_rate_hz: float
    expected_rate_hz: float
    latency_ms: float
    error_count: int
    uptime_s: float
    last_error: str


@dataclass
class _SensorState:
    """Internal mutable state for a monitored sensor."""

    sensor_id: str
    sensor_type: str
    expected_rate_hz: float
    registered_at: float = field(default_factory=time.monotonic)
    status: str = UNKNOWN
    last_data_time: float = 0.0
    data_timestamps: Deque[float] = field(default_factory=deque)
    error_timestamps: Deque[float] = field(default_factory=deque)
    error_messages: Deque[str] = field(default_factory=deque)
    latency_samples: Deque[float] = field(default_factory=deque)
    total_errors: int = 0


class HealthMonitor:
    """Monitors health of all registered sensors.

    Call register_sensor for each data source, then record_data / record_error
    as events arrive. check_health evaluates all sensors against rate and error
    thresholds, generating alerts on status transitions.
    """

    def __init__(self) -> None:
        self._sensors: dict[str, _SensorState] = {}
        self._alerts: list[dict] = []
        self._watchdog_task: asyncio.Task | None = None

    # -- Registration --------------------------------------------------------

    def register_sensor(
        self,
        sensor_id: str,
        sensor_type: str,
        expected_rate_hz: float,
    ) -> None:
        """Add a sensor to the monitor."""
        if sensor_id in self._sensors:
            logger.warning("Sensor %s already registered, skipping", sensor_id)
            return
        self._sensors[sensor_id] = _SensorState(
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            expected_rate_hz=expected_rate_hz,
        )
        logger.info(
            "Registered sensor %s (type=%s, expected=%.1f Hz)",
            sensor_id, sensor_type, expected_rate_hz,
        )

    def unregister_sensor(self, sensor_id: str) -> None:
        """Remove a sensor from monitoring."""
        self._sensors.pop(sensor_id, None)

    # -- Data recording ------------------------------------------------------

    def record_data(
        self,
        sensor_id: str,
        timestamp: float | None = None,
    ) -> None:
        """Record a data arrival event for a sensor."""
        state = self._sensors.get(sensor_id)
        if state is None:
            return
        now = time.monotonic()
        ts = timestamp if timestamp is not None else now
        state.last_data_time = ts
        state.data_timestamps.append(ts)
        self._trim_timestamps(state.data_timestamps, ts, _ERROR_WINDOW_S)

    def record_error(self, sensor_id: str, error_msg: str) -> None:
        """Record a sensor error event."""
        state = self._sensors.get(sensor_id)
        if state is None:
            return
        now = time.monotonic()
        state.total_errors += 1
        state.error_timestamps.append(now)
        state.error_messages.append(error_msg)
        self._trim_timestamps(state.error_timestamps, now, _ERROR_WINDOW_S)
        self._trim_messages(state.error_messages, max_len=50)

    def record_latency(self, sensor_id: str, latency_ms: float) -> None:
        """Record a processing latency sample."""
        state = self._sensors.get(sensor_id)
        if state is None:
            return
        state.latency_samples.append(latency_ms)
        if len(state.latency_samples) > 100:
            state.latency_samples.popleft()

    # -- Health evaluation ---------------------------------------------------

    def check_health(self) -> list[SensorHealth]:
        """Evaluate all sensors and return their health snapshots."""
        now = time.monotonic()
        results: list[SensorHealth] = []
        for state in self._sensors.values():
            health = self._evaluate_sensor(state, now)
            self._check_transition(state, health.status, now)
            state.status = health.status
            results.append(health)
        return results

    def _evaluate_sensor(
        self, state: _SensorState, now: float,
    ) -> SensorHealth:
        """Build a SensorHealth snapshot for one sensor."""
        rate = self._compute_rate(state, now)
        errors = self._count_recent_errors(state, now)
        latency = self._avg_latency(state)
        uptime = now - state.registered_at
        status = self._classify(state, rate, errors, now)
        last_err = state.error_messages[-1] if state.error_messages else ""
        return SensorHealth(
            sensor_id=state.sensor_id,
            sensor_type=state.sensor_type,
            status=status,
            last_data_time=state.last_data_time,
            data_rate_hz=rate,
            expected_rate_hz=state.expected_rate_hz,
            latency_ms=latency,
            error_count=errors,
            uptime_s=uptime,
            last_error=last_err,
        )

    def _classify(
        self,
        state: _SensorState,
        rate: float,
        errors: int,
        now: float,
    ) -> str:
        """Determine sensor status from rate and error metrics."""
        if state.last_data_time == 0.0:
            return UNKNOWN
        silence = now - state.last_data_time
        ratio = rate / state.expected_rate_hz if state.expected_rate_hz > 0 else 1.0
        if silence >= _OFFLINE_SILENCE_S:
            return OFFLINE
        if ratio < _DEGRADED_RATE_RATIO or errors >= _ERROR_OFFLINE_THRESHOLD:
            return OFFLINE
        if ratio < _HEALTHY_RATE_RATIO or errors >= _ERROR_DEGRADED_THRESHOLD:
            return DEGRADED
        return HEALTHY

    # -- Alerts --------------------------------------------------------------

    def get_alerts(self) -> list[dict]:
        """Return and clear pending alerts."""
        alerts = list(self._alerts)
        self._alerts.clear()
        return alerts

    def _check_transition(
        self, state: _SensorState, new_status: str, now: float,
    ) -> None:
        """Generate an alert if sensor status changed."""
        old = state.status
        if old == new_status:
            return
        msg = self._transition_message(state.sensor_id, old, new_status)
        self._alerts.append({
            "sensor_id": state.sensor_id,
            "sensor_type": state.sensor_type,
            "old_status": old,
            "new_status": new_status,
            "timestamp": now,
            "message": msg,
        })
        log_fn = logger.warning if new_status in (DEGRADED, OFFLINE) else logger.info
        log_fn("Sensor %s: %s -> %s", state.sensor_id, old, new_status)

    def _transition_message(
        self, sensor_id: str, old: str, new: str,
    ) -> str:
        """Build a human-readable transition message."""
        if new == HEALTHY:
            return f"Sensor {sensor_id} recovered from {old} to {new}"
        return f"Sensor {sensor_id} status changed from {old} to {new}"

    # -- System summary ------------------------------------------------------

    def get_summary(self) -> dict:
        """Return overall system health summary."""
        healths = self.check_health()
        counts = {HEALTHY: 0, DEGRADED: 0, OFFLINE: 0, UNKNOWN: 0}
        for h in healths:
            counts[h.status] = counts.get(h.status, 0) + 1
        overall = self._overall_status(counts)
        return {
            "total_sensors": len(healths),
            "healthy": counts[HEALTHY],
            "degraded": counts[DEGRADED],
            "offline": counts[OFFLINE],
            "unknown": counts[UNKNOWN],
            "overall": overall,
        }

    def _overall_status(self, counts: dict[str, int]) -> str:
        """Determine system-wide status from per-sensor counts."""
        if counts.get(OFFLINE, 0) > 0:
            return OFFLINE
        if counts.get(DEGRADED, 0) > 0:
            return DEGRADED
        if counts.get(HEALTHY, 0) > 0:
            return HEALTHY
        return UNKNOWN

    # -- Watchdog ------------------------------------------------------------

    async def start_watchdog(self) -> None:
        """Start background health check loop."""
        if self._watchdog_task is not None:
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Health monitor watchdog started")

    async def stop_watchdog(self) -> None:
        """Stop the background watchdog."""
        if self._watchdog_task is None:
            return
        self._watchdog_task.cancel()
        try:
            await self._watchdog_task
        except asyncio.CancelledError:
            pass
        self._watchdog_task = None
        logger.info("Health monitor watchdog stopped")

    async def _watchdog_loop(self) -> None:
        """Periodic health check that logs warnings."""
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL_S)
            healths = self.check_health()
            for h in healths:
                if h.status == DEGRADED:
                    logger.warning(
                        "Watchdog: %s DEGRADED (rate=%.1f/%.1f Hz, errors=%d)",
                        h.sensor_id, h.data_rate_hz, h.expected_rate_hz,
                        h.error_count,
                    )
                elif h.status == OFFLINE:
                    logger.warning(
                        "Watchdog: %s OFFLINE (last data %.1fs ago)",
                        h.sensor_id, time.monotonic() - h.last_data_time,
                    )

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _trim_timestamps(
        dq: Deque[float], now: float, window: float,
    ) -> None:
        """Remove timestamps older than window seconds."""
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()

    @staticmethod
    def _trim_messages(dq: Deque[str], max_len: int) -> None:
        """Keep only the most recent messages."""
        while len(dq) > max_len:
            dq.popleft()

    def _compute_rate(self, state: _SensorState, now: float) -> float:
        """Compute data rate in Hz from recent timestamps."""
        self._trim_timestamps(state.data_timestamps, now, _ERROR_WINDOW_S)
        n = len(state.data_timestamps)
        if n < 2:
            return 0.0
        span = state.data_timestamps[-1] - state.data_timestamps[0]
        if span <= 0:
            return 0.0
        return (n - 1) / span

    def _count_recent_errors(
        self, state: _SensorState, now: float,
    ) -> int:
        """Count errors in the last 60 seconds."""
        self._trim_timestamps(state.error_timestamps, now, _ERROR_WINDOW_S)
        return len(state.error_timestamps)

    @staticmethod
    def _avg_latency(state: _SensorState) -> float:
        """Average of recent latency samples, or 0.0 if none."""
        if not state.latency_samples:
            return 0.0
        return sum(state.latency_samples) / len(state.latency_samples)
