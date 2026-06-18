"""
Tests for DroneRegistry and ConnectionRegistry integration in SwarmCoordinator.

Verifies backward compatibility with raw dicts, proper delegation to the
typed registry, lifecycle callbacks, and stale connection detection.
"""
from __future__ import annotations

import logging
import time
import pytest
from typing import Any
from unittest.mock import MagicMock

from swarm.coordinator import SwarmCoordinator
from telemetry.collector import DroneState
from registries.drone_registry import DroneRegistry
from registries.connection_registry import ConnectionInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides: Any) -> Any:
    """Build a minimal settings object with sane defaults."""
    defaults = {
        "safety_bubble_m": 5.0,
        "min_vertical_sep_m": 3.0,
        "geofence_vertices": [
            (35.0, -97.0),
            (35.0, -96.0),
            (36.0, -96.0),
            (36.0, -97.0),
        ],
        "max_altitude_m": 120.0,
        "default_formation": "V_FORMATION",
        "formation_spacing_m": 15.0,
        "telemetry_rate_hz": 10,
        "max_speed_ms": 20.0,
        "min_altitude_m": 5.0,
        "low_battery_critical_pct": 10,
        "low_battery_warning_pct": 25,
        "heartbeat_timeout_ms": 3000,
    }
    defaults.update(overrides)
    obj = MagicMock()
    for key, val in defaults.items():
        setattr(obj, key, val)
    return obj


def _make_drone(
    drone_id: str = "ALPHA-1",
    lat: float = 35.5,
    lon: float = -96.5,
    alt_msl: float = 30.0,
    in_air: bool = True,
) -> DroneState:
    """Create a DroneState with required fields populated."""
    return DroneState(
        drone_id=drone_id,
        lat=lat,
        lon=lon,
        alt_msl=alt_msl,
        in_air=in_air,
    )


# ---------------------------------------------------------------------------
# Tests: backward compatibility with dict
# ---------------------------------------------------------------------------

