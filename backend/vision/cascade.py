from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any

from vision.models import VisualTarget


PRUNE_THRESHOLD = 0.05
_FLOAT_EPSILON = 1e-9


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
            if prob < PRUNE_THRESHOLD - _FLOAT_EPSILON and node_id != start_id:
                continue
            visited.add(node_id)
            node = self._targets[node_id]

            chain.append(node_id)
            if is_kinetic:
                total_value += node.base_value
                if node_id != start_id:
                    min_chain_prob = min(min_chain_prob, prob)
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

        # If BFS traversed secondaries, use the minimum path probability.
        # If all secondaries were pruned (chain length == 1) but there are
        # proximity edges from the start, cascade_probability must still
        # reflect that secondary effects are possible but uncertain.
        direct_edges = prox_edges.get(start_id, [])
        if len(chain) > 1:
            cascade_prob = min_chain_prob
        elif direct_edges:
            cascade_prob = max(p for _, p in direct_edges)
        else:
            cascade_prob = 1.0

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
