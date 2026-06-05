"""
SensorSource — the abstract contract every sensor adapter implements.

This is the boundary the design spec calls "one engine, two sources, one
picture". The fusion engine pulls Detection events from a SensorSource and does
not care if they come from drone-sim or a live radar.

Interface choice
----------------
A SensorSource is an async stream. Lifecycle is explicit start/stop. Detections
are pulled through an async generator, stream(). This is a clean single pattern.
It backpressures naturally and needs no callback registry.

  source = SomeSensorSource(...)
  await source.start()
  async for detection in source.stream():
      fusion.ingest(detection)
  await source.stop()

stream() yields Detection objects at the sensor rate. It runs until stop() is
called, at which point it returns cleanly. Implementations must not raise on a
normal stop. They must surface real faults as exceptions, never swallow them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from csontology import Detection


class SensorSource(ABC):
    """Abstract async source of Detection events.

    Subclasses implement start, stop, and stream. sensor_id identifies the
    physical or simulated sensor and is stamped onto every Detection it emits.
    """

    def __init__(self, sensor_id: str) -> None:
        self.sensor_id = sensor_id
        self._running = False

    @property
    def is_running(self) -> bool:
        """True between a completed start() and a stop()."""
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Open the sensor and begin producing detections.

        Must set the source to running. Must raise on a hardware or connection
        fault rather than fail silently.
        """
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """Stop the sensor and release resources.

        Must set the source to not running and cause stream() to finish.
        """
        raise NotImplementedError

    @abstractmethod
    def stream(self) -> AsyncIterator[Detection]:
        """Yield Detection events at the sensor rate until stop() is called.

        Returns an async iterator. Implementations write this as an async
        generator. It returns cleanly on stop and raises only on real faults.
        """
        raise NotImplementedError
