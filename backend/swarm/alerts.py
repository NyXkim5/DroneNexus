"""
NEXUS Alert Engine — Evaluates drone telemetry against safety rules
and emits AlertPacket messages to the HUD.

Built-in rules:
  LOW_BATTERY, WEAK_SIGNAL, LINK_DEGRADED, ALTITUDE_LIMIT,
  SPEED_LIMIT, DRONE_LOST, GPS_DEGRADED
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel

from config import NexusSettings
from protocol import AlertSeverity, DroneStatus, MessageType
from telemetry.collector import DroneState

logger = logging.getLogger("nexus.alerts")


# ---------------------------------------------------------------------------
# AlertRule — a single named check with cooldown
# ---------------------------------------------------------------------------

@dataclass
class AlertRule:
    """
    Describes one alert check.

    Parameters
    ----------
    name : str
        Rule identifier (e.g. "LOW_BATTERY").
    check : Callable[[DroneState, NexusSettings], List[AlertPacket] | None]
        Function that inspects a single DroneState and returns zero or more
        AlertPacket instances if the condition fires.
    cooldown_seconds : float
        Minimum interval between repeated alerts for the same
        (drone_id, rule_name) pair.
    """
    name: str
    check: Callable[["DroneState", "NexusSettings"], Optional[List["AlertPacket"]]]
    cooldown_seconds: float = 30.0


# ---------------------------------------------------------------------------
# AlertPacket — Pydantic model matching the HUD's expected format
# ---------------------------------------------------------------------------

class AlertPacket(BaseModel):
    """Wire-format alert broadcast to WebSocket clients."""
    type: MessageType = MessageType.ALERT
    drone_id: str
    severity: AlertSeverity
    alert_type: str
    message: str
    value: float
    threshold: float
    timestamp: str

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# Built-in rule check functions
# ---------------------------------------------------------------------------

def _check_low_battery(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    alerts: List[AlertPacket] = []
    ts = _now_iso()
    pct = state.remaining_pct

    if pct < settings.low_battery_critical_pct:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.CRITICAL,
            alert_type="LOW_BATTERY",
            message=f"Battery at {pct:.0f}%",
            value=pct,
            threshold=float(settings.low_battery_critical_pct),
            timestamp=ts,
        ))
    elif pct < settings.low_battery_warning_pct:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="LOW_BATTERY",
            message=f"Battery at {pct:.0f}%",
            value=pct,
            threshold=float(settings.low_battery_warning_pct),
            timestamp=ts,
        ))
    return alerts or None


def _check_weak_signal(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    alerts: List[AlertPacket] = []
    ts = _now_iso()
    rssi = state.rssi

    if rssi < 40:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.CRITICAL,
            alert_type="WEAK_SIGNAL",
            message=f"RSSI at {rssi}",
            value=float(rssi),
            threshold=40.0,
            timestamp=ts,
        ))
    elif rssi < 60:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="WEAK_SIGNAL",
            message=f"RSSI at {rssi}",
            value=float(rssi),
            threshold=60.0,
            timestamp=ts,
        ))
    return alerts or None


def _check_link_degraded(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    alerts: List[AlertPacket] = []
    ts = _now_iso()

    if state.quality < 70:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="LINK_DEGRADED",
            message=f"Link quality at {state.quality}%",
            value=float(state.quality),
            threshold=70.0,
            timestamp=ts,
        ))

    if state.latency_ms > 50:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="LINK_DEGRADED",
            message=f"Latency at {state.latency_ms}ms",
            value=float(state.latency_ms),
            threshold=50.0,
            timestamp=ts,
        ))
    return alerts or None


def _check_altitude_limit(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    if state.alt_agl > settings.max_altitude_m:
        return [AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="ALTITUDE_LIMIT",
            message=f"Altitude {state.alt_agl:.1f}m exceeds limit",
            value=state.alt_agl,
            threshold=settings.max_altitude_m,
            timestamp=_now_iso(),
        )]
    return None


def _check_speed_limit(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    if state.ground_speed > settings.max_speed_ms:
        return [AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="SPEED_LIMIT",
            message=f"Speed {state.ground_speed:.1f}m/s exceeds limit",
            value=state.ground_speed,
            threshold=settings.max_speed_ms,
            timestamp=_now_iso(),
        )]
    return None


def _check_drone_lost(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    if state.status == DroneStatus.LOST:
        return [AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.CRITICAL,
            alert_type="DRONE_LOST",
            message=f"{state.drone_id} link lost",
            value=0.0,
            threshold=float(settings.heartbeat_timeout_ms),
            timestamp=_now_iso(),
        )]
    return None


def _check_gps_degraded(
    state: DroneState, settings: NexusSettings
) -> Optional[List[AlertPacket]]:
    alerts: List[AlertPacket] = []
    ts = _now_iso()

    if state.hdop > 2.0:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="GPS_DEGRADED",
            message=f"HDOP at {state.hdop:.1f}",
            value=state.hdop,
            threshold=2.0,
            timestamp=ts,
        ))

    if state.satellites < 8:
        alerts.append(AlertPacket(
            drone_id=state.drone_id,
            severity=AlertSeverity.WARNING,
            alert_type="GPS_DEGRADED",
            message=f"Only {state.satellites} satellites",
            value=float(state.satellites),
            threshold=8.0,
            timestamp=ts,
        ))
    return alerts or None


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# AlertEngine — runs all rules, enforces cooldowns
# ---------------------------------------------------------------------------

class AlertEngine:
    """
    Evaluates a set of AlertRules against every DroneState and returns
    AlertPacket instances for any violations, respecting per-drone per-rule
    cooldowns to avoid flooding the HUD.
    """

    def __init__(self, settings: NexusSettings) -> None:
        self.settings = settings

        # Cooldown tracker: (drone_id, rule_name) -> monotonic timestamp
        self._last_alert: Dict[str, float] = {}

        # Register built-in rules
        self.rules: List[AlertRule] = [
            AlertRule(name="LOW_BATTERY",    check=_check_low_battery,    cooldown_seconds=15.0),
            AlertRule(name="WEAK_SIGNAL",    check=_check_weak_signal,    cooldown_seconds=15.0),
            AlertRule(name="LINK_DEGRADED",  check=_check_link_degraded,  cooldown_seconds=20.0),
            AlertRule(name="ALTITUDE_LIMIT", check=_check_altitude_limit, cooldown_seconds=10.0),
            AlertRule(name="SPEED_LIMIT",    check=_check_speed_limit,    cooldown_seconds=10.0),
            AlertRule(name="DRONE_LOST",     check=_check_drone_lost,     cooldown_seconds=5.0),
            AlertRule(name="GPS_DEGRADED",   check=_check_gps_degraded,   cooldown_seconds=20.0),
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_all(
        self, states: Dict[str, DroneState]
    ) -> List[AlertPacket]:
        """
        Run every rule against every drone.  Returns only those alerts
        whose cooldown has expired since the last time that same
        (drone_id, rule_name) pair fired.
        """
        now = time.monotonic()
        results: List[AlertPacket] = []

        for drone_id, state in states.items():
            for rule in self.rules:
                cooldown_key = f"{drone_id}:{rule.name}"

                # Respect cooldown
                last = self._last_alert.get(cooldown_key, 0.0)
                if now - last < rule.cooldown_seconds:
                    continue

                packets = rule.check(state, self.settings)
                if packets:
                    results.extend(packets)
                    self._last_alert[cooldown_key] = now

        if results:
            logger.debug(
                "Alert engine produced %d alert(s) for %d drone(s)",
                len(results),
                len(states),
            )
        return results

    def add_rule(self, rule: AlertRule) -> None:
        """Register an additional custom rule."""
        self.rules.append(rule)
        logger.info("Added custom alert rule: %s", rule.name)

    def reset_cooldowns(self) -> None:
        """Clear all cooldown timers (useful for testing)."""
        self._last_alert.clear()
