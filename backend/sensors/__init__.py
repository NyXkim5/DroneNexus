"""
BULWARK sensor layer.

Holds the SensorSource interface plus its adapters. The decision engine consumes
Detection events from a SensorSource and never knows whether the source is a
simulator or a real radar. That single boundary delivers both the wargaming tool
and the deployable system from one codebase.
"""
from sensors.base import SensorSource
from sensors.real_source import RealSensorSource

__all__ = ["SensorSource", "RealSensorSource"]
