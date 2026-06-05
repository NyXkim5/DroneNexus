"""
BULWARK threat module — swarm-aware classification and prioritization.

Public surface:
  assess(tracks, site, now)       -> ranked list[Threat]
  detect_swarms(tracks, site, now) -> list[Swarm] with intent
  infer_intent(swarm, members, site) -> SwarmIntent
  cluster_tracks(tracks, now)      -> list[Swarm]
"""
from threat.classifier import assess, detect_swarms
from threat.clustering import cluster_tracks
from threat.intent import infer_intent

__all__ = ["assess", "detect_swarms", "cluster_tracks", "infer_intent"]
