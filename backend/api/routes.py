"""
FastAPI REST router — all HTTP control and query endpoints for OVERWATCH.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from protocol import OverlayType, Waypoint
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
    formation: OverlayType

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
    from main import overwatch_app
    return overwatch_app


# ---- Asset endpoints ----

@router.get("/ontology/assets")
async def get_assets():
    app = _get_app()
    states = app.aggregator.drone_states
    return [s.to_telemetry_packet().model_dump(mode="json") for s in states.values()]


@router.post("/actions/assets/{asset_id}/launch-prep")
async def launch_prep_asset(asset_id: str, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    ok = await app.command_dispatcher.arm(asset_id)
    if not ok:
        raise HTTPException(400, f"Failed to arm {asset_id}")
    return {"status": "armed", "drone_id": asset_id}


@router.post("/actions/assets/{asset_id}/stand-down")
async def stand_down_asset(asset_id: str, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    ok = await app.command_dispatcher.disarm(asset_id)
    if not ok:
        raise HTTPException(400, f"Failed to disarm {asset_id}")
    return {"status": "disarmed", "drone_id": asset_id}


# ---- Taskforce endpoints ----

@router.post("/actions/taskforce/launch")
async def taskforce_launch(req: TakeoffRequest = TakeoffRequest(), _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.takeoff(None, req.altitude)
    return {"status": "taking_off", "altitude": req.altitude}


@router.post("/actions/taskforce/recover")
async def taskforce_recover(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.land(None)
    return {"status": "landing"}


@router.post("/actions/taskforce/abort")
async def taskforce_abort(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.emergency_stop()
    return {"status": "emergency_stop"}


@router.post("/overlays/formation")
async def set_formation(req: FormationRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_formation(req.formation.value)
    return {"status": "formation_set", "formation": req.formation.value}


@router.post("/actions/taskforce/set-speed")
async def set_speed(req: SpeedRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_speed(req.speed)
    return {"status": "speed_set", "speed": req.speed}


@router.post("/actions/taskforce/set-altitude")
async def set_altitude(req: AltitudeRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.set_altitude(req.altitude)
    return {"status": "altitude_set", "altitude": req.altitude}


@router.get("/ontology/taskforce/health")
async def get_taskforce_health():
    app = _get_app()
    return app.aggregator.get_swarm_health()


# ---- Operations endpoints ----

@router.post("/operations/create")
async def create_operation(req: MissionRequest, _user: UserModel = Depends(require_operator)):
    app = _get_app()
    app.current_mission = req.waypoints
    return {"status": "created", "waypoint_count": len(req.waypoints)}


@router.post("/operations/execute")
async def execute_operation(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    if not hasattr(app, 'current_mission') or not app.current_mission:
        raise HTTPException(400, "No mission created")
    await app.command_dispatcher.execute_mission(
        [w.model_dump() for w in app.current_mission],
    )
    return {"status": "executing"}


@router.post("/operations/abort")
async def abort_operation(_user: UserModel = Depends(require_operator)):
    app = _get_app()
    await app.command_dispatcher.rtl(None)
    return {"status": "aborted"}


# ---- Activity endpoints ----

@router.get("/activity/directives")
async def get_directive_logs(limit: int = 100):
    app = _get_app()
    if not app.db:
        return []
    return await app.db.get_commands(limit)


@router.get("/activity/stream")
async def get_activity_stream(limit: int = 100, severity: Optional[str] = None):
    app = _get_app()
    if not app.db:
        return []
    return await app.db.get_events(limit, severity)


@router.get("/platform/connections")
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


@router.get("/platform/status")
async def get_status():
    app = _get_app()
    return {
        "mode": "SIMULATION" if app.settings.simulation_mode else "LIVE",
        "drones": len(app.aggregator.drone_states),
        "ws_clients": len(app.aggregator.ws_clients),
    }


# ---- Debrief (replay) endpoints ----

@router.post("/debrief/start")
async def debrief_start_recording(req: RecordStartRequest):
    """Begin recording telemetry packets with a session tag."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        app.replay_engine.start_recording(req.session_name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "recording", "session_name": req.session_name}


@router.post("/debrief/stop")
async def debrief_stop_recording():
    """Stop the active recording and return session summary."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        summary = await app.replay_engine.stop_recording()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "stopped", **summary}


@router.get("/debrief/sessions")
async def debrief_list_sessions():
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


@router.post("/debrief/play")
async def debrief_play(req: ReplayPlayRequest):
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


@router.post("/debrief/pause")
async def debrief_pause():
    """Pause (stop) the active replay."""
    app = _get_app()
    if not app.replay_engine:
        raise HTTPException(503, "Replay engine not available")
    try:
        await app.replay_engine.stop_replay()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "paused"}


# ---- Platform Device endpoints ----

@router.get("/platform/devices/scan")
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


@router.post("/platform/devices/connect")
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


@router.get("/platform/devices/connected")
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


# ---- ISR Feed (video) endpoints ----

@router.get("/isr/feeds/sources")
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


@router.post("/isr/feeds/sources")
async def add_video_source(req: VideoSourceRequest, _user: UserModel = Depends(require_operator)):
    """Add a video source for an asset."""
    app = _get_app()
    if not app.video_manager:
        raise HTTPException(503, "Video manager not initialized")
    app.video_manager.add_source(req.drone_id, req.name, req.url, req.type, req.codec)
    return {"status": "added", "drone_id": req.drone_id}


@router.delete("/isr/feeds/sources/{drone_id}")
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
