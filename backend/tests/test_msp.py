"""
Tests for MSP protocol encoder/decoder and translator.
Run: python -m pytest backend/tests/test_msp.py -v
      or: python backend/tests/test_msp.py
"""
import struct
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from msp.protocol import (
    MSPEncoder, MSPDecoder, MSPCode, MSPMessage,
    parse_attitude, parse_raw_gps, parse_analog, parse_altitude,
    parse_status_ex, parse_battery_state, parse_api_version,
)
from msp.translator import MSPTranslator, msp_to_flight_mode


passed = 0
failed = 0


def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}")
        print(f"        {e}")
        failed += 1


print("NEXUS MSP Protocol Tests")
print("========================\n")


# ---- Encoder Tests ----

def test_encode_no_payload():
    frame = MSPEncoder.encode(MSPCode.MSP_API_VERSION)
    assert frame[:3] == b'$M<', f"Bad header: {frame[:3]}"
    assert frame[3] == 0, f"Size should be 0, got {frame[3]}"
    assert frame[4] == MSPCode.MSP_API_VERSION, f"Code mismatch"
    checksum = 0 ^ MSPCode.MSP_API_VERSION
    assert frame[5] == checksum & 0xFF, f"Bad checksum"

test("Encode MSP request with no payload", test_encode_no_payload)


def test_encode_with_payload():
    payload = struct.pack('<HH', 1500, 1500)
    frame = MSPEncoder.encode(MSPCode.MSP_SET_RAW_RC, payload)
    assert frame[:3] == b'$M<'
    assert frame[3] == len(payload)
    assert frame[4] == MSPCode.MSP_SET_RAW_RC

test("Encode MSP request with payload", test_encode_with_payload)


# ---- Decoder Tests ----

def test_decode_roundtrip():
    """Encode a request, make a fake response, decode it."""
    code = MSPCode.MSP_API_VERSION
    payload = bytes([0, 2, 5])  # protocol=0, api_major=2, api_minor=5
    size = len(payload)
    checksum = size ^ code
    for b in payload:
        checksum ^= b
    response = b'$M>' + bytes([size, code]) + payload + bytes([checksum & 0xFF])

    decoder = MSPDecoder()
    messages = decoder.feed(response)
    assert len(messages) == 1, f"Expected 1 message, got {len(messages)}"
    assert messages[0].code == code
    assert messages[0].payload == payload
    assert messages[0].direction == "response"

test("Decode MSP response roundtrip", test_decode_roundtrip)


def test_decode_multiple_messages():
    """Feed multiple responses in one stream."""
    decoder = MSPDecoder()

    def make_response(code, payload=b''):
        size = len(payload)
        checksum = size ^ code
        for b in payload:
            checksum ^= b
        return b'$M>' + bytes([size, code]) + payload + bytes([checksum & 0xFF])

    stream = make_response(MSPCode.MSP_ATTITUDE, struct.pack('<hhH', 25, -10, 180))
    stream += make_response(MSPCode.MSP_ALTITUDE, struct.pack('<ih', 12000, 50))
    messages = decoder.feed(stream)
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    assert messages[0].code == MSPCode.MSP_ATTITUDE
    assert messages[1].code == MSPCode.MSP_ALTITUDE

test("Decode multiple MSP messages in stream", test_decode_multiple_messages)


def test_decode_bad_checksum():
    """Bad checksum should be silently dropped."""
    decoder = MSPDecoder()
    response = b'$M>' + bytes([0, MSPCode.MSP_API_VERSION, 0xFF])  # bad checksum
    messages = decoder.feed(response)
    assert len(messages) == 0, "Should reject bad checksum"

test("Reject message with bad checksum", test_decode_bad_checksum)


# ---- Parser Tests ----

def test_parse_attitude():
    payload = struct.pack('<hhH', 25, -15, 270)
    result = parse_attitude(payload)
    assert result['roll'] == 2.5, f"Roll: {result['roll']}"
    assert result['pitch'] == -1.5, f"Pitch: {result['pitch']}"
    assert result['heading'] == 270

test("Parse MSP_ATTITUDE", test_parse_attitude)


def test_parse_raw_gps():
    payload = struct.pack('<BBiiHHH', 2, 14, 336405000, -1178443000, 120, 1050, 1800)
    result = parse_raw_gps(payload)
    assert abs(result['lat'] - 33.6405) < 0.0001
    assert abs(result['lon'] - (-117.8443)) < 0.0001
    assert result['num_sat'] == 14
    assert result['fix_type'] == 2

test("Parse MSP_RAW_GPS", test_parse_raw_gps)


def test_parse_analog():
    payload = struct.pack('<BHHh', 225, 450, 950, 1020)
    result = parse_analog(payload)
    assert result['vbat'] == 22.5
    assert result['mah_drawn'] == 450
    assert result['rssi'] == 950

test("Parse MSP_ANALOG", test_parse_analog)


def test_parse_api_version():
    payload = struct.pack('<BBB', 0, 2, 5)
    result = parse_api_version(payload)
    assert result['api_major'] == 2
    assert result['api_minor'] == 5

test("Parse MSP_API_VERSION", test_parse_api_version)


# ---- Translator Tests ----

def test_translator_attitude():
    translator = MSPTranslator()
    state = translator.translate({'attitude': {'roll': 5.0, 'pitch': -3.0, 'heading': 180}})
    assert state['roll'] == 5.0
    assert state['pitch'] == -3.0
    assert state['yaw'] == 180

test("Translator maps attitude", test_translator_attitude)


def test_translator_protocol():
    translator = MSPTranslator()
    state = translator.translate({})
    assert state['protocol'] == 'MSP'

test("Translator sets protocol to MSP", test_translator_protocol)


def test_flight_mode_mapping():
    assert msp_to_flight_mode(1 << 1) == 'ANGLE'
    assert msp_to_flight_mode(1 << 2) == 'HORIZON'
    assert msp_to_flight_mode(1 << 36) == 'GPS_RESCUE'
    assert msp_to_flight_mode(0) == 'ACRO'

test("Flight mode flag mapping", test_flight_mode_mapping)


# ---- Summary ----
print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
