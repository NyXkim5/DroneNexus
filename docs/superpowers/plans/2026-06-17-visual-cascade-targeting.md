# Visual Cascade Targeting System -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a vision pipeline, cascade damage engine, and unified decision engine to OVERWATCH/BULWARK so the system identifies ground targets from camera feeds, scores them by cascading destruction potential, and merges offensive + defensive priorities into one engagement order.

**Architecture:** Dual-layer scoring. A standalone vision pipeline (`backend/vision/`) processes camera frames and scores cascade damage independently. A new decision engine (`backend/decision/`) merges BULWARK's defensive threat scores with the vision pipeline's offensive cascade scores into a single ranked engagement order. The HUD gets a camera overlay panel and engagement order sidebar. Wargame scenarios exercise both layers simultaneously.

**Tech Stack:** Python 3.11+, FastAPI, WebSocket, numpy, Pillow (new dep for sim frame rendering), pytest, vanilla JS + Canvas API.

## Global Constraints

- All new Python code uses type hints and dataclasses (match existing style in csontology.py)
- Vec3 is `Tuple[float, float, float]` imported from `backend/csontology.py`
- All coordinates are ENU meters relative to site origin
- Tests use pytest with `@pytest.mark.slow` for e2e scenarios
- No breaking changes to existing modules -- new code wraps or extends
- Pillow is the only new Python dependency (add to requirements.txt)
- Frontend uses vanilla JS + Canvas API, no new dependencies
- Async interfaces match existing pattern: `async def start/stop` + async generators

---

### Task 1: Vision Data Models (`backend/vision/models.py`)

**Files:**
- Create: `backend/vision/__init__.py`
- Create: `backend/vision/models.py`
- Test: `backend/tests/test_vision_models.py`

**Interfaces:**
- Consumes: `Vec3` from `backend/csontology.py:61`
- Produces: `TargetType`, `BoundingBox`, `VisualTarget`, `TARGET_DEFAULTS` -- used by every subsequent task

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_vision_models.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_vision_models.py -v`
Expected: `ModuleNotFoundError: No module named 'vision'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/vision/__init__.py`:

```python
```

Create `backend/vision/models.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_vision_models.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/__init__.py backend/vision/models.py backend/tests/test_vision_models.py
git commit -m "feat: add vision data models (TargetType, VisualTarget, TARGET_DEFAULTS)"
```

---

### Task 2: Cascade Damage Engine (`backend/vision/cascade.py`)

**Files:**
- Create: `backend/vision/cascade.py`
- Test: `backend/tests/test_cascade.py`

**Interfaces:**
- Consumes: `VisualTarget`, `TargetType`, `TARGET_DEFAULTS` from `backend/vision/models.py`
- Produces: `DependencyType`, `DependencyEdge`, `CascadeResult`, `CascadeEngine` -- used by Task 5 (decision engine), Task 6 (scenarios), Task 8 (wargame)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_cascade.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_cascade.py -v`
Expected: `ModuleNotFoundError: No module named 'vision.cascade'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/vision/cascade.py`:

```python
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any

from vision.models import VisualTarget


PRUNE_THRESHOLD = 0.05


class DependencyType(Enum):
    POWERS = "powers"
    ENABLES_MOVEMENT = "enables_movement"
    PROVIDES_COMMS = "provides_comms"
    SHELTERS = "shelters"
    SUPPLIES = "supplies"


@dataclass(frozen=True)
class DependencyEdge:
    source_id: str
    target_id: str
    dependency_type: DependencyType
    impact_factor: float


@dataclass
class CascadeResult:
    target_id: str
    direct_value: float
    cascade_value: float
    cascade_chain: List[str]
    cascade_probability: float
    expected_value: float
    personnel_at_risk: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "direct_value": self.direct_value,
            "cascade_value": self.cascade_value,
            "cascade_chain": self.cascade_chain,
            "cascade_probability": self.cascade_probability,
            "expected_value": self.expected_value,
            "personnel_at_risk": self.personnel_at_risk,
        }


def _distance(a: VisualTarget, b: VisualTarget) -> float:
    dx = a.position[0] - b.position[0]
    dy = a.position[1] - b.position[1]
    dz = a.position[2] - b.position[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


class CascadeEngine:
    def __init__(
        self,
        targets: List[VisualTarget],
        dependencies: List[DependencyEdge],
    ) -> None:
        self._targets = {t.id: t for t in targets}
        self._dependencies = dependencies
        self._dep_graph: Dict[str, List[DependencyEdge]] = {}
        for dep in dependencies:
            self._dep_graph.setdefault(dep.source_id, []).append(dep)

    def _build_proximity_edges(self) -> Dict[str, List[tuple]]:
        edges: Dict[str, List[tuple]] = {}
        targets = list(self._targets.values())
        for i, a in enumerate(targets):
            if a.blast_radius_m <= 0:
                continue
            for j, b in enumerate(targets):
                if i == j:
                    continue
                dist = _distance(a, b)
                if dist < a.blast_radius_m:
                    p_kill = max(0.0, 1.0 - dist / a.blast_radius_m)
                    edges.setdefault(a.id, []).append((b.id, p_kill))
        return edges

    def _cascade_from(
        self,
        start_id: str,
        prox_edges: Dict[str, List[tuple]],
    ) -> CascadeResult:
        start = self._targets[start_id]
        visited: set = set()
        chain: List[str] = []
        total_value = 0.0
        total_personnel = 0
        min_chain_prob = 1.0

        queue: deque = deque()
        queue.append((start_id, 1.0, True))

        while queue:
            node_id, prob, is_kinetic = queue.popleft()
            if node_id in visited:
                continue
            if prob < PRUNE_THRESHOLD and node_id != start_id:
                continue
            visited.add(node_id)
            node = self._targets[node_id]

            chain.append(node_id)
            if is_kinetic:
                total_value += node.base_value
                min_chain_prob = min(min_chain_prob, prob) if node_id != start_id else min_chain_prob
            else:
                dep_edge = next(
                    (d for d in self._dependencies if d.target_id == node_id and d.source_id in visited),
                    None,
                )
                impact = dep_edge.impact_factor if dep_edge else 1.0
                total_value += node.base_value * impact

            total_personnel += node.occupancy_estimate

            for neighbor_id, p_kill in prox_edges.get(node_id, []):
                if neighbor_id not in visited:
                    queue.append((neighbor_id, prob * p_kill, True))

            for dep in self._dep_graph.get(node_id, []):
                if dep.target_id not in visited:
                    queue.append((dep.target_id, prob, False))

        cascade_prob = min_chain_prob if len(chain) > 1 else 1.0

        return CascadeResult(
            target_id=start_id,
            direct_value=start.base_value,
            cascade_value=total_value,
            cascade_chain=chain,
            cascade_probability=cascade_prob,
            expected_value=total_value * cascade_prob,
            personnel_at_risk=total_personnel,
        )

    def score_all(self) -> List[CascadeResult]:
        prox_edges = self._build_proximity_edges()
        results = [
            self._cascade_from(tid, prox_edges)
            for tid in self._targets
        ]
        results.sort(key=lambda r: r.expected_value, reverse=True)
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_cascade.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/cascade.py backend/tests/test_cascade.py
git commit -m "feat: add cascade damage engine with proximity + dependency graphs"
```

