"""Unit tests for the ODID decoder pure functions."""
from __future__ import annotations

import math
import struct
import pytest

from sensors.odid_decoder import (
    ODID_MESSAGE_SIZE,
    MSG_BASIC_ID,
    MSG_LOCATION,
    MSG_SELF_ID,
    MSG_SYSTEM,
    MSG_OPERATOR_ID,
    MSG_PACKED,
    decode_odid_message,
    decode_message_pack,
)


def _make_basic_id_msg(
    ua_type: int = 2,
    id_type: int = 1,
    uas_id: str = "ABC123",
) -> bytes:
    """Build a 25-byte BasicID ODID message."""
    buf = bytearray(ODID_MESSAGE_SIZE)
    buf[0] = (MSG_BASIC_ID << 4) | 0x02  # type=0, proto=2
    buf[1] = (id_type << 4) | ua_type
    encoded_id = uas_id.encode("ascii")[:20].ljust(20, b"\x00")
    buf[2:22] = encoded_id
    return bytes(buf)


def _make_location_msg(
    lat: float = 40.7128,
    lon: float = -74.006,
    alt_geo: float = 120.0,
    speed_h: int = 10,
    direction: int = 90,
    status: int = 2,
) -> bytes:
    """Build a 25-byte Location ODID message."""
    buf = bytearray(ODID_MESSAGE_SIZE)
    buf[0] = (MSG_LOCATION << 4) | 0x02
    buf[1] = (status << 4) | 0x00  # no speed mult, height type=takeoff
    buf[2] = direction
    buf[3] = speed_h
    buf[4] = 0  # speed_vertical = 0

    lat_enc = int(lat * 1e7)
    lon_enc = int(lon * 1e7)
    struct.pack_into("<i", buf, 5, lat_enc)
    struct.pack_into("<i", buf, 9, lon_enc)

    alt_geo_enc = int(alt_geo * 2 + 2000)
    struct.pack_into("<H", buf, 13, alt_geo_enc)  # baro
    struct.pack_into("<H", buf, 15, alt_geo_enc)  # geo
    struct.pack_into("<H", buf, 17, alt_geo_enc)  # height

    buf[19] = 0x33  # horiz=3, vert=3
    return bytes(buf)


def _make_system_msg(
    op_lat: float = 40.712,
    op_lon: float = -74.005,
) -> bytes:
    """Build a 25-byte System ODID message."""
    buf = bytearray(ODID_MESSAGE_SIZE)
    buf[0] = (MSG_SYSTEM << 4) | 0x02
    buf[1] = 0x01  # operator_location_type=1 (dynamic)

    op_lat_enc = int(op_lat * 1e7)
    op_lon_enc = int(op_lon * 1e7)
    struct.pack_into("<i", buf, 2, op_lat_enc)
    struct.pack_into("<i", buf, 6, op_lon_enc)
    struct.pack_into("<H", buf, 10, 1)  # area count
    buf[12] = 50  # area radius

    op_alt_enc = int(10.0 * 2 + 2000)
    struct.pack_into("<h", buf, 18, op_alt_enc)
    return bytes(buf)


def _make_self_id_msg(desc: str = "Test flight") -> bytes:
    """Build a 25-byte SelfID ODID message."""
    buf = bytearray(ODID_MESSAGE_SIZE)
    buf[0] = (MSG_SELF_ID << 4) | 0x02
    buf[1] = 0  # desc type = text
    encoded_desc = desc.encode("ascii")[:23].ljust(23, b"\x00")
    buf[2:25] = encoded_desc
    return bytes(buf)


def _make_operator_id_msg(op_id: str = "OP-USA-001") -> bytes:
    """Build a 25-byte OperatorID ODID message."""
    buf = bytearray(ODID_MESSAGE_SIZE)
    buf[0] = (MSG_OPERATOR_ID << 4) | 0x02
    buf[1] = 0  # operator ID type
    encoded_id = op_id.encode("ascii")[:20].ljust(20, b"\x00")
    buf[2:22] = encoded_id
    return bytes(buf)