class TestDictBackwardCompat:
    def test_accepts_dict_and_wraps_in_registry(self) -> None:
        settings = _make_settings()
        leader = _make_drone("ALPHA-1")
        wingman = _make_drone("BRAVO-2")
        raw_dict = {"ALPHA-1": leader, "BRAVO-2": wingman}

        coord = SwarmCoordinator(settings, raw_dict)

        assert isinstance(coord._registry, DroneRegistry)
        assert coord._registry.get("ALPHA-1") is leader
        assert coord._registry.get("BRAVO-2") is wingman

    def test_empty_dict_produces_empty_registry(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        assert len(coord._registry) == 0

    def test_drone_states_property_returns_dict(self) -> None:
        settings = _make_settings()
        leader = _make_drone("ALPHA-1")
        coord = SwarmCoordinator(settings, {"ALPHA-1": leader})

        result = coord.drone_states
        assert isinstance(result, dict)
        assert "ALPHA-1" in result
        assert result["ALPHA-1"] is leader


# ---------------------------------------------------------------------------
# Tests: DroneRegistry passed directly
# ---------------------------------------------------------------------------

class TestDroneRegistryDirect:
    def test_accepts_drone_registry_directly(self) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        leader = _make_drone("ALPHA-1")
        registry.add("ALPHA-1", leader)

        coord = SwarmCoordinator(settings, registry)

        assert coord._registry is registry
        assert coord._registry.get("ALPHA-1") is leader

    def test_drone_states_property_reflects_registry(self) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        leader = _make_drone("ALPHA-1")
        registry.add("ALPHA-1", leader)

        coord = SwarmCoordinator(settings, registry)
        view = coord.drone_states
        assert view == {"ALPHA-1": leader}

    def test_drone_states_property_returns_snapshot(self) -> None:
        """Mutating the returned dict must not affect the registry."""
        settings = _make_settings()
        registry = DroneRegistry()
        registry.add("ALPHA-1", _make_drone("ALPHA-1"))

        coord = SwarmCoordinator(settings, registry)
        view = coord.drone_states
        view["GHOST"] = _make_drone("GHOST")

        assert coord._registry.get("GHOST") is None


# ---------------------------------------------------------------------------
# Tests: on_added / on_removed callbacks
# ---------------------------------------------------------------------------

class TestRegistryCallbacks:
    def test_on_added_callback_fires(self) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        coord = SwarmCoordinator(settings, registry)

        added_ids: list[str] = []
        coord._registry.on_added.append(lambda did, _: added_ids.append(did))

        new_drone = _make_drone("CHARLIE-3")
        coord._registry.add("CHARLIE-3", new_drone)

        assert "CHARLIE-3" in added_ids

    def test_on_removed_callback_fires(self) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        drone = _make_drone("ALPHA-1")
        registry.add("ALPHA-1", drone)

        coord = SwarmCoordinator(settings, registry)

        removed_ids: list[str] = []
        coord._registry.on_removed.append(lambda did, _: removed_ids.append(did))

        coord._registry.remove("ALPHA-1")
        assert "ALPHA-1" in removed_ids

    def test_builtin_on_added_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        coord = SwarmCoordinator(settings, registry)

        with caplog.at_level(logging.INFO, logger="overwatch.coordinator"):
            coord._registry.add("DELTA-4", _make_drone("DELTA-4"))

        assert any("Drone joined swarm: DELTA-4" in r.message for r in caplog.records)

    def test_builtin_on_removed_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _make_settings()
        registry = DroneRegistry()
        registry.add("ALPHA-1", _make_drone("ALPHA-1"))
        coord = SwarmCoordinator(settings, registry)

        with caplog.at_level(logging.INFO, logger="overwatch.coordinator"):
            coord._registry.remove("ALPHA-1")

        assert any("Drone left swarm: ALPHA-1" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: ConnectionRegistry integration
# ---------------------------------------------------------------------------

class TestConnectionRegistry:
    def test_register_connection(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        coord.register_connection("conn-1", "ALPHA-1", "mavlink")

        conn = coord._connections.get("conn-1")
        assert conn is not None
        assert conn.drone_id == "ALPHA-1"
        assert conn.protocol == "mavlink"
        assert conn.status == "connected"

    def test_unregister_connection(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        coord.register_connection("conn-1", "ALPHA-1", "mavlink")
        coord.unregister_connection("conn-1")

        assert coord._connections.get("conn-1") is None

    def test_unregister_missing_raises(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        with pytest.raises(KeyError):
            coord.unregister_connection("ghost")


# ---------------------------------------------------------------------------
# Tests: stale connection detection
# ---------------------------------------------------------------------------

class TestStaleConnectionDetection:
    def test_stale_connections_detected(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        now = time.monotonic()
        stale_conn = ConnectionInfo(
            connection_id="conn-old",
            drone_id="ALPHA-1",
            protocol="mavlink",
            connected_at=now - 60,
            last_heartbeat=now - 30,
            status="connected",
        )
        coord._connections.add("conn-old", stale_conn)

        stale = coord._connections.stale(10.0)
        assert len(stale) == 1
        assert stale[0].connection_id == "conn-old"

    def test_fresh_connections_not_flagged(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        coord.register_connection("conn-fresh", "BRAVO-2", "msp")

        stale = coord._connections.stale(10.0)
        assert len(stale) == 0

    def test_tick_stale_connections_logs_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})

        now = time.monotonic()
        stale_conn = ConnectionInfo(
            connection_id="conn-stale",
            drone_id="ALPHA-1",
            protocol="mavlink",
            connected_at=now - 60,
            last_heartbeat=now - 15,
            status="connected",
        )
        coord._connections.add("conn-stale", stale_conn)

        with caplog.at_level(logging.WARNING, logger="overwatch.coordinator"):
            coord._tick_stale_connections()

        assert any("Stale connection: conn-stale" in r.message for r in caplog.records)
