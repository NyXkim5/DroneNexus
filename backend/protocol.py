"""
OVERWATCH Wire Protocol — Pydantic models matching src/shared/protocol.js exactly.

The HUD reads these exact field paths:
  p.position.lat, p.position.lon (NOT lng)
  p.attitude.roll, p.attitude.pitch, p.attitude.yaw
  p.velocity.ground_speed, p.velocity.vertical_speed, p.velocity.heading
  p.battery.remaining_pct, p.battery.voltage, p.battery.current
  p.gps.satellites, p.gps.hdop
  p.link.rssi, p.link.quality, p.link.latency_ms
  p.status
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List


# ---- Enums (must match protocol.js string values exactly) ----

class MessageType(str, Enum):
    ASSET_STATE = "ASSET_STATE"
    HEARTBEAT = "HEARTBEAT"
    DIRECTIVE = "DIRECTIVE"
    OVERLAY_UPDATE = "OVERLAY_UPDATE"
    OBJECTIVE = "OBJECTIVE"
    PEER_STATE = "PEER_STATE"
    ACTIVITY = "ACTIVITY"
    ACK = "ACK"
    ISR_CTRL = "ISR_CTRL"
    SENSOR_CTRL = "SENSOR_CTRL"
    MSP_STATE = "MSP_STATE"
    HMD_STATE = "HMD_STATE"
    RECORD_CTRL = "RECORD_CTRL"
    DEVICE_SCAN = "DEVICE_SCAN"


class AssetClassification(str, Enum):
    PRIMARY = "PRIMARY"
    ESCORT = "ESCORT"
    ISR = "ISR"
    LOGISTICS = "LOGISTICS"
    OVERWATCH = "OVERWATCH"


class OperationalStatus(str, Enum):
    NOMINAL = "NOMINAL"
    DEGRADED = "DEGRADED"
    COMMS_DEGRADED = "COMMS_DEGRADED"
    RTB = "RTB"
    GROUNDED = "GROUNDED"
    OFFLINE = "OFFLINE"
    ISR_SOLO = "ISR_SOLO"


class OverlayType(str, Enum):
    V_FORMATION = "V_FORMATION"
    LINE_ABREAST = "LINE_ABREAST"
    COLUMN = "COLUMN"
    DIAMOND = "DIAMOND"
    ORBIT = "ORBIT"
    SCATTER = "SCATTER"


class DirectiveType(str, Enum):
    LAUNCH_PREP = "LAUNCH_PREP"
    STAND_DOWN = "STAND_DOWN"
    LAUNCH = "LAUNCH"
    RECOVER = "RECOVER"
    RTB = "RTB"
    GOTO = "GOTO"
    SET_MODE = "SET_MODE"
    SET_OVERLAY = "SET_OVERLAY"
    SET_SPEED = "SET_SPEED"
    SET_ALTITUDE = "SET_ALTITUDE"
    ABORT = "ABORT"
    EXECUTE_MISSION = "EXECUTE_MISSION"
    SENSOR_TILT = "SENSOR_TILT"
    SENSOR_RECORD = "SENSOR_RECORD"
    SENSOR_CAPTURE = "SENSOR_CAPTURE"
    GIMBAL_CONTROL = "GIMBAL_CONTROL"
    MSP_LAUNCH_PREP = "MSP_LAUNCH_PREP"
    MSP_STAND_DOWN = "MSP_STAND_DOWN"
    MSP_SET_MODE = "MSP_SET_MODE"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class FlightMode(str, Enum):
    ANGLE = "ANGLE"
    HORIZON = "HORIZON"
    ACRO = "ACRO"
    AIR = "AIR"
    TURTLE = "TURTLE"
    GPS_RESCUE = "GPS_RESCUE"
    STABILIZE = "STABILIZE"
    ALT_HOLD = "ALT_HOLD"
    LOITER = "LOITER"
    AUTO = "AUTO"
    GUIDED = "GUIDED"
    RTL = "RTL"


class ProtocolType(str, Enum):
    MAVLINK = "MAVLINK"
    MSP = "MSP"
    UNKNOWN = "UNKNOWN"


class MissionState(str, Enum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    TAKING_OFF = "TAKING_OFF"
    IN_MISSION = "IN_MISSION"
    LOITERING = "LOITERING"
    RETURNING = "RETURNING"
    LANDED = "LANDED"


# ---- Telemetry sub-models ----

class Position(BaseModel):
    lat: float
    lon: float  # CRITICAL: "lon" not "lng" — HUD reads p.position.lon
    alt_msl: float
    alt_agl: float


class Attitude(BaseModel):
    roll: float
    pitch: float
    yaw: float


class Velocity(BaseModel):
    ground_speed: float
    vertical_speed: float
    heading: float


class Battery(BaseModel):
    voltage: float
    current: float
    remaining_pct: float


class GPS(BaseModel):
    fix_type: str = "3D-RTK"
    satellites: int
    hdop: float


class Link(BaseModel):
    rssi: int
    quality: int
    latency_ms: int


class OffsetVector(BaseModel):
    dx: float
    dy: float


class Formation(BaseModel):
    role: AssetClassification
    offset_vector: OffsetVector
    cohesion: float

    model_config = {"use_enum_values": True}


# ---- FPV sub-models ----

class VideoLinkData(BaseModel):
    quality: int = 0
    channel: int = 0
    frequency_mhz: int = 0
    recording: bool = False
    system: str = ""


class FPVData(BaseModel):
    flight_mode: FlightMode = FlightMode.ANGLE
    camera_tilt: float = 0.0
    mah_consumed: float = 0.0
    cell_voltage: float = 0.0
    flight_timer_s: float = 0.0
    arm_timer_s: float = 0.0
    home_distance_m: float = 0.0
    home_direction_deg: float = 0.0
    video_link: Optional[VideoLinkData] = None
    protocol: ProtocolType = ProtocolType.MAVLINK

    model_config = {"use_enum_values": True}


# ---- Top-level messages ----

class AssetStatePacket(BaseModel):
    type: MessageType = MessageType.ASSET_STATE
    drone_id: str
    timestamp: str
    seq: int
    position: Position
    attitude: Attitude
    velocity: Velocity
    battery: Battery
    gps: GPS
    link: Link
    status: OperationalStatus
    formation: Formation
    fpv: Optional[FPVData] = None

    model_config = {"use_enum_values": True}


class DirectivePacket(BaseModel):
    type: MessageType = MessageType.DIRECTIVE
    command: DirectiveType
    params: dict = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class AckPacket(BaseModel):
    type: MessageType = MessageType.ACK
    command: str
    drone_id: str
    success: bool
    message: str = ""

    model_config = {"use_enum_values": True}


class Waypoint(BaseModel):
    lat: float
    lng: float  # HUD sends "lng" in waypoints
    alt: float = 30.0
    type: str = "WAYPOINT"
    radius: Optional[float] = None
    speed: Optional[float] = None
    direction: Optional[str] = None
