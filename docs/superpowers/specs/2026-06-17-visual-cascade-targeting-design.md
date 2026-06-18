# Visual Cascade Targeting System -- Design Spec

**Date:** 2026-06-17
**Status:** Draft
**Approach:** Dual-Layer Scoring (Approach 3)

## Overview

Add a visual threat assessment layer to OVERWATCH/BULWARK. Drone camera feeds (simulated now, real CV later) identify ground targets, score them by cascading damage potential (kinetic chain reactions + logical dependency graphs), and feed into a unified decision engine that merges offensive ground-target priorities with defensive incoming-threat priorities into one engagement order.

Wargame mode: full auto-engage. Live mode: advisory with operator confirmation.

## Goals

1. Camera feed detects and classifies ground targets (vehicles, personnel, infrastructure, ordnance)
2. Cascade damage engine scores each target by total downstream destruction if engaged
3. Decision engine merges BULWARK defensive scores with offensive cascade scores into one ranked engagement order
4. HUD displays camera feed with bounding boxes, priority rankings, and expandable cascade visualizations
5. Wargame scenarios test combined attack + defense with cascade dynamics
6. Architecture supports drop-in replacement of simulated CV with real YOLO/vision models

## Non-Goals

- Real CV model training or deployment (future)
- Hardware integration with actual drone cameras (future)
- Multi-drone camera fusion -- single feed per view for now
- 3D volumetric blast modeling -- 2D radius approximation is sufficient

---

## Module 1: Vision Pipeline (`backend/vision/`)

### Files

| File | Purpose |
|------|---------|
| `models.py` | Data models: `VisualTarget`, `TargetType`, `BoundingBox` |
| `feed_source.py` | Abstract `FeedSource` interface + `SimFeedSource` (synthetic frames) + `StreamFeedSource` (wraps existing `VideoStreamManager`) |
| `detector.py` | Abstract `Detector` interface + `SimDetector` (ground truth from sim) + `CVDetector` stub (future YOLO wrapper) |
| `cascade.py` | Cascade damage engine: proximity graph, dependency graph, scoring |
| `scenarios.py` | Pre-built target layouts for wargame scenarios |

### Data Models

```python
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

@dataclass
class VisualTarget:
    id: str
    target_type: TargetType
    position: Vec3                    # ENU meters
    bounding_box: BoundingBox         # pixel coords for HUD overlay
    confidence: float                 # 0..1
    occupancy_estimate: int           # estimated people in/on target
    base_value: float                 # dollar value of target alone
    blast_radius_m: float             # kinetic chain radius (0 for personnel)
    properties: dict                  # type-specific (fuel_capacity_l, structural_class, etc.)

@dataclass
class BoundingBox:
    x: int
    y: int
    width: int
    height: int
```

### Target Type Defaults

| Type | Base Value ($) | Blast Radius (m) | Default Occupancy |
|------|---------------|-------------------|-------------------|
| VEHICLE_CAR | 30,000 | 10 | 3 |
| VEHICLE_TRUCK | 80,000 | 15 | 2 |
| VEHICLE_APC | 500,000 | 20 | 8 |
| VEHICLE_FUEL_TANKER | 200,000 | 50 | 2 |
| PERSONNEL_INDIVIDUAL | 0 | 0 | 1 |
| PERSONNEL_GROUP | 0 | 0 | estimated |
| INFRA_GENERATOR | 150,000 | 5 | 0 |
| INFRA_ANTENNA | 100,000 | 3 | 0 |
| INFRA_BRIDGE | 2,000,000 | 0 | 0 |
| INFRA_BUILDING | 500,000 | 10 | variable |
| ORDNANCE_AMMO_CACHE | 1,000,000 | 80 | 0 |
| ORDNANCE_FUEL_DEPOT | 3,000,000 | 100 | 0 |

### Feed Source Interface

```python
class FeedSource(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def next_frame(self) -> tuple[np.ndarray, float]: ...  # (frame, timestamp)

class SimFeedSource(FeedSource):
    """Renders top-down synthetic scene with placed targets.
    Returns frames + ground truth target positions."""

    def __init__(self, scenario: TargetScenario, resolution: tuple[int, int] = (1280, 720), fps: float = 5.0): ...

class StreamFeedSource(FeedSource):
    """Wraps existing VideoStreamManager for real camera feeds."""

    def __init__(self, video_manager: VideoStreamManager, drone_id: str): ...
```

