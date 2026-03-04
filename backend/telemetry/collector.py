"""
DroneState dataclass — shared mutable state per drone.
Written by telemetry collectors (MAVSDK streams) or mock drones.
Read by the aggregator at 10Hz to build wire-format packets.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from protocol import (
    AssetStatePacket, Position, Attitude, Velocity,
    Battery, GPS, Link, Formation, OffsetVector,
    OperationalStatus, AssetClassification, FPVData, VideoLinkData,
    FlightMode, ProtocolType,
)


@dataclass
class DroneState:
    """Mutable state updated by telemetry streams, read by aggregator."""
    drone_id: str
    role: AssetClassification = AssetClassification.ESCORT
    seq: int = 0

    # Position
    lat: float = 0.0
    lon: float = 0.0
    alt_msl: float = 0.0
    alt_agl: float = 0.0

    # Attitude
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    # Velocity
    ground_speed: float = 0.0
    vertical_speed: float = 0.0
    heading: float = 0.0

    # Battery
    voltage: float = 0.0
    current: float = 0.0
    remaining_pct: float = 100.0

    # GPS
    fix_type: str = "NO_FIX"
    satellites: int = 0
    hdop: float = 99.9

    # Link
    rssi: int = 0
    quality: int = 0
    latency_ms: int = 0

    # Formation
    offset_dx: float = 0.0
    offset_dy: float = 0.0
    cohesion: float = 0.0

    # Derived status
    status: OperationalStatus = OperationalStatus.GROUNDED
    armed: bool = False
    in_air: bool = False
    last_update: float = 0.0

    # FPV extensions
    flight_mode: str = "ANGLE"
    camera_tilt: float = 0.0
    mah_consumed: float = 0.0
    cell_voltage: float = 0.0
    flight_timer_s: float = 0.0
    arm_timer_s: float = 0.0
    home_distance_m: float = 0.0
    home_direction_deg: float = 0.0
    video_link_quality: int = 0
    video_link_channel: int = 0
    video_link_frequency_mhz: int = 0
    video_link_recording: bool = False
    video_link_system: str = ""
    protocol_type: str = "MAVLINK"

    # Simulation state
    drone_state_label: str = "FLYING"

    def to_telemetry_packet(self) -> AssetStatePacket:
        self.seq += 1
        return AssetStatePacket(
            type="ASSET_STATE",
            drone_id=self.drone_id,
            timestamp=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            seq=self.seq,
            position=Position(
                lat=self.lat, lon=self.lon,
                alt_msl=self.alt_msl, alt_agl=self.alt_agl,
            ),
            attitude=Attitude(
                roll=self.roll, pitch=self.pitch, yaw=self.yaw,
            ),
            velocity=Velocity(
                ground_speed=self.ground_speed,
                vertical_speed=self.vertical_speed,
                heading=self.heading,
            ),
            battery=Battery(
                voltage=self.voltage,
                current=self.current,
                remaining_pct=self.remaining_pct,
            ),
            gps=GPS(
                fix_type=self.fix_type,
                satellites=self.satellites,
                hdop=self.hdop,
            ),
            link=Link(
                rssi=self.rssi,
                quality=self.quality,
                latency_ms=self.latency_ms,
            ),
            status=self.status,
            formation=Formation(
                role=self.role,
                offset_vector=OffsetVector(dx=self.offset_dx, dy=self.offset_dy),
                cohesion=self.cohesion,
            ),
            fpv=FPVData(
                flight_mode=self.flight_mode,
                camera_tilt=self.camera_tilt,
                mah_consumed=self.mah_consumed,
                cell_voltage=self.cell_voltage,
                flight_timer_s=self.flight_timer_s,
                arm_timer_s=self.arm_timer_s,
                home_distance_m=self.home_distance_m,
                home_direction_deg=self.home_direction_deg,
                video_link=VideoLinkData(
                    quality=self.video_link_quality,
                    channel=self.video_link_channel,
                    frequency_mhz=self.video_link_frequency_mhz,
                    recording=self.video_link_recording,
                    system=self.video_link_system,
                ) if self.video_link_system else None,
                protocol=self.protocol_type,
            ),
        )
