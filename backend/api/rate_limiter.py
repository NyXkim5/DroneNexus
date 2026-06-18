"""
Per-topic rate limiter and bandwidth tracker for WebSocket message dispatch.

Uses monotonic clock for all timing to avoid wall-clock drift issues.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

logger = logging.getLogger("overwatch.rate_limiter")


class MessageRateLimiter:
    """Per-topic rate limiter for WebSocket broadcasts."""

    def __init__(self, default_hz: float = 10.0) -> None:
        self._default_interval = 1.0 / default_hz
        self._intervals: Dict[str, float] = {}
        self._last_send: Dict[str, float] = {}
        self._lock = threading.Lock()

    def set_rate(self, topic: str, hz: float) -> None:
        """Set the maximum send rate for a specific topic."""
        if hz <= 0:
            raise ValueError(f"Rate must be positive, got {hz}")
        with self._lock:
            self._intervals[topic] = 1.0 / hz

    def should_send(self, topic: str) -> bool:
        """Return True if enough time has elapsed to send on this topic.

        Automatically updates last_send timestamp when returning True.
        """
        now = time.monotonic()
        with self._lock:
            interval = self._intervals.get(topic, self._default_interval)
            last = self._last_send.get(topic, 0.0)
            if now - last < interval:
                return False
            self._last_send[topic] = now
            return True

    def reset(self, topic: str) -> None:
        """Clear state for a topic so the next send is allowed immediately."""
        with self._lock:
            self._last_send.pop(topic, None)


class BandwidthTracker:
    """Tracks bytes sent per client for monitoring."""

    _WINDOW_S = 10.0

    def __init__(self) -> None:
        self._records: Dict[str, List[Tuple[float, int]]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, client_id: str, bytes_sent: int) -> None:
        """Record bytes sent for a client at the current time."""
        now = time.monotonic()
        with self._lock:
            self._records[client_id].append((now, bytes_sent))
            self._prune(client_id, now)

    def get_rate(self, client_id: str) -> float:
        """Return bytes/sec over the last 10 seconds for a client."""
        now = time.monotonic()
        with self._lock:
            self._prune(client_id, now)
            entries = self._records.get(client_id, [])
            if not entries:
                return 0.0
            total_bytes = sum(b for _, b in entries)
            window = min(self._WINDOW_S, now - entries[0][0])
            if window <= 0:
                return float(total_bytes)
            return total_bytes / window

    def reset(self, client_id: str) -> None:
        """Clear all records for a client."""
        with self._lock:
            self._records.pop(client_id, None)

    def _prune(self, client_id: str, now: float) -> None:
        """Remove entries older than the tracking window."""
        cutoff = now - self._WINDOW_S
        entries = self._records.get(client_id)
        if entries is None:
            return
        while entries and entries[0][0] < cutoff:
            entries.pop(0)
