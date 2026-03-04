"""Tests for wire protocol models."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import (
    AssetStatePacket, Position, Attitude, Velocity, Battery,
    GPS, Link, Formation, OffsetVector, OperationalStatus, AssetClassification,
    DirectivePacket, DirectiveType, MessageType, OverlayType,
)


def test_telemetry_packet_serialization():
    packet = AssetStatePacket(
        type="ASSET_STATE",
        drone_id="ALPHA-1",
        timestamp="2025-02-11T12:34:56.789Z",
        seq=42,
        position=Position(lat=33.6405, lon=-117.8443, alt_msl=135.0, alt_agl=120.0),
        attitude=Attitude(roll=2.5, pitch=-1.2, yaw=180.0),
        velocity=Velocity(ground_speed=12.0, vertical_speed=0.5, heading=180.0),
        battery=Battery(voltage=22.5, current=10.2, remaining_pct=85.0),
        gps=GPS(fix_type="3D-RTK", satellites=15, hdop=0.8),
        link=Link(rssi=90, quality=95, latency_ms=25),
        status=OperationalStatus.NOMINAL,
        formation=Formation(
            role=AssetClassification.PRIMARY,
            offset_vector=OffsetVector(dx=0, dy=0),
            cohesion=0.95,
        ),
    )

    data = packet.model_dump(mode="json")

    # Verify exact field names the HUD reads
    assert data["type"] == "ASSET_STATE"
    assert data["drone_id"] == "ALPHA-1"
    assert data["position"]["lat"] == 33.6405
    assert data["position"]["lon"] == -117.8443  # NOT "lng"
    assert data["position"]["alt_agl"] == 120.0
    assert data["attitude"]["roll"] == 2.5
    assert data["velocity"]["ground_speed"] == 12.0
    assert data["velocity"]["vertical_speed"] == 0.5
    assert data["battery"]["remaining_pct"] == 85.0
    assert data["gps"]["satellites"] == 15
    assert data["link"]["rssi"] == 90
    assert data["link"]["quality"] == 95
    assert data["link"]["latency_ms"] == 25
    assert data["status"] == "NOMINAL"
    assert data["formation"]["role"] == "PRIMARY"
    assert data["formation"]["offset_vector"]["dx"] == 0
    assert data["formation"]["cohesion"] == 0.95


def test_command_packet():
    cmd = DirectivePacket(
        type=MessageType.DIRECTIVE,
        command=DirectiveType.LAUNCH_PREP,
        params={"droneId": "ALPHA-1"},
    )
    data = cmd.model_dump(mode="json")
    assert data["type"] == "DIRECTIVE"
    assert data["command"] == "LAUNCH_PREP"
    assert data["params"]["droneId"] == "ALPHA-1"


def test_all_formation_types():
    for ft in OverlayType:
        assert isinstance(ft.value, str)


def test_all_asset_classifications():
    expected = ["PRIMARY", "ESCORT", "ISR", "LOGISTICS", "OVERWATCH"]
    for role in expected:
        assert AssetClassification(role).value == role


def test_all_operational_statuses():
    expected = ["NOMINAL", "DEGRADED", "COMMS_DEGRADED", "RTB", "GROUNDED", "OFFLINE", "ISR_SOLO"]
    for status in expected:
        assert OperationalStatus(status).value == status


if __name__ == "__main__":
    test_telemetry_packet_serialization()
    print("  PASS  telemetry_packet_serialization")
    test_command_packet()
    print("  PASS  command_packet")
    test_all_formation_types()
    print("  PASS  all_formation_types")
    test_all_asset_classifications()
    print("  PASS  all_asset_classifications")
    test_all_operational_statuses()
    print("  PASS  all_operational_statuses")
    print(f"\n5 tests passed")
