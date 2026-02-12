"""
FastAPI REST router — all HTTP control and query endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from protocol import FormationType, Waypoint
from api.auth import require_operator, UserModel

router = APIRouter()


# ---- Request Models ----

class TakeoffRequest(BaseModel):
    altitude: float = Field(default=30.0, ge=5.0, le=120.0)

class GotoRequest(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    alt: float = Field(default=30.0, ge=5.0, le=120.0)

class FormationRequest(BaseModel):
    formation: FormationType

class SpeedRequest(BaseModel):
    speed: float = Field(ge=0.0, le=20.0)

class AltitudeRequest(BaseModel):
    altitude: float = Field(ge=5.0, le=120.0)

class MissionRequest(BaseModel):
    waypoints: List[Waypoint] = Field(min_length=1, max_length=100)

class RecordStartRequest(BaseModel):
    session_name: str

class ReplayPlayRequest(BaseModel):
    session_name: str
    speed: float = 1.0


def _get_app():
    from main import nexus_app
    return nexus_app


# ---- Drone endpoints ----

@router.get("/drones")
async def get_drones():
    app = _get_app()
    states = app.aggregator.drone_states
    return [s.to_telemetry_packet().model_dump(mode="json") for s in states.values()]


@router.post("/drones/{drone_id}/arm")
async def arm_drone(drone_id: str, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    ok = await app.command_dispatcher.arm(drone_id)
    if not ok:
        raise HTTPException(400, f"Failed to arm {drone_id}")
    return {"status": "armed", "drone_id": drone_id}


@router.post("/drones/{drone_id}/disarm")
async def disarm_drone(drone_id: str, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    ok = await app.command_dispatcher.disarm(drone_id)
    if not ok:
        raise HTTPException(400, f"Failed to disarm {drone_id}")
    return {"status": "disarmed", "drone_id": drone_id}


# ---- Swarm endpoints ----

@router.post("/swarm/takeoff")
async def swarm_takeoff(req: TakeoffRequest = TakeoffRequest(), _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.takeoff(None, req.altitude)
    return {"status": "taking_off", "altitude": req.altitude}


@router.post("/swarm/land")
async def swarm_land(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.land(None)
    return {"status": "landing"}


@router.post("/swarm/emergency-stop")
async def swarm_emergency_stop(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.emergency_stop()
    return {"status": "emergency_stop"}


@router.post("/swarm/formation")
async def set_formation(req: FormationRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_formation(req.formation.value)
    return {"status": "formation_set", "formation": req.formation.value}


@router.post("/swarm/speed")
async def set_speed(req: SpeedRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_speed(req.speed)
    return {"status": "speed_set", "speed": req.speed}


@router.post("/swarm/altitude")
async def set_altitude(req: AltitudeRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_altitude(req.altitude)
    return {"status": "altitude_set", "altitude": req.altitude}


@router.get("/swarm/health")
async def get_swarm_health():
    app = _get_app()
    return app.aggregator.get_swarm_health()


# ---- Mission endpoints ----

@router.post("/mission/create")
async def create_mission(req: MissionRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    app.current_mission = req.waypoints
    return {"status": "created", "waypoint_count": len(req.waypoints)}


@router.post("/mission/execute")
async def execute_mission(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    if not hasattr(app, 'current_mission') or not app.current_mission:
        raise HTTPException(400, "No mission created")
    await app.command_dispatcher.execute_mission(
        [w.model_dump() for w in app.current_mission],
    )
    return {"status": "executing"}


@router.post("/mission/abort")
async def abort_mission(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.rtl(None)
    return {"status": "aborted"}


# ---- Log endpoints ----

@router.get("/logs/commands")
async def get_command_logs(limit: int = 100):
    app = _get_app()
    if not app.db:
        return []
    return await app.db.get_commands(limit)


@router.get("/logs/events")
async def get_event_logs(limit: int = 100, severity: Optional[str] = None):
    app = _get_app()
    if not app.db:
        return []
    return await app.db.get_events(limit, severity)


@router.get("/connections")
async def get_connections():
    app = _get_app()
    ws_handler = app.ws_handler
    return {
        "active": len(app.aggregator.ws_clients),
        "clients": [
            {
                "client_id": c.client_id,
                "connected_at": c.connected_at,
                "messages_sent": c.messages_sent,
                "messages_received": c.messages_received,
            }
            for c in ws_handler.ws_client_meta.values()
        ],
    }


@router.get("/status")
async def get_status():
    app = _get_app()
    return {
        "mode": "SIMULATION" if app.settings.simulation_mode else "LIVE",
        "drones": len(app.aggregator.drone_states),
        "ws_clients": len(app.aggregator.ws_clients),
    }


# ---- Replay endpoints ----

@router.post("/replay/start")
async def replay_start_recording(req: RecordStartRequest):
    """Begin recording telemetry packets with a session tag."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        app.replay_engine.start_recording(req.session_name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "recording", "session_name": req.session_name}


