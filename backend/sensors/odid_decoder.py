"""
ASTM F3411 Open Drone ID message decoder.

Pure functions to decode ODID messages from raw bytes. Handles individual
messages (BasicID, Location, System, SelfID, OperatorID) and packed message
containers (type 0xF). Uses only the struct module for binary unpacking.

Ported from DroneCOT odid.py and open_drone_id.py. Adapted to avoid bitstruct
and pytz dependencies.
"""
from __future__ import annotations

import struct
from typing import Optional

ODID_MESSAGE_SIZE = 25
ODID_ID_SIZE = 20
ODID_STR_SIZE = 23

# ASTM F3411 message type codes (upper nibble of byte 0)
MSG_BASIC_ID = 0
MSG_LOCATION = 1
MSG_AUTH = 2
MSG_SELF_ID = 3
MSG_SYSTEM = 4
MSG_OPERATOR_ID = 5
MSG_PACKED = 0xF


def _clean_ascii(data: bytes) -> str:
    """Strip null bytes and control characters from ASCII payload."""
    try:
        text = data.decode("ascii", errors="replace")
    except (UnicodeDecodeError, AttributeError):
        return ""
    return text.replace("\x00", "").replace("\t", "").replace("\n", "").replace("\r", "").strip()


def _decode_speed_horizontal(raw: int, mult: int) -> float:
    """Decode horizontal speed from encoded byte and multiplier flag."""
    if raw == 255:
        return float("nan")
    if mult == 1:
        return (float(raw) * 0.75) + (255.0 * 0.25)
    return float(raw) * 0.75


def _decode_basic_id(payload: bytes) -> dict:
    """Decode a BasicID message (type 0) from a 25-byte ODID message."""
    id_type = payload[1] >> 4
    ua_type = payload[1] & 0x0F
    uas_id_raw = payload[2:2 + ODID_ID_SIZE]
    if id_type in (1, 2):
        uas_id = _clean_ascii(uas_id_raw).replace(" ", "")
    else:
        uas_id = bytes(uas_id_raw).hex()
    return {"IDType": id_type, "UAType": ua_type, "BasicID": uas_id}


def _decode_location(payload: bytes) -> dict:
    """Decode a Location/Vector message (type 1) from 25 bytes."""
    status = (payload[1] >> 4) & 0x0F
    speed_mult = payload[1] & 0x01
    height_type = (payload[1] >> 2) & 0x01

    direction = float(payload[2])
    if direction > 360 or direction < 0:
        direction = float("nan")

    speed_h = _decode_speed_horizontal(payload[3], speed_mult)
    speed_v = int(payload[4]) * 0.5
    if speed_v == 63.0:
        speed_v = float("nan")

    lat_raw = struct.unpack_from("<i", bytes(payload), 5)[0]
    lon_raw = struct.unpack_from("<i", bytes(payload), 9)[0]
    lat = float(lat_raw) / 1e7
    lon = float(lon_raw) / 1e7

    alt_baro_raw = struct.unpack_from("<H", bytes(payload), 13)[0]
    alt_geo_raw = struct.unpack_from("<H", bytes(payload), 15)[0]
    height_raw = struct.unpack_from("<H", bytes(payload), 17)[0]

    alt_baro = (alt_baro_raw - 2000) / 2.0
    alt_geo = (alt_geo_raw - 2000) / 2.0
    height = (height_raw - 2000) / 2.0

    return _build_location_dict(
        status, direction, speed_h, speed_v,
        lat, lon, alt_baro, alt_geo, height_type, height, payload,
    )


def _build_location_dict(
    status: int, direction: float, speed_h: float, speed_v: float,
    lat: float, lon: float, alt_baro: float, alt_geo: float,
    height_type: int, height: float, payload: bytes,
) -> dict:
    """Assemble validated location fields into a dict."""
    if lat == 0.0 or lat > 90.0 or lat < -90.0:
        lat = float("nan")
    if lon == 0.0 or lon > 180.0 or lon < -180.0:
        lon = float("nan")
    for val_name, val, lo, hi in [
        ("alt_baro", alt_baro, -1000.0, 31767.5),
        ("alt_geo", alt_geo, -1000.0, 31767.5),
        ("height", height, -1000.0, 31767.5),
    ]:
        if val <= lo or val > hi:
            if val_name == "alt_baro":
                alt_baro = float("nan")
            elif val_name == "alt_geo":
                alt_geo = float("nan")
            else:
                height = float("nan")

    horiz_acc = payload[19] & 0x0F
    vert_acc = (payload[19] >> 4) & 0x0F

    return {
        "Status": status, "Direction": direction,
        "SpeedHorizontal": speed_h, "SpeedVertical": speed_v,
        "Latitude": lat, "Longitude": lon,
        "AltitudeBaro": alt_baro, "AltitudeGeo": alt_geo,
        "HeightType": height_type, "Height": height,
        "HorizAccuracy": horiz_acc, "VertAccuracy": vert_acc,
    }


