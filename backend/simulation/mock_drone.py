"""
Mock drone swarm — generates realistic telemetry without MAVSDK/PX4.
Replicates the behavior of src/simulation/drone-simulator.js in Python.
Used when OverwatchSettings.simulation_mode = True.
"""
import asyncio
import math
import time
import logging
from typing import Dict, Optional, List

from telemetry.collector import DroneState
from protocol import AssetClassification, OperationalStatus, OffsetVector
from config import ASSET_ROSTER, OverwatchSettings

logger = logging.getLogger("overwatch.simulation")

CENTER_LAT = 33.6405
CENTER_LON = -117.8443

# V-Formation offsets matching protocol.js
V_OFFSETS: Dict[str, OffsetVector] = {
    "ALPHA-1":   OffsetVector(dx=0,   dy=0),
    "BRAVO-2":   OffsetVector(dx=-12, dy=-10),
    "CHARLIE-3": OffsetVector(dx=12,  dy=-10),
    "DELTA-4":   OffsetVector(dx=-24, dy=-20),
    "ECHO-5":    OffsetVector(dx=24,  dy=-20),
    "FOXTROT-6": OffsetVector(dx=0,   dy=-30),
}


def _offset_to_latlon(
    leader_lat: float, leader_lon: float, leader_heading: float,
    offset: OffsetVector,
) -> tuple:
    heading_rad = math.radians(leader_heading)
    meters_to_deg = 1.0 / 111320.0
    rotated_dx = offset.dx * math.cos(heading_rad) - offset.dy * math.sin(heading_rad)
    rotated_dy = offset.dx * math.sin(heading_rad) + offset.dy * math.cos(heading_rad)
    target_lat = leader_lat + rotated_dy * meters_to_deg
    target_lon = leader_lon + rotated_dx * meters_to_deg / math.cos(math.radians(leader_lat))
    return target_lat, target_lon


