"""
WebSocket endpoint that the HUD connects to.
Registers client with aggregator for telemetry broadcast.
Receives DIRECTIVE packets from HUD and dispatches them.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from api.auth import UserModel, decode_access_token
from api.metrics import (
    COMMANDS_TOTAL,
    COST_EXCHANGE_RATIO,
    ENGAGEMENTS_TOTAL,
    WARGAME_FRAME_DURATION,
    WARGAME_TICKS_TOTAL,
    WS_CONNECTIONS_ACTIVE,
    WS_CONNECTIONS_TOTAL,
)
from api.rate_limiter import BandwidthTracker, MessageRateLimiter
from config import OverwatchSettings

logger = logging.getLogger("overwatch.websocket")

# Default rate limits (Hz) per topic
_DEFAULT_RATES: Dict[str, float] = {
    "telemetry": 10.0,
    "wargame": 10.0,
    "status": 1.0,
}


@dataclass
class ClientSubscription:
    """Per-client topic rate overrides."""

    rates: Dict[str, float] = field(default_factory=dict)
    limiter: MessageRateLimiter = field(default_factory=lambda: MessageRateLimiter(10.0))

    def apply_defaults(self) -> None:
        """Set default topic rates on the limiter."""
        for topic, hz in _DEFAULT_RATES.items():
            self.limiter.set_rate(topic, hz)


@dataclass
class WSClient:
    websocket: WebSocket
    connected_at: float
    last_ping: float
    client_id: str
    messages_sent: int = 0
    messages_received: int = 0
    subscription: ClientSubscription = field(default_factory=ClientSubscription)


class WebSocketHandler:
    def __init__(self, app: object) -> None:
        self.app = app
        self.ws_client_meta: Dict[str, WSClient] = {}
        self.bandwidth: BandwidthTracker = BandwidthTracker()
        self._wargame_rate_limiter = MessageRateLimiter(10.0)
        self._wargame_rate_limiter.set_rate("wargame", _DEFAULT_RATES["wargame"])

    async def _authenticate_ws(self, websocket: WebSocket) -> Optional[UserModel]:
        """Validate JWT from query params. Returns user or closes socket."""
        settings = OverwatchSettings()
        if not settings.auth_enabled:
            return UserModel(username="anonymous", role="operator")

        token = websocket.query_params.get("token")
        if token is None:
            await websocket.close(code=4001, reason="Authentication required")
            return None

        try:
            payload = decode_access_token(token)
            return UserModel(username=payload["sub"], role=payload["role"])
        except HTTPException as exc:
            await websocket.close(code=4003, reason=exc.detail)
            return None

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()

        user = await self._authenticate_ws(websocket)
        if user is None:
            return
        logger.info(f"WS authenticated: user={user.username} role={user.role}")

        # Register with aggregator broadcast set
        self.app.aggregator.ws_clients.add(websocket)

        # Track metadata
        client_id = str(uuid.uuid4())[:8]
        now = time.time()
        sub = ClientSubscription()
        sub.apply_defaults()
        client = WSClient(
            websocket=websocket,
            connected_at=now,
            last_ping=now,
            client_id=client_id,
            subscription=sub,
        )
        self.ws_client_meta[client_id] = client

        WS_CONNECTIONS_TOTAL.inc()
        WS_CONNECTIONS_ACTIVE.inc()
        client_count = len(self.app.aggregator.ws_clients)
        logger.info(f"WebSocket client {client_id} connected ({client_count} total)")

        # Send full state snapshot so reconnecting clients are immediately current
        try:
            await self._send_state_sync(websocket)
            client.messages_sent += 1
        except Exception as e:
            logger.warning(f"Failed to send state sync to {client_id}: {e}")

        try:
            while True:
                raw = await websocket.receive_text()
                client.messages_received += 1
                try:
                    data = json.loads(raw)
                    msg_type = data.get("type")
                    if msg_type == "DIRECTIVE":
                        await self._dispatch_command(data, websocket)
                        client.messages_sent += 1
                    elif msg_type == "CMD":
                        await self._dispatch_command(data, websocket)
                        client.messages_sent += 1
                    elif msg_type == "PING":
                        client.last_ping = time.time()
                        await websocket.send_text(json.dumps({
                            "type": "PONG",
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        }))
                        client.messages_sent += 1
                    elif msg_type == "SUBSCRIBE":
                        self._handle_subscribe(client, data)
                    else:
                        logger.debug(f"Unknown message type: {msg_type}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await websocket.send_text(json.dumps({
                        "type": "ACK",
                        "command": data.get("command", "UNKNOWN") if isinstance(data, dict) else "UNKNOWN",
                        "drone_id": "",
                        "success": False,
                        "message": str(e),
                    }))
                    client.messages_sent += 1
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.app.aggregator.ws_clients.discard(websocket)
            self.ws_client_meta.pop(client_id, None)
            self.bandwidth.reset(client_id)
            WS_CONNECTIONS_ACTIVE.dec()
            logger.info(f"WebSocket client {client_id} disconnected ({len(self.app.aggregator.ws_clients)} total)")

    async def _send_state_sync(self, ws: WebSocket) -> None:
        """Send current state snapshot when client connects."""
        states = self.app.aggregator.drone_states
        packets = [s.to_telemetry_packet().model_dump(mode="json") for s in states.values()]

        formation = "V_FORMATION"
        if hasattr(self.app, "coordinator") and self.app.coordinator:
            formation = self.app.coordinator.current_formation.value

        sync_msg = {
            "type": "STATE_SYNC",
            "assets": packets,
            "drones": packets,
            "formation": formation,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        await ws.send_text(json.dumps(sync_msg))

    def _handle_subscribe(self, client: WSClient, data: dict) -> None:
        """Process a SUBSCRIBE message to set per-topic rate overrides.

        Expected format: {"type": "SUBSCRIBE", "rates": {"telemetry": 5, "status": 2}}
        """
        rates = data.get("rates", {})
        for topic, hz in rates.items():
            if not isinstance(hz, (int, float)) or hz <= 0:
                logger.warning(f"Invalid rate for {topic}: {hz}")
                continue
            client.subscription.limiter.set_rate(topic, float(hz))
            client.subscription.rates[topic] = float(hz)
        logger.info(f"Client {client.client_id} updated subscriptions: {rates}")

    def should_send_to_client(self, client_id: str, topic: str) -> bool:
        """Check whether a message on this topic should be sent to a client."""
        client = self.ws_client_meta.get(client_id)
        if client is None:
            return False
        return client.subscription.limiter.should_send(topic)

    async def send_rate_limited(
        self,
        client: WSClient,
        topic: str,
        payload: str,
    ) -> bool:
        """Send payload to a client if the topic rate allows it.

        Returns True if the message was sent, False if throttled.
        """
        if not client.subscription.limiter.should_send(topic):
            return False
        await client.websocket.send_text(payload)
        client.messages_sent += 1
        self.bandwidth.record(client.client_id, len(payload))
        return True

    async def handle_wargame(self, websocket: WebSocket, scenario_name: str) -> None:
        """Stream BULWARK wargame Frame snapshots as JSON over a websocket.

        Each connection runs one independent wargame for the named scenario and
        pushes a WARGAME_FRAME message per tick. The run ends when the scenario
        terminates or the client disconnects. Unknown scenarios get an error frame
        then a clean close. This endpoint is isolated from the telemetry stream.
        """
        await websocket.accept()

        user = await self._authenticate_ws(websocket)
        if user is None:
            return
        logger.info(f"Wargame WS authenticated: user={user.username} role={user.role}")

        client_id = str(uuid.uuid4())[:8]
        logger.info(f"Wargame WS {client_id} connected, scenario={scenario_name}")

        rate_limiter = MessageRateLimiter(_DEFAULT_RATES["wargame"])
        cot_bridge = self._get_cot_bridge()

        try:
            from wargame import WargameRunner, load_scenario

            scenario = load_scenario(scenario_name)
            events_db = getattr(self.app, "db", None)
            runner = WargameRunner(scenario, events_db=events_db)
            async for frame in runner.run():
                WARGAME_TICKS_TOTAL.inc()
                self._record_frame_metrics(frame)
                if rate_limiter.should_send("wargame"):
                    payload = json.dumps(frame.to_dict())
                    await websocket.send_text(payload)
                    self.bandwidth.record(client_id, len(payload))
                await self._publish_frame_cot(cot_bridge, frame)
        except KeyError as e:
            await websocket.send_text(json.dumps({"type": "ERROR", "error": str(e)}))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Wargame WS error: {e}")
            try:
                await websocket.send_text(json.dumps({"type": "ERROR", "error": str(e)}))
            except Exception:
                logger.debug("Wargame WS already closed")
        finally:
            self.bandwidth.reset(client_id)
            logger.info(f"Wargame WS {client_id} disconnected")

    def _get_cot_bridge(self) -> Optional[object]:
        """Return the app CoTBridge if TAK integration is enabled."""
        return getattr(self.app, "cot_bridge", None)

    async def _publish_frame_cot(self, cot_bridge: Optional[object], frame: object) -> None:
        """Publish the full wargame picture to TAK if a bridge is configured."""
        if cot_bridge is None:
            return
        try:
            await cot_bridge.publish_full_picture(
                tracks=frame.tracks,
                threats=frame.threats,
                defenders=frame.defenders,
                engagements=frame.engagements,
            )
        except Exception as e:
            logger.warning(f"CoT publish failed: {e}")

    @staticmethod
    def _record_frame_metrics(frame: object) -> None:
        """Update Prometheus gauges and counters from a wargame frame."""
        engagements = getattr(frame, "engagements", None) or []
        for eng in engagements:
            status = getattr(eng, "status", None)
            if status is not None:
                ENGAGEMENTS_TOTAL.labels(status=status.value).inc()
        metrics = getattr(frame, "metrics", None)
        if metrics is not None:
            ratio = getattr(metrics, "cost_exchange_ratio", None)
            if ratio is not None:
                COST_EXCHANGE_RATIO.set(ratio)

    async def _dispatch_command(self, data: dict, ws: WebSocket) -> None:
        command = data.get("command", "")
        params = data.get("params", {})
        drone_id = params.get("droneId")

        logger.info(f"Directive received: {command} -> {drone_id or 'ALL'}")

        success = False
        message = ""

        try:
            if command == "LAUNCH_PREP" or command == "ARM":
                success = await self.app.command_dispatcher.arm(drone_id)
            elif command == "STAND_DOWN" or command == "DISARM":
                success = await self.app.command_dispatcher.disarm(drone_id)
            elif command == "LAUNCH" or command == "TAKEOFF":
                altitude = params.get("altitude", 30.0)
                success = await self.app.command_dispatcher.takeoff(drone_id, altitude)
            elif command == "RECOVER" or command == "LAND":
                success = await self.app.command_dispatcher.land(drone_id)
            elif command == "RTB" or command == "RTL":
                success = await self.app.command_dispatcher.rtl(drone_id)
            elif command == "GOTO":
                success = await self.app.command_dispatcher.goto(
                    params.get("lat"), params.get("lng"),
                )
            elif command == "SET_OVERLAY" or command == "SET_FORMATION":
                success = await self.app.command_dispatcher.set_formation(
                    params.get("formation"),
                )
            elif command == "SET_SPEED":
                success = await self.app.command_dispatcher.set_speed(
                    params.get("speed"), drone_id,
                )
            elif command == "SET_ALTITUDE":
                success = await self.app.command_dispatcher.set_altitude(
                    params.get("altitude"), drone_id,
                )
            elif command == "ABORT" or command == "EMERGENCY_STOP":
                success = await self.app.command_dispatcher.emergency_stop()
            elif command == "EXECUTE_MISSION":
                waypoints = params.get("waypoints", [])
                success = await self.app.command_dispatcher.execute_mission(waypoints)
            # ---- ISR / Sensor Commands ----
            elif command == "SENSOR_TILT" or command == "CAMERA_TILT":
                success = await self.app.command_dispatcher.camera_tilt(
                    params.get("angle", 0), drone_id,
                )
            elif command == "MSP_LAUNCH_PREP" or command == "MSP_ARM":
                success = await self.app.command_dispatcher.msp_arm(drone_id)
            elif command == "MSP_STAND_DOWN" or command == "MSP_DISARM":
                success = await self.app.command_dispatcher.msp_disarm(drone_id)
            elif command == "MSP_SET_MODE":
                success = await self.app.command_dispatcher.msp_set_mode(
                    params.get("mode", "ANGLE"), drone_id,
                )
            else:
                message = f"Unknown command: {command}"

        except Exception as e:
            message = str(e)
            logger.error(f"Directive execution error: {e}")

        # Record metric
        COMMANDS_TOTAL.labels(command=command, success=str(success)).inc()

        # Log command
        if hasattr(self.app, 'db') and self.app.db:
            await self.app.db.log_command(command, params, success, message)

        # Send ACK
        ack = {
            "type": "ACK",
            "command": command,
            "drone_id": drone_id or "ALL",
            "success": success,
            "message": message,
        }
        await ws.send_text(json.dumps(ack))
