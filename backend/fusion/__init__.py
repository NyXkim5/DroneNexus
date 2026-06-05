"""
Fusion package — multi-sensor track fusion for BULWARK.

Public surface:
  TrackManager            the fusion engine, update()/predict() driven
  FusionConfig            tuning knobs for gating, coasting, and confidence
  ConstantVelocityKalman  the per-track constant-velocity filter
"""
from fusion.kalman import ConstantVelocityKalman
from fusion.track_manager import FusionConfig, TrackManager

__all__ = ["TrackManager", "FusionConfig", "ConstantVelocityKalman"]