@router.post("/replay/stop")
async def replay_stop_recording():
    """Stop the active recording and return session summary."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        summary = await app.replay_engine.stop_recording()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "stopped", **summary}


@router.get("/replay/sessions")
async def replay_list_sessions():
    """List all recorded telemetry sessions."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    sessions = await app.replay_engine.list_sessions()
    return {
        "sessions": sessions,
        "is_recording": app.replay_engine.is_recording,
        "is_replaying": app.replay_engine.is_replaying,
    }


@router.post("/replay/play")
async def replay_play(req: ReplayPlayRequest):
    """Start replaying a recorded session at the given speed factor."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        await app.replay_engine.start_replay(req.session_name, req.speed)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "status": "replaying",
        "session_name": req.session_name,
        "speed": req.speed,
    }


@router.post("/replay/pause")
async def replay_pause():
    """Pause (stop) the active replay."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        await app.replay_engine.stop_replay()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "paused"}


# ---- FPV Device endpoints ----

@router.get("/devices/scan")
async def scan_devices():
    """Scan USB ports for flight controllers."""
    app = _get_app()
    if not app.usb_auto_connector:
        try:
            from usb.scanner import USBScanner
            scanner = USBScanner()
            devices = await scanner.scan()
            return {
                "devices": [
                    {
                        "port": d.port,
                        "label": d.label,
                        "vid": d.vid,
                        "pid": d.pid,
                        "protocol": d.protocol,
                    }
                    for d in devices
                ],
            }
        except Exception as e:
            raise HTTPException(503, f"USB scanning unavailable: {e}")
    scanner = app.usb_auto_connector.scanner
    devices = await scanner.scan()
    return {
        "devices": [
            {
                "port": d.port,
                "label": d.label,
                "vid": d.vid,
                "pid": d.pid,
                "protocol": d.protocol,
            }
            for d in devices
        ],
    }


class DeviceConnectRequest(BaseModel):
    port: str
    protocol: str = "auto"
    baud_rate: int = 115200


@router.post("/devices/connect")
async def connect_device(req: DeviceConnectRequest, _user: UserModel = Depends(require_operator)):
    """Connect to a flight controller on the given serial port."""
    app = _get_app()
    if req.protocol == "MSP" or req.protocol == "auto":
        try:
            from msp.connection import MSPConnection
            conn = await MSPConnection.connect(req.port, req.baud_rate)
            drone_id = f"FPV-{req.port.split('/')[-1].upper()}"
            app.msp_connections[drone_id] = conn
            return {"status": "connected", "drone_id": drone_id, "protocol": "MSP"}
        except Exception as e:
            if req.protocol == "MSP":
                raise HTTPException(400, f"MSP connection failed: {e}")
    return {"status": "unsupported", "message": "Only MSP protocol auto-connect supported"}


@router.get("/devices/connected")
async def get_connected_devices():
    """List currently connected FPV devices."""
    app = _get_app()
    devices = []
    for drone_id, conn in app.msp_connections.items():
        devices.append({
            "drone_id": drone_id,
            "protocol": "MSP",
            "connected": getattr(conn, '_connected', True),
        })
    return {"devices": devices}


# ---- FPV Video endpoints ----

@router.get("/video/sources")
async def get_video_sources():
    """List configured video sources."""
    app = _get_app()
    if not app.video_manager:
        return {"sources": []}
    return {
        "sources": [
            {
                "drone_id": drone_id,
                "name": src.name,
                "url": src.url,
                "type": src.type,
                "active": src.active,
            }
            for drone_id, src in app.video_manager.sources.items()
        ],
    }


class VideoSourceRequest(BaseModel):
    drone_id: str
    name: str
    url: str
    type: str = "rtsp"
    codec: str = "h264"


@router.post("/video/sources")
async def add_video_source(req: VideoSourceRequest, _user: UserModel = Depends(require_operator)):
    """Add a video source for a drone."""
    app = _get_app()
    if not app.video_manager:
        raise HTTPException(503, "Video manager not initialized")
    app.video_manager.add_source(req.drone_id, req.name, req.url, req.type, req.codec)
    return {"status": "added", "drone_id": req.drone_id}


@router.delete("/video/sources/{drone_id}")
async def remove_video_source(drone_id: str, _user: UserModel = Depends(require_operator)):
    """Remove a video source."""
    app = _get_app()
    if not app.video_manager:
        raise HTTPException(503, "Video manager not initialized")
    if drone_id in app.video_manager.sources:
        src = app.video_manager.sources.pop(drone_id)
        src.active = False
        return {"status": "removed", "drone_id": drone_id}
    raise HTTPException(404, f"No video source for {drone_id}")
