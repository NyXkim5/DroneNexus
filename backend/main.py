"""
NEXUS Ground Control Station Backend
FastAPI application with WebSocket telemetry streaming and REST control API.

Usage:
    python3 main.py                              # Simulation mode (default)
    NEXUS_SIMULATION_MODE=false python3 main.py  # Live MAVSDK mode
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional, List, Dict

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config import NexusSettings, DRONE_FLEET
from protocol import FormationType, Waypoint, DroneRole
from telemetry.collector import DroneState
from telemetry.aggregator import SwarmAggregator
from api.websocket import WebSocketHandler
from api.routes import router as api_router
from api.auth import auth_router
from api.export import export_router
from db.models import NexusDB
from telemetry.replay import ReplayEngine
from missions.state_machine import MissionStateMachine
from swarm.coordinator import SwarmCoordinator
from simulation.mock_drone import MockSwarm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("nexus")


class CommandDispatcher:
    """Routes commands to mock drones (simulation) or MAVSDK (live)."""

    def __init__(self, app: "NexusApp"):
        self.app = app

    def _get_targets(self, drone_id: Optional[str]) -> List[str]:
        if drone_id:
            return [drone_id]
        return list(self.app.aggregator.drone_states.keys())

    async def _log(self, command: str, params: dict = None, target: str = "ALL"):
        if self.app.db:
            await self.app.db.log_command(command, params or {})
            await self.app.db.log_event(
                target if target != "ALL" else None,
                "INFO", f"Command: {command}", params,
            )

    async def arm(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            if self.app.mock_swarm:
                mock = self.app.mock_swarm.drones.get(did)
                if mock:
                    mock.drone_sim_state = "ARMED"
                    mock.state.armed = True
                    mock.state.alt_agl = 0
                    mock.state.alt_msl = 15
            sm = self.app.state_machines.get(did)
            if sm:
                sm.transition("ARMED")
        await self._log("ARM", {"droneId": drone_id}, drone_id or "ALL")
        logger.info(f"ARM -> {drone_id or 'ALL'}")
        return True

    async def disarm(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            if self.app.mock_swarm:
                mock = self.app.mock_swarm.drones.get(did)
                if mock:
                    mock.drone_sim_state = "IDLE"
                    mock.state.armed = False
            sm = self.app.state_machines.get(did)
            if sm:
                sm.force_idle()
        await self._log("DISARM", {"droneId": drone_id}, drone_id or "ALL")
        logger.info(f"DISARM -> {drone_id or 'ALL'}")
        return True

    async def takeoff(self, drone_id: Optional[str] = None, altitude: float = 30.0) -> bool:
        for did in self._get_targets(drone_id):
            if self.app.mock_swarm:
                mock = self.app.mock_swarm.drones.get(did)
                if mock:
                    mock.target_alt = altitude
                    mock.drone_sim_state = "TAKING_OFF"
                    mock.state.armed = True
                    if mock.state.alt_agl < 0.5:
                        mock.state.alt_agl = 0.5
            sm = self.app.state_machines.get(did)
            if sm:
                sm.transition("ARMED")
                sm.transition("TAKING_OFF")
        await self._log("TAKEOFF", {"altitude": altitude, "droneId": drone_id}, drone_id or "ALL")
        logger.info(f"TAKEOFF {altitude}m -> {drone_id or 'ALL'}")
        return True

    async def land(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            if self.app.mock_swarm:
                mock = self.app.mock_swarm.drones.get(did)
                if mock:
                    mock.drone_sim_state = "LANDING"
        await self._log("LAND", {"droneId": drone_id}, drone_id or "ALL")
        logger.info(f"LAND -> {drone_id or 'ALL'}")
        return True

    async def rtl(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            if self.app.mock_swarm:
                mock = self.app.mock_swarm.drones.get(did)
                if mock:
                    mock.drone_sim_state = "LANDING"
        await self._log("RTL", {"droneId": drone_id}, drone_id or "ALL")
        logger.info(f"RTL -> {drone_id or 'ALL'}")
        return True

    async def goto(self, lat: float, lon: float, alt: float = 30.0) -> bool:
        if self.app.mock_swarm:
            leader = self.app.mock_swarm.drones.get("ALPHA-1")
            if leader:
                leader.set_goto(lat, lon, alt)
                logger.info(f"GOTO leader -> ({lat:.5f}, {lon:.5f}, {alt}m)")
        await self._log("GOTO", {"lat": lat, "lng": lon, "alt": alt})
        logger.info(f"GOTO {lat:.5f}, {lon:.5f}")
        return True

    async def set_formation(self, formation: str) -> bool:
        if self.app.coordinator:
            self.app.coordinator.set_formation(formation)
        await self._log("SET_FORMATION", {"formation": formation})
        logger.info(f"SET_FORMATION -> {formation}")
        return True

    async def set_speed(self, speed: float, drone_id: Optional[str] = None) -> bool:
        if self.app.coordinator:
            self.app.coordinator.set_speed(speed)
        await self._log("SET_SPEED", {"speed": speed, "droneId": drone_id})
        logger.info(f"SET_SPEED {speed} m/s -> {drone_id or 'ALL'}")
        return True

    async def set_altitude(self, altitude: float, drone_id: Optional[str] = None) -> bool:
        if self.app.coordinator:
            self.app.coordinator.set_altitude(altitude)
        await self._log("SET_ALTITUDE", {"altitude": altitude, "droneId": drone_id})
        logger.info(f"SET_ALTITUDE {altitude}m -> {drone_id or 'ALL'}")
        return True

    async def emergency_stop(self) -> bool:
        if self.app.mock_swarm:
            for mock in self.app.mock_swarm.drones.values():
                mock.drone_sim_state = "EMERGENCY"
        for sm in self.app.state_machines.values():
            sm.force_idle()
        await self._log("EMERGENCY_STOP", {})
        logger.warning("EMERGENCY STOP — all drones")
        return True

    async def camera_tilt(self, angle: float, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            state = self.app.aggregator.drone_states.get(did)
            if state:
                state.camera_tilt = angle
        await self._log("CAMERA_TILT", {"angle": angle, "droneId": drone_id})
        logger.info(f"CAMERA_TILT {angle}deg -> {drone_id or 'ALL'}")
        return True

    async def msp_arm(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            conn = self.app.msp_connections.get(did)
            if conn:
                from msp.commands import MSPCommander
                await MSPCommander.arm(conn)
        await self._log("MSP_ARM", {"droneId": drone_id})
        return await self.arm(drone_id)

    async def msp_disarm(self, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            conn = self.app.msp_connections.get(did)
            if conn:
                from msp.commands import MSPCommander
                await MSPCommander.disarm(conn)
        await self._log("MSP_DISARM", {"droneId": drone_id})
        return await self.disarm(drone_id)

    async def msp_set_mode(self, mode: str, drone_id: Optional[str] = None) -> bool:
        for did in self._get_targets(drone_id):
            conn = self.app.msp_connections.get(did)
            if conn:
                from msp.commands import MSPCommander
                await MSPCommander.set_flight_mode(conn, mode)
            state = self.app.aggregator.drone_states.get(did)
            if state:
                state.flight_mode = mode
        await self._log("MSP_SET_MODE", {"mode": mode, "droneId": drone_id})
        logger.info(f"MSP_SET_MODE {mode} -> {drone_id or 'ALL'}")
        return True

    async def execute_mission(self, waypoints_data: List[dict]) -> bool:
        if self.app.mock_swarm:
            # Leader flies the full waypoint sequence
            leader = self.app.mock_swarm.drones.get("ALPHA-1")
            if leader:
                leader.set_mission(waypoints_data)
                leader.start_mission()
                logger.info(
                    f"Mission assigned to ALPHA-1: {len(waypoints_data)} waypoints"
                )

            # Followers also get the mission so they fly the same path
            # (formation offsets are applied during FLYING; during MISSION
            #  each drone navigates independently through the waypoints)
            for drone_id, mock in self.app.mock_swarm.drones.items():
                if drone_id == "ALPHA-1":
                    continue
                mock.set_mission(waypoints_data)
                mock.start_mission()

        await self._log("EXECUTE_MISSION", {"waypoint_count": len(waypoints_data)})
        logger.info(f"EXECUTE_MISSION with {len(waypoints_data)} waypoints")
        return True


class NexusApp:
    """Central application object holding all subsystems."""

    def __init__(self):
        self.settings = NexusSettings()
        self.db = NexusDB(self.settings.db_path)
        self.aggregator = SwarmAggregator(self.settings)
        self.replay_engine = ReplayEngine(self.db, self.aggregator)
        self.mock_swarm: Optional[MockSwarm] = None
        self.coordinator: Optional[SwarmCoordinator] = None
        self.command_dispatcher = CommandDispatcher(self)
        self.ws_handler = WebSocketHandler(self)
        self.state_machines: Dict[str, MissionStateMachine] = {}
        self.current_mission: List[Waypoint] = []

        # FPV subsystems
        self.usb_auto_connector = None
        self.video_manager = None
        self.goggles_bridge = None
        self.msp_connections: Dict[str, object] = {}

    async def startup(self) -> None:
        logger.info("=" * 60)
        logger.info("NEXUS Ground Control Station — Starting")
        logger.info("=" * 60)

        # Database
        await self.db.connect()
        await self.db.ensure_session_column()

        if self.settings.simulation_mode:
            # Mock drone mode
            self.mock_swarm = MockSwarm(self.settings)
            for drone_id, state in self.mock_swarm.get_states().items():
                self.aggregator.register_drone(state)
                self.state_machines[drone_id] = MissionStateMachine(drone_id)
            await self.mock_swarm.start()
            logger.info(f"SIMULATION mode — {len(self.mock_swarm.drones)} mock drones active")
        else:
            logger.info("LIVE mode — MAVSDK connections")

        # Swarm coordinator
        self.coordinator = SwarmCoordinator(
            self.settings, self.aggregator.drone_states,
        )
        await self.coordinator.start()

        # Telemetry aggregator (10Hz WebSocket broadcast)
        await self.aggregator.start()

        # FPV subsystems (when enabled)
        if self.settings.fpv.enabled:
            try:
                from video.stream_proxy import VideoStreamManager
                self.video_manager = VideoStreamManager()
                for src in self.settings.fpv.video_sources:
                    self.video_manager.add_source(
                        'default', src.name, src.url, src.type, src.codec
                    )
                logger.info("FPV video manager initialized")
            except Exception as e:
                logger.warning(f"FPV video init skipped: {e}")

            if self.settings.fpv.goggles_enabled:
                try:
                    from goggles.bridge import GogglesBridgeFactory
                    self.goggles_bridge = await GogglesBridgeFactory.create(
                        self.settings.fpv.goggles_serial_device,
                        self.settings.fpv.goggles_system,
                        self.settings.fpv.goggles_baud_rate,
                    )
                    if self.goggles_bridge:
                        logger.info(f"Goggles bridge: {self.goggles_bridge.latest_data.system}")
                except Exception as e:
                    logger.warning(f"Goggles bridge init skipped: {e}")

        # USB auto-detection (when enabled)
        if self.settings.usb.auto_scan:
            try:
                from usb.auto_connect import AutoConnector
                self.usb_auto_connector = AutoConnector()
                await self.usb_auto_connector.start(
                    interval=float(self.settings.usb.scan_interval_s)
                )
                logger.info("USB auto-detection started")
            except Exception as e:
                logger.warning(f"USB auto-detect init skipped: {e}")

        logger.info(f"WebSocket: ws://0.0.0.0:{self.settings.ws_port}/telemetry/stream")
        logger.info(f"REST API:  http://0.0.0.0:{self.settings.ws_port}/api")
        logger.info(f"API Docs:  http://0.0.0.0:{self.settings.ws_port}/docs")
        logger.info("=" * 60)

    async def shutdown(self) -> None:
        # Stop replay engine if active
        if self.replay_engine.is_recording:
            await self.replay_engine.stop_recording()
        if self.replay_engine.is_replaying:
            await self.replay_engine.stop_replay()

        await self.aggregator.stop()
        if self.coordinator:
            await self.coordinator.stop()
        if self.mock_swarm:
            await self.mock_swarm.stop()
        if self.usb_auto_connector:
            await self.usb_auto_connector.stop()
        if self.goggles_bridge:
            await self.goggles_bridge.stop()
        await self.db.close()
        logger.info("NEXUS shutdown complete")


# ---- Singleton ----
nexus_app = NexusApp()


# ---- FastAPI lifespan ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    await nexus_app.startup()
    yield
    await nexus_app.shutdown()


# ---- FastAPI application ----
app = FastAPI(
    title="NEXUS Ground Control Station",
    version="1.0.0",
    description="Drone swarm telemetry and command backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.middleware import RateLimiter
app.add_middleware(RateLimiter, rate_limit=120, window_seconds=60)

# Mount REST routes
app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(export_router, prefix="/api/export")


# ---- WebSocket endpoints ----
@app.websocket("/telemetry/stream")
async def telemetry_websocket(websocket: WebSocket):
    await nexus_app.ws_handler.handle(websocket)


@app.websocket("/ws")
async def ws_compat(websocket: WebSocket):
    await nexus_app.ws_handler.handle(websocket)


# ---- Entry point ----
if __name__ == "__main__":
    settings = NexusSettings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.ws_port,
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
