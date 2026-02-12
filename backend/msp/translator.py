"""
Translates MSP telemetry responses into NEXUS DroneState updates and FPV data.
Maps Betaflight-specific field formats to the unified NEXUS telemetry model.
"""
import math
import logging
from typing import Optional

logger = logging.getLogger("nexus.msp.translator")

# Betaflight flight mode flag bit positions
BETAFLIGHT_MODE_FLAGS = {
    0: 'ARM',
    1: 'ANGLE',
    2: 'HORIZON',
    3: 'NAV_ALTHOLD',
    5: 'HEADFREE',
    6: 'HEADADJ',
    10: 'GPS_HOME',
    11: 'GPS_HOLD',
    15: 'PASSTHRU',
    28: 'ACRO',
    36: 'GPS_RESCUE',
    37: 'TURTLE',
    38: 'AIR',
}


def msp_to_flight_mode(flags: int) -> str:
    """Convert Betaflight flight_mode_flags bitmask to a FlightMode string."""
    if flags & (1 << 1):
        return 'ANGLE'
    if flags & (1 << 2):
        return 'HORIZON'
    if flags & (1 << 36):
        return 'GPS_RESCUE'
    if flags & (1 << 37):
        return 'TURTLE'
    if flags & (1 << 38):
        return 'AIR'
    if flags & (1 << 28):
        return 'ACRO'
    return 'ACRO'


class MSPTranslator:
    """Maps MSP telemetry poll results to NEXUS-compatible drone state dicts."""

    def __init__(self):
        self._flight_timer = 0.0
        self._arm_timer = 0.0
        self._was_armed = False
        self._home_lat: Optional[float] = None
        self._home_lon: Optional[float] = None

    def translate(self, msp_data: dict, dt: float = 0.1) -> dict:
        """
        Convert a dict from MSPConnection.poll_telemetry() into a flat dict
        suitable for updating DroneState or creating an FPV telemetry packet.
        """
        state = {}

        # Attitude
        att = msp_data.get('attitude', {})
        state['roll'] = att.get('roll', 0.0)
        state['pitch'] = att.get('pitch', 0.0)
        state['yaw'] = att.get('heading', 0.0)

        # GPS
        gps = msp_data.get('gps', {})
        state['lat'] = gps.get('lat', 0.0)
        state['lon'] = gps.get('lon', 0.0)
        state['satellites'] = gps.get('num_sat', 0)
        fix = gps.get('fix_type', 0)
        state['fix_type'] = '3D' if fix >= 2 else ('2D' if fix == 1 else 'NO_FIX')
        state['ground_speed'] = gps.get('speed', 0.0)
        state['heading'] = gps.get('ground_course', 0.0)

        # Altitude
        alt = msp_data.get('altitude', {})
        alt_m = alt.get('alt_cm', 0) / 100.0
        state['alt_msl'] = alt_m
        state['alt_agl'] = alt_m
        state['vertical_speed'] = alt.get('vario_cms', 0) / 100.0

        # Battery (analog)
        analog = msp_data.get('analog', {})
        state['voltage'] = analog.get('vbat', 0.0)
        state['current'] = analog.get('amperage', 0.0)
        state['rssi'] = min(100, int(analog.get('rssi', 0) / 10.24))
        state['mah_consumed'] = analog.get('mah_drawn', 0)

        # Battery state (extended)
        batt = msp_data.get('battery', {})
        cell_count = batt.get('cell_count', 0)
        if cell_count > 0:
            state['cell_voltage'] = state['voltage'] / cell_count
            capacity = batt.get('capacity_mah', 0)
            if capacity > 0:
                state['remaining_pct'] = max(0, min(100,
                    100 * (1 - state['mah_consumed'] / capacity)))
            else:
                state['remaining_pct'] = _voltage_to_pct(state['cell_voltage'])
        else:
            state['cell_voltage'] = state['voltage']
            state['remaining_pct'] = _voltage_to_pct(state['voltage'])

        # Status
        status = msp_data.get('status', {})
        armed = status.get('armed', False)
        flags = status.get('flight_mode_flags', 0)
        state['flight_mode'] = msp_to_flight_mode(flags)

        # Timers
        self._flight_timer += dt
        if armed and not self._was_armed:
            self._arm_timer = 0.0
            if state['lat'] != 0 and state['lon'] != 0:
                self._home_lat = state['lat']
                self._home_lon = state['lon']
        if armed:
            self._arm_timer += dt
        self._was_armed = armed
        state['flight_timer_s'] = self._flight_timer
        state['arm_timer_s'] = self._arm_timer

        # Home distance/direction
        if self._home_lat is not None and state['lat'] != 0:
            d_lat = (state['lat'] - self._home_lat) * 111320
            d_lon = (state['lon'] - self._home_lon) * 111320 * math.cos(
                math.radians(state['lat']))
            state['home_distance_m'] = math.sqrt(d_lat ** 2 + d_lon ** 2)
            state['home_direction_deg'] = math.degrees(
                math.atan2(-d_lon, -d_lat)) % 360
        else:
            state['home_distance_m'] = 0.0
            state['home_direction_deg'] = 0.0

        # Derived status
        if state['remaining_pct'] < 25:
            state['status'] = 'LOW_BATT'
        elif state['rssi'] < 60:
            state['status'] = 'WEAK_SIGNAL'
        else:
            state['status'] = 'ACTIVE'

        state['protocol'] = 'MSP'
        state['quality'] = min(100, state['rssi'])
        state['latency_ms'] = 0
        state['hdop'] = 1.5 if state['satellites'] > 6 else 5.0

        return state


def _voltage_to_pct(cell_v: float) -> float:
    """Rough LiPo cell voltage to percentage mapping."""
    if cell_v >= 4.2:
        return 100.0
    if cell_v <= 3.3:
        return 0.0
    return max(0, min(100, (cell_v - 3.3) / 0.9 * 100))
