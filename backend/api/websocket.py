"""
WebSocket endpoint that the HUD connects to.
Registers client with aggregator for telemetry broadcast.
Receives CMD packets from HUD and dispatches them.
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("nexus.websocket")


@dataclass
class WSClient:
    websocket: WebSocket
    connected_at: float
    last_ping: float
    client_id: str
    messages_sent: int = 0
    messages_received: int = 0


class WebSocketHandler:
    def __init__(self, app):
        self.app = app
        self.ws_client_meta: Dict[str, WSClient] = {}

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()

        # Register with aggregator broadcast set
        self.app.aggregator.ws_clients.add(websocket)

        # Track metadata
        client_id = str(uuid.uuid4())[:8]
        now = time.time()
        client = WSClient(
            websocket=websocket,
            connected_at=now,
            last_ping=now,
            client_id=client_id,
        )
        self.ws_client_meta[client_id] = client

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
                    if data.get("type") == "CMD":
                        await self._dispatch_command(data, websocket)
                        client.messages_sent += 1  # ACK sent
                    elif data.get("type") == "PING":
                        client.last_ping = time.time()
                        await websocket.send_text(json.dumps({
                            "type": "PONG",
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        }))
                        client.messages_sent += 1
                    else:
                        logger.debug(f"Unknown message type: {data.get('type')}")
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
            logger.info(f"WebSocket client {client_id} disconnected ({len(self.app.aggregator.ws_clients)} total)")

    async def _send_state_sync(self, ws: WebSocket):
        """Send current state snapshot when client connects."""
        states = self.app.aggregator.drone_states
        packets = [s.to_telemetry_packet().model_dump(mode="json") for s in states.values()]

        formation = "V_FORMATION"
        if hasattr(self.app, "coordinator") and self.app.coordinator:
            formation = self.app.coordinator.current_formation.value

        sync_msg = {
            "type": "STATE_SYNC",
            "drones": packets,
            "formation": formation,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        await ws.send_text(json.dumps(sync_msg))

    async def _dispatch_command(self, data: dict, ws: WebSocket) -> None:
        command = data.get("command", "")
        params = data.get("params", {})
        drone_id = params.get("droneId")

        logger.info(f"Command received: {command} -> {drone_id or 'ALL'}")

        success = False
        message = ""

        try:
            if command == "ARM":
                success = await self.app.command_dispatcher.arm(drone_id)
            elif command == "DISARM":
                success = await self.app.command_dispatcher.disarm(drone_id)
            elif command == "TAKEOFF":
                altitude = params.get("altitude", 30.0)
                success = await self.app.command_dispatcher.takeoff(drone_id, altitude)
            elif command == "LAND":
                success = await self.app.command_dispatcher.land(drone_id)
            elif command == "RTL":
                success = await self.app.command_dispatcher.rtl(drone_id)
            elif command == "GOTO":
                success = await self.app.command_dispatcher.goto(
                    params.get("lat"), params.get("lng"),
                )
            elif command == "SET_FORMATION":
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
            elif command == "EMERGENCY_STOP":
                success = await self.app.command_dispatcher.emergency_stop()
            elif command == "EXECUTE_MISSION":
                waypoints = params.get("waypoints", [])
                success = await self.app.command_dispatcher.execute_mission(waypoints)
            # ---- FPV Commands ----
            elif command == "CAMERA_TILT":
                success = await self.app.command_dispatcher.camera_tilt(
                    params.get("angle", 0), drone_id,
                )
            elif command == "MSP_ARM":
                success = await self.app.command_dispatcher.msp_arm(drone_id)
            elif command == "MSP_DISARM":
                success = await self.app.command_dispatcher.msp_disarm(drone_id)
            elif command == "MSP_SET_MODE":
                success = await self.app.command_dispatcher.msp_set_mode(
                    params.get("mode", "ANGLE"), drone_id,
                )
            else:
                message = f"Unknown command: {command}"

        except Exception as e:
            message = str(e)
            logger.error(f"Command execution error: {e}")

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
