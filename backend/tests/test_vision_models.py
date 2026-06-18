import pytest
from vision.models import (
    TargetType,
    BoundingBox,
    VisualTarget,
    TARGET_DEFAULTS,
)


def test_target_type_has_all_categories():
    assert TargetType.VEHICLE_CAR.value == "vehicle_car"
    assert TargetType.VEHICLE_FUEL_TANKER.value == "vehicle_fuel_tanker"
    assert TargetType.PERSONNEL_GROUP.value == "personnel_group"
    assert TargetType.INFRA_BRIDGE.value == "infra_bridge"
    assert TargetType.ORDNANCE_FUEL_DEPOT.value == "ordnance_fuel_depot"
    assert len(TargetType) == 12


def test_bounding_box_fields():
    bb = BoundingBox(x=10, y=20, width=100, height=50)
    assert bb.x == 10
    assert bb.width == 100


def test_visual_target_construction():
    target = VisualTarget(
        id="t-1",
        target_type=TargetType.VEHICLE_FUEL_TANKER,
        position=(100.0, 200.0, 0.0),
        bounding_box=BoundingBox(x=0, y=0, width=64, height=32),
        confidence=0.95,
        occupancy_estimate=2,
        base_value=200_000.0,
        blast_radius_m=50.0,
        properties={"fuel_capacity_l": 20_000},
    )
    assert target.target_type == TargetType.VEHICLE_FUEL_TANKER
    assert target.blast_radius_m == 50.0
    assert target.properties["fuel_capacity_l"] == 20_000


def test_target_defaults_all_types_present():
    for tt in TargetType:
        assert tt in TARGET_DEFAULTS, f"Missing default for {tt}"
        d = TARGET_DEFAULTS[tt]
        assert "base_value" in d
        assert "blast_radius_m" in d
        assert "default_occupancy" in d


def test_target_defaults_values():
    tanker = TARGET_DEFAULTS[TargetType.VEHICLE_FUEL_TANKER]
    assert tanker["base_value"] == 200_000
    assert tanker["blast_radius_m"] == 50
    assert tanker["default_occupancy"] == 2

    depot = TARGET_DEFAULTS[TargetType.ORDNANCE_FUEL_DEPOT]
    assert depot["base_value"] == 3_000_000
    assert depot["blast_radius_m"] == 100


def test_visual_target_to_dict():
    target = VisualTarget(
        id="t-1",
        target_type=TargetType.VEHICLE_CAR,
        position=(10.0, 20.0, 0.0),
        bounding_box=BoundingBox(x=5, y=5, width=50, height=30),
        confidence=0.9,
        occupancy_estimate=3,
        base_value=30_000.0,
        blast_radius_m=10.0,
        properties={},
    )
    d = target.to_dict()
    assert d["id"] == "t-1"
    assert d["target_type"] == "vehicle_car"
    assert d["bounding_box"] == {"x": 5, "y": 5, "width": 50, "height": 30}
    assert d["position"] == [10.0, 20.0, 0.0]
