from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from vision.models import TargetType
from vision.detector import SimTargetPlacement
from vision.cascade import DependencyEdge, DependencyType


@dataclass
class TargetScenario:
    name: str
    placements: List[SimTargetPlacement]
    dependencies: List[DependencyEdge] = field(default_factory=list)


def _convoy() -> TargetScenario:
    return TargetScenario(
        name="ground_strike_convoy",
        placements=[
            SimTargetPlacement("lead-car", TargetType.VEHICLE_CAR, (-60, 0, 0)),
            SimTargetPlacement("truck-1", TargetType.VEHICLE_TRUCK, (-20, 0, 0)),
            SimTargetPlacement("fuel-tanker", TargetType.VEHICLE_FUEL_TANKER, (20, 0, 0)),
            SimTargetPlacement("ammo-truck", TargetType.ORDNANCE_AMMO_CACHE, (60, 5, 0)),
            SimTargetPlacement("trail-car", TargetType.VEHICLE_CAR, (100, 0, 0)),
            SimTargetPlacement("bridge", TargetType.INFRA_BRIDGE, (0, 0, 0)),
        ],
        dependencies=[
            DependencyEdge("bridge", "lead-car", DependencyType.ENABLES_MOVEMENT, 0.9),
            DependencyEdge("bridge", "truck-1", DependencyType.ENABLES_MOVEMENT, 0.9),
            DependencyEdge("bridge", "fuel-tanker", DependencyType.ENABLES_MOVEMENT, 0.9),
            DependencyEdge("bridge", "ammo-truck", DependencyType.ENABLES_MOVEMENT, 0.9),
            DependencyEdge("bridge", "trail-car", DependencyType.ENABLES_MOVEMENT, 0.9),
            DependencyEdge("fuel-tanker", "ammo-truck", DependencyType.SUPPLIES, 0.5),
        ],
    )


def _dispersed() -> TargetScenario:
    return TargetScenario(
        name="ground_strike_dispersed",
        placements=[
            SimTargetPlacement("car-1", TargetType.VEHICLE_CAR, (-200, 100, 0)),
            SimTargetPlacement("car-2", TargetType.VEHICLE_CAR, (150, -80, 0)),
            SimTargetPlacement("truck-1", TargetType.VEHICLE_TRUCK, (0, 300, 0)),
            SimTargetPlacement("apc-1", TargetType.VEHICLE_APC, (400, 200, 0), occupancy_override=6),
        ],
        dependencies=[],
    )


def _base() -> TargetScenario:
    return TargetScenario(
        name="ground_strike_base",
        placements=[
            SimTargetPlacement("generator", TargetType.INFRA_GENERATOR, (0, 0, 0)),
            SimTargetPlacement("antenna", TargetType.INFRA_ANTENNA, (30, 10, 0)),
            SimTargetPlacement("command-post", TargetType.INFRA_BUILDING, (50, 0, 0), occupancy_override=8),
            SimTargetPlacement("fuel-depot", TargetType.ORDNANCE_FUEL_DEPOT, (-80, 40, 0)),
            SimTargetPlacement("barracks", TargetType.INFRA_BUILDING, (80, 50, 0), occupancy_override=20),
            SimTargetPlacement("motor-pool", TargetType.VEHICLE_TRUCK, (-40, -30, 0)),
            SimTargetPlacement("apc-1", TargetType.VEHICLE_APC, (-50, -50, 0), occupancy_override=8),
            SimTargetPlacement("personnel-hq", TargetType.PERSONNEL_GROUP, (60, 30, 0), occupancy_override=12),
        ],
        dependencies=[
            DependencyEdge("generator", "antenna", DependencyType.POWERS, 1.0),
            DependencyEdge("generator", "command-post", DependencyType.POWERS, 0.8),
            DependencyEdge("antenna", "command-post", DependencyType.PROVIDES_COMMS, 0.6),
            DependencyEdge("fuel-depot", "motor-pool", DependencyType.SUPPLIES, 0.7),
            DependencyEdge("fuel-depot", "apc-1", DependencyType.SUPPLIES, 0.7),
            DependencyEdge("command-post", "personnel-hq", DependencyType.SHELTERS, 0.3),
        ],
    )


SCENARIO_REGISTRY: Dict[str, callable] = {
    "ground_strike_convoy": _convoy,
    "ground_strike_dispersed": _dispersed,
    "ground_strike_base": _base,
}


def load_target_scenario(name: str) -> TargetScenario:
    if name not in SCENARIO_REGISTRY:
        raise KeyError(f"Unknown target scenario: {name}. Available: {list(SCENARIO_REGISTRY.keys())}")
    return SCENARIO_REGISTRY[name]()
