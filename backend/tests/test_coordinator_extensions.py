"""
Tests for the SwarmCoordinator extension system integration.

Verifies that the coordinator delegates collision, geofence, and alert
checks to the ExtensionManager while preserving backward compatibility.
"""
from __future__ import annotations

import asyncio
import logging
import pytest
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from extensions.base import Extension, ExtensionState
from extensions.manager import ExtensionManager
from swarm.coordinator import SwarmCoordinator
from telemetry.collector import DroneState

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


class DummyExtension(Extension):
    """Minimal extension for registration tests."""

    name = "dummy"
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.loaded = False

    async def load(self, app_context: dict[str, Any]) -> None:
        self.loaded = True

    async def unload(self) -> None:
        self.loaded = False

    def exports(self) -> dict[str, Any]:
        return {"ping": lambda: "pong"}


# ---------------------------------------------------------------------------
# Tests: extension creation and loading
# ---------------------------------------------------------------------------

class TestCoordinatorCreatesExtensions:
    def test_ext_manager_exists(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        assert isinstance(coord._ext_manager, ExtensionManager)

    def test_default_extensions_registered(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        mgr = coord._ext_manager
        assert mgr.get("collision") is not None
        assert mgr.get("geofence") is not None
        assert mgr.get("alerts") is not None

    @pytest.mark.asyncio
    async def test_extensions_loaded_on_start(self) -> None:
        settings = _make_settings()
        states: Dict[str, DroneState] = {}
        coord = SwarmCoordinator(settings, states)

        await coord.start()
        try:
            loaded = coord._ext_manager.loaded_extensions
            assert "collision" in loaded
            assert "geofence" in loaded
            assert "alerts" in loaded
        finally:
            await coord.stop()


# ---------------------------------------------------------------------------
# Tests: collision checks through extension
# ---------------------------------------------------------------------------

class TestCollisionThroughExtension:
    @pytest.mark.asyncio
    async def test_collision_check_uses_extension_export(self) -> None:
        settings = _make_settings()
        leader = _make_drone("ALPHA-1", lat=35.5, lon=-96.5)
        states = {"ALPHA-1": leader}
        coord = SwarmCoordinator(settings, states)
        await coord.start()

        try:
            check_fn = coord._ext_manager.get_export("collision", "check_all")
            result = check_fn(list(states.values()))
            assert isinstance(result, list)
        finally:
            await coord.stop()

    @pytest.mark.asyncio
    async def test_backward_compat_collision_attr(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        await coord.start()
        try:
            assert hasattr(coord, "collision")
            assert hasattr(coord.collision, "check_all")
        finally:
            await coord.stop()


# ---------------------------------------------------------------------------
# Tests: geofence checks through extension
# ---------------------------------------------------------------------------

class TestGeofenceThroughExtension:
    @pytest.mark.asyncio
    async def test_geofence_check_uses_extension_export(self) -> None:
        settings = _make_settings()
        leader = _make_drone("ALPHA-1", lat=35.5, lon=-96.5)
        states = {"ALPHA-1": leader}
        coord = SwarmCoordinator(settings, states)
        await coord.start()

        try:
            check_fn = coord._ext_manager.get_export("geofence", "check_all")
            result = check_fn(list(states.values()))
            assert isinstance(result, list)
        finally:
            await coord.stop()

    @pytest.mark.asyncio
    async def test_backward_compat_geofence_attr(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        await coord.start()
        try:
            assert hasattr(coord, "geofence")
            assert hasattr(coord.geofence, "check_all")
            assert hasattr(coord.geofence, "check")
        finally:
            await coord.stop()


# ---------------------------------------------------------------------------
# Tests: custom extension registration
# ---------------------------------------------------------------------------

class TestCustomExtensions:
    def test_register_extension_delegates_to_manager(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        dummy = DummyExtension()
        coord.register_extension(dummy)
        assert coord._ext_manager.get("dummy") is dummy

    @pytest.mark.asyncio
    async def test_custom_extension_loaded_on_start(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        dummy = DummyExtension()
        coord.register_extension(dummy)
        await coord.start()
        try:
            assert dummy.loaded is True
            assert "dummy" in coord._ext_manager.loaded_extensions
        finally:
            await coord.stop()

    @pytest.mark.asyncio
    async def test_custom_extension_exports_accessible(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        dummy = DummyExtension()
        coord.register_extension(dummy)
        await coord.start()
        try:
            ping = coord._ext_manager.get_export("dummy", "ping")
            assert ping() == "pong"
        finally:
            await coord.stop()


# ---------------------------------------------------------------------------
# Tests: stop/unload lifecycle
# ---------------------------------------------------------------------------

class TestStopUnloadLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cancels_extension_tasks(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        await coord.start()

        alerts_ext = coord._ext_manager.get("alerts")
        assert alerts_ext.state == ExtensionState.RUNNING

        await coord.stop()
        assert alerts_ext.state == ExtensionState.UNLOADED

    @pytest.mark.asyncio
    async def test_stop_unloads_all_extensions(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        await coord.start()
        await coord.stop()
        assert coord._ext_manager.loaded_extensions == []

    @pytest.mark.asyncio
    async def test_tick_task_cancelled_on_stop(self) -> None:
        settings = _make_settings()
        coord = SwarmCoordinator(settings, {})
        await coord.start()
        assert coord._task is not None
        await coord.stop()
        assert coord._task.cancelled() or coord._task.done()
