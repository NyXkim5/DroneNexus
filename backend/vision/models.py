from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any

from csontology import Vec3


class TargetType(Enum):
    VEHICLE_CAR = "vehicle_car"
    VEHICLE_TRUCK = "vehicle_truck"
    VEHICLE_APC = "vehicle_apc"
    VEHICLE_FUEL_TANKER = "vehicle_fuel_tanker"
    PERSONNEL_INDIVIDUAL = "personnel_individual"
    PERSONNEL_GROUP = "personnel_group"
    INFRA_GENERATOR = "infra_generator"
    INFRA_ANTENNA = "infra_antenna"
    INFRA_BRIDGE = "infra_bridge"
    INFRA_BUILDING = "infra_building"
    ORDNANCE_AMMO_CACHE = "ordnance_ammo_cache"
    ORDNANCE_FUEL_DEPOT = "ordnance_fuel_depot"


@dataclass(frozen=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class VisualTarget:
    id: str
    target_type: TargetType
    position: Vec3
    bounding_box: BoundingBox
    confidence: float
    occupancy_estimate: int
    base_value: float
    blast_radius_m: float
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type.value,
            "position": list(self.position),
            "bounding_box": self.bounding_box.to_dict(),
            "confidence": self.confidence,
            "occupancy_estimate": self.occupancy_estimate,
            "base_value": self.base_value,
            "blast_radius_m": self.blast_radius_m,
            "properties": self.properties,
        }


TARGET_DEFAULTS: Dict[TargetType, Dict[str, Any]] = {
    TargetType.VEHICLE_CAR:            {"base_value": 30_000,    "blast_radius_m": 10,  "default_occupancy": 3},
    TargetType.VEHICLE_TRUCK:          {"base_value": 80_000,    "blast_radius_m": 15,  "default_occupancy": 2},
    TargetType.VEHICLE_APC:            {"base_value": 500_000,   "blast_radius_m": 20,  "default_occupancy": 8},
    TargetType.VEHICLE_FUEL_TANKER:    {"base_value": 200_000,   "blast_radius_m": 50,  "default_occupancy": 2},
    TargetType.PERSONNEL_INDIVIDUAL:   {"base_value": 0,         "blast_radius_m": 0,   "default_occupancy": 1},
    TargetType.PERSONNEL_GROUP:        {"base_value": 0,         "blast_radius_m": 0,   "default_occupancy": 0},
    TargetType.INFRA_GENERATOR:        {"base_value": 150_000,   "blast_radius_m": 5,   "default_occupancy": 0},
    TargetType.INFRA_ANTENNA:          {"base_value": 100_000,   "blast_radius_m": 3,   "default_occupancy": 0},
    TargetType.INFRA_BRIDGE:           {"base_value": 2_000_000, "blast_radius_m": 0,   "default_occupancy": 0},
    TargetType.INFRA_BUILDING:         {"base_value": 500_000,   "blast_radius_m": 10,  "default_occupancy": 0},
    TargetType.ORDNANCE_AMMO_CACHE:    {"base_value": 1_000_000, "blast_radius_m": 80,  "default_occupancy": 0},
    TargetType.ORDNANCE_FUEL_DEPOT:    {"base_value": 3_000_000, "blast_radius_m": 100, "default_occupancy": 0},
}
