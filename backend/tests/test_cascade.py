import pytest
from vision.models import VisualTarget, TargetType, BoundingBox
from vision.cascade import (
    DependencyType,
    DependencyEdge,
    CascadeResult,
    CascadeEngine,
)

BB = BoundingBox(x=0, y=0, width=64, height=32)


def _make_target(
    id: str,
    target_type: TargetType,
    position: tuple,
    blast_radius_m: float = 0.0,
    base_value: float = 100_000.0,
    occupancy: int = 0,
) -> VisualTarget:
    return VisualTarget(
        id=id,
        target_type=target_type,
        position=position,
        bounding_box=BB,
        confidence=0.95,
        occupancy_estimate=occupancy,
        base_value=base_value,
        blast_radius_m=blast_radius_m,
        properties={},
    )


class TestProximityGraph:
    def test_two_targets_in_blast_radius(self):
        tanker = _make_target("tanker", TargetType.VEHICLE_FUEL_TANKER, (0, 0, 0), blast_radius_m=50, base_value=200_000)
        car = _make_target("car", TargetType.VEHICLE_CAR, (30, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[tanker, car], dependencies=[])
        results = engine.score_all()
        tanker_result = next(r for r in results if r.target_id == "tanker")
        assert tanker_result.cascade_value > tanker_result.direct_value
        assert "car" in tanker_result.cascade_chain

    def test_targets_outside_blast_radius(self):
        a = _make_target("a", TargetType.VEHICLE_CAR, (0, 0, 0), blast_radius_m=10, base_value=30_000)
        b = _make_target("b", TargetType.VEHICLE_CAR, (500, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[a, b], dependencies=[])
        results = engine.score_all()
        a_result = next(r for r in results if r.target_id == "a")
        assert a_result.cascade_value == a_result.direct_value
        assert len(a_result.cascade_chain) == 1

    def test_chain_reaction_three_targets(self):
        depot = _make_target("depot", TargetType.ORDNANCE_FUEL_DEPOT, (0, 0, 0), blast_radius_m=100, base_value=3_000_000)
        ammo = _make_target("ammo", TargetType.ORDNANCE_AMMO_CACHE, (80, 0, 0), blast_radius_m=80, base_value=1_000_000)
        car = _make_target("car", TargetType.VEHICLE_CAR, (140, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[depot, ammo, car], dependencies=[])
        results = engine.score_all()
        depot_result = next(r for r in results if r.target_id == "depot")
        assert "ammo" in depot_result.cascade_chain
        assert "car" in depot_result.cascade_chain
        assert depot_result.cascade_value > 3_000_000

    def test_cycle_does_not_infinite_loop(self):
        a = _make_target("a", TargetType.ORDNANCE_AMMO_CACHE, (0, 0, 0), blast_radius_m=80, base_value=1_000_000)
        b = _make_target("b", TargetType.ORDNANCE_AMMO_CACHE, (50, 0, 0), blast_radius_m=80, base_value=1_000_000)
        engine = CascadeEngine(targets=[a, b], dependencies=[])
        results = engine.score_all()
        assert len(results) == 2

    def test_probability_decays_with_distance(self):
        tanker = _make_target("tanker", TargetType.VEHICLE_FUEL_TANKER, (0, 0, 0), blast_radius_m=50, base_value=200_000)
        close = _make_target("close", TargetType.VEHICLE_CAR, (10, 0, 0), blast_radius_m=10, base_value=30_000)
        far = _make_target("far", TargetType.VEHICLE_CAR, (45, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[tanker, close, far], dependencies=[])
        results = engine.score_all()
        tanker_result = next(r for r in results if r.target_id == "tanker")
        assert tanker_result.cascade_probability < 1.0


class TestDependencyGraph:
    def test_dependency_adds_value(self):
        gen = _make_target("gen", TargetType.INFRA_GENERATOR, (0, 0, 0), blast_radius_m=5, base_value=150_000)
        cp = _make_target("cp", TargetType.INFRA_BUILDING, (200, 0, 0), blast_radius_m=10, base_value=500_000)
        dep = DependencyEdge(source_id="gen", target_id="cp", dependency_type=DependencyType.POWERS, impact_factor=0.8)
        engine = CascadeEngine(targets=[gen, cp], dependencies=[dep])
        results = engine.score_all()
        gen_result = next(r for r in results if r.target_id == "gen")
        assert gen_result.cascade_value > gen_result.direct_value
        expected_dep_value = 500_000 * 0.8
        assert gen_result.cascade_value == pytest.approx(150_000 + expected_dep_value, rel=0.01)

    def test_dependency_chain_propagates(self):
        gen = _make_target("gen", TargetType.INFRA_GENERATOR, (0, 0, 0), blast_radius_m=5, base_value=150_000)
        antenna = _make_target("antenna", TargetType.INFRA_ANTENNA, (300, 0, 0), blast_radius_m=3, base_value=100_000)
        cp = _make_target("cp", TargetType.INFRA_BUILDING, (600, 0, 0), blast_radius_m=10, base_value=500_000)
        deps = [
            DependencyEdge(source_id="gen", target_id="antenna", dependency_type=DependencyType.POWERS, impact_factor=1.0),
            DependencyEdge(source_id="antenna", target_id="cp", dependency_type=DependencyType.PROVIDES_COMMS, impact_factor=0.5),
        ]
        engine = CascadeEngine(targets=[gen, antenna, cp], dependencies=deps)
        results = engine.score_all()
        gen_result = next(r for r in results if r.target_id == "gen")
        assert "antenna" in gen_result.cascade_chain
        assert "cp" in gen_result.cascade_chain


class TestCascadeResult:
    def test_sorted_by_expected_value(self):
        big = _make_target("big", TargetType.ORDNANCE_FUEL_DEPOT, (0, 0, 0), blast_radius_m=100, base_value=3_000_000)
        small = _make_target("small", TargetType.VEHICLE_CAR, (500, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[small, big], dependencies=[])
        results = engine.score_all()
        assert results[0].target_id == "big"
        assert results[0].expected_value > results[1].expected_value

    def test_personnel_at_risk_counts(self):
        tanker = _make_target("tanker", TargetType.VEHICLE_FUEL_TANKER, (0, 0, 0), blast_radius_m=50, base_value=200_000, occupancy=2)
        group = _make_target("group", TargetType.PERSONNEL_GROUP, (20, 0, 0), blast_radius_m=0, base_value=0, occupancy=12)
        engine = CascadeEngine(targets=[tanker, group], dependencies=[])
        results = engine.score_all()
        tanker_result = next(r for r in results if r.target_id == "tanker")
        assert tanker_result.personnel_at_risk >= 14

    def test_to_dict_serialization(self):
        target = _make_target("t", TargetType.VEHICLE_CAR, (0, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[target], dependencies=[])
        results = engine.score_all()
        d = results[0].to_dict()
        assert d["target_id"] == "t"
        assert "expected_value" in d
        assert "cascade_chain" in d


class TestPruning:
    def test_low_probability_branches_pruned(self):
        source = _make_target("src", TargetType.VEHICLE_CAR, (0, 0, 0), blast_radius_m=10, base_value=30_000)
        barely = _make_target("barely", TargetType.VEHICLE_CAR, (9.9, 0, 0), blast_radius_m=10, base_value=30_000)
        beyond = _make_target("beyond", TargetType.VEHICLE_CAR, (19.5, 0, 0), blast_radius_m=10, base_value=30_000)
        engine = CascadeEngine(targets=[source, barely, beyond], dependencies=[])
        results = engine.score_all()
        src_result = next(r for r in results if r.target_id == "src")
        assert src_result.cascade_probability < 1.0