class TestDecodeODIDMessage:
    def test_returns_none_for_empty_data(self) -> None:
        assert decode_odid_message(b"") is None

    def test_returns_none_for_short_data(self) -> None:
        assert decode_odid_message(b"\x00" * 10) is None

    def test_returns_none_for_unknown_type(self) -> None:
        buf = bytearray(ODID_MESSAGE_SIZE)
        buf[0] = 0xA0  # type 10 is unknown
        assert decode_odid_message(bytes(buf)) is None

    def test_basic_id_serial(self) -> None:
        msg = _make_basic_id_msg(ua_type=2, id_type=1, uas_id="DJI-SN12345")
        result = decode_odid_message(msg)
        assert result is not None
        assert result["UAType"] == 2
        assert result["IDType"] == 1
        assert result["BasicID"] == "DJI-SN12345"
        assert result["msg_type"] == MSG_BASIC_ID

    def test_basic_id_hex_fallback(self) -> None:
        msg = _make_basic_id_msg(id_type=0, uas_id="")
        result = decode_odid_message(msg)
        assert result is not None
        assert isinstance(result["BasicID"], str)

    def test_location_message(self) -> None:
        msg = _make_location_msg(lat=40.7128, lon=-74.006, alt_geo=120.0)
        result = decode_odid_message(msg)
        assert result is not None
        assert result["msg_type"] == MSG_LOCATION
        assert abs(result["Latitude"] - 40.7128) < 0.001
        assert abs(result["Longitude"] - (-74.006)) < 0.001
        assert result["Status"] == 2
        assert result["Direction"] == 90.0

    def test_location_zero_latlon_becomes_nan(self) -> None:
        msg = _make_location_msg(lat=0.0, lon=0.0)
        result = decode_odid_message(msg)
        assert result is not None
        assert math.isnan(result["Latitude"])
        assert math.isnan(result["Longitude"])

    def test_system_message(self) -> None:
        msg = _make_system_msg(op_lat=40.712, op_lon=-74.005)
        result = decode_odid_message(msg)
        assert result is not None
        assert result["msg_type"] == MSG_SYSTEM
        assert abs(result["OperatorLatitude"] - 40.712) < 0.001
        assert abs(result["OperatorLongitude"] - (-74.005)) < 0.001
        assert result["AreaRadius"] == 50

    def test_self_id_message(self) -> None:
        msg = _make_self_id_msg(desc="Survey mission")
        result = decode_odid_message(msg)
        assert result is not None
        assert result["msg_type"] == MSG_SELF_ID
        assert result["Desc"] == "Survey mission"
        assert result["DescType"] == 0

    def test_operator_id_message(self) -> None:
        msg = _make_operator_id_msg(op_id="FAA-REG-999")
        result = decode_odid_message(msg)
        assert result is not None
        assert result["msg_type"] == MSG_OPERATOR_ID
        assert result["OperatorID"] == "FAA-REG-999"


class TestDecodeMessagePack:
    def test_empty_data(self) -> None:
        assert decode_message_pack(b"") == []

    def test_wrong_type_header(self) -> None:
        buf = bytearray(3)
        buf[0] = 0x00  # not 0xF
        assert decode_message_pack(bytes(buf)) == []

    def test_wrong_single_size(self) -> None:
        buf = bytearray(3)
        buf[0] = 0xF0
        buf[1] = 30  # not 25
        buf[2] = 1
        assert decode_message_pack(bytes(buf)) == []

    def test_truncated_pack(self) -> None:
        buf = bytearray(3)
        buf[0] = 0xF0
        buf[1] = ODID_MESSAGE_SIZE
        buf[2] = 2  # claims 2 messages but no payload
        assert decode_message_pack(bytes(buf)) == []

    def test_single_message_pack(self) -> None:
        basic = _make_basic_id_msg(uas_id="PACK-001")
        header = bytearray(3)
        header[0] = 0xF0
        header[1] = ODID_MESSAGE_SIZE
        header[2] = 1
        pack = bytes(header) + basic
        results = decode_message_pack(pack)
        assert len(results) == 1
        assert results[0]["BasicID"] == "PACK-001"

    def test_multi_message_pack(self) -> None:
        basic = _make_basic_id_msg(uas_id="MULTI-001")
        loc = _make_location_msg(lat=35.0, lon=-118.0)
        system = _make_system_msg(op_lat=35.001, op_lon=-118.001)
        header = bytearray(3)
        header[0] = 0xF0
        header[1] = ODID_MESSAGE_SIZE
        header[2] = 3
        pack = bytes(header) + basic + loc + system
        results = decode_message_pack(pack)
        assert len(results) == 3
        types = [r["msg_type"] for r in results]
        assert MSG_BASIC_ID in types
        assert MSG_LOCATION in types
        assert MSG_SYSTEM in types
