"""
Tests for the DroneNexus extension/plugin system.

Covers registration, dependency resolution, lifecycle management,
exports, and error handling.
"""
from __future__ import annotations

import asyncio
import pytest
from typing import Any
from unittest.mock import AsyncMock

from extensions.base import Extension, ExtensionError, ExtensionState
from extensions.manager import ExtensionManager


# ---------------------------------------------------------------------------
# Test extensions
# ---------------------------------------------------------------------------

class TestExtA(Extension):
    """Standalone extension with no dependencies."""

    name = "ext_a"
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.loaded = False
        self.unloaded = False

    async def load(self, app_context: dict[str, Any]) -> None:
        self.loaded = True

    async def unload(self) -> None:
        self.unloaded = True
        self.loaded = False

    def exports(self) -> dict[str, Any]:
        return {"greet": lambda: "hello from A"}


class TestExtB(Extension):
    """Extension that depends on ext_a."""

    name = "ext_b"
    dependencies: tuple[str, ...] = ("ext_a",)

    def __init__(self) -> None:
        super().__init__()
        self.loaded = False

    async def load(self, app_context: dict[str, Any]) -> None:
        self.loaded = True

    async def unload(self) -> None:
        self.loaded = False

    def exports(self) -> dict[str, Any]:
        return {"value": 42}


class TestExtC(Extension):
    """Extension that depends on ext_b (transitive dep on ext_a)."""

    name = "ext_c"
    dependencies: tuple[str, ...] = ("ext_b",)

    async def load(self, app_context: dict[str, Any]) -> None:
        pass


class CircularX(Extension):
    """Circular dependency: X -> Y -> X."""

    name = "circ_x"
    dependencies: tuple[str, ...] = ("circ_y",)

    async def load(self, app_context: dict[str, Any]) -> None:
        pass


class CircularY(Extension):
    """Circular dependency: Y -> X -> Y."""

    name = "circ_y"
    dependencies: tuple[str, ...] = ("circ_x",)

    async def load(self, app_context: dict[str, Any]) -> None:
        pass


class FailingExt(Extension):
    """Extension whose load() always raises."""

    name = "failing"

    async def load(self, app_context: dict[str, Any]) -> None:
        raise RuntimeError("load exploded")


class RunningExt(Extension):
    """Extension with a long-running task."""

    name = "runner"
    dependencies: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.tick_count = 0

    async def load(self, app_context: dict[str, Any]) -> None:
        pass

    async def run(self, app_context: dict[str, Any]) -> None:
        while True:
            self.tick_count += 1
            await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_extension(self) -> None:
        mgr = ExtensionManager()
        ext = TestExtA()
        mgr.register(ext)
        assert mgr.get("ext_a") is ext

    def test_duplicate_registration_raises(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtA())
        with pytest.raises(ExtensionError, match="already registered"):
            mgr.register(TestExtA())

    def test_get_unknown_raises(self) -> None:
        mgr = ExtensionManager()
        with pytest.raises(ExtensionError, match="not found"):
            mgr.get("nonexistent")


class TestDependencyResolution:
    @pytest.mark.asyncio
    async def test_load_order_respects_dependencies(self) -> None:
        mgr = ExtensionManager()
        ext_b = TestExtB()
        ext_a = TestExtA()
        # Register B before A to prove ordering is computed, not insertion-based
        mgr.register(ext_b)
        mgr.register(ext_a)
        await mgr.load_all()

        loaded = mgr.loaded_extensions
        assert loaded.index("ext_a") < loaded.index("ext_b")

    @pytest.mark.asyncio
    async def test_transitive_dependencies(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtC())
        mgr.register(TestExtB())
        mgr.register(TestExtA())
        await mgr.load_all()

        loaded = mgr.loaded_extensions
        assert loaded.index("ext_a") < loaded.index("ext_b")
        assert loaded.index("ext_b") < loaded.index("ext_c")

    @pytest.mark.asyncio
    async def test_circular_dependency_raises(self) -> None:
        mgr = ExtensionManager()
        mgr.register(CircularX())
        mgr.register(CircularY())
        with pytest.raises(ExtensionError, match="Circular dependency"):
            await mgr.load_all()

    @pytest.mark.asyncio
    async def test_missing_dependency_raises(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtB())  # depends on ext_a which is not registered
        with pytest.raises(ExtensionError, match="Missing dependency"):
            await mgr.load_all()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_load_sets_state(self) -> None:
        mgr = ExtensionManager()
        ext = TestExtA()
        mgr.register(ext)
        assert ext.state == ExtensionState.UNLOADED
        await mgr.load_all()
        assert ext.state == ExtensionState.LOADED
        assert ext.loaded is True

    @pytest.mark.asyncio
    async def test_unload_reverses_order(self) -> None:
        mgr = ExtensionManager()
        ext_a = TestExtA()
        ext_b = TestExtB()
        mgr.register(ext_a)
        mgr.register(ext_b)
        await mgr.load_all()

        unload_order: list[str] = []
        original_unload_a = ext_a.unload
        original_unload_b = ext_b.unload

        async def track_a() -> None:
            unload_order.append("ext_a")
            await original_unload_a()

        async def track_b() -> None:
            unload_order.append("ext_b")
            await original_unload_b()

        ext_a.unload = track_a  # type: ignore[assignment]
        ext_b.unload = track_b  # type: ignore[assignment]

        await mgr.unload_all()
        # B should unload before A (reverse of load order)
        assert unload_order == ["ext_b", "ext_a"]
        assert ext_a.state == ExtensionState.UNLOADED
        assert ext_b.state == ExtensionState.UNLOADED

    @pytest.mark.asyncio
    async def test_load_failure_sets_error_state(self) -> None:
        mgr = ExtensionManager()
        ext = FailingExt()
        mgr.register(ext)
        with pytest.raises(ExtensionError, match="load exploded"):
            await mgr.load_all()
        assert ext.state == ExtensionState.ERROR


class TestExports:
    @pytest.mark.asyncio
    async def test_get_export(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtA())
        await mgr.load_all()

        greet = mgr.get_export("ext_a", "greet")
        assert greet() == "hello from A"

    @pytest.mark.asyncio
    async def test_get_export_missing_raises(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtA())
        await mgr.load_all()

        with pytest.raises(ExtensionError, match="no export"):
            mgr.get_export("ext_a", "nonexistent")

    @pytest.mark.asyncio
    async def test_get_export_from_unknown_ext_raises(self) -> None:
        mgr = ExtensionManager()
        with pytest.raises(ExtensionError, match="not found"):
            mgr.get_export("nope", "whatever")


class TestRunTasks:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        mgr = ExtensionManager()
        ext = RunningExt()
        mgr.register(ext)
        await mgr.load_all()
        await mgr.start_all()

        assert ext.state == ExtensionState.RUNNING
        # Let it tick a few times
        await asyncio.sleep(0.15)
        assert ext.tick_count >= 2

        await mgr.stop_all()
        assert ext.state == ExtensionState.LOADED
        final_count = ext.tick_count
        await asyncio.sleep(0.1)
        # No more ticks after stop
        assert ext.tick_count == final_count

    @pytest.mark.asyncio
    async def test_loaded_extensions_property(self) -> None:
        mgr = ExtensionManager()
        mgr.register(TestExtA())
        mgr.register(TestExtB())

        assert mgr.loaded_extensions == []
        await mgr.load_all()
        assert set(mgr.loaded_extensions) == {"ext_a", "ext_b"}

        await mgr.unload_all()
        assert mgr.loaded_extensions == []
