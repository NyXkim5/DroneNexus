"""Unit tests for the DJI decoder pure functions."""
from __future__ import annotations

import struct
import pytest

from sensors.dji_decoder import (
    parse_dji_frame,
    parse_dji_binary_frame,
    parse_dji_text_line,
)


def _make_dji_binary_data(
    serial: str = "DJI-UNIT-001",
    device_type: str = "Mavic3",
    uas_lat: float = 33.641,
    uas_lon: float = -117.844,
    op_lat: float = 33.640,
    op_lon: float = -117.845,
    home_lat: float = 33.639,
    home_lon: float = -117.846,
    altitude: float = 100.0,
    speed_e: float = 5.0,
    speed_n: float = 3.0,
    speed_u: float = 1.0,
    rssi: int = -65,
) -> bytes:
    """Build a minimal DJI binary data payload (227+ bytes)."""
    buf = bytearray(227)
    # serial_number: bytes 0-63
    sn_bytes = serial.encode("utf-8")[:64].ljust(64, b"\x00")
    buf[0:64] = sn_bytes
    # device_type: bytes 64-127
    dt_bytes = device_type.encode("utf-8")[:64].ljust(64, b"\x00")
    buf[64:128] = dt_bytes
    # device_type_8: byte 128
    buf[128] = 2
    # doubles at known offsets
    struct.pack_into("<d", buf, 129, op_lat)
    struct.pack_into("<d", buf, 137, op_lon)
    struct.pack_into("<d", buf, 145, uas_lat)
    struct.pack_into("<d", buf, 153, uas_lon)
    struct.pack_into("<d", buf, 161, 50.0)   # height
    struct.pack_into("<d", buf, 169, altitude)
    struct.pack_into("<d", buf, 177, home_lat)
    struct.pack_into("<d", buf, 185, home_lon)
    struct.pack_into("<d", buf, 193, 2437.0)  # freq
    struct.pack_into("<d", buf, 201, speed_e)
    struct.pack_into("<d", buf, 209, speed_n)
    struct.pack_into("<d", buf, 217, speed_u)
    struct.pack_into("<h", buf, 225, rssi)
    return bytes(buf)


def _wrap_in_frame(data: bytes) -> bytes:
    """Wrap data payload into a DJI frame with header."""
    total_len = 5 + len(data)
    header = bytearray(5)
    header[0] = 0xAA
    header[1] = 0x55
    header[2] = 0x01  # package type
    struct.pack_into("<H", header, 3, total_len)
    return bytes(header) + data


class TestParseDjiFrame:
    def test_returns_none_for_empty(self) -> None:
        assert parse_dji_frame(b"") is None

    def test_returns_none_for_short(self) -> None:
        assert parse_dji_frame(b"\xAA\x55") is None

    def test_valid_frame(self) -> None:
        data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        frame = _wrap_in_frame(data)
        result = parse_dji_frame(frame)
        assert result is not None
        pkg_type, payload = result
        assert pkg_type == 0x01
        assert len(payload) == len(data)


class TestParseDjiBinaryFrame:
    def test_returns_none_for_empty(self) -> None:
        assert parse_dji_binary_frame(b"") is None

    def test_returns_none_for_short(self) -> None:
        assert parse_dji_binary_frame(b"\x00" * 100) is None

    def test_valid_payload(self) -> None:
        data = _make_dji_binary_data()
        result = parse_dji_binary_frame(data)
        assert result is not None
        assert result["serial_number"] == "DJI-UNIT-001"
        assert result["device_type"] == "Mavic3"
        assert result["device_type_8"] == 2
        assert abs(result["uas_lat"] - 33.641) < 0.001
        assert abs(result["uas_lon"] - (-117.844)) < 0.001
        assert abs(result["op_lat"] - 33.640) < 0.001
        assert abs(result["speed_e"] - 5.0) < 0.01
        assert abs(result["speed_n"] - 3.0) < 0.01
        assert abs(result["speed_u"] - 1.0) < 0.01
        assert result["rssi"] == -65
        assert abs(result["altitude"] - 100.0) < 0.01

    def test_coordinates_round_trip(self) -> None:
        data = _make_dji_binary_data(
            uas_lat=47.6062, uas_lon=-122.3321,
            home_lat=47.607, home_lon=-122.333,
        )
        result = parse_dji_binary_frame(data)
        assert result is not None
        assert abs(result["uas_lat"] - 47.6062) < 1e-6
        assert abs(result["home_lon"] - (-122.333)) < 1e-6


class TestParseDjiTextLine:
    def test_returns_none_for_empty(self) -> None:
        assert parse_dji_text_line("") is None

    def test_returns_none_for_non_dji_line(self) -> None:
        assert parse_dji_text_line("some random text") is None

    def test_returns_none_for_short_line(self) -> None:
        assert parse_dji_text_line("dji_O,a,b") is None

    def test_valid_text_line(self) -> None:
        line = (
            "dji_O,2,2437.0,-65,Mavic3,DJI-SN-TEXT,"
            "-117.845,33.640,-117.844,33.641,-117.846,33.639,"
            "5.0|3.0,50.0|100.0|1.0,2024-01-15T12:00:00Z"
        )
        result = parse_dji_text_line(line)
        assert result is not None
        assert result["serial_number"] == "DJI-SN-TEXT"
        assert result["device_type"] == "Mavic3"
        assert abs(result["uas_lat"] - 33.641) < 0.001
        assert abs(result["uas_lon"] - (-117.844)) < 0.001
        assert abs(result["op_lat"] - 33.640) < 0.001
        assert abs(result["op_lon"] - (-117.845)) < 0.001
        assert abs(result["speed_e"] - 5.0) < 0.01
        assert abs(result["speed_n"] - 3.0) < 0.01
        assert result["rssi"] == -65

    def test_trailing_semicolon(self) -> None:
        line = (
            "dji_O,2,2437.0,-65,Mavic3,DJI-SN,"
            "-117.0,33.0,-117.1,33.1,-117.2,33.2,"
            "1.0|2.0,10.0|50.0|0.5,2024-01-01T00:00:00Z;"
        )
        result = parse_dji_text_line(line)
        assert result is not None
        assert result["serial_number"] == "DJI-SN"

    def test_missing_serial(self) -> None:
        line = (
            "dji_O,4,2437.0,-70,,,"
            "-117.0,33.0,-117.1,33.1,-117.2,33.2,"
            "0|0,0|0|0,2024-01-01T00:00:00Z"
        )
        result = parse_dji_text_line(line)
        assert result is not None
        assert result["serial_number"] is None
        assert result["device_type_8"] == 4
