"""
ExtensionManager -- loads, starts, stops, and unloads extensions
in dependency order. Provides access to extension exports.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from extensions.base import Extension, ExtensionError, ExtensionState

logger = logging.getLogger("dronenexus.extensions.manager")


class ExtensionManager:
    """Registry and lifecycle manager for DroneNexus extensions."""

    def __init__(self) -> None:
        self._extensions: dict[str, Extension] = {}
        self._load_order: list[str] = []
        self._run_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, extension: Extension) -> None:
        """Register an extension. Raises if name already taken."""
        if extension.name in self._extensions:
            raise ExtensionError(
                f"Extension '{extension.name}' is already registered"
            )
        self._extensions[extension.name] = extension
        logger.info("Registered extension: %s", extension.name)

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def _resolve_order(self) -> list[str]:
        """Topological sort of extensions by dependencies.

        Raises ExtensionError on circular or missing dependencies.
        """
        order: list[str] = []
        visited: set[str] = set()
        in_stack: set[str] = set()

        def visit(name: str) -> None:
            if name in in_stack:
                raise ExtensionError(
                    f"Circular dependency detected involving '{name}'"
                )
            if name in visited:
                return
            if name not in self._extensions:
                raise ExtensionError(
                    f"Missing dependency: '{name}'"
                )
            in_stack.add(name)
            for dep in self._extensions[name].dependencies:
                visit(dep)
            in_stack.remove(name)
            visited.add(name)
            order.append(name)

        for name in self._extensions:
            visit(name)

        return order

    # ------------------------------------------------------------------
    # Lifecycle: load / unload
    # ------------------------------------------------------------------

    async def load_all(self, app_context: dict[str, Any] | None = None) -> None:
        """Load all registered extensions in dependency order."""
        if app_context is None:
            app_context = {}
        self._load_order = self._resolve_order()

        for name in self._load_order:
            ext = self._extensions[name]
            ext.state = ExtensionState.LOADING
            try:
                await ext.load(app_context)
                ext.state = ExtensionState.LOADED
                logger.info("Loaded extension: %s", name)
            except Exception as exc:
                ext.state = ExtensionState.ERROR
                raise ExtensionError(
                    f"Failed to load extension '{name}': {exc}"
                ) from exc

    async def unload_all(self) -> None:
        """Unload all extensions in reverse load order."""
        for name in reversed(self._load_order):
            ext = self._extensions[name]
            try:
                await ext.unload()
                ext.state = ExtensionState.UNLOADED
                logger.info("Unloaded extension: %s", name)
            except Exception as exc:
                ext.state = ExtensionState.ERROR
                logger.error("Error unloading '%s': %s", name, exc)
        self._load_order = []

    # ------------------------------------------------------------------
    # Lifecycle: start / stop run() tasks
    # ------------------------------------------------------------------

    async def start_all(self, app_context: dict[str, Any] | None = None) -> None:
        """Start run() tasks for all loaded extensions."""
        if app_context is None:
            app_context = {}
        for name in self._load_order:
            ext = self._extensions[name]
            if ext.state != ExtensionState.LOADED:
                continue
            task = asyncio.create_task(
                self._run_extension(ext, app_context), name=f"ext:{name}"
            )
            self._run_tasks[name] = task
            ext.state = ExtensionState.RUNNING
            logger.info("Started extension: %s", name)

    async def _run_extension(
        self, ext: Extension, app_context: dict[str, Any]
    ) -> None:
        """Wrapper that catches errors from an extension run loop."""
        try:
            await ext.run(app_context)
        except asyncio.CancelledError:
            logger.info("Extension '%s' run task cancelled", ext.name)
        except Exception as exc:
            ext.state = ExtensionState.ERROR
            logger.error("Extension '%s' run error: %s", ext.name, exc)

    async def stop_all(self) -> None:
        """Cancel all running extension tasks."""
        for name, task in self._run_tasks.items():
            task.cancel()
        if self._run_tasks:
            await asyncio.gather(
                *self._run_tasks.values(), return_exceptions=True
            )
        for name in self._run_tasks:
            ext = self._extensions[name]
            if ext.state == ExtensionState.RUNNING:
                ext.state = ExtensionState.LOADED
        self._run_tasks.clear()
        logger.info("All extension run tasks stopped")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, name: str) -> Extension:
        """Get a registered extension by name."""
        if name not in self._extensions:
            raise ExtensionError(f"Extension '{name}' not found")
        return self._extensions[name]

    def get_export(self, ext_name: str, export_name: str) -> Any:
        """Get a specific export from a loaded extension."""
        ext = self.get(ext_name)
        exports = ext.exports()
        if export_name not in exports:
            raise ExtensionError(
                f"Extension '{ext_name}' has no export '{export_name}'"
            )
        return exports[export_name]

    @property
    def loaded_extensions(self) -> list[str]:
        """Return names of extensions in LOADED or RUNNING state, in load order."""
        return [
            name
            for name in self._load_order
            if self._extensions[name].state in (ExtensionState.LOADED, ExtensionState.RUNNING)
        ]