---

### Task 3: Detector Interface + SimDetector (`backend/vision/detector.py`)

**Files:**
- Create: `backend/vision/detector.py`
- Test: `backend/tests/test_detector.py`

**Interfaces:**
- Consumes: `VisualTarget`, `TargetType`, `BoundingBox`, `TARGET_DEFAULTS` from `backend/vision/models.py`
- Consumes: `TargetScenario` from Task 6 (but for now we define a minimal inline scenario for testing)
- Produces: `Detector` (ABC), `SimDetector` -- used by Task 7 (vision pipeline integration), Task 8 (wargame)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_detector.py`:

```python
import pytest
import numpy as np
from vision.models import VisualTarget, TargetType, BoundingBox, TARGET_DEFAULTS
from vision.detector import Detector, SimDetector, SimTargetPlacement


def _make_placement(
    id: str,
    target_type: TargetType,
    position: tuple,
) -> SimTargetPlacement:
    return SimTargetPlacement(
        id=id,
        target_type=target_type,
        position=position,
    )


class TestSimDetector:
    def test_returns_all_placed_targets(self):
        placements = [
            _make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0)),
            _make_placement("t2", TargetType.VEHICLE_TRUCK, (300, 400, 0)),
        ]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ids == {"t1", "t2"}

    def test_uses_target_defaults(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_FUEL_TANKER, (0, 0, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        defaults = TARGET_DEFAULTS[TargetType.VEHICLE_FUEL_TANKER]
        assert results[0].base_value == defaults["base_value"]
        assert results[0].blast_radius_m == defaults["blast_radius_m"]

    def test_noise_perturbs_position(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=5.0, false_positive_rate=0.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        pos = results[0].position
        assert pos != (100.0, 200.0, 0.0)
        assert abs(pos[0] - 100.0) < 30
        assert abs(pos[1] - 200.0) < 30

    def test_false_positives(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=1.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) > 1
        false_ids = [r.id for r in results if r.id.startswith("fp-")]
        assert len(false_ids) > 0

    def test_zero_false_positive_rate(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert len(results) == 1

    def test_all_results_have_bounding_boxes(self):
        placements = [
            _make_placement("t1", TargetType.INFRA_BRIDGE, (0, 0, 0)),
            _make_placement("t2", TargetType.PERSONNEL_GROUP, (50, 50, 0)),
        ]
        detector = SimDetector(placements=placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        for r in results:
            assert r.bounding_box.width > 0
            assert r.bounding_box.height > 0

    def test_confidence_below_one_with_noise(self):
        placements = [_make_placement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        detector = SimDetector(placements=placements, noise_sigma_m=5.0, false_positive_rate=0.0, seed=42)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        results = detector.detect(frame, timestamp=1.0)
        assert results[0].confidence < 1.0

    def test_detector_is_abstract():
        with pytest.raises(TypeError):
            Detector()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_detector.py -v`
Expected: `ModuleNotFoundError: No module named 'vision.detector'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/vision/detector.py`:

```python
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from vision.models import (
    BoundingBox,
    TargetType,
    VisualTarget,
    TARGET_DEFAULTS,
)


@dataclass
class SimTargetPlacement:
    id: str
    target_type: TargetType
    position: tuple
    occupancy_override: Optional[int] = None
    properties: Optional[dict] = None


class Detector(ABC):
    @abstractmethod
    def detect(self, frame: np.ndarray, timestamp: float) -> List[VisualTarget]:
        ...


_BB_SIZES = {
    TargetType.VEHICLE_CAR: (48, 28),
    TargetType.VEHICLE_TRUCK: (64, 32),
    TargetType.VEHICLE_APC: (60, 36),
    TargetType.VEHICLE_FUEL_TANKER: (72, 32),
    TargetType.PERSONNEL_INDIVIDUAL: (16, 32),
    TargetType.PERSONNEL_GROUP: (64, 48),
    TargetType.INFRA_GENERATOR: (40, 40),
    TargetType.INFRA_ANTENNA: (24, 48),
    TargetType.INFRA_BRIDGE: (120, 40),
    TargetType.INFRA_BUILDING: (80, 60),
    TargetType.ORDNANCE_AMMO_CACHE: (56, 40),
    TargetType.ORDNANCE_FUEL_DEPOT: (96, 64),
}


class SimDetector(Detector):
    def __init__(
        self,
        placements: List[SimTargetPlacement],
        noise_sigma_m: float = 2.0,
        false_positive_rate: float = 0.02,
        seed: Optional[int] = None,
    ) -> None:
        self._placements = placements
        self._noise_sigma = noise_sigma_m
        self._fp_rate = false_positive_rate
        self._rng = random.Random(seed)
        self._np_rng = np.random.default_rng(seed)
        self._fp_counter = 0

    def detect(self, frame: np.ndarray, timestamp: float) -> List[VisualTarget]:
        h, w = frame.shape[:2]
        results: List[VisualTarget] = []

        for p in self._placements:
            defaults = TARGET_DEFAULTS[p.target_type]
            noise_x = self._np_rng.normal(0, self._noise_sigma) if self._noise_sigma > 0 else 0.0
            noise_y = self._np_rng.normal(0, self._noise_sigma) if self._noise_sigma > 0 else 0.0
            pos = (
                p.position[0] + noise_x,
                p.position[1] + noise_y,
                p.position[2] if len(p.position) > 2 else 0.0,
            )

            confidence = max(0.5, 1.0 - (self._noise_sigma / 20.0)) if self._noise_sigma > 0 else 1.0

            bb_w, bb_h = _BB_SIZES.get(p.target_type, (48, 32))
            bb_x = self._rng.randint(0, max(0, w - bb_w))
            bb_y = self._rng.randint(0, max(0, h - bb_h))

            occupancy = p.occupancy_override if p.occupancy_override is not None else defaults["default_occupancy"]

            results.append(VisualTarget(
                id=p.id,
                target_type=p.target_type,
                position=pos,
                bounding_box=BoundingBox(x=bb_x, y=bb_y, width=bb_w, height=bb_h),
                confidence=confidence,
                occupancy_estimate=occupancy,
                base_value=defaults["base_value"],
                blast_radius_m=defaults["blast_radius_m"],
                properties=p.properties or {},
            ))

        num_fp = sum(1 for _ in range(len(self._placements)) if self._rng.random() < self._fp_rate)
        if self._fp_rate >= 1.0:
            num_fp = max(1, len(self._placements))
        for _ in range(num_fp):
            self._fp_counter += 1
            fp_type = self._rng.choice(list(TargetType))
            fp_defaults = TARGET_DEFAULTS[fp_type]
            bb_w, bb_h = _BB_SIZES.get(fp_type, (48, 32))
            results.append(VisualTarget(
                id=f"fp-{self._fp_counter}",
                target_type=fp_type,
                position=(
                    self._rng.uniform(-1000, 1000),
                    self._rng.uniform(-1000, 1000),
                    0.0,
                ),
                bounding_box=BoundingBox(
                    x=self._rng.randint(0, max(0, w - bb_w)),
                    y=self._rng.randint(0, max(0, h - bb_h)),
                    width=bb_w,
                    height=bb_h,
                ),
                confidence=self._rng.uniform(0.3, 0.7),
                occupancy_estimate=0,
                base_value=fp_defaults["base_value"],
                blast_radius_m=fp_defaults["blast_radius_m"],
                properties={},
            ))

        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_detector.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/detector.py backend/tests/test_detector.py
git commit -m "feat: add Detector interface and SimDetector with noise + false positives"
```

---

### Task 4: Decision Engine Models + Engine (`backend/decision/`)

**Files:**
- Create: `backend/decision/__init__.py`
- Create: `backend/decision/models.py`
- Create: `backend/decision/engine.py`
- Test: `backend/tests/test_decision.py`

**Interfaces:**
- Consumes: `Threat` from `backend/csontology.py:168`; `CascadeResult` from `backend/vision/cascade.py`
- Produces: `EngagementMode`, `EngagementPriority`, `EngagementOrder`, `DecisionEngine` -- used by Task 8 (wargame), Task 9 (WebSocket), Task 10 (HUD)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_decision.py`:

```python
import pytest
from csontology import Threat, SwarmIntent
from vision.cascade import CascadeResult
from decision.models import EngagementMode, EngagementPriority, EngagementOrder
from decision.engine import DecisionEngine


def _make_threat(id: str, score: float, tti: float = 30.0) -> Threat:
    return Threat(
        id=id,
        score=score,
        time_to_impact_s=tti,
        value_at_risk=1_000_000.0,
        priority_rank=1,
        track_id=f"track-{id}",
        intent=SwarmIntent.SATURATION,
    )


def _make_cascade(target_id: str, expected_value: float, personnel: int = 0) -> CascadeResult:
    return CascadeResult(
        target_id=target_id,
        direct_value=expected_value * 0.5,
        cascade_value=expected_value,
        cascade_chain=[target_id],
        cascade_probability=1.0,
        expected_value=expected_value,
        personnel_at_risk=personnel,
    )


class TestDecisionEngine:
    def test_defensive_threats_only(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [_make_threat("d1", 0.9, 10.0), _make_threat("d2", 0.5, 50.0)]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        assert len(order.priorities) == 2
        assert order.priorities[0].target_id == "d1"
        assert order.priorities[0].source == "bulwark"
        assert order.mode == EngagementMode.ADVISORY

    def test_offensive_targets_only(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        cascades = [
            _make_cascade("t1", 3_000_000),
            _make_cascade("t2", 30_000),
        ]
        order = engine.merge(threats=[], cascade_results=cascades, now=0.0)
        assert len(order.priorities) == 2
        assert order.priorities[0].target_id == "t1"
        assert order.priorities[0].source == "vision"
        assert order.mode == EngagementMode.AUTO

    def test_merged_order_defensive_urgency_wins(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [_make_threat("d1", 0.95, 5.0)]
        cascades = [_make_cascade("t1", 10_000_000)]
        order = engine.merge(threats=threats, cascade_results=cascades, now=0.0)
        assert order.priorities[0].target_id == "d1"

    def test_merged_order_both_sources_present(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        threats = [_make_threat("d1", 0.5, 60.0)]
        cascades = [_make_cascade("t1", 500_000)]
        order = engine.merge(threats=threats, cascade_results=cascades, now=0.0)
        sources = {p.source for p in order.priorities}
        assert sources == {"bulwark", "vision"}

    def test_time_sensitivity_breaks_ties(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        threats = [
            _make_threat("d1", 0.7, 60.0),
            _make_threat("d2", 0.7, 10.0),
        ]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        assert order.priorities[0].target_id == "d2"

    def test_engagement_order_to_dict(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        threats = [_make_threat("d1", 0.8)]
        order = engine.merge(threats=threats, cascade_results=[], now=0.0)
        d = order.to_dict()
        assert d["mode"] == "auto"
        assert len(d["priorities"]) == 1
        assert "rationale" in d

    def test_personnel_impact_in_priority(self):
        engine = DecisionEngine(mode=EngagementMode.ADVISORY)
        cascades = [_make_cascade("t1", 500_000, personnel=20)]
        order = engine.merge(threats=[], cascade_results=cascades, now=0.0)
        assert order.priorities[0].personnel_impact == 20

    def test_empty_inputs(self):
        engine = DecisionEngine(mode=EngagementMode.AUTO)
        order = engine.merge(threats=[], cascade_results=[], now=0.0)
        assert len(order.priorities) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_decision.py -v`
Expected: `ModuleNotFoundError: No module named 'decision'`

- [ ] **Step 3: Write models**

Create `backend/decision/__init__.py`:

```python
```

Create `backend/decision/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any


class EngagementMode(Enum):
    AUTO = "auto"
    ADVISORY = "advisory"


@dataclass
class EngagementPriority:
    target_id: str
    source: str
    normalized_score: float
    time_sensitivity: float
    personnel_impact: int
    cascade_depth: int
    recommended_effector: str = "any"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "source": self.source,
            "normalized_score": round(self.normalized_score, 4),
            "time_sensitivity": round(self.time_sensitivity, 2),
            "personnel_impact": self.personnel_impact,
            "cascade_depth": self.cascade_depth,
            "recommended_effector": self.recommended_effector,
        }


@dataclass
class EngagementOrder:
    priorities: List[EngagementPriority]
    mode: EngagementMode
    timestamp: float
    rationale: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "ENGAGEMENT_ORDER",
            "mode": self.mode.value,
            "timestamp": self.timestamp,
            "priorities": [p.to_dict() for p in self.priorities],
            "rationale": self.rationale,
        }
```

- [ ] **Step 4: Write engine**

Create `backend/decision/engine.py`:

```python
from __future__ import annotations

from typing import List

from csontology import Threat
from vision.cascade import CascadeResult
from decision.models import EngagementMode, EngagementPriority, EngagementOrder


class DecisionEngine:
    def __init__(self, mode: EngagementMode = EngagementMode.ADVISORY) -> None:
        self._mode = mode

    @property
    def mode(self) -> EngagementMode:
        return self._mode

    @mode.setter
    def mode(self, value: EngagementMode) -> None:
        self._mode = value

    def merge(
        self,
        threats: List[Threat],
        cascade_results: List[CascadeResult],
        now: float,
    ) -> EngagementOrder:
        priorities: List[EngagementPriority] = []
        rationale: dict = {}

        for t in threats:
            time_sens = max(0.0, 1.0 - (t.time_to_impact_s or 300.0) / 300.0)
            score = t.score * 0.8 + time_sens * 0.2
            priorities.append(EngagementPriority(
                target_id=t.id,
                source="bulwark",
                normalized_score=min(1.0, score),
                time_sensitivity=t.time_to_impact_s or 300.0,
                personnel_impact=0,
                cascade_depth=0,
            ))
            rationale[t.id] = (
                f"Defensive threat: score={t.score:.2f}, "
                f"TTI={t.time_to_impact_s:.0f}s, intent={t.intent.value}"
            )

        if cascade_results:
            max_ev = max(c.expected_value for c in cascade_results)
            for c in cascade_results:
                norm_score = c.expected_value / max_ev if max_ev > 0 else 0.0
                priorities.append(EngagementPriority(
                    target_id=c.target_id,
                    source="vision",
                    normalized_score=min(1.0, norm_score * 0.7),
                    time_sensitivity=300.0,
                    personnel_impact=c.personnel_at_risk,
                    cascade_depth=len(c.cascade_chain) - 1,
                ))
                rationale[c.target_id] = (
                    f"Offensive target: EV=${c.expected_value:,.0f}, "
                    f"chain={len(c.cascade_chain)}, "
                    f"personnel={c.personnel_at_risk}"
                )

        priorities.sort(key=lambda p: (-p.normalized_score, p.time_sensitivity))

        return EngagementOrder(
            priorities=priorities,
            mode=self._mode,
            timestamp=now,
            rationale=rationale,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_decision.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/decision/__init__.py backend/decision/models.py backend/decision/engine.py backend/tests/test_decision.py
git commit -m "feat: add decision engine merging BULWARK threats with cascade scores"
```

---

### Task 5: SimFeedSource (`backend/vision/feed_source.py`)

**Files:**
- Create: `backend/vision/feed_source.py`
- Modify: `backend/requirements.txt` (add Pillow)
- Test: `backend/tests/test_feed_source.py`

**Interfaces:**
- Consumes: `SimTargetPlacement` from `backend/vision/detector.py`; `VideoStreamManager` from `backend/video/stream_proxy.py`
- Produces: `FeedSource` (ABC), `SimFeedSource`, `StreamFeedSource` -- used by Task 7 (pipeline), Task 8 (wargame)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_feed_source.py`:

```python
import pytest
import numpy as np
from vision.models import TargetType
from vision.detector import SimTargetPlacement
from vision.feed_source import FeedSource, SimFeedSource


class TestSimFeedSource:
    def test_returns_frame_and_timestamp(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (100, 200, 0))]
        source = SimFeedSource(placements=placements, resolution=(640, 480), fps=5.0)
        frame, ts = source.next_frame()
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (480, 640, 3)
        assert isinstance(ts, float)

    def test_resolution_matches(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (0, 0, 0))]
        source = SimFeedSource(placements=placements, resolution=(1280, 720))
        frame, _ = source.next_frame()
        assert frame.shape == (720, 1280, 3)

    def test_timestamps_increment(self):
        placements = [SimTargetPlacement("t1", TargetType.VEHICLE_CAR, (0, 0, 0))]
        source = SimFeedSource(placements=placements, fps=10.0)
        _, ts1 = source.next_frame()
        _, ts2 = source.next_frame()
        assert ts2 > ts1
        assert abs((ts2 - ts1) - 0.1) < 0.01

    def test_frame_not_all_black(self):
        placements = [
            SimTargetPlacement("t1", TargetType.VEHICLE_FUEL_TANKER, (100, 100, 0)),
            SimTargetPlacement("t2", TargetType.INFRA_BUILDING, (300, 300, 0)),
        ]
        source = SimFeedSource(placements=placements, resolution=(640, 480))
        frame, _ = source.next_frame()
        assert frame.sum() > 0

    def test_feed_source_is_abstract(self):
        with pytest.raises(TypeError):
            FeedSource()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_feed_source.py -v`
Expected: `ModuleNotFoundError: No module named 'vision.feed_source'`

- [ ] **Step 3: Add Pillow dependency**

Append to `backend/requirements.txt`:

```
Pillow>=10.0.0
```

- [ ] **Step 4: Write implementation**

Create `backend/vision/feed_source.py`:

```python
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from vision.detector import SimTargetPlacement
from vision.models import TargetType


_TARGET_COLORS = {
    TargetType.VEHICLE_CAR: (0, 180, 0),
    TargetType.VEHICLE_TRUCK: (0, 200, 50),
    TargetType.VEHICLE_APC: (0, 160, 80),
    TargetType.VEHICLE_FUEL_TANKER: (255, 100, 0),
    TargetType.PERSONNEL_INDIVIDUAL: (0, 100, 255),
    TargetType.PERSONNEL_GROUP: (0, 80, 200),
    TargetType.INFRA_GENERATOR: (200, 200, 0),
    TargetType.INFRA_ANTENNA: (180, 180, 0),
    TargetType.INFRA_BRIDGE: (160, 160, 160),
    TargetType.INFRA_BUILDING: (120, 120, 120),
    TargetType.ORDNANCE_AMMO_CACHE: (255, 0, 0),
    TargetType.ORDNANCE_FUEL_DEPOT: (255, 50, 50),
}

_TARGET_SIZES = {
    TargetType.VEHICLE_CAR: (20, 12),
    TargetType.VEHICLE_TRUCK: (28, 14),
    TargetType.VEHICLE_APC: (26, 16),
    TargetType.VEHICLE_FUEL_TANKER: (32, 14),
    TargetType.PERSONNEL_INDIVIDUAL: (4, 4),
    TargetType.PERSONNEL_GROUP: (16, 16),
    TargetType.INFRA_GENERATOR: (18, 18),
    TargetType.INFRA_ANTENNA: (8, 20),
    TargetType.INFRA_BRIDGE: (60, 16),
    TargetType.INFRA_BUILDING: (36, 28),
    TargetType.ORDNANCE_AMMO_CACHE: (24, 18),
    TargetType.ORDNANCE_FUEL_DEPOT: (40, 30),
}


class FeedSource(ABC):
    @abstractmethod
    def next_frame(self) -> Tuple[np.ndarray, float]:
        ...


class SimFeedSource(FeedSource):
    def __init__(
        self,
        placements: List[SimTargetPlacement],
        resolution: Tuple[int, int] = (1280, 720),
        fps: float = 5.0,
    ) -> None:
        self._placements = placements
        self._width, self._height = resolution
        self._fps = fps
        self._frame_count = 0
        self._start_time = time.monotonic()
        self._scene = self._render_scene()

    def _render_scene(self) -> np.ndarray:
        img = Image.new("RGB", (self._width, self._height), (30, 35, 30))
        draw = ImageDraw.Draw(img)

        for i in range(0, self._width, 40):
            draw.line([(i, 0), (i, self._height)], fill=(40, 45, 40))
        for i in range(0, self._height, 40):
            draw.line([(0, i), (self._width, i)], fill=(40, 45, 40))

        cx, cy = self._width // 2, self._height // 2
        scale = min(self._width, self._height) / 2000.0

        for p in self._placements:
            color = _TARGET_COLORS.get(p.target_type, (128, 128, 128))
            tw, th = _TARGET_SIZES.get(p.target_type, (16, 16))
            px = int(cx + p.position[0] * scale)
            py = int(cy - p.position[1] * scale)
            draw.rectangle(
                [px - tw // 2, py - th // 2, px + tw // 2, py + th // 2],
                fill=color,
                outline=(255, 255, 255),
            )

        return np.array(img)

    def next_frame(self) -> Tuple[np.ndarray, float]:
        ts = self._start_time + self._frame_count / self._fps
        self._frame_count += 1
        return self._scene.copy(), ts


class StreamFeedSource(FeedSource):
    def __init__(self, video_manager: object, drone_id: str) -> None:
        self._vm = video_manager
        self._drone_id = drone_id
        self._frame_count = 0

    def next_frame(self) -> Tuple[np.ndarray, float]:
        frame_bytes = self._vm.get_latest_frame(self._drone_id)  # type: ignore[attr-defined]
        if frame_bytes is None:
            arr = np.zeros((720, 1280, 3), dtype=np.uint8)
        else:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(frame_bytes))
            arr = np.array(img)
        self._frame_count += 1
        return arr, time.monotonic()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && pip install Pillow>=10.0.0 && python -m pytest tests/test_feed_source.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/feed_source.py backend/tests/test_feed_source.py backend/requirements.txt
git commit -m "feat: add FeedSource interface and SimFeedSource with Pillow rendering"
```

---

### Task 6: Target Scenarios (`backend/vision/scenarios.py`)

**Files:**
- Create: `backend/vision/scenarios.py`
- Test: `backend/tests/test_vision_scenarios.py`

**Interfaces:**
- Consumes: `SimTargetPlacement` from `backend/vision/detector.py`; `DependencyEdge`, `DependencyType` from `backend/vision/cascade.py`; `TargetType` from `backend/vision/models.py`
- Produces: `TargetScenario`, `load_target_scenario()`, built-in scenarios -- used by Task 8 (wargame)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_vision_scenarios.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_vision_scenarios.py -v`
Expected: `ModuleNotFoundError: No module named 'vision.scenarios'`

- [ ] **Step 3: Write implementation**

Create `backend/vision/scenarios.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_vision_scenarios.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/scenarios.py backend/tests/test_vision_scenarios.py
git commit -m "feat: add target scenarios (convoy, dispersed, base) for wargames"
```

---

### Task 7: Vision Pipeline Integration Test

**Files:**
- Test: `backend/tests/test_visual_pipeline.py`

**Interfaces:**
- Consumes: All vision + decision modules from Tasks 1-6
- Produces: Validates end-to-end pipeline works before wargame integration

- [ ] **Step 1: Write the integration test**

Create `backend/tests/test_visual_pipeline.py`:

```python
import pytest
import numpy as np
from vision.models import TargetType
from vision.detector import SimDetector
from vision.feed_source import SimFeedSource
from vision.cascade import CascadeEngine
from vision.scenarios import load_target_scenario
from decision.engine import DecisionEngine
from decision.models import EngagementMode


class TestFullPipeline:
    def test_convoy_pipeline(self):
        scenario = load_target_scenario("ground_strike_convoy")
        feed = SimFeedSource(placements=scenario.placements, resolution=(640, 480))
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)

        frame, ts = feed.next_frame()
        targets = detector.detect(frame, ts)
        assert len(targets) == len(scenario.placements)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()
        assert len(results) == len(targets)
        assert results[0].expected_value > results[-1].expected_value

        decision = DecisionEngine(mode=EngagementMode.AUTO)
        order = decision.merge(threats=[], cascade_results=results, now=ts)
        assert len(order.priorities) == len(targets)
        assert order.mode == EngagementMode.AUTO

    def test_base_scenario_generator_has_high_cascade(self):
        scenario = load_target_scenario("ground_strike_base")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()

        gen_result = next(r for r in results if r.target_id == "generator")
        assert len(gen_result.cascade_chain) >= 3
        assert gen_result.cascade_value > gen_result.direct_value

    def test_dispersed_no_cascades(self):
        scenario = load_target_scenario("ground_strike_dispersed")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()

        for r in results:
            assert len(r.cascade_chain) == 1
            assert r.cascade_value == r.direct_value

    def test_pipeline_with_noise_still_works(self):
        scenario = load_target_scenario("ground_strike_convoy")
        detector = SimDetector(
            placements=scenario.placements,
            noise_sigma_m=5.0,
            false_positive_rate=0.05,
            seed=42,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)
        assert len(targets) >= len(scenario.placements)

        engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        results = engine.score_all()
        assert len(results) > 0

    def test_combined_defensive_and_offensive(self):
        from csontology import Threat, SwarmIntent

        scenario = load_target_scenario("ground_strike_convoy")
        detector = SimDetector(placements=scenario.placements, noise_sigma_m=0.0, false_positive_rate=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        targets = detector.detect(frame, 0.0)

        cascade_engine = CascadeEngine(targets=targets, dependencies=scenario.dependencies)
        cascade_results = cascade_engine.score_all()

        threats = [
            Threat(id="swarm-1", score=0.95, time_to_impact_s=8.0,
                   value_at_risk=2_000_000, priority_rank=1,
                   track_id="track-1", intent=SwarmIntent.SATURATION),
            Threat(id="swarm-2", score=0.6, time_to_impact_s=45.0,
                   value_at_risk=500_000, priority_rank=2,
                   track_id="track-2", intent=SwarmIntent.PROBE),
        ]

        decision = DecisionEngine(mode=EngagementMode.ADVISORY)
        order = decision.merge(threats=threats, cascade_results=cascade_results, now=0.0)

        sources = {p.source for p in order.priorities}
        assert "bulwark" in sources
        assert "vision" in sources
        assert order.priorities[0].source == "bulwark"
        assert order.priorities[0].target_id == "swarm-1"

        d = order.to_dict()
        assert d["mode"] == "advisory"
        assert len(d["priorities"]) == len(threats) + len(cascade_results)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_visual_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/tests/test_visual_pipeline.py
git commit -m "test: add end-to-end visual pipeline integration tests"
```

---

### Task 8: Protocol + WebSocket + Wargame Integration

**Files:**
- Modify: `src/shared/protocol.js:11-26` (add new message types)
- Modify: `backend/wargame/frame.py:136-170` (extend Frame with vision data)
- Modify: `backend/wargame/scenario.py:87-108` (add target_scenario field)
- Modify: `backend/wargame/runner.py:148-168` (add vision pipeline to tick)
- Modify: `backend/api/websocket.py:120-153` (add ENGAGE_CONFIRM handler)
- Test: `backend/tests/test_wargame_vision.py`

**Interfaces:**
- Consumes: All vision + decision modules; existing `Frame`, `Scenario`, `WargameRunner`, WebSocket handler
- Produces: Extended wargame loop with vision pipeline; new WebSocket messages; combined scenarios

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_wargame_vision.py`:

```python
import pytest
from wargame.scenario import Scenario, load_scenario
from wargame.frame import Frame
from vision.cascade import CascadeResult
from decision.models import EngagementOrder


class TestScenarioWithTargets:
    def test_scenario_accepts_target_scenario_name(self):
        s = Scenario(
            name="test_combined",
            swarm_intent="SATURATION",
            swarm_count=10,
            target_scenario="ground_strike_convoy",
        )
        assert s.target_scenario == "ground_strike_convoy"

    def test_existing_scenarios_have_no_targets(self):
        s = load_scenario("saturation_1000")
        assert s.target_scenario is None


class TestFrameWithVision:
    def test_frame_includes_cascade_results(self):
        cr = CascadeResult(
            target_id="t1",
            direct_value=100_000,
            cascade_value=500_000,
            cascade_chain=["t1", "t2"],
            cascade_probability=0.8,
            expected_value=400_000,
            personnel_at_risk=5,
        )
        f = Frame(
            metrics=None,
            tracks=[],
            defenders=[],
            cascade_results=[cr],
        )
        d = f.to_dict()
        assert "cascade_results" in d
        assert len(d["cascade_results"]) == 1
        assert d["cascade_results"][0]["target_id"] == "t1"

    def test_frame_includes_engagement_order(self):
        from decision.models import EngagementMode, EngagementPriority, EngagementOrder
        order = EngagementOrder(
            priorities=[EngagementPriority("t1", "vision", 0.9, 300.0, 5, 2)],
            mode=EngagementMode.AUTO,
            timestamp=1.0,
        )
        f = Frame(
            metrics=None,
            tracks=[],
            defenders=[],
            engagement_order=order,
        )
        d = f.to_dict()
        assert "engagement_order" in d
        assert d["engagement_order"]["mode"] == "auto"

    def test_existing_frame_still_works(self):
        f = Frame(metrics=None, tracks=[], defenders=[])
        d = f.to_dict()
        assert "cascade_results" in d
        assert d["cascade_results"] == []
        assert d["engagement_order"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_wargame_vision.py -v`
Expected: Failures due to missing `target_scenario` field on Scenario and missing `cascade_results` on Frame

- [ ] **Step 3: Add message types to protocol.js**

Read `src/shared/protocol.js` and add after existing MessageType entries (around line 25):

```javascript
  VISUAL_TARGETS: 'VISUAL_TARGETS',
  CASCADE_SCORES: 'CASCADE_SCORES',
  ENGAGEMENT_ORDER: 'ENGAGEMENT_ORDER',
  ENGAGE_CONFIRM: 'ENGAGE_CONFIRM',
```

- [ ] **Step 4: Extend Scenario dataclass**

Read `backend/wargame/scenario.py` and add field to the `Scenario` dataclass after `hardened_fraction` (around line 104):

```python
    target_scenario: Optional[str] = None
```

Add import at top:

```python
from typing import Optional
```

- [ ] **Step 5: Extend Frame dataclass**

Read `backend/wargame/frame.py` and add fields to the `Frame` dataclass after existing fields:

```python
    cascade_results: List["CascadeResult"] = field(default_factory=list)
    engagement_order: Optional["EngagementOrder"] = None
```

Update `to_dict()` to include:

```python
    "cascade_results": [cr.to_dict() for cr in self.cascade_results],
    "engagement_order": self.engagement_order.to_dict() if self.engagement_order else None,
```

Add imports at top:

```python
from typing import Optional
```

- [ ] **Step 6: Add vision pipeline to wargame runner tick**

Read `backend/wargame/runner.py`. In the `__init__` method, after existing setup, add conditional vision pipeline initialization:

```python
        self._vision_detector: Optional[SimDetector] = None
        self._vision_feed: Optional[SimFeedSource] = None
        self._cascade_dependencies: List[DependencyEdge] = []
        self._decision_engine = DecisionEngine(mode=EngagementMode.AUTO)

        if scenario.target_scenario:
            from vision.scenarios import load_target_scenario
            ts = load_target_scenario(scenario.target_scenario)
            self._vision_detector = SimDetector(
                placements=ts.placements, noise_sigma_m=2.0, false_positive_rate=0.02, seed=scenario.seed
            )
            self._vision_feed = SimFeedSource(placements=ts.placements, resolution=(1280, 720))
            self._cascade_dependencies = ts.dependencies
```

In the `_step` method (or `_build_frame`), after threat assessment, add:

```python
        cascade_results = []
        engagement_order = None
        if self._vision_detector and self._vision_feed:
            frame_img, frame_ts = self._vision_feed.next_frame()
            visual_targets = self._vision_detector.detect(frame_img, frame_ts)
            cascade_engine = CascadeEngine(targets=visual_targets, dependencies=self._cascade_dependencies)
            cascade_results = cascade_engine.score_all()
            engagement_order = self._decision_engine.merge(
                threats=threats, cascade_results=cascade_results, now=now
            )
```

Pass `cascade_results` and `engagement_order` to `Frame()`.

Add imports at top of runner.py:

```python
from vision.detector import SimDetector
from vision.feed_source import SimFeedSource
from vision.cascade import CascadeEngine, DependencyEdge
from decision.engine import DecisionEngine
from decision.models import EngagementMode
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_wargame_vision.py -v`
Expected: 5 passed

- [ ] **Step 8: Run full existing test suite to verify no regressions**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest -m "not slow" -v`
Expected: All existing tests pass

- [ ] **Step 9: Commit**

```bash
cd /Users/jay/DroneNexus
git add src/shared/protocol.js backend/wargame/scenario.py backend/wargame/frame.py backend/wargame/runner.py backend/api/websocket.py backend/tests/test_wargame_vision.py
git commit -m "feat: integrate vision pipeline into wargame loop and protocol"
```

---

### Task 9: HUD Camera Overlay + Engagement Panel

**Files:**
- Create: `src/hud/js/cascadeView.js`
- Create: `src/hud/js/engagementPanel.js`
- Modify: `src/hud/bulwark.html` (add panels, load scripts)

**Interfaces:**
- Consumes: WebSocket messages `VISUAL_TARGETS`, `CASCADE_SCORES`, `ENGAGEMENT_ORDER` from backend; sends `ENGAGE_CONFIRM` uplink
- Produces: Camera overlay with bounding boxes, cascade visualization, engagement order sidebar

- [ ] **Step 1: Create cascadeView.js**

Create `src/hud/js/cascadeView.js`:

```javascript
class CascadeView {
  constructor(canvasId) {
    this._canvas = document.getElementById(canvasId);
    this._ctx = this._canvas ? this._canvas.getContext('2d') : null;
    this._targets = [];
    this._cascadeScores = [];
    this._selectedTargetId = null;
    this._tacticalMode = false;
  }

  updateTargets(targets) {
    this._targets = targets || [];
    this._draw();
  }

  updateCascadeScores(scores) {
    this._cascadeScores = scores || [];
    this._draw();
  }

  toggleTacticalMode() {
    this._tacticalMode = !this._tacticalMode;
    this._draw();
    return this._tacticalMode;
  }

  selectTarget(targetId) {
    this._selectedTargetId = targetId === this._selectedTargetId ? null : targetId;
    this._draw();
  }

  _draw() {
    if (!this._ctx) return;
    const ctx = this._ctx;
    const w = this._canvas.width;
    const h = this._canvas.height;
    ctx.clearRect(0, 0, w, h);

    const scoreMap = {};
    this._cascadeScores.forEach((cs, i) => {
      scoreMap[cs.target_id] = { rank: i + 1, ...cs };
    });

    for (const t of this._targets) {
      const cs = scoreMap[t.id];
      const rank = cs ? cs.rank : 999;
      const bb = t.bounding_box;

      const color = rank <= 1 ? '#ff3333'
                  : rank <= 3 ? '#ffaa00'
                  : '#33cc33';

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(bb.x, bb.y, bb.width, bb.height);

      ctx.fillStyle = color;
      ctx.font = 'bold 12px JetBrains Mono, monospace';
      const label = `#${rank}`;
      ctx.fillText(label, bb.x, bb.y - 4);

      if (cs) {
        const evLabel = `$${(cs.expected_value / 1000).toFixed(0)}k`;
        ctx.fillStyle = '#ffffff';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.fillText(evLabel, bb.x + bb.width + 4, bb.y + 12);
      }

      if (this._tacticalMode && cs && cs.cascade_chain && cs.cascade_chain.length > 1) {
        ctx.beginPath();
        ctx.arc(
          bb.x + bb.width / 2,
          bb.y + bb.height / 2,
          t.blast_radius_m * 0.5,
          0, Math.PI * 2
        );
        ctx.strokeStyle = `${color}44`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      if (this._selectedTargetId === t.id && cs) {
        this._drawCascadeDetail(ctx, bb, cs);
      }
    }
  }

  _drawCascadeDetail(ctx, bb, cs) {
    const x = bb.x + bb.width + 8;
    const y = bb.y;
    ctx.fillStyle = '#000000cc';
    ctx.fillRect(x, y, 200, 20 + cs.cascade_chain.length * 16);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 11px JetBrains Mono, monospace';
    ctx.fillText(`CASCADE: $${(cs.cascade_value / 1000).toFixed(0)}k`, x + 4, y + 14);
    ctx.font = '10px JetBrains Mono, monospace';
    cs.cascade_chain.forEach((id, i) => {
      ctx.fillText(`${i + 1}. ${id}`, x + 8, y + 30 + i * 16);
    });
  }

  handleClick(event) {
    const rect = this._canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    for (const t of this._targets) {
      const bb = t.bounding_box;
      if (mx >= bb.x && mx <= bb.x + bb.width && my >= bb.y && my <= bb.y + bb.height) {
        this.selectTarget(t.id);
        return t.id;
      }
    }
    this.selectTarget(null);
    return null;
  }
}
```

- [ ] **Step 2: Create engagementPanel.js**

Create `src/hud/js/engagementPanel.js`:

```javascript
class EngagementPanel {
  constructor(containerId, onConfirm) {
    this._container = document.getElementById(containerId);
    this._onConfirm = onConfirm || (() => {});
    this._order = null;
  }

  update(engagementOrder) {
    this._order = engagementOrder;
    this._render();
  }

  _render() {
    if (!this._container) return;
    if (!this._order || !this._order.priorities || this._order.priorities.length === 0) {
      this._container.innerHTML = '<div class="ep-empty">NO TARGETS</div>';
      return;
    }

    const isAdvisory = this._order.mode === 'advisory';
    let html = '<div class="ep-header">ENGAGEMENT ORDER</div>';

    this._order.priorities.forEach((p, i) => {
      const sourceIcon = p.source === 'bulwark' ? '&#x1f6e1;' : '&#x1f3af;';
      const urgencyClass = p.normalized_score > 0.7 ? 'ep-urgent'
                         : p.normalized_score > 0.4 ? 'ep-moderate'
                         : 'ep-low';

      html += `<div class="ep-entry ${urgencyClass}" data-target="${p.target_id}">
        <span class="ep-rank">#${i + 1}</span>
        <span class="ep-source">${sourceIcon}</span>
        <span class="ep-id">${p.target_id}</span>
        <span class="ep-score">${(p.normalized_score * 100).toFixed(0)}%</span>
        ${p.personnel_impact > 0 ? `<span class="ep-personnel">${p.personnel_impact}p</span>` : ''}
        ${isAdvisory ? `<button class="ep-confirm" onclick="window._epConfirm('${p.target_id}')">CONFIRM</button>` : ''}
      </div>`;
    });

    this._container.innerHTML = html;
    window._epConfirm = (targetId) => this._onConfirm(targetId);
  }
}
```

- [ ] **Step 3: Integrate into bulwark.html**

Read `src/hud/bulwark.html`. Before the closing `</body>` tag, add:

```html
<!-- Cascade targeting overlay canvas -->
<canvas id="cascade-overlay" style="position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:400;"></canvas>

<!-- Engagement order panel -->
<div id="engagement-panel" style="position:fixed;top:160px;right:12px;width:260px;max-height:60vh;overflow-y:auto;z-index:500;font-family:'JetBrains Mono',monospace;font-size:11px;"></div>

<!-- Tactical mode toggle -->
<button id="tactical-toggle" style="position:fixed;bottom:12px;right:12px;z-index:500;padding:6px 12px;background:#1a1a2e;color:#0f0;border:1px solid #0f0;font-family:'JetBrains Mono',monospace;cursor:pointer;">TACTICAL</button>

<script src="js/cascadeView.js"></script>
<script src="js/engagementPanel.js"></script>
<script>
(function() {
  const cascadeCanvas = document.getElementById('cascade-overlay');
  const cascadeView = new CascadeView('cascade-overlay');
  const engPanel = new EngagementPanel('engagement-panel', function(targetId) {
    if (window._bulwarkWs && window._bulwarkWs.readyState === 1) {
      window._bulwarkWs.send(JSON.stringify({
        type: 'ENGAGE_CONFIRM',
        target_id: targetId,
        timestamp: Date.now() / 1000
      }));
    }
  });

  cascadeCanvas.addEventListener('click', function(e) {
    cascadeCanvas.style.pointerEvents = 'auto';
    cascadeView.handleClick(e);
  });

  document.getElementById('tactical-toggle').addEventListener('click', function() {
    const active = cascadeView.toggleTacticalMode();
    this.style.borderColor = active ? '#ff3333' : '#0f0';
    this.style.color = active ? '#ff3333' : '#0f0';
    this.textContent = active ? 'TACTICAL ON' : 'TACTICAL';
    cascadeCanvas.style.pointerEvents = active ? 'auto' : 'none';
  });

  const origOnMessage = window._bulwarkOnMessage || function(){};
  window._bulwarkOnMessage = function(data) {
    origOnMessage(data);
    if (data.cascade_results) {
      cascadeView.updateCascadeScores(data.cascade_results);
    }
    if (data.visual_targets) {
      cascadeView.updateTargets(data.visual_targets);
    }
    if (data.engagement_order) {
      engPanel.update(data.engagement_order);
    }
  };

  function resizeCanvas() {
    cascadeCanvas.width = window.innerWidth;
    cascadeCanvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();
})();
</script>

<style>
  .ep-header { color: #0f0; font-weight: bold; padding: 8px; border-bottom: 1px solid #0f033; }
  .ep-entry { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-bottom: 1px solid #1a1a2e; }
  .ep-urgent { background: #330000; }
  .ep-moderate { background: #332200; }
  .ep-low { background: #003300; }
  .ep-rank { color: #888; width: 28px; }
  .ep-source { font-size: 14px; }
  .ep-id { flex: 1; color: #ccc; overflow: hidden; text-overflow: ellipsis; }
  .ep-score { color: #0f0; width: 36px; text-align: right; }
  .ep-personnel { color: #ff6; width: 28px; text-align: right; }
  .ep-confirm { background: #0a0; color: #000; border: none; padding: 2px 8px; cursor: pointer; font-family: inherit; font-size: 10px; font-weight: bold; }
  .ep-confirm:hover { background: #0f0; }
  .ep-empty { color: #555; padding: 12px; text-align: center; }
  #engagement-panel { background: #0a0a14ee; border: 1px solid #0f033; border-radius: 4px; }
</style>
```

- [ ] **Step 4: Verify bulwark.html loads without JS errors**

Run: `cd /Users/jay/DroneNexus && python3 -c "
import http.server, threading, webbrowser
handler = http.server.SimpleHTTPRequestHandler
server = http.server.HTTPServer(('localhost', 8765), handler)
print('Serving at http://localhost:8765/src/hud/bulwark.html')
server.handle_request()
"`

Open in browser, check console for errors. The cascade overlay and engagement panel should render (empty, since no WebSocket data).

- [ ] **Step 5: Commit**

```bash
cd /Users/jay/DroneNexus
git add src/hud/js/cascadeView.js src/hud/js/engagementPanel.js src/hud/bulwark.html src/shared/protocol.js
git commit -m "feat: add cascade overlay and engagement panel to BULWARK HUD"
```

---

### Task 10: Combined Wargame Scenario + Regression

**Files:**
- Modify: `backend/vision/scenarios.py` (add combined_saturation_strike)
- Modify: `backend/wargame/scenario.py` (add combined preset)
- Test: `backend/tests/test_combined_wargame.py`

**Interfaces:**
- Consumes: Everything from Tasks 1-9
- Produces: Working combined wargame scenario, full regression pass

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_combined_wargame.py`:

```python
import pytest
from wargame.scenario import Scenario, load_scenario


@pytest.mark.slow
class TestCombinedWargame:
    def test_combined_scenario_loads(self):
        s = load_scenario("combined_saturation_strike")
        assert s.target_scenario == "ground_strike_base"
        assert s.swarm_count >= 100

    @pytest.mark.asyncio
    async def test_combined_scenario_runs(self):
        from wargame.runner import WargameRunner
        s = load_scenario("combined_saturation_strike")
        runner = WargameRunner(s)
        frame_count = 0
        has_cascade = False
        has_engagement_order = False

        async for frame in runner.run(pace=False):
            frame_count += 1
            if frame.cascade_results:
                has_cascade = True
            if frame.engagement_order:
                has_engagement_order = True
            if frame_count > 20:
                break

        assert frame_count > 0
        assert has_cascade
        assert has_engagement_order

    @pytest.mark.asyncio
    async def test_existing_scenario_unaffected(self):
        from wargame.runner import WargameRunner
        s = load_scenario("skirmish_80")
        runner = WargameRunner(s)
        frame_count = 0
        async for frame in runner.run(pace=False):
            frame_count += 1
            assert frame.cascade_results == []
            assert frame.engagement_order is None
            if frame_count > 10:
                break
        assert frame_count > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_combined_wargame.py -v`
Expected: `KeyError: 'combined_saturation_strike'`

- [ ] **Step 3: Add combined scenario to wargame presets**

Read `backend/wargame/scenario.py`. In the built-in presets section (around line 337-343), add:

```python
    "combined_saturation_strike": Scenario(
        name="combined_saturation_strike",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=500,
        unit_cost=500.0,
        sensors=[...],  # copy from saturation_1000 preset
        defenders=[...],  # copy from saturation_1000 preset
        target_scenario="ground_strike_base",
        tick_hz=5.0,
        max_ticks=600,
        seed=42,
    ),
```

Use the exact sensor and defender configs from the existing `saturation_1000` preset.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest tests/test_combined_wargame.py -v`
Expected: 3 passed

- [ ] **Step 5: Run full regression**

Run: `cd /Users/jay/DroneNexus/backend && python -m pytest -m "not slow" -v`
Expected: All tests pass, including all original tests

- [ ] **Step 6: Commit**

```bash
cd /Users/jay/DroneNexus
git add backend/vision/scenarios.py backend/wargame/scenario.py backend/tests/test_combined_wargame.py
git commit -m "feat: add combined_saturation_strike scenario and regression tests"
```

---

## Task Dependency Graph

```
Task 1 (models) ─────┬──→ Task 2 (cascade) ──┬──→ Task 4 (decision) ──→ Task 7 (integration test)
                      │                        │                              │
                      ├──→ Task 3 (detector) ──┤                              │
                      │                        │                              ▼
                      └──→ Task 5 (feed src) ──┴──→ Task 6 (scenarios) ──→ Task 8 (wargame + protocol)
                                                                              │
                                                                              ▼
                                                                         Task 9 (HUD) ──→ Task 10 (combined + regression)
```

Tasks 2, 3, 5 can run in parallel after Task 1 completes.
Task 4 needs Task 2.
Task 6 needs Tasks 2, 3, 5.
Task 7 needs Tasks 1-6.
Tasks 8-10 are sequential.
