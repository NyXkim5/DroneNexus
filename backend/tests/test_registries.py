"""Tests for the typed registry pattern."""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from registries.base import Registry
from registries.drone_registry import DroneRegistry
from registries.connection_registry import ConnectionInfo, ConnectionRegistry


# ---------------------------------------------------------------------------
# Base Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    """Tests for the generic Registry[T] base class."""

    def test_add_and_get(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        assert reg.get("a") == "alpha"

    def test_get_missing_returns_none(self) -> None:
        reg: Registry[str] = Registry()
        assert reg.get("missing") is None

    def test_remove(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        removed = reg.remove("a")
        assert removed == "alpha"
        assert reg.get("a") is None

    def test_update(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        reg.update("a", "ALPHA")
        assert reg.get("a") == "ALPHA"

    def test_duplicate_add_raises(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        with pytest.raises(KeyError, match="already exists"):
            reg.add("a", "beta")

    def test_missing_remove_raises(self) -> None:
        reg: Registry[str] = Registry()
        with pytest.raises(KeyError, match="not found"):
            reg.remove("ghost")

    def test_missing_update_raises(self) -> None:
        reg: Registry[str] = Registry()
        with pytest.raises(KeyError, match="not found"):
            reg.update("ghost", "value")

    def test_contains(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        assert "a" in reg
        assert "b" not in reg

    def test_len(self) -> None:
        reg: Registry[str] = Registry()
        assert len(reg) == 0
        reg.add("a", "alpha")
        reg.add("b", "beta")
        assert len(reg) == 2

    def test_iter(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("b", "beta")
        reg.add("a", "alpha")
        keys = list(reg)
        assert set(keys) == {"a", "b"}

    def test_items(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        reg.add("b", "beta")
        items = dict(reg.items())
        assert items == {"a": "alpha", "b": "beta"}

    def test_values(self) -> None:
        reg: Registry[str] = Registry()
        reg.add("a", "alpha")
        result = list(reg.values())
        assert result == ["alpha"]

    def test_on_added_callback(self) -> None:
        reg: Registry[str] = Registry()
        cb = MagicMock()
        reg.on_added.append(cb)
        reg.add("a", "alpha")
        cb.assert_called_once_with("a", "alpha")

    def test_on_removed_callback(self) -> None:
        reg: Registry[str] = Registry()
        cb = MagicMock()
        reg.on_removed.append(cb)
        reg.add("a", "alpha")
        reg.remove("a")
        cb.assert_called_once_with("a", "alpha")

    def test_on_updated_callback(self) -> None:
        reg: Registry[str] = Registry()
        cb = MagicMock()
        reg.on_updated.append(cb)
        reg.add("a", "alpha")
        reg.update("a", "ALPHA")
        cb.assert_called_once_with("a", "ALPHA")

    def test_callback_exception_does_not_propagate(self) -> None:
        reg: Registry[str] = Registry()
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        reg.on_added.append(bad_cb)
        reg.add("a", "alpha")  # should not raise
        assert reg.get("a") == "alpha"


# ---------------------------------------------------------------------------
# DroneRegistry tests
# ---------------------------------------------------------------------------


def _make_drone(drone_id: str, fix_type: str = "3D_FIX",
                lat: float = 0.0, lon: float = 0.0,
                alt_msl: float = 0.0) -> object:
    """Build a minimal DroneState-like object for testing."""
    from telemetry.collector import DroneState
    ds = DroneState(drone_id=drone_id)
    ds.fix_type = fix_type
    ds.lat = lat
    ds.lon = lon
    ds.alt_msl = alt_msl
    return ds


class TestDroneRegistry:

    def test_active_drones(self) -> None:
        reg = DroneRegistry()
        assert reg.active_drones == 0
        reg.add("d1", _make_drone("d1"))  # type: ignore[arg-type]
        reg.add("d2", _make_drone("d2"))  # type: ignore[arg-type]
        assert reg.active_drones == 2

    def test_get_by_status(self) -> None:
        reg = DroneRegistry()
        reg.add("d1", _make_drone("d1", fix_type="3D_FIX"))  # type: ignore[arg-type]
        reg.add("d2", _make_drone("d2", fix_type="NO_FIX"))  # type: ignore[arg-type]
        reg.add("d3", _make_drone("d3", fix_type="3D_FIX"))  # type: ignore[arg-type]
        result = reg.get_by_status("3D_FIX")
        assert len(result) == 2

    def test_get_positions(self) -> None:
        reg = DroneRegistry()
        reg.add("d1", _make_drone("d1", lat=1.0, lon=2.0, alt_msl=3.0))  # type: ignore[arg-type]
        positions = reg.get_positions()
        assert positions == {"d1": (1.0, 2.0, 3.0)}


# ---------------------------------------------------------------------------
# ConnectionRegistry tests
# ---------------------------------------------------------------------------


def _make_conn(conn_id: str, drone_id: str = "d1",
               protocol: str = "mavlink",
               heartbeat_offset: float = 0.0) -> ConnectionInfo:
    now = time.monotonic()
    return ConnectionInfo(
        connection_id=conn_id,
        drone_id=drone_id,
        protocol=protocol,
        connected_at=now,
        last_heartbeat=now - heartbeat_offset,
        status="connected",
    )


class TestConnectionRegistry:

    def test_stale_detection(self) -> None:
        reg = ConnectionRegistry()
        fresh = _make_conn("c1", heartbeat_offset=1.0)
        stale = _make_conn("c2", heartbeat_offset=30.0)
        reg.add("c1", fresh)
        reg.add("c2", stale)
        result = reg.stale(timeout_s=10.0)
        assert len(result) == 1
        assert result[0].connection_id == "c2"

    def test_get_by_protocol(self) -> None:
        reg = ConnectionRegistry()
        reg.add("c1", _make_conn("c1", protocol="mavlink"))
        reg.add("c2", _make_conn("c2", protocol="msp"))
        reg.add("c3", _make_conn("c3", protocol="mavlink"))
        result = reg.get_by_protocol("mavlink")
        assert len(result) == 2

    def test_stale_empty_registry(self) -> None:
        reg = ConnectionRegistry()
        assert reg.stale(timeout_s=5.0) == []
