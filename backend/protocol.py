"""
NEXUS Wire Protocol — Pydantic models matching src/shared/protocol.js exactly.

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
    TELEM = "TELEM"
    HEARTBEAT = "HEARTBEAT"
    CMD = "CMD"
    FORMATION = "FORMATION"
    WAYPOINT = "WAYPOINT"
    PEER = "PEER"
    ALERT = "ALERT"
    ACK = "ACK"
    VIDEO_CTRL = "VIDEO_CTRL"
    CAMERA_CTRL = "CAMERA_CTRL"
    MSP_TELEM = "MSP_TELEM"
    GOGGLES = "GOGGLES"
    DVR_CTRL = "DVR_CTRL"
    DEVICE_SCAN = "DEVICE_SCAN"


class DroneRole(str, Enum):
    LEADER = "LEADER"
    WINGMAN = "WINGMAN"
    RECON = "RECON"
    SUPPORT = "SUPPORT"
    TAIL = "TAIL"


class DroneStatus(str, Enum):
    ACTIVE = "ACTIVE"
    LOW_BATT = "LOW_BATT"
    WEAK_SIGNAL = "WEAK_SIGNAL"
    RTL = "RTL"
    LANDED = "LANDED"
    LOST = "LOST"
    FPV_SOLO = "FPV_SOLO"


class FormationType(str, Enum):
    V_FORMATION = "V_FORMATION"
    LINE_ABREAST = "LINE_ABREAST"
    COLUMN = "COLUMN"
    DIAMOND = "DIAMOND"
    ORBIT = "ORBIT"
    SCATTER = "SCATTER"


class CommandType(str, Enum):
    ARM = "ARM"
    DISARM = "DISARM"
    TAKEOFF = "TAKEOFF"
    LAND = "LAND"
    RTL = "RTL"
    GOTO = "GOTO"
    SET_MODE = "SET_MODE"
    SET_FORMATION = "SET_FORMATION"
    SET_SPEED = "SET_SPEED"
    SET_ALTITUDE = "SET_ALTITUDE"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    EXECUTE_MISSION = "EXECUTE_MISSION"
    CAMERA_TILT = "CAMERA_TILT"
    CAMERA_RECORD = "CAMERA_RECORD"
    CAMERA_PHOTO = "CAMERA_PHOTO"
    GIMBAL_CONTROL = "GIMBAL_CONTROL"
    MSP_ARM = "MSP_ARM"
    MSP_DISARM = "MSP_DISARM"
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
    role: DroneRole
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

class TelemetryPacket(BaseModel):
    type: MessageType = MessageType.TELEM
    drone_id: str
    timestamp: str
    seq: int
    position: Position
    attitude: Attitude
    velocity: Velocity
    battery: Battery
    gps: GPS
    link: Link
    status: DroneStatus
    formation: Formation
    fpv: Optional[FPVData] = None

    model_config = {"use_enum_values": True}


class CommandPacket(BaseModel):
    type: MessageType = MessageType.CMD
    command: CommandType
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