### Detector Interface

```python
class Detector(ABC):
    async def detect(self, frame: np.ndarray, timestamp: float) -> list[VisualTarget]: ...

class SimDetector(Detector):
    """Returns ground truth from SimFeedSource. Perfect detection with configurable noise."""

    def __init__(self, scenario: TargetScenario, noise_sigma_m: float = 2.0, false_positive_rate: float = 0.02): ...

class CVDetector(Detector):
    """Future: wraps YOLO or other vision model. Same interface."""
    pass
```

### Data Flow

```
FeedSource.next_frame() -> Detector.detect(frame) -> List[VisualTarget]
                                                          |
                                                          v
                                                   CascadeEngine.score()
```

---

## Module 2: Cascade Damage Engine (`backend/vision/cascade.py`)

### Proximity Graph (Kinetic Chain)

For each target pair (A, B):
- If distance(A, B) < A.blast_radius_m: edge A -> B exists
- Kill probability decays linearly: `p_kill = max(0, 1 - distance / blast_radius)`
- Chain reactions propagate: A explodes -> hits B -> B explodes -> hits C
- Visited set prevents infinite loops

### Dependency Graph (Logical Chain)

Explicit edges defined per scenario:

```python
class DependencyType(Enum):
    POWERS = "powers"                    # generator -> command post
    ENABLES_MOVEMENT = "enables_movement"  # bridge -> convoy
    PROVIDES_COMMS = "provides_comms"    # antenna -> site
    SHELTERS = "shelters"                # building -> personnel inside
    SUPPLIES = "supplies"                # fuel depot -> vehicle fleet

@dataclass
class DependencyEdge:
    source_id: str
    target_id: str
    dependency_type: DependencyType
    impact_factor: float              # 0..1, how much of target's value is lost
```

When a dependency source is destroyed:
- Each dependent loses `value * impact_factor`
- Cascades through the graph (generator powers antenna, antenna provides comms to command post)

### Cascade Scoring

```python
@dataclass
class CascadeResult:
    target_id: str
    direct_value: float              # dollar value of this target alone
    cascade_value: float             # total value destroyed if chain fully propagates
    cascade_chain: list[str]         # ordered list of targets destroyed
    cascade_probability: float       # probability full chain fires (product of edge probs)
    expected_value: float            # cascade_value * cascade_probability
    personnel_at_risk: int           # total people in the cascade chain
```

**Algorithm:** Weighted BFS from each target node.
- At each hop, multiply running probability by edge kill probability
- Accumulate value of each reached node
- Track visited set to prevent cycles
- Prune branches below 5% probability threshold
- Return sorted by `expected_value` descending

---

## Module 3: Decision Engine (`backend/decision/`)

### Files

| File | Purpose |
|------|---------|
| `engine.py` | `DecisionEngine` -- merges threat + cascade scores into unified engagement order |
| `models.py` | `EngagementPriority`, `EngagementOrder`, `EngagementMode` |

### Unified Scoring

The decision engine normalizes both input streams to 0-1 and produces a single ranked list:

```python
class EngagementMode(Enum):
    AUTO = "auto"          # wargame -- execute immediately
    ADVISORY = "advisory"  # live -- recommend, wait for confirm

@dataclass
class EngagementPriority:
    target_id: str
    source: str                      # "bulwark" or "vision"
    normalized_score: float          # 0..1
    time_sensitivity: float          # seconds until opportunity expires
    personnel_impact: int            # people affected
    cascade_depth: int               # 0 for direct threats, N for chain length
    recommended_effector: str        # defender ID or "any"

@dataclass
class EngagementOrder:
    priorities: list[EngagementPriority]   # sorted by score descending
    mode: EngagementMode
    timestamp: float
    rationale: dict                         # per-target explanation for operator
```

**Merge logic:**
- Defensive threats (BULWARK): weighted by `threat.score` (existing formula: time-to-impact 50%, closing speed 30%, value-at-risk 20%)
- Offensive targets (vision): weighted by `cascade_result.expected_value` normalized against total scene value
- Time sensitivity breaks ties -- expiring opportunities rank higher
- Personnel impact is a secondary factor surfaced to the operator but does not override score

### Mode Behavior

| Mode | Trigger | Behavior |
|------|---------|----------|
| AUTO | Wargame runner active | Decision engine emits engagement order, wargame runner executes immediately |
| ADVISORY | Live/connected mode | Decision engine emits recommendation over WebSocket, waits for `ENGAGE_CONFIRM` from operator |

