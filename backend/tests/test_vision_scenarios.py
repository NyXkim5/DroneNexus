import pytest
from vision.scenarios import TargetScenario, load_target_scenario, SCENARIO_REGISTRY
from vision.detector import SimTargetPlacement
from vision.cascade import DependencyEdge


def test_convoy_scenario_exists():
    s = load_target_scenario("ground_strike_convoy")
    assert isinstance(s, TargetScenario)
    assert len(s.placements) >= 4
    assert len(s.dependencies) >= 1


def test_dispersed_scenario_no_dependencies():
    s = load_target_scenario("ground_strike_dispersed")
    assert len(s.placements) >= 3
    assert len(s.dependencies) == 0


def test_base_scenario_has_dependencies():
    s = load_target_scenario("ground_strike_base")
    assert len(s.placements) >= 5
    assert len(s.dependencies) >= 2


def test_all_registered_scenarios_loadable():
    for name in SCENARIO_REGISTRY:
        s = load_target_scenario(name)
        assert isinstance(s, TargetScenario)
        assert len(s.placements) > 0
        for p in s.placements:
            assert isinstance(p, SimTargetPlacement)
        for d in s.dependencies:
            assert isinstance(d, DependencyEdge)


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        load_target_scenario("nonexistent_scenario")


def test_scenario_placement_ids_unique():
    for name in SCENARIO_REGISTRY:
        s = load_target_scenario(name)
        ids = [p.id for p in s.placements]
        assert len(ids) == len(set(ids)), f"Duplicate IDs in {name}"


def test_dependency_references_valid_placements():
    for name in SCENARIO_REGISTRY:
        s = load_target_scenario(name)
        ids = {p.id for p in s.placements}
        for d in s.dependencies:
            assert d.source_id in ids, f"Dep source {d.source_id} not in {name}"
            assert d.target_id in ids, f"Dep target {d.target_id} not in {name}"