def _decode_self_id(payload: bytes) -> dict:
    """Decode a SelfID message (type 3) from 25 bytes."""
    desc_type = payload[1]
    desc = _clean_ascii(payload[2:2 + ODID_STR_SIZE])
    return {"DescType": desc_type, "Desc": desc}


def _decode_system(payload: bytes) -> dict:
    """Decode a System message (type 4) from 25 bytes."""
    flags = payload[1]
    classification_type = (flags >> 2) & 0x03
    operator_location_type = flags & 0x03

    op_lat_raw = struct.unpack_from("<i", bytes(payload), 2)[0]
    op_lon_raw = struct.unpack_from("<i", bytes(payload), 6)[0]
    op_lat = float(op_lat_raw) / 1e7
    op_lon = float(op_lon_raw) / 1e7

    if op_lat == 0.0 or op_lat > 90.0 or op_lat < -90.0:
        op_lat = float("nan")
    if op_lon == 0.0 or op_lon > 180.0 or op_lon < -180.0:
        op_lon = float("nan")

    area_count = struct.unpack_from("<H", bytes(payload), 10)[0]
    area_radius = int(payload[12])

    op_alt_raw = struct.unpack_from("<h", bytes(payload), 18)[0]
    op_alt = (op_alt_raw - 2000) / 2.0
    if op_alt <= -1000.0 or op_alt > 31767.5:
        op_alt = float("nan")

    return {
        "ClassificationType": classification_type,
        "OperatorLocationType": operator_location_type,
        "OperatorLatitude": op_lat, "OperatorLongitude": op_lon,
        "AreaCount": area_count, "AreaRadius": area_radius,
        "OperatorAltitudeGeo": op_alt,
    }


def _decode_operator_id(payload: bytes) -> dict:
    """Decode an OperatorID message (type 5) from 25 bytes."""
    op_id_type = payload[1]
    op_id = _clean_ascii(payload[2:2 + ODID_ID_SIZE])
    return {"OperatorIdType": op_id_type, "OperatorID": op_id}


_DECODERS = {
    MSG_BASIC_ID: _decode_basic_id,
    MSG_LOCATION: _decode_location,
    MSG_SELF_ID: _decode_self_id,
    MSG_SYSTEM: _decode_system,
    MSG_OPERATOR_ID: _decode_operator_id,
}


def decode_odid_message(data: bytes) -> Optional[dict]:
    """Decode a single 25-byte ASTM F3411 ODID message.

    Returns a dict with fields appropriate to the message type, or None if
    the data is too short or the message type is unrecognized.
    """
    if not data or len(data) < ODID_MESSAGE_SIZE:
        return None
    msg_type = data[0] >> 4
    decoder = _DECODERS.get(msg_type)
    if decoder is None:
        return None
    result = decoder(data[:ODID_MESSAGE_SIZE])
    result["msg_type"] = msg_type
    return result


def decode_message_pack(data: bytes) -> list[dict]:
    """Decode a packed ODID message container (type 0xF).

    The pack header is 3 bytes: byte 0 upper nibble = 0xF, byte 1 = single
    message size (must be 25), byte 2 = number of messages. The remaining
    bytes are concatenated 25-byte messages.

    Returns a list of decoded message dicts. Returns an empty list if the
    pack is malformed or truncated.
    """
    if not data or len(data) < 3:
        return []

    header_type = data[0] >> 4
    if header_type != MSG_PACKED:
        return []

    single_size = data[1]
    pack_count = data[2]

    if single_size != ODID_MESSAGE_SIZE:
        return []

    expected_len = 3 + single_size * pack_count
    if len(data) < expected_len:
        return []

    results: list[dict] = []
    merged: dict = {}
    for i in range(pack_count):
        offset = 3 + i * single_size
        msg_bytes = data[offset:offset + single_size]
        decoded = decode_odid_message(msg_bytes)
        if decoded is not None:
            results.append(decoded)
            merged.update(decoded)

    return results