---

## Module 4: HUD Integration

### Camera Panel (in `bulwark.html`)

Extends existing ISR feed panel:
- Canvas overlay draws bounding boxes colored by priority (red = highest, yellow = medium, green = low)
- Priority number and expected value label on each box
- Click target: expands cascade view showing blast radius circle + dependency lines + chain list
- Toggle button: clean view (boxes + numbers only) vs full tactical (radii, lines, everything)

### Engagement Order Panel (new sidebar section)

- Ranked list of all engagement priorities (both defensive and offensive)
- Each entry shows: rank, target type icon, expected value, personnel at risk, source (shield icon for BULWARK, crosshair for vision)
- In ADVISORY mode: "Confirm" button next to each entry
- Color-coded urgency based on time sensitivity

### New Protocol Messages

```javascript
// Downlink (backend -> HUD)
VISUAL_TARGETS    // List of detected targets with bounding boxes, 5Hz
CASCADE_SCORES    // Ranked cascade results, emitted on change
ENGAGEMENT_ORDER  // Unified priority list, emitted on change

// Uplink (HUD -> backend)
ENGAGE_CONFIRM    // Operator confirms recommended engagement { target_id, priority_id }
```

---

## Module 5: Wargame Integration

### New Scenario Types

- `ground_strike_*`: Offensive only -- drone surveys ground targets, cascade scoring, auto-engage
- `combined_*`: Simultaneous incoming swarm defense + ground target strike -- decision engine merges both

### Pre-built Scenarios

| Scenario | Description |
|----------|-------------|
| `ground_strike_convoy` | Convoy on bridge near fuel depot. High cascade potential. |
| `ground_strike_dispersed` | Scattered vehicles in open terrain. Low cascade, tests prioritization. |
| `ground_strike_base` | FOB with generator, antenna, vehicles, personnel. Dependency-heavy. |
| `combined_saturation_strike` | 500 incoming drones + ground targets. Tests dual-priority merge. |

### After-Action Report Extensions

- Cascade outcomes: which chains fired, which didn't, predicted vs actual
- Offensive cost-exchange ratio: value destroyed vs ordnance expended
- Decision engine accuracy: did auto recommendations match optimal play
- Combined timeline: defensive engagements interleaved with offensive strikes

---

## Testing Strategy

### Unit Tests

- `test_cascade.py`: Graph construction, BFS scoring, cycle handling, probability decay
- `test_detector.py`: SimDetector output format, noise injection, false positive rate
- `test_decision.py`: Score normalization, merge logic, mode switching, tie-breaking
- `test_models.py`: VisualTarget, CascadeResult, EngagementOrder serialization

### Integration Tests

- Vision pipeline end-to-end: SimFeedSource -> SimDetector -> CascadeEngine -> ranked results
- Decision engine: mock BULWARK threats + mock cascade results -> unified order
- Wargame: `ground_strike_convoy` scenario runs to completion, audit log validates cascade chains

### Wargame Regression

- All existing BULWARK scenarios still pass unchanged
- New combined scenarios produce valid engagement orders with both sources represented

---

## File Tree (new files only)

```
backend/
  vision/
    __init__.py
    models.py              # VisualTarget, TargetType, BoundingBox
    feed_source.py         # FeedSource, SimFeedSource, StreamFeedSource
    detector.py            # Detector, SimDetector, CVDetector stub
    cascade.py             # CascadeEngine, ProximityGraph, DependencyGraph, CascadeResult
    scenarios.py           # Pre-built target layouts
  decision/
    __init__.py
    engine.py              # DecisionEngine
    models.py              # EngagementPriority, EngagementOrder, EngagementMode
  tests/
    test_cascade.py
    test_detector.py
    test_decision.py
    test_visual_pipeline.py
src/
  hud/
    js/
      cascadeView.js       # Cascade visualization overlay
      engagementPanel.js   # Engagement order sidebar
  shared/
    protocol.js            # New message types added
```

## Dependencies

- `numpy` (already in project for fusion)
- `Pillow` or `opencv-python-headless` for SimFeedSource frame rendering
- No new frontend dependencies (canvas API handles overlays)

## Migration

- Zero breaking changes to existing modules
- Existing BULWARK pipeline untouched -- decision engine wraps it
- Existing HUD panels untouched -- new panels added alongside
- Existing wargame scenarios unchanged -- new scenarios added
