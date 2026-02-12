"""
NEXUS Ground Control Station — Configuration
Loaded from environment variables with NEXUS_ prefix.
"""
from pydantic_settings import BaseSettings
from pydantic import BaseModel
from typing import List, Tuple


class DroneConfig(BaseModel):
    id: str
    role: str
    color: str


DRONE_FLEET: List[DroneConfig] = [
    DroneConfig(id="ALPHA-1",   role="LEADER",  color="#00ff88"),
    DroneConfig(id="BRAVO-2",   role="WINGMAN", color="#3388ff"),
    DroneConfig(id="CHARLIE-3", role="RECON",   color="#ffaa00"),
    DroneConfig(id="DELTA-4",   role="WINGMAN", color="#ff3355"),
    DroneConfig(id="ECHO-5",    role="SUPPORT", color="#00ccff"),
    DroneConfig(id="FOXTROT-6", role="TAIL",    color="#aa55ff"),
]


class VideoSourceConfig(BaseModel):
    name: str = "Main Camera"
    url: str = ""
    type: str = "rtsp"
    codec: str = "h264"


class FPVSettings(BaseModel):
    enabled: bool = False
    solo_mode_default: bool = False
    video_sources: List[VideoSourceConfig] = []
    video_resolution: str = "1280x720"
    video_framerate: int = 60
    video_low_latency: bool = True
    msp_enabled: bool = False
    msp_serial_device: str = ""
    msp_baud_rate: int = 115200
    msp_auto_detect: bool = True
    msp_poll_rate_hz: int = 10
    goggles_enabled: bool = False
    goggles_system: str = "auto"
    goggles_serial_device: str = ""
    goggles_baud_rate: int = 115200
    camera_tilt_min: int = -90
    camera_tilt_max: int = 30
    camera_tilt_speed: int = 30
    gimbal_enabled: bool = False
    osd_enabled: bool = True
    osd_layout: str = "default"
    dvr_auto_record_on_arm: bool = False
    dvr_timestamp_overlay: bool = True
    dvr_telemetry_sync: bool = True
    dvr_max_duration_minutes: int = 30


class KnownUSBDevice(BaseModel):
    vid: str
    pid: str
    label: str


class USBSettings(BaseModel):
    auto_scan: bool = True
    scan_interval_s: int = 5
    known_devices: List[KnownUSBDevice] = [
        KnownUSBDevice(vid="0x10c4", pid="0xea60", label="CP2102 Flight Controller"),
        KnownUSBDevice(vid="0x0483", pid="0x5740", label="STM32 Betaflight FC"),
        KnownUSBDevice(vid="0x1a86", pid="0x7523", label="CH340 Serial Adapter"),
    ]


class NexusSettings(BaseSettings):
    # Server
    ws_port: int = 8765
    http_port: int = 8080
    telemetry_rate_hz: int = 10

    # MAVLink
    mavlink_connection: str = "/dev/ttyAMA0"
    mavlink_baud: int = 921600
    sitl_base_port: int = 14540
    sitl_drone_count: int = 6

    # Safety
    safety_bubble_m: float = 5.0
    max_altitude_m: float = 120.0
    min_altitude_m: float = 5.0
    max_speed_ms: float = 20.0
    return_altitude_m: float = 50.0
    geofence_vertices: List[Tuple[float, float]] = [
        (33.6450, -117.8500),
        (33.6450, -117.8380),
        (33.6360, -117.8380),
        (33.6360, -117.8500),
    ]

    # Failover
    heartbeat_timeout_ms: int = 3000
    low_battery_warning_pct: int = 25
    low_battery_critical_pct: int = 10
    reconnect_interval_ms: int = 3000

    # Formation
    default_formation: str = "V_FORMATION"
    formation_spacing_m: float = 15.0
    cohesion_threshold: float = 0.75

    # Database
    db_path: str = "nexus.db"

    # Mode
    simulation_mode: bool = True

    # Authentication (disabled by default so existing tests pass)
    auth_enabled: bool = False

    # FPV
    fpv: FPVSettings = FPVSettings()

    # USB Auto-Detection
    usb: USBSettings = USBSettings()

    model_config = {"env_prefix": "NEXUS_"}
