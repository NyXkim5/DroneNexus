"""
MSP command wrappers for Betaflight flight controller actions.
Translates high-level commands (arm, disarm, set mode) into MSP frames.
"""
import struct
import logging
from typing import Optional

from msp.connection import MSPConnection
from msp.protocol import MSPCode

logger = logging.getLogger("overwatch.msp.commands")

# Betaflight RC channel assignments (standard)
CHANNEL_ROLL = 0
CHANNEL_PITCH = 1
CHANNEL_YAW = 2
CHANNEL_THROTTLE = 3
CHANNEL_AUX1 = 4    # Typically arm switch
CHANNEL_AUX2 = 5    # Typically flight mode
CHANNEL_AUX3 = 6
CHANNEL_AUX4 = 7

# RC values
RC_MID = 1500
RC_LOW = 1000
RC_HIGH = 2000
RC_ARM_THRESHOLD = 1800
RC_DISARM_VALUE = 1000

# Betaflight flight mode channel ranges (AUX2 typical mapping)
MODE_RANGES = {
    'ANGLE':      1000,
    'HORIZON':    1300,
    'ACRO':       1600,
    'AIR':        1800,
    'GPS_RESCUE': 1900,
}


class MSPCommander:
    """Sends commands to a Betaflight FC via MSP."""

    @staticmethod
    async def arm(conn: MSPConnection) -> bool:
        """Arm the drone via MSP_SET_RAW_RC with AUX1 high."""
        channels = [RC_MID, RC_MID, RC_MID, RC_LOW,
                     RC_ARM_THRESHOLD, RC_MID, RC_MID, RC_MID]
        return await MSPCommander._send_rc(conn, channels)

    @staticmethod
    async def disarm(conn: MSPConnection) -> bool:
        """Disarm the drone via MSP_SET_RAW_RC with AUX1 low."""
        channels = [RC_MID, RC_MID, RC_MID, RC_LOW,
                     RC_DISARM_VALUE, RC_MID, RC_MID, RC_MID]
        return await MSPCommander._send_rc(conn, channels)

    @staticmethod
    async def set_flight_mode(conn: MSPConnection, mode: str) -> bool:
        """Switch flight mode by setting AUX2 to the appropriate range."""
        mode_value = MODE_RANGES.get(mode.upper(), RC_MID)
        channels = [RC_MID, RC_MID, RC_MID, RC_MID,
                     RC_ARM_THRESHOLD, mode_value, RC_MID, RC_MID]
        return await MSPCommander._send_rc(conn, channels)

    @staticmethod
    async def reboot(conn: MSPConnection) -> bool:
        """Send MSP_REBOOT to restart the flight controller."""
        resp = await conn.request(MSPCode.MSP_REBOOT)
        if resp is not None:
            logger.info("[MSP] Reboot command sent")
            return True
        logger.warning("[MSP] Reboot command failed")
        return False

    @staticmethod
    async def _send_rc(conn: MSPConnection, channels: list) -> bool:
        """Send MSP_SET_RAW_RC with 8 channel values (uint16 each)."""
        payload = struct.pack('<' + 'H' * len(channels), *channels)
        resp = await conn.request(MSPCode.MSP_SET_RAW_RC, payload)
        if resp is not None:
            return True
        logger.warning("[MSP] SET_RAW_RC failed")
        return False
