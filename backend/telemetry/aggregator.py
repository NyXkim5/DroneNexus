"""
Aggregates all DroneState objects into wire-format JSON arrays
and broadcasts to WebSocket clients at 10Hz.
"""
import asyncio
import json
import time
import logging
from typing import Dict, Set

from config import NexusSettings
from telemetry.collector import DroneState
from protocol import DroneStatus
from swarm.alerts import AlertEngine

logger = logging.getLogger("nexus.aggregator")


class SwarmAggregator:
    """
    Runs the 10Hz publish loop:
    1. Snapshot all DroneState objects
    2. Derive status (LOW_BATT, WEAK_SIGNAL, LOST, etc.)
    3. Serialize as JSON array matching wire protocol
    4. Broadcast to all WebSocket clients
    """

    def __init__(self, settings: NexusSettings):
        self.settings = settings
        self.drone_states: Dict[str, DroneState] = {}
        self.ws_clients: Set = set()
        self._publish_task: asyncio.Task | None = None
        self.alert_engine = AlertEngine(settings)

    def register_drone(self, state: DroneState) -> None:
        self.drone_states[state.drone_id] = state

    async def start(self) -> None:
        interval = 1.0 / self.settings.telemetry_rate_hz
        self._publish_task = asyncio.create_task(self._publish_loop(interval))
        logger.info(f"Aggregator started at {self.settings.telemetry_rate_hz}Hz")

    async def stop(self) -> None:
        if self._publish_task:
            self._publish_task.cancel()

    async def _publish_loop(self, interval: float) -> None:
        while True:
            t0 = time.monotonic()

            packets = []
            for state in self.drone_states.values():
                self._derive_status(state)
                packets.append(state.to_telemetry_packet())

            if packets and self.ws_clients:
                payload = json.dumps(
                    [p.model_dump(mode="json") for p in packets]
                )
                dead = []
                for ws in self.ws_clients:
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.ws_clients.discard(ws)

            # --- Alert engine: check all rules, broadcast any alerts ---
            alerts = self.alert_engine.check_all(self.drone_states)
            if alerts and self.ws_clients:
                alert_payload = json.dumps(
                    [a.model_dump(mode="json") for a in alerts]
                )
                dead = []
                for ws in self.ws_clients:
                    try:
                        await ws.send_text(alert_payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.ws_clients.discard(ws)

            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))

    def _derive_status(self, state: DroneState) -> None:
        now = time.monotonic()
        if state.last_update > 0 and now - state.last_update > self.settings.heartbeat_timeout_ms / 1000:
            state.status = DroneStatus.LOST
        elif not state.in_air and state.alt_agl < 1:
            state.status = DroneStatus.LANDED
        elif state.remaining_pct < self.settings.low_battery_warning_pct:
            state.status = DroneStatus.LOW_BATT
        elif state.rssi < 60:
            state.status = DroneStatus.WEAK_SIGNAL
        else:
            state.status = DroneStatus.ACTIVE

    def get_swarm_health(self) -> dict:
        states = list(self.drone_states.values())
        if not states:
            return {"score": 0, "active": 0, "total": 0}

        avg_battery = sum(s.remaining_pct for s in states) / len(states)
        avg_cohesion = sum(s.cohesion for s in states) / len(states)
        active_count = sum(1 for s in states if s.status == DroneStatus.ACTIVE)
        avg_link = sum(s.quality for s in states) / len(states)

        score = (
            0.30 * (avg_battery / 100) +
            0.25 * avg_cohesion +
            0.25 * (avg_link / 100) +
            0.20 * (active_count / len(states))
        )

        return {
            "score": round(score, 3),
            "active": active_count,
            "total": len(states),
            "avg_battery": round(avg_battery, 1),
            "avg_cohesion": round(avg_cohesion, 3),
            "mesh_status": "OK" if avg_link > 80 else ("DEGRADED" if avg_link > 50 else "LOST"),
        }
