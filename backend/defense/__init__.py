"""
BULWARK defense module — defender allocation and engagement resolution.

The allocation engine assigns finite Defender effectors to ranked Threats. It
respects range and capacity, maximizes expected threats neutralized, and emits
Engagement objects. A resolution step rolls each engagement to HIT, MISS, or
LEAK using single-shot kill probability. A cost ledger tracks defender dollars
spent against attacker dollars destroyed for the cost-exchange ratio.
"""
from defense.allocator import (
    Allocator,
    GreedyAllocator,
    LayeredAllocator,
    PositionResolver,
    CostLedger,
    DEFAULT_THREAT_VALUE,
)

__all__ = [
    "Allocator",
    "GreedyAllocator",
    "LayeredAllocator",
    "PositionResolver",
    "CostLedger",
    "DEFAULT_THREAT_VALUE",
]
