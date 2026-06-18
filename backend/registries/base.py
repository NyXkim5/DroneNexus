"""Generic typed registry with event-driven callbacks on add/remove/update."""
from __future__ import annotations

import logging
import threading
from collections.abc import ItemsView, Iterator, ValuesView
from typing import Generic, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger("registries.base")

Callback = list  # list[Callable[[str, T], None]] - simplified for runtime


class Registry(Generic[T]):
    """Typed registry with event callbacks on add/remove/update.

    Stores entries keyed by string ID. Fires registered callbacks
    synchronously when entries are added, removed, or updated.
    Thread-safe via a reentrant lock.
    """

    def __init__(self) -> None:
        self._entries: dict[str, T] = {}
        self._lock = threading.RLock()
        self.on_added: list[object] = []
        self.on_removed: list[object] = []
        self.on_updated: list[object] = []

    def add(self, entry_id: str, entry: T) -> None:
        """Add an entry. Raises KeyError if entry_id already exists."""
        with self._lock:
            if entry_id in self._entries:
                raise KeyError(
                    f"Entry '{entry_id}' already exists in registry"
                )
            self._entries[entry_id] = entry
            logger.debug("Added entry: %s", entry_id)
        self._fire_callbacks(self.on_added, entry_id, entry)

    def remove(self, entry_id: str) -> T:
        """Remove and return an entry. Raises KeyError if not found."""
        with self._lock:
            if entry_id not in self._entries:
                raise KeyError(
                    f"Entry '{entry_id}' not found in registry"
                )
            entry = self._entries.pop(entry_id)
            logger.debug("Removed entry: %s", entry_id)
        self._fire_callbacks(self.on_removed, entry_id, entry)
        return entry

    def update(self, entry_id: str, entry: T) -> None:
        """Replace an existing entry. Raises KeyError if not found."""
        with self._lock:
            if entry_id not in self._entries:
                raise KeyError(
                    f"Entry '{entry_id}' not found in registry"
                )
            self._entries[entry_id] = entry
            logger.debug("Updated entry: %s", entry_id)
        self._fire_callbacks(self.on_updated, entry_id, entry)

    def get(self, entry_id: str) -> Optional[T]:
        """Return entry by ID or None if not found."""
        with self._lock:
            return self._entries.get(entry_id)

    def __contains__(self, entry_id: str) -> bool:
        with self._lock:
            return entry_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._entries.keys()))

    def items(self) -> ItemsView[str, T]:
        """Return a snapshot-safe items view."""
        with self._lock:
            return dict(self._entries).items()

    def values(self) -> ValuesView[T]:
        """Return a snapshot-safe values view."""
        with self._lock:
            return dict(self._entries).values()

    def _fire_callbacks(
        self, callbacks: list[object], entry_id: str, entry: T
    ) -> None:
        """Invoke all registered callbacks for an event."""
        for cb in callbacks:
            try:
                cb(entry_id, entry)  # type: ignore[operator]
            except Exception:
                logger.exception(
                    "Callback %r failed for entry '%s'", cb, entry_id
                )
