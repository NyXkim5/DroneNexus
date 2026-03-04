"""
OVERWATCH Backend -- Integration Test Suite
=============================================
Tests all REST endpoints, WebSocket streams, and command round-trips
against the live FastAPI app running in simulation mode.

Run with:
    pytest tests/test_api.py -v
    python3 tests/test_api.py
"""
import sys
import os
import json
import tempfile

# Ensure the backend root is on sys.path so "from main import ..." works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the OverwatchApp singleton at a temporary database BEFORE importing main,
# so the schema is created cleanly (the production overwatch.db may have stale
# columns from earlier migrations).
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="overwatch_test_")
os.close(_test_db_fd)
os.environ["OVERWATCH_DB_PATH"] = _test_db_path

import pytest
import pytest_asyncio
import asyncio
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient

from main import app, overwatch_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def event_loop():
    """Provide a dedicated event loop for the entire test module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def startup():
    """
    Boot the OverwatchApp subsystems (DB, mock swarm, coordinator, aggregator)
    once for all tests in this module, then tear down afterwards.

    httpx.AsyncClient + ASGITransport does NOT trigger FastAPI lifespan
    events, so we drive startup/shutdown manually here.
    """
    overwatch_app.db.db_path = _test_db_path
    await overwatch_app.startup()
    yield overwatch_app
    await overwatch_app.shutdown()


@pytest_asyncio.fixture
async def client(startup):
    """Async HTTP client wired directly into the ASGI app (no real socket)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# 1. GET /api/v1/ontology/assets -- telemetry packet list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_assets_returns_list(client: AsyncClient):
    """GET /api/v1/ontology/assets returns a JSON list of telemetry packets."""
    resp = await client.get("/api/v1/ontology/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0, "Simulation mode should have at least one asset"


@pytest.mark.asyncio
async def test_get_assets_packet_structure(client: AsyncClient):
    """Each telemetry packet contains all required top-level keys."""
    resp = await client.get("/api/v1/ontology/assets")
    packet = resp.json()[0]

    required_keys = {
        "type", "drone_id", "timestamp", "seq",
        "position", "attitude", "velocity",
        "battery", "gps", "link",
        "status", "formation",
    }
    assert required_keys.issubset(packet.keys()), (
        f"Missing keys: {required_keys - packet.keys()}"
    )


@pytest.mark.asyncio
async def test_get_assets_position_fields(client: AsyncClient):
    """Position sub-object has lat, lon, alt_msl, alt_agl."""
    resp = await client.get("/api/v1/ontology/assets")
    pos = resp.json()[0]["position"]
    for key in ("lat", "lon", "alt_msl", "alt_agl"):
        assert key in pos, f"Missing position.{key}"
        assert isinstance(pos[key], (int, float))


@pytest.mark.asyncio
async def test_get_assets_battery_fields(client: AsyncClient):
    """Battery sub-object has voltage, current, remaining_pct."""
    resp = await client.get("/api/v1/ontology/assets")
    batt = resp.json()[0]["battery"]
    for key in ("voltage", "current", "remaining_pct"):
        assert key in batt
        assert isinstance(batt[key], (int, float))


@pytest.mark.asyncio
async def test_get_assets_all_six_present(client: AsyncClient):
    """Simulation fleet has exactly 6 assets."""
    resp = await client.get("/api/v1/ontology/assets")
    drone_ids = {p["drone_id"] for p in resp.json()}
    expected = {"ALPHA-1", "BRAVO-2", "CHARLIE-3", "DELTA-4", "ECHO-5", "FOXTROT-6"}
    assert drone_ids == expected


# ---------------------------------------------------------------------------
# 2. GET /api/v1/ontology/taskforce/health -- health score
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_taskforce_health_returns_object(client: AsyncClient):
    """GET /api/v1/ontology/taskforce/health returns a JSON object with score fields."""
    resp = await client.get("/api/v1/ontology/taskforce/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert "active" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_taskforce_health_score_range(client: AsyncClient):
    """Health score is between 0 and 1."""
    data = (await client.get("/api/v1/ontology/taskforce/health")).json()
    assert 0 <= data["score"] <= 1.0


@pytest.mark.asyncio
async def test_taskforce_health_total_matches_fleet(client: AsyncClient):
    """Total should match the number of registered assets."""
    data = (await client.get("/api/v1/ontology/taskforce/health")).json()
    assert data["total"] == 6


# ---------------------------------------------------------------------------
# 3. POST /api/v1/actions/assets/ALPHA-1/launch-prep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_launch_prep_asset(client: AsyncClient):
    """Launch-prep (arm) ALPHA-1 returns status 'armed'."""
    resp = await client.post("/api/v1/actions/assets/ALPHA-1/launch-prep")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "armed"
    assert data["drone_id"] == "ALPHA-1"


@pytest.mark.asyncio
async def test_launch_prep_unknown_id(client: AsyncClient):
    """Arming a non-existent asset still succeeds (dispatcher is lenient)."""
    resp = await client.post("/api/v1/actions/assets/UNKNOWN-99/launch-prep")
    assert resp.status_code == 200
    assert resp.json()["drone_id"] == "UNKNOWN-99"


# ---------------------------------------------------------------------------
# 4. POST /api/v1/actions/assets/ALPHA-1/stand-down
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stand_down_asset(client: AsyncClient):
    """Stand-down (disarm) ALPHA-1 returns status 'disarmed'."""
    resp = await client.post("/api/v1/actions/assets/ALPHA-1/stand-down")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "disarmed"
    assert data["drone_id"] == "ALPHA-1"


# ---------------------------------------------------------------------------
# 5. POST /api/v1/actions/taskforce/launch -- with and without body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_taskforce_launch_default_altitude(client: AsyncClient):
    """Taskforce launch with no body uses default altitude (30m)."""
    resp = await client.post("/api/v1/actions/taskforce/launch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "taking_off"
    assert data["altitude"] == 30.0


@pytest.mark.asyncio
async def test_taskforce_launch_custom_altitude(client: AsyncClient):
    """Taskforce launch with explicit altitude in body."""
    resp = await client.post("/api/v1/actions/taskforce/launch", json={"altitude": 50.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "taking_off"
    assert data["altitude"] == 50.0


# ---------------------------------------------------------------------------
# 6. POST /api/v1/actions/taskforce/recover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_taskforce_recover(client: AsyncClient):
    """Recover (land) command returns status 'landing'."""
    resp = await client.post("/api/v1/actions/taskforce/recover")
    assert resp.status_code == 200
    assert resp.json()["status"] == "landing"


# ---------------------------------------------------------------------------
# 7. POST /api/v1/actions/taskforce/abort
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_taskforce_abort(client: AsyncClient):
    """Abort (emergency stop) returns status 'emergency_stop'."""
    resp = await client.post("/api/v1/actions/taskforce/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "emergency_stop"


# ---------------------------------------------------------------------------
# 8. POST /api/v1/overlays/formation -- with DIAMOND payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_formation_diamond(client: AsyncClient):
    """Setting DIAMOND formation returns confirmation."""
    resp = await client.post("/api/v1/overlays/formation", json={"formation": "DIAMOND"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "formation_set"
    assert data["formation"] == "DIAMOND"


@pytest.mark.asyncio
async def test_set_formation_invalid(client: AsyncClient):
    """Invalid formation name should return 422 validation error."""
    resp = await client.post("/api/v1/overlays/formation", json={"formation": "INVALID"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_formation_all_types(client: AsyncClient):
    """All defined overlay types are accepted."""
    for formation in ("V_FORMATION", "LINE_ABREAST", "COLUMN", "DIAMOND", "ORBIT", "SCATTER"):
        resp = await client.post("/api/v1/overlays/formation", json={"formation": formation})
        assert resp.status_code == 200, f"Formation {formation} rejected"
        assert resp.json()["formation"] == formation


# ---------------------------------------------------------------------------
# 9. POST /api/v1/actions/taskforce/set-speed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_speed(client: AsyncClient):
    """Setting speed returns confirmation with value."""
    resp = await client.post("/api/v1/actions/taskforce/set-speed", json={"speed": 15.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "speed_set"
    assert data["speed"] == 15.0


@pytest.mark.asyncio
async def test_set_speed_missing_body(client: AsyncClient):
    """Missing speed field returns 422."""
    resp = await client.post("/api/v1/actions/taskforce/set-speed", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 10. POST /api/v1/actions/taskforce/set-altitude
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_altitude(client: AsyncClient):
    """Setting altitude returns confirmation with value."""
    resp = await client.post("/api/v1/actions/taskforce/set-altitude", json={"altitude": 50.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "altitude_set"
    assert data["altitude"] == 50.0


@pytest.mark.asyncio
async def test_set_altitude_missing_body(client: AsyncClient):
    """Missing altitude field returns 422."""
    resp = await client.post("/api/v1/actions/taskforce/set-altitude", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 11. POST /api/v1/operations/create -- with waypoints
# ---------------------------------------------------------------------------

SAMPLE_WAYPOINTS = [
    {"lat": 33.6405, "lng": -117.8443, "alt": 30.0, "type": "WAYPOINT"},
    {"lat": 33.6410, "lng": -117.8450, "alt": 35.0, "type": "WAYPOINT"},
    {"lat": 33.6415, "lng": -117.8440, "alt": 30.0, "type": "WAYPOINT"},
]


@pytest.mark.asyncio
async def test_create_operation(client: AsyncClient):
    """Creating an operation with waypoints returns waypoint count."""
    resp = await client.post("/api/v1/operations/create", json={"waypoints": SAMPLE_WAYPOINTS})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["waypoint_count"] == 3


@pytest.mark.asyncio
async def test_create_operation_empty_waypoints(client: AsyncClient):
    """Empty waypoint list is rejected with 422 (at least one waypoint required)."""
    resp = await client.post("/api/v1/operations/create", json={"waypoints": []})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 12. POST /api/v1/operations/execute -- after creating an operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_operation_after_create(client: AsyncClient):
    """Execute should succeed after an operation has been created."""
    # First create an operation
    create_resp = await client.post(
        "/api/v1/operations/create", json={"waypoints": SAMPLE_WAYPOINTS}
    )
    assert create_resp.status_code == 200

    # Then execute
    exec_resp = await client.post("/api/v1/operations/execute")
    assert exec_resp.status_code == 200
    assert exec_resp.json()["status"] == "executing"


@pytest.mark.asyncio
async def test_execute_operation_without_create(client: AsyncClient):
    """Execute without a prior create returns 400 when mission list is empty."""
    # Clear any existing mission
    overwatch_app.current_mission = []
    resp = await client.post("/api/v1/operations/execute")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 13. POST /api/v1/operations/abort
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_abort_operation(client: AsyncClient):
    """Abort returns status 'aborted'."""
    resp = await client.post("/api/v1/operations/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "aborted"


# ---------------------------------------------------------------------------
# 14. GET /api/v1/activity/directives -- returns list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_directive_logs(client: AsyncClient):
    """Directive logs endpoint returns a list."""
    # Issue a command first so there is at least one log entry
    await client.post("/api/v1/actions/assets/ALPHA-1/launch-prep")

    resp = await client.get("/api/v1/activity/directives")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_get_directive_logs_structure(client: AsyncClient):
    """Each directive log entry has expected fields."""
    # Ensure at least one log entry
    await client.post("/api/v1/actions/taskforce/recover")

    resp = await client.get("/api/v1/activity/directives")
    logs = resp.json()
    if len(logs) > 0:
        entry = logs[0]
        for key in ("id", "command", "params", "timestamp"):
            assert key in entry, f"Missing key {key} in directive log entry"


@pytest.mark.asyncio
async def test_get_directive_logs_limit(client: AsyncClient):
    """Limit query parameter caps the number of returned entries."""
    resp = await client.get("/api/v1/activity/directives", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


# ---------------------------------------------------------------------------
# 15. GET /api/v1/activity/stream -- returns list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_stream(client: AsyncClient):
    """Activity stream endpoint returns a list."""
    resp = await client.get("/api/v1/activity/stream")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_activity_stream_structure(client: AsyncClient):
    """Each activity stream entry has expected fields."""
    # Trigger a command that also logs an event
    await client.post("/api/v1/actions/assets/BRAVO-2/launch-prep")

    resp = await client.get("/api/v1/activity/stream")
    events = resp.json()
    if len(events) > 0:
        entry = events[0]
        for key in ("id", "severity", "message", "timestamp"):
            assert key in entry, f"Missing key {key} in activity stream entry"


@pytest.mark.asyncio
async def test_get_activity_stream_severity_filter(client: AsyncClient):
    """Severity query parameter filters results."""
    resp = await client.get("/api/v1/activity/stream", params={"severity": "INFO"})
    assert resp.status_code == 200
    events = resp.json()
    for entry in events:
        assert entry["severity"] == "INFO"


# ---------------------------------------------------------------------------
# 16. GET /api/v1/platform/status -- mode and asset count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_status(client: AsyncClient):
    """Status endpoint returns mode and asset count."""
    resp = await client.get("/api/v1/platform/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "SIMULATION"
    assert data["drones"] == 6
    assert "ws_clients" in data


# ---------------------------------------------------------------------------
# Extra integration tests -- workflow sequences
# (These MUST come before the WebSocket tests because TestClient triggers
# a full lifespan cycle which shuts down the shared DB connection.)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_operation_workflow(client: AsyncClient):
    """
    End-to-end: launch-prep -> launch -> create operation -> execute -> abort -> recover.
    Verifies that sequential commands are all accepted.
    """
    # Launch-prep (arm)
    r = await client.post("/api/v1/actions/assets/ALPHA-1/launch-prep")
    assert r.status_code == 200

    # Launch (takeoff)
    r = await client.post("/api/v1/actions/taskforce/launch", json={"altitude": 40.0})
    assert r.status_code == 200

    # Create operation
    r = await client.post("/api/v1/operations/create", json={"waypoints": SAMPLE_WAYPOINTS})
    assert r.status_code == 200
    assert r.json()["waypoint_count"] == 3

    # Execute operation
    r = await client.post("/api/v1/operations/execute")
    assert r.status_code == 200

    # Abort operation
    r = await client.post("/api/v1/operations/abort")
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"

    # Recover (land)
    r = await client.post("/api/v1/actions/taskforce/recover")
    assert r.status_code == 200
    assert r.json()["status"] == "landing"


@pytest.mark.asyncio
async def test_launch_prep_then_stand_down_sequence(client: AsyncClient):
    """Launch-prep and immediately stand-down the same asset."""
    r1 = await client.post("/api/v1/actions/assets/CHARLIE-3/launch-prep")
    assert r1.status_code == 200
    assert r1.json()["status"] == "armed"

    r2 = await client.post("/api/v1/actions/assets/CHARLIE-3/stand-down")
    assert r2.status_code == 200
    assert r2.json()["status"] == "disarmed"


@pytest.mark.asyncio
async def test_formation_then_speed_then_altitude(client: AsyncClient):
    """Set formation, speed, and altitude in sequence."""
    r1 = await client.post("/api/v1/overlays/formation", json={"formation": "ORBIT"})
    assert r1.status_code == 200

    r2 = await client.post("/api/v1/actions/taskforce/set-speed", json={"speed": 18.0})
    assert r2.status_code == 200
    assert r2.json()["speed"] == 18.0

    r3 = await client.post("/api/v1/actions/taskforce/set-altitude", json={"altitude": 80.0})
    assert r3.status_code == 200
    assert r3.json()["altitude"] == 80.0


@pytest.mark.asyncio
async def test_directives_logged_after_operations(client: AsyncClient):
    """Verify that commands executed via REST appear in the directive log."""
    # Execute a distinctive command
    await client.post("/api/v1/actions/taskforce/abort")

    resp = await client.get("/api/v1/activity/directives")
    logs = resp.json()
    commands = [entry["command"] for entry in logs]
    assert "EMERGENCY_STOP" in commands


# ---------------------------------------------------------------------------
# 17. WebSocket /ws/v1/stream -- connect and receive telemetry
#
# starlette.testclient.TestClient manages the ASGI lifespan automatically.
# Each TestClient context creates its own startup/shutdown cycle on the
# OverwatchApp singleton, so these are self-contained.  They are placed LAST
# in the file because the shutdown phase closes the shared DB connection,
# which would break later async tests that rely on the httpx client fixture.
# ---------------------------------------------------------------------------

def test_websocket_telemetry_connect():
    """
    Connect to /ws/v1/stream.  The first message is a STATE_SYNC
    snapshot (dict); subsequent messages are bare telemetry arrays from
    the aggregator's 10Hz publish loop.

    Uses starlette.testclient.TestClient (sync) because httpx does not
    support WebSocket.
    """
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/stream") as ws:
            # First message: STATE_SYNC snapshot
            state_sync = ws.receive_json()
            assert isinstance(state_sync, dict), "First message should be a STATE_SYNC dict"
            assert state_sync.get("type") == "STATE_SYNC"
            assert "drones" in state_sync
            assert len(state_sync["drones"]) >= 1

            # Second message: bare telemetry array from publish loop
            data = ws.receive_json()
            assert isinstance(data, list), "Telemetry payload should be a JSON array"
            assert len(data) >= 1, "Should contain at least one asset packet"


def test_websocket_telemetry_packet_shape():
    """The STATE_SYNC drones array has correct wire-protocol packet shape."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/stream") as ws:
            state_sync = ws.receive_json()
            packet = state_sync["drones"][0]
            assert packet["type"] == "ASSET_STATE"
            assert "drone_id" in packet
            assert "position" in packet
            assert "battery" in packet


def test_websocket_telemetry_via_ws_compat():
    """The /ws/v1/compat endpoint also serves telemetry (STATE_SYNC first)."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            state_sync = ws.receive_json()
            assert isinstance(state_sync, dict)
            assert state_sync.get("type") == "STATE_SYNC"
            assert len(state_sync["drones"]) >= 1


# ---------------------------------------------------------------------------
# 18. WebSocket command round-trip -- send CMD, receive ACK
# ---------------------------------------------------------------------------

def test_websocket_cmd_arm_ack():
    """
    Send an ARM command via WebSocket and receive an ACK with success=True.
    """
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            # Consume the STATE_SYNC snapshot sent on connect
            ws.receive_json()

            # Send a CMD message (legacy compat)
            cmd = {
                "type": "CMD",
                "command": "ARM",
                "params": {"droneId": "ALPHA-1"},
            }
            ws.send_json(cmd)

            # The next message should be the ACK (may need to skip telemetry)
            ack = _receive_until_type(ws, "ACK", max_messages=20)
            assert ack is not None, "Never received ACK for ARM command"
            assert ack["command"] == "ARM"
            assert ack["success"] is True
            assert ack["drone_id"] == "ALPHA-1"


def test_websocket_cmd_takeoff_ack():
    """Send a TAKEOFF command and verify ACK."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            ws.receive_json()  # skip STATE_SYNC

            cmd = {
                "type": "CMD",
                "command": "TAKEOFF",
                "params": {"altitude": 40.0},
            }
            ws.send_json(cmd)

            ack = _receive_until_type(ws, "ACK", max_messages=20)
            assert ack is not None, "Never received ACK for TAKEOFF command"
            assert ack["command"] == "TAKEOFF"
            assert ack["success"] is True


def test_websocket_cmd_emergency_stop_ack():
    """Send EMERGENCY_STOP and verify ACK."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            ws.receive_json()  # skip STATE_SYNC

            cmd = {
                "type": "CMD",
                "command": "EMERGENCY_STOP",
                "params": {},
            }
            ws.send_json(cmd)

            ack = _receive_until_type(ws, "ACK", max_messages=20)
            assert ack is not None, "Never received ACK for EMERGENCY_STOP"
            assert ack["command"] == "EMERGENCY_STOP"
            assert ack["success"] is True


def test_websocket_cmd_set_formation_ack():
    """Send SET_FORMATION via WebSocket and verify ACK."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            ws.receive_json()  # skip STATE_SYNC

            cmd = {
                "type": "CMD",
                "command": "SET_FORMATION",
                "params": {"formation": "COLUMN"},
            }
            ws.send_json(cmd)

            ack = _receive_until_type(ws, "ACK", max_messages=20)
            assert ack is not None, "Never received ACK for SET_FORMATION"
            assert ack["command"] == "SET_FORMATION"
            assert ack["success"] is True


def test_websocket_unknown_command_ack():
    """Unknown command yields an ACK with success=False and a message."""
    with TestClient(app) as ws_client:
        with ws_client.websocket_connect("/ws/v1/compat") as ws:
            ws.receive_json()  # skip STATE_SYNC

            cmd = {
                "type": "CMD",
                "command": "SELF_DESTRUCT",
                "params": {},
            }
            ws.send_json(cmd)

            ack = _receive_until_type(ws, "ACK", max_messages=20)
            assert ack is not None, "Never received ACK for unknown command"
            assert ack["command"] == "SELF_DESTRUCT"
            assert ack["success"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _receive_until_type(ws, msg_type: str, max_messages: int = 20):
    """
    Read messages from the WebSocket until we find one whose 'type' field
    matches *msg_type*, or until we have read *max_messages* without a
    match (return None).

    Telemetry broadcasts arrive as JSON arrays; ACKs arrive as JSON
    objects.  We skip the telemetry arrays to find the ACK.
    """
    for _ in range(max_messages):
        raw = ws.receive_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Telemetry comes as a list; commands/ACKs come as dicts
        if isinstance(data, dict) and data.get("type") == msg_type:
            return data

    return None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def teardown_module():
    """Remove the temporary test database."""
    try:
        os.unlink(_test_db_path)
    except OSError:
        pass
    # Clean up WAL/SHM files that SQLite may leave behind
    for suffix in ("-shm", "-wal"):
        try:
            os.unlink(_test_db_path + suffix)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Entry point -- allow running directly with python3
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
