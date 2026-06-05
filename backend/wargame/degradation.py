"""
Comms-denied and degraded-sensing model for BULWARK.

Real counter-swarm fights are contested. Sensors get jammed, links drop, and the
picture goes partial or dark. This model degrades the detection stream so the
autonomy must keep defending on coasted and predicted tracks with no operator
input, which is the comms-denied requirement.

Two effects:
  - Jamming: a fraction of detections is lost every tick at random.
  - Blackout windows: whole tick ranges where the picture goes fully dark, so the
    fusion engine coasts every track and the allocator fires on predicted state.

The model is deterministic given the injected rng so a run reproduces exactly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from random import Random
from typing import List, Tuple

from csontology import Detection

logger = logging.getLogger("overwatch.degradation")


@dataclass
class DegradationModel:
    """Applies jamming and blackout to a per-tick detection stream.

    jam_fraction is the share of detections dropped each tick to jamming, in
    0..1. blackout_windows are inclusive (start_tick, end_tick) ranges where the
    sensing picture is fully denied. During a blackout the autonomy runs purely
    on coasted tracks, which is the test of comms-denied operation.
    """

    jam_fraction: float = 0.0
    blackout_windows: List[Tuple[int, int]] = field(default_factory=list)

    def is_blacked_out(self, tick: int) -> bool:
        """True when this tick falls inside any full-denial blackout window."""
        return any(start <= tick <= end for start, end in self.blackout_windows)

    def apply(self, detections: List[Detection], tick: int, rng: Random) -> List[Detection]:
        """Return the surviving detections after jamming and blackout for a tick."""
        if self.is_blacked_out(tick):
            if detections:
                logger.debug("tick %d blacked out, %d detections denied", tick, len(detections))
            return []
        if self.jam_fraction <= 0.0:
            return detections
        kept = [d for d in detections if rng.random() > self.jam_fraction]
        return kept

    @property
    def active(self) -> bool:
        """True when this model degrades anything at all."""
        return self.jam_fraction > 0.0 or bool(self.blackout_windows)
