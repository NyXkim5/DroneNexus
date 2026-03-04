"""
Typed async wrappers around MAVSDK action/mission calls.
Each method takes a DroneConnection and executes the corresponding action.
"""
import logging
from typing import List
from mavlink.connection import DroneConnection
from protocol import Waypoint

logger = logging.getLogger("overwatch.commands")


class DroneCommander:
    """Executes MAVLink commands against a single drone."""

    @staticmethod
    async def arm(conn: DroneConnection) -> bool:
        try:
            await conn.system.action.arm()
            logger.info(f"[{conn.drone_id}] Armed")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Arm failed: {e}")
            return False

    @staticmethod
    async def disarm(conn: DroneConnection) -> bool:
        try:
            await conn.system.action.disarm()
            logger.info(f"[{conn.drone_id}] Disarmed")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Disarm failed: {e}")
            return False

    @staticmethod
    async def takeoff(conn: DroneConnection, altitude: float = 30.0) -> bool:
        try:
            await conn.system.action.set_takeoff_altitude(altitude)
            await conn.system.action.takeoff()
            logger.info(f"[{conn.drone_id}] Takeoff {altitude}m")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Takeoff failed: {e}")
            return False

    @staticmethod
    async def land(conn: DroneConnection) -> bool:
        try:
            await conn.system.action.land()
            logger.info(f"[{conn.drone_id}] Landing")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Land failed: {e}")
            return False

    @staticmethod
    async def return_to_launch(conn: DroneConnection) -> bool:
        try:
            await conn.system.action.return_to_launch()
            logger.info(f"[{conn.drone_id}] RTL")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] RTL failed: {e}")
            return False

    @staticmethod
    async def goto(conn: DroneConnection, lat: float, lon: float,
                   alt: float, yaw: float = 0) -> bool:
        try:
            await conn.system.action.goto_location(lat, lon, alt, yaw)
            logger.info(f"[{conn.drone_id}] Goto {lat:.5f},{lon:.5f}")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Goto failed: {e}")
            return False

    @staticmethod
    async def emergency_stop(conn: DroneConnection) -> bool:
        try:
            await conn.system.action.kill()
            logger.warning(f"[{conn.drone_id}] EMERGENCY STOP")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Emergency stop failed: {e}")
            return False

    @staticmethod
    async def set_speed(conn: DroneConnection, speed_ms: float) -> bool:
        try:
            await conn.system.action.set_maximum_speed(speed_ms)
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Set speed failed: {e}")
            return False

    @staticmethod
    async def upload_mission(conn: DroneConnection, waypoints: List[Waypoint]) -> bool:
        try:
            from mavsdk.mission import MissionItem, MissionPlan
            items = []
            for wp in waypoints:
                item = MissionItem(
                    latitude_deg=wp.lat,
                    longitude_deg=wp.lng,
                    relative_altitude_m=wp.alt,
                    speed_m_s=wp.speed or 10.0,
                    is_fly_through=wp.type == "WAYPOINT",
                    gimbal_pitch_deg=float("nan"),
                    gimbal_yaw_deg=float("nan"),
                    camera_action=MissionItem.CameraAction.NONE,
                    loiter_time_s=30.0 if wp.type == "LOITER" else 0,
                    camera_photo_interval_s=float("nan"),
                    acceptance_radius_m=wp.radius or 5.0,
                    yaw_deg=float("nan"),
                    camera_photo_distance_m=float("nan"),
                )
                items.append(item)
            await conn.system.mission.upload_mission(MissionPlan(items))
            logger.info(f"[{conn.drone_id}] Mission uploaded ({len(items)} items)")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Mission upload failed: {e}")
            return False

    @staticmethod
    async def start_mission(conn: DroneConnection) -> bool:
        try:
            await conn.system.mission.start_mission()
            logger.info(f"[{conn.drone_id}] Mission started")
            return True
        except Exception as e:
            logger.error(f"[{conn.drone_id}] Mission start failed: {e}")
            return False