class MockDrone:
    """Simulates a single drone with orbiting behavior."""

    def __init__(self, drone_id: str, role: str, index: int):
        self.drone_id = drone_id
        self.role = role
        self.index = index
        self.state = DroneState(drone_id=drone_id, role=AssetClassification(role))

        # Orbit parameters
        self.orbit_angle = (index / 6) * math.pi * 2
        self.orbit_radius = 0.001  # ~111m in degrees
        self.orbit_speed = 0.005
        self.phase_offset = index * 0.5

        # Initialize state with realistic values
        s = self.state
        s.lat = CENTER_LAT
        s.lon = CENTER_LON
        s.alt_msl = 135.0
        s.alt_agl = 120.0
        s.remaining_pct = 75 + (hash(drone_id) % 25)
        s.voltage = 22.2 + (hash(drone_id) % 200) / 100
        s.current = 10.0
        s.satellites = 14 + hash(drone_id) % 4
        s.hdop = 0.7 + (hash(drone_id) % 30) / 100
        s.rssi = 85 + hash(drone_id) % 10
        s.quality = 92 + hash(drone_id) % 8
        s.latency_ms = 20 + hash(drone_id) % 10
        s.heading = 0.0
        s.ground_speed = 11.0
        s.armed = True
        s.in_air = True
        s.status = OperationalStatus.NOMINAL
        s.fix_type = "3D-RTK"
        s.offset_dx = V_OFFSETS.get(drone_id, OffsetVector(dx=0, dy=0)).dx
        s.offset_dy = V_OFFSETS.get(drone_id, OffsetVector(dx=0, dy=0)).dy

        # FPV extensions
        s.flight_mode = "ANGLE"
        s.camera_tilt = 0.0
        s.mah_consumed = 0.0
        s.cell_voltage = s.voltage / 6.0
        s.flight_timer_s = 0.0
        s.arm_timer_s = 0.0
        s.home_distance_m = 0.0
        s.home_direction_deg = 0.0
        s.video_link_quality = 90 + hash(drone_id) % 10
        s.video_link_channel = 1 + index
        s.video_link_frequency_mhz = 5740 + index * 20
        s.video_link_recording = False
        s.video_link_system = "Simulation"
        s.protocol_type = "MAVLINK"

        # Command targets
        self.target_alt: float = 120.0
        self.drone_sim_state: str = "FLYING"

        # Mission waypoints
        self.mission_waypoints: List[dict] = []
        self.mission_index: int = 0
        self.mission_speed: float = 10.0  # m/s

        # Single-point goto
        self.goto_target: Optional[tuple] = None  # (lat, lon, alt)

    def set_mission(self, waypoints: List[dict]) -> None:
        """Store a sequence of waypoints for mission flight."""
        self.mission_waypoints = waypoints
        self.mission_index = 0

    def start_mission(self) -> None:
        """Begin flying the stored mission waypoints."""
        self.drone_sim_state = "MISSION"

    def set_goto(self, lat: float, lon: float, alt: float = 30.0) -> None:
        """Fly to a single target point, then resume orbit."""
        self.goto_target = (lat, lon, alt)
        self.drone_sim_state = "GOTO"

    def update(self, dt: float, leader: Optional["MockDrone"] = None) -> None:
        t = time.time()
        s = self.state

        if self.drone_sim_state in ("IDLE", "LANDED"):
            s.ground_speed = 0
            s.vertical_speed = 0
            s.in_air = False
            s.armed = False
            s.status = OperationalStatus.GROUNDED
            s.last_update = time.monotonic()
            return

        if self.drone_sim_state == "ARMED":
            s.ground_speed = 0
            s.vertical_speed = 0
            s.in_air = False
            s.armed = True
            s.last_update = time.monotonic()
            return

        if self.drone_sim_state == "TAKING_OFF":
            s.vertical_speed = 2.0
            s.alt_agl += 2.0 * dt
            s.alt_msl = s.alt_agl + 15
            s.in_air = True
            s.armed = True
            if s.alt_agl >= self.target_alt:
                s.alt_agl = self.target_alt
                self.drone_sim_state = "FLYING"
            s.last_update = time.monotonic()
            return

        if self.drone_sim_state == "LANDING":
            s.vertical_speed = -1.5
            s.alt_agl = max(0, s.alt_agl - 1.5 * dt)
            s.alt_msl = s.alt_agl + 15
            s.ground_speed = max(0, s.ground_speed - 2 * dt)
            if s.alt_agl <= 0.5:
                s.alt_agl = 0
                s.alt_msl = 15
                self.drone_sim_state = "LANDED"
                s.armed = False
                s.in_air = False
            s.last_update = time.monotonic()
            return

        if self.drone_sim_state == "EMERGENCY":
            s.vertical_speed = -5.0
            s.alt_agl = max(0, s.alt_agl - 5.0 * dt)
            s.alt_msl = s.alt_agl + 15
            s.ground_speed = max(0, s.ground_speed - 5 * dt)
            if s.alt_agl <= 0:
                self.drone_sim_state = "LANDED"
                s.armed = False
                s.in_air = False
            s.last_update = time.monotonic()
            return

        # MISSION state — fly through waypoint sequence
        if self.drone_sim_state == "MISSION":
            if not self.mission_waypoints or self.mission_index >= len(self.mission_waypoints):
                self.drone_sim_state = "FLYING"  # mission complete, resume orbit
                logger.info(f"{self.drone_id} mission complete, resuming orbit")
                return

            wp = self.mission_waypoints[self.mission_index]
            target_lat = wp["lat"]
            target_lon = wp["lng"]  # HUD sends lng
            target_alt = wp.get("alt", 30.0)

            # Fly toward waypoint
            dlat = target_lat - s.lat
            dlon = target_lon - s.lon
            dist = math.sqrt(dlat**2 + dlon**2) * 111320  # approx meters

            if dist < 3.0:  # within 3m, advance to next
                logger.info(
                    f"{self.drone_id} reached waypoint {self.mission_index + 1}"
                    f"/{len(self.mission_waypoints)}"
                )
                self.mission_index += 1
            else:
                # Move toward target at mission_speed
                step = (self.mission_speed * dt) / 111320  # convert m/s to degrees/s
                ratio = min(1.0, step / (dist / 111320))
                s.lat += dlat * ratio
                s.lon += dlon * ratio
                s.heading = math.degrees(math.atan2(dlon, dlat)) % 360

            # Altitude management
            alt_diff = target_alt - s.alt_agl
            s.alt_agl += max(-2.0, min(2.0, alt_diff)) * dt
            s.alt_msl = s.alt_agl + 15
            s.vertical_speed = max(-2.0, min(2.0, alt_diff))
            s.ground_speed = self.mission_speed if dist > 3.0 else 0

            # Battery drain during mission
            s.remaining_pct = max(0, s.remaining_pct - 0.01 * dt)
            s.voltage = 18 + (s.remaining_pct / 100) * 7
            s.current = 8 + math.sin(time.time() + self.index) * 2.5
            s.status = OperationalStatus.NOMINAL
            s.in_air = True
            s.armed = True
            s.last_update = time.monotonic()
            return

        # GOTO state — fly to a single target point
        if self.drone_sim_state == "GOTO":
            if self.goto_target is None:
                self.drone_sim_state = "FLYING"
                return

            target_lat, target_lon, target_alt = self.goto_target

            dlat = target_lat - s.lat
            dlon = target_lon - s.lon
            dist = math.sqrt(dlat**2 + dlon**2) * 111320

            if dist < 3.0:
                logger.info(f"{self.drone_id} reached goto target")
                self.goto_target = None
                self.drone_sim_state = "FLYING"
            else:
                step = (self.mission_speed * dt) / 111320
                ratio = min(1.0, step / (dist / 111320))
                s.lat += dlat * ratio
                s.lon += dlon * ratio
                s.heading = math.degrees(math.atan2(dlon, dlat)) % 360

            # Altitude management
            alt_diff = target_alt - s.alt_agl
            s.alt_agl += max(-2.0, min(2.0, alt_diff)) * dt
            s.alt_msl = s.alt_agl + 15
            s.vertical_speed = max(-2.0, min(2.0, alt_diff))
            s.ground_speed = self.mission_speed if dist > 3.0 else 0

            s.remaining_pct = max(0, s.remaining_pct - 0.01 * dt)
            s.voltage = 18 + (s.remaining_pct / 100) * 7
            s.current = 8 + math.sin(time.time() + self.index) * 2.5
            s.status = OperationalStatus.NOMINAL
            s.in_air = True
            s.armed = True
            s.last_update = time.monotonic()
            return

        # FLYING state — normal flight
        s.in_air = True
        s.armed = True

        if self.role == "PRIMARY":
            self.orbit_angle += self.orbit_speed * dt
            s.lat = CENTER_LAT + math.cos(self.orbit_angle) * self.orbit_radius
            s.lon = CENTER_LON + math.sin(self.orbit_angle) * self.orbit_radius
            s.heading = (math.degrees(self.orbit_angle) + 90) % 360
        elif leader:
            ls = leader.state
            offset = V_OFFSETS.get(self.drone_id, OffsetVector(dx=0, dy=0))
            target_lat, target_lon = _offset_to_latlon(
                ls.lat, ls.lon, ls.heading, offset,
            )
            s.lat += (target_lat - s.lat) * 0.1
            s.lon += (target_lon - s.lon) * 0.1
            s.heading = ls.heading + math.sin(t + self.index) * 1.5
            if s.heading < 0:
                s.heading += 360
            if s.heading >= 360:
                s.heading -= 360

        # Altitude oscillation
        s.alt_agl = 120 + math.sin(t * 0.1 + self.phase_offset) * 5
        s.alt_msl = s.alt_agl + 15
        s.vertical_speed = math.cos(t * 0.1 + self.phase_offset) * 0.5

        # Attitude
        s.roll = math.sin(t * 0.3 + self.phase_offset) * 8
        s.pitch = math.sin(t * 0.2 + self.phase_offset * 1.5) * 3
        s.yaw = s.heading

        # Speed
        s.ground_speed = 10 + math.sin(t * 0.05 + self.index) * 3

        # Battery drain
        s.remaining_pct = max(0, s.remaining_pct - 0.01 * dt)
        s.voltage = 18 + (s.remaining_pct / 100) * 7
        s.current = 8 + math.sin(t + self.index) * 2.5

        # Sensor jitter
        s.satellites = max(8, min(20, int(14 + math.sin(t * 0.5 + self.index) * 2)))
        s.hdop = max(0.5, min(3.0, 0.8 + math.sin(t * 0.3) * 0.15))
        s.rssi = max(40, min(99, int(85 + math.sin(t * 0.2 + self.index) * 5)))
        s.quality = max(70, min(100, int(93 + math.sin(t * 0.15) * 4)))
        s.latency_ms = max(5, min(80, int(25 + math.sin(t * 0.4) * 10)))

        # Cohesion
        s.cohesion = 0.85 + abs(math.sin(t * 0.1 + self.index)) * 0.15

        # FPV data updates
        s.mah_consumed += abs(s.current) * (dt / 3600) * 1000
        s.cell_voltage = s.voltage / 6.0
        s.flight_timer_s += dt
        if s.armed:
            s.arm_timer_s += dt
        d_lat = (s.lat - CENTER_LAT) * 111320
        d_lon = (s.lon - CENTER_LON) * 111320 * math.cos(math.radians(s.lat))
        s.home_distance_m = math.sqrt(d_lat ** 2 + d_lon ** 2)
        s.home_direction_deg = math.degrees(math.atan2(-d_lon, -d_lat)) % 360
        s.video_link_quality = max(60, min(100, s.video_link_quality + int((hash(str(t)) % 3) - 1)))

        # Status derivation
        if s.remaining_pct < 25:
            s.status = OperationalStatus.DEGRADED
        elif s.rssi < 60:
            s.status = OperationalStatus.COMMS_DEGRADED
        else:
            s.status = OperationalStatus.NOMINAL

        s.last_update = time.monotonic()


class MockSwarm:
    """Manages all mock drones and runs the simulation tick loop."""

    def __init__(self, settings: OverwatchSettings):
        self.settings = settings
        self.drones: Dict[str, MockDrone] = {}
        self._task: Optional[asyncio.Task] = None

        for i, cfg in enumerate(ASSET_ROSTER[:settings.sitl_drone_count]):
            self.drones[cfg.id] = MockDrone(cfg.id, cfg.role, i)

    def get_states(self) -> Dict[str, DroneState]:
        return {d.drone_id: d.state for d in self.drones.values()}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._tick_loop())
        logger.info(f"Mock swarm started with {len(self.drones)} assets")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _tick_loop(self) -> None:
        interval = 1.0 / self.settings.telemetry_rate_hz
        while True:
            t0 = time.monotonic()
            leader = self.drones.get("ALPHA-1")
            for drone in self.drones.values():
                drone.update(interval, leader if drone.role != "PRIMARY" else None)
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))
