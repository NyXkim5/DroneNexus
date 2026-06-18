"""
DJI Drone ID binary frame and AntSDR CSV text line decoders.

Pure functions to decode DJI OcuSync / AeroScope protocol frames from raw
bytes (AntSDR binary TCP feed) and from AntSDR CSV text lines. Uses only the
struct module for binary unpacking.

Ported from DroneCOT dji_functions.py and dji_text_parser.py.
"""
from __future__ import annotations

import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum binary data payload length after frame header extraction.
_MIN_DATA_LEN = 227


def _float_or_none(value: str) -> Optional[float]:
    """Parse a string to float, returning None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _band_to_device_type_8(band: str) -> int:
    """Map AntSDR band field to a device_type_8 integer."""
    if band.isdigit():
        return int(band)
    if "/" in band:
        try:
            return int(band.split("/", maxsplit=1)[0])
        except ValueError:
            pass
    return 255


def parse_dji_frame(frame: bytes) -> Optional[tuple[int, bytes]]:
    """Extract package type and data payload from a raw DJI frame.

    Frame layout:
      bytes 0-1: header
      byte 2: package type
      bytes 3-4: total package length (little-endian uint16)
      bytes 5+: data payload

    Returns (package_type, data) or None if the frame is too short.
    """
    if not frame or len(frame) < 5:
        return None
    package_type = frame[2]
    package_length = struct.unpack_from("<H", frame, 3)[0]
    data = frame[5:5 + package_length - 5]
    if len(data) == 0:
        return None
    return package_type, data


def parse_dji_binary_frame(data: bytes) -> Optional[dict]:
    """Parse the data payload of a DJI Drone ID binary frame.

    Expects the data portion after frame header extraction (from
    parse_dji_frame). Returns a dict with serial_number, device_type,
    coordinates, speed components, rssi, and freq. Returns None if the
    data is truncated or malformed.
    """
    if not data or len(data) < _MIN_DATA_LEN:
        logger.debug("DJI binary payload too short: %d bytes", len(data) if data else 0)
        return None

    try:
        serial_number = data[:64].decode("utf-8", errors="replace").rstrip("\x00")
        device_type = data[64:128].decode("utf-8", errors="replace").rstrip("\x00")
    except (UnicodeDecodeError, AttributeError):
        logger.debug("DJI binary payload string decode failed")
        return None

    return _unpack_dji_fields(data, serial_number, device_type)


def _unpack_dji_fields(
    data: bytes, serial_number: str, device_type: str,
) -> Optional[dict]:
    """Unpack numeric fields from a DJI binary data payload."""
    try:
        return {
            "serial_number": serial_number,
            "device_type": device_type,
            "device_type_8": data[128],
            "op_lat": struct.unpack_from("<d", data, 129)[0],
            "op_lon": struct.unpack_from("<d", data, 137)[0],
            "uas_lat": struct.unpack_from("<d", data, 145)[0],
            "uas_lon": struct.unpack_from("<d", data, 153)[0],
            "height": struct.unpack_from("<d", data, 161)[0],
            "altitude": struct.unpack_from("<d", data, 169)[0],
            "home_lat": struct.unpack_from("<d", data, 177)[0],
            "home_lon": struct.unpack_from("<d", data, 185)[0],
            "freq": struct.unpack_from("<d", data, 193)[0],
            "speed_e": struct.unpack_from("<d", data, 201)[0],
            "speed_n": struct.unpack_from("<d", data, 209)[0],
            "speed_u": struct.unpack_from("<d", data, 217)[0],
            "rssi": struct.unpack_from("<h", data, 225)[0],
        }
    except struct.error as exc:
        logger.debug("DJI binary unpack error: %s", exc)
        return None


def parse_dji_text_line(line: str) -> Optional[dict]:
    """Parse an AntSDR 'dji_O,...' CSV text line into a DJI payload dict.

    Returns None if the line is not a valid dji_O CSV line or has
    insufficient fields.
    """
    line = line.strip()
    if not line or not line.startswith("dji_O,"):
        return None
    if line.endswith(";"):
        line = line[:-1]

    parts = line.split(",")
    if len(parts) < 15:
        logger.debug("DJI text line too few fields: %d", len(parts))
        return None

    return _parse_dji_csv_parts(parts)


def _parse_dji_csv_parts(parts: list[str]) -> dict:
    """Build a DJI payload dict from parsed CSV parts."""
    speeds = parts[12].split("|")
    extra = parts[13].split("|")
    band = parts[1].strip()
    serial = parts[5].strip()

    return {
        "serial_number": serial or None,
        "device_type": parts[4].strip() or "Unknown",
        "device_type_8": _band_to_device_type_8(band),
        "op_lon": _float_or_none(parts[6]),
        "op_lat": _float_or_none(parts[7]),
        "uas_lon": _float_or_none(parts[8]),
        "uas_lat": _float_or_none(parts[9]),
        "home_lon": _float_or_none(parts[10]),
        "home_lat": _float_or_none(parts[11]),
        "freq": _float_or_none(parts[2]),
        "speed_e": _float_or_none(speeds[0]) if speeds else None,
        "speed_n": _float_or_none(speeds[1]) if len(speeds) > 1 else None,
        "speed_u": _float_or_none(extra[2]) if len(extra) > 2 else None,
        "height": _float_or_none(extra[0]) if extra else None,
        "altitude": _float_or_none(extra[1]) if len(extra) > 1 else None,
        "rssi": int(float(parts[3])) if parts[3] else None,
    }
