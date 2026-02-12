"""
MSP (MultiWii Serial Protocol) v1/v2 encoder/decoder.
Reference: Betaflight MSP protocol specification.

MSP v1 frame format:
  $M<direction><size><command><payload...><checksum>
  - direction: '<' (request to FC) or '>' (response from FC)
  - size: payload length (1 byte)
  - command: MSP code (1 byte)
  - checksum: XOR of size ^ command ^ each payload byte
"""
import struct
from enum import IntEnum
from dataclasses import dataclass, field
from typing import List, Optional


class MSPCode(IntEnum):
    MSP_API_VERSION = 1
    MSP_FC_VARIANT = 2
    MSP_FC_VERSION = 3
    MSP_BOARD_INFO = 4
    MSP_BUILD_INFO = 5
    MSP_STATUS = 101
    MSP_RAW_IMU = 102
    MSP_MOTOR = 104
    MSP_RC = 105
    MSP_RAW_GPS = 106
    MSP_ATTITUDE = 108
    MSP_ALTITUDE = 109
    MSP_ANALOG = 110
    MSP_RC_TUNING = 111
    MSP_BATTERY_STATE = 130
    MSP_VOLTAGE_METERS = 128
    MSP_CURRENT_METERS = 129
    MSP_STATUS_EX = 150
    MSP_OSD_CONFIG = 84
    MSP_SET_RAW_RC = 200
    MSP_SET_HEAD = 211
    MSP_SET_COMMAND = 217
    MSP_REBOOT = 68


@dataclass
class MSPMessage:
    code: int
    payload: bytes = b''
    direction: str = "request"


class MSPEncoder:
    """Encodes MSP v1 request frames."""

    @staticmethod
    def encode(code: int, payload: bytes = b'') -> bytes:
        size = len(payload)
        checksum = size ^ code
        for b in payload:
            checksum ^= b
        header = b'$M<'
        return header + bytes([size, code]) + payload + bytes([checksum & 0xFF])


class MSPDecoder:
    """Stateful MSP v1 response decoder — feed raw bytes, get decoded messages."""

    # Parser states
    IDLE = 0
    HEADER_M = 1
    HEADER_ARROW = 2
    HEADER_SIZE = 3
    HEADER_CMD = 4
    PAYLOAD = 5
    CHECKSUM = 6

    def __init__(self):
        self._state = self.IDLE
        self._size = 0
        self._code = 0
        self._payload = bytearray()
        self._checksum = 0
        self._offset = 0
        self._direction = ">"

    def feed(self, data: bytes) -> List[MSPMessage]:
        messages = []
        for byte in data:
            msg = self._process_byte(byte)
            if msg is not None:
                messages.append(msg)
        return messages

    def _process_byte(self, b: int) -> Optional[MSPMessage]:
        if self._state == self.IDLE:
            if b == ord('$'):
                self._state = self.HEADER_M
            return None

        elif self._state == self.HEADER_M:
            if b == ord('M'):
                self._state = self.HEADER_ARROW
            else:
                self._state = self.IDLE
            return None

        elif self._state == self.HEADER_ARROW:
            if b == ord('>'):
                self._direction = "response"
                self._state = self.HEADER_SIZE
            elif b == ord('!'):
                self._direction = "error"
                self._state = self.HEADER_SIZE
            else:
                self._state = self.IDLE
            return None

        elif self._state == self.HEADER_SIZE:
            self._size = b
            self._checksum = b
            self._state = self.HEADER_CMD
            return None

        elif self._state == self.HEADER_CMD:
            self._code = b
            self._checksum ^= b
            self._payload = bytearray()
            self._offset = 0
            if self._size > 0:
                self._state = self.PAYLOAD
            else:
                self._state = self.CHECKSUM
            return None

        elif self._state == self.PAYLOAD:
            self._payload.append(b)
            self._checksum ^= b
            self._offset += 1
            if self._offset >= self._size:
                self._state = self.CHECKSUM
            return None

        elif self._state == self.CHECKSUM:
            self._state = self.IDLE
            if (self._checksum & 0xFF) == b:
                return MSPMessage(
                    code=self._code,
                    payload=bytes(self._payload),
                    direction=self._direction,
                )
            return None

        return None

    def reset(self):
        self._state = self.IDLE


def parse_attitude(payload: bytes) -> dict:
    """Parse MSP_ATTITUDE response: roll, pitch (decidegrees), heading (degrees)."""
    if len(payload) < 6:
        return {}
    roll, pitch, heading = struct.unpack('<hhH', payload[:6])
    return {'roll': roll / 10.0, 'pitch': pitch / 10.0, 'heading': heading}


def parse_raw_gps(payload: bytes) -> dict:
    """Parse MSP_RAW_GPS response."""
    if len(payload) < 16:
        return {}
    fix, num_sat, lat, lon, alt, speed, ground_course = struct.unpack(
        '<BBiiHHH', payload[:16]
    )
    return {
        'fix_type': fix,
        'num_sat': num_sat,
        'lat': lat / 1e7,
        'lon': lon / 1e7,
        'alt': alt,
        'speed': speed / 100.0,
        'ground_course': ground_course / 10.0,
    }


def parse_analog(payload: bytes) -> dict:
    """Parse MSP_ANALOG response: vbat, mah_drawn, rssi, amperage."""
    if len(payload) < 7:
        return {}
    vbat, mah_drawn, rssi, amperage = struct.unpack('<BHHh', payload[:7])
    return {
        'vbat': vbat / 10.0,
        'mah_drawn': mah_drawn,
        'rssi': rssi,
        'amperage': amperage / 100.0,
    }


def parse_altitude(payload: bytes) -> dict:
    """Parse MSP_ALTITUDE response: estimated altitude (cm), vario (cm/s)."""
    if len(payload) < 6:
        return {}
    est_alt, vario = struct.unpack('<ih', payload[:6])
    return {'alt_cm': est_alt, 'vario_cms': vario}


def parse_status_ex(payload: bytes) -> dict:
    """Parse MSP_STATUS_EX response (Betaflight extended status)."""
    if len(payload) < 15:
        return {}
    cycle_time, i2c_errors, sensors, flight_mode_flags, _profile = struct.unpack(
        '<HHHIb', payload[:11]
    )
    armed = bool(flight_mode_flags & (1 << 0))
    return {
        'cycle_time': cycle_time,
        'i2c_errors': i2c_errors,
        'sensors': sensors,
        'flight_mode_flags': flight_mode_flags,
        'armed': armed,
    }


def parse_battery_state(payload: bytes) -> dict:
    """Parse MSP_BATTERY_STATE response."""
    if len(payload) < 9:
        return {}
    cell_count, capacity, vbat, mah_drawn, amperage = struct.unpack(
        '<BHBHh', payload[:8]
    )
    return {
        'cell_count': cell_count,
        'capacity_mah': capacity,
        'vbat': vbat / 10.0,
        'mah_drawn': mah_drawn,
        'amperage': amperage / 100.0,
    }


def parse_api_version(payload: bytes) -> dict:
    """Parse MSP_API_VERSION response."""
    if len(payload) < 3:
        return {}
    protocol_ver, api_major, api_minor = struct.unpack('<BBB', payload[:3])
    return {
        'protocol_version': protocol_ver,
        'api_major': api_major,
        'api_minor': api_minor,
    }
