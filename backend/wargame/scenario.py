"""
Scenario configuration for the BULWARK counter-swarm wargame.

A scenario fully describes one wargame run. It names the attacking swarm size and
behavior, the sensor layout that watches the airspace, the defender loadout that
answers the threat, and the site under attack. Scenarios are plain dataclasses so
they validate at construction and serialize cleanly to JSON for the HUD.

Presets ship inline and as YAML under wargame/scenarios. load_scenario(name)
returns a preset by name. load_scenario_file(path) reads a YAML scenario. Both
raise on an unknown name or malformed file. No silent fallbacks.

All positions are ENU meters about the site origin, the shared world-model frame.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from csontology import DefenderKind, SwarmIntent, Vec3
from wargame.calibration import load_calibration, to_defender_kwargs

# Effector performance is loaded from a calibration file with provenance and
# uncertainty bands, so measured kill curves can replace the current estimates
# without code changes. The path is resolved absolutely so any working directory
# loads the same calibration.
_CALIBRATION = load_calibration(
    str(Path(__file__).parent.parent / "config" / "effectors.yaml")
)

logger = logging.getLogger("overwatch.wargame")

# Directory holding YAML scenario presets shipped with the module.
SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@dataclass
class SensorConfig:
    """One sensor in the layout, mapped onto a SimSensorSpec at build time."""

    sensor_id: str
    position: Vec3 = (0.0, 0.0, 0.0)
    range_m: float = 5000.0
    fov_deg: float = 360.0
    bearing_deg: float = 0.0
    detection_prob: float = 0.9
    pos_noise_m: float = 8.0
    vel_noise_ms: float = 1.5


@dataclass
class DefenderConfig:
    """One defender effector loadout entry, mapped onto a Defender at build time.

    count expands into count identical defenders, each suffixed by index. This
    keeps a loadout of twenty interceptors to a single readable line.
    """

    id_prefix: str
    kind: DefenderKind
    count: int
    position: Vec3
    capacity: int
    range_m: float
    reload_s: float
    kill_prob: float
    unit_cost: float
    effect_radius_m: float = 0.0
    max_simultaneous: int = 1


@dataclass
class SiteConfig:
    """The defended site and the value it protects."""

    id: str = "SITE-1"
    position: Vec3 = (0.0, 0.0, 0.0)
    protected_assets: List[str] = field(default_factory=lambda: ["C2", "RADAR"])
    value: float = 1_000_000.0


@dataclass
class Scenario:
    """A full wargame configuration: attacker, sensors, defenders, and site.

    swarm_intent and swarm_count drive the red force. unit_cost is the per-drone
    attacker dollar cost. tick_hz is the sim loop rate. max_ticks bounds a CLI
    run so it always terminates. seed pins randomness for repeatable runs.
    """

    name: str
    swarm_intent: SwarmIntent
    swarm_count: int
    unit_cost: float = 500.0
    sensors: List[SensorConfig] = field(default_factory=list)
    defenders: List[DefenderConfig] = field(default_factory=list)
    site: SiteConfig = field(default_factory=SiteConfig)
    tick_hz: float = 5.0
    max_ticks: int = 600
    seed: int = 7
    jam_fraction: float = 0.0
    blackout_windows: List[Tuple[int, int]] = field(default_factory=list)
    jam_resistant_fraction: float = 0.0
    hardened_fraction: float = 0.0
    target_scenario: Optional[str] = None

    def __post_init__(self) -> None:
        if not 10 <= self.swarm_count <= 1000:
            raise ValueError(f"swarm_count must be in 10..1000, got {self.swarm_count}")
        if self.tick_hz <= 0:
            raise ValueError("tick_hz must be positive")
        # Vision-only scenarios wire in a target_scenario instead of a sensor
        # layout, so the sensor and defender lists may be empty in that case.
        if not self.target_scenario:
            if not self.sensors:
                raise ValueError("scenario needs at least one sensor")
            if not self.defenders:
                raise ValueError("scenario needs at least one defender")


def _ring_sensors(count: int, range_m: float, radius_m: float) -> List[SensorConfig]:
    """Build a ring of omnidirectional sensors evenly spaced around the site."""
    import math

    sensors: List[SensorConfig] = []
    for i in range(count):
        bearing = (i / count) * 2.0 * math.pi
        x = radius_m * math.cos(bearing)
        y = radius_m * math.sin(bearing)
        sensors.append(
            SensorConfig(
                sensor_id=f"radar-{i + 1}",
                position=(x, y, 0.0),
                range_m=range_m,
            )
        )
    return sensors


def _hpm(count: int) -> DefenderConfig:
    """A high-power-microwave area effector: cheap per shot, many kills per shot.

    HPM fries drone electronics across a cone regardless of jam resistance, so it
    is the workhorse against dense swarms. Each shot costs only power, yet
    neutralizes every airframe inside its effect radius up to the cap. This is the
    layer that drives the cost-exchange ratio below one.
    """
    return DefenderConfig(
        id_prefix="HPM",
        kind=DefenderKind.HPM,
        count=count,
        position=(0.0, 0.0, 0.0),
        capacity=18,
        **to_defender_kwargs(_CALIBRATION.profile(DefenderKind.HPM)),
    )


def _ew(count: int) -> DefenderConfig:
    """A wide electronic-warfare effector: very cheap, broad, but jam-resistible.

    EW defeats control and navigation across a wide footprint at almost no cost.
    Modern autonomous drones resist it, so its kill probability is modest. It
    thins the swarm cheaply and hands the survivors to HPM and interceptors.
    """
    return DefenderConfig(
        id_prefix="EW",
        kind=DefenderKind.EW,
        count=count,
        position=(0.0, 0.0, 0.0),
        capacity=28,
        **to_defender_kwargs(_CALIBRATION.profile(DefenderKind.EW)),
    )


def _saturation_1000() -> Scenario:
    """A 1000-drone all-axis saturation attack against a hardened site."""
    return Scenario(
        name="saturation_1000",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=1000,
        unit_cost=500.0,
        sensors=_ring_sensors(count=4, range_m=3500.0, radius_m=600.0),
        defenders=[
            _hpm(count=5),
            _ew(count=5),
            DefenderConfig(
                id_prefix="INT",
                kind=DefenderKind.INTERCEPTOR,
                count=20,
                position=(0.0, 0.0, 0.0),
                capacity=6,
                range_m=2500.0,
                reload_s=2.0,
                kill_prob=0.85,
                unit_cost=8_000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=600,
        seed=11,
        jam_resistant_fraction=0.35,
        hardened_fraction=0.2,
    )


def _probe_120() -> Scenario:
    """A small probing attack to test sensors and defender reaction."""
    return Scenario(
        name="probe_120",
        swarm_intent=SwarmIntent.PROBE,
        swarm_count=120,
        unit_cost=500.0,
        sensors=_ring_sensors(count=3, range_m=3200.0, radius_m=400.0),
        defenders=[
            _hpm(count=2),
            _ew(count=2),
            DefenderConfig(
                id_prefix="INT",
                kind=DefenderKind.INTERCEPTOR,
                count=6,
                position=(0.0, 0.0, 0.0),
                capacity=4,
                range_m=2200.0,
                reload_s=2.0,
                kill_prob=0.85,
                unit_cost=8_000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=400,
        seed=7,
    )


def _decoy_300() -> Scenario:
    """A mixed decoy raid where most airframes are cheap throwaways."""
    return Scenario(
        name="decoy_300",
        swarm_intent=SwarmIntent.DECOY,
        swarm_count=300,
        unit_cost=500.0,
        sensors=_ring_sensors(count=4, range_m=3400.0, radius_m=500.0),
        defenders=[
            _hpm(count=3),
            _ew(count=3),
            DefenderConfig(
                id_prefix="INT",
                kind=DefenderKind.INTERCEPTOR,
                count=10,
                position=(0.0, 0.0, 0.0),
                capacity=5,
                range_m=2400.0,
                reload_s=2.0,
                kill_prob=0.82,
                unit_cost=8_000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=500,
        seed=23,
        jam_resistant_fraction=0.25,
        hardened_fraction=0.15,
    )


def _contested_500() -> Scenario:
    """A 500-drone saturation fought under heavy jamming and a sensor blackout.

    Forty percent of detections are jammed every tick and the picture goes fully
    dark for a window mid-fight. The autonomy must keep tracking and engaging on
    coasted, predicted state with no operator input. This is the comms-denied
    test the design calls for.
    """
    base = _saturation_1000()
    return Scenario(
        name="contested_500",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=500,
        unit_cost=500.0,
        sensors=_ring_sensors(count=4, range_m=3500.0, radius_m=600.0),
        defenders=base.defenders,
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=600,
        seed=29,
        jam_fraction=0.4,
        blackout_windows=[(60, 80)],
        jam_resistant_fraction=0.35,
        hardened_fraction=0.2,
    )


def _skirmish_80() -> Scenario:
    """A small, fast saturation of cheap drones for quick sweeps and tests.

    Eighty plain airframes converge so a sweep resolves in a few seconds. With
    healthy area effectors the defense wins on cost. With the area layer crippled
    the only kills come from kinetic interceptors fired at cheap drones, so the
    cost-exchange ratio climbs past one. That makes it the right scenario to test
    the crossover boundary quickly.
    """
    return Scenario(
        name="skirmish_80",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=80,
        unit_cost=500.0,
        sensors=_ring_sensors(count=3, range_m=3200.0, radius_m=400.0),
        defenders=[
            _hpm(count=1),
            _ew(count=1),
            DefenderConfig(
                id_prefix="INT",
                kind=DefenderKind.INTERCEPTOR,
                count=10,
                position=(0.0, 0.0, 0.0),
                capacity=4,
                range_m=2500.0,
                reload_s=2.0,
                kill_prob=0.85,
                unit_cost=8_000.0,
            ),
        ],
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=300,
        seed=5,
        jam_resistant_fraction=0.2,
        hardened_fraction=0.1,
    )


def _combined_saturation_strike() -> Scenario:
    """A 500-drone saturation strike with ground target cascade analysis.

    Uses the same sensor ring and defender loadout as saturation_1000 but
    binds the ground_strike_base vision scenario so every tick also runs the
    visual cascade pipeline and produces an engagement order for ground targets.
    This is the combined wargame: counter-swarm defense plus target prioritization
    in a single run.
    """
    base = _saturation_1000()
    return Scenario(
        name="combined_saturation_strike",
        swarm_intent=SwarmIntent.SATURATION,
        swarm_count=500,
        unit_cost=500.0,
        sensors=_ring_sensors(count=4, range_m=3500.0, radius_m=600.0),
        defenders=base.defenders,
        site=SiteConfig(),
        tick_hz=5.0,
        max_ticks=600,
        seed=42,
        jam_resistant_fraction=0.35,
        hardened_fraction=0.2,
        target_scenario="ground_strike_base",
    )


# Built-in presets keyed by name. At least two ship, including the 1000-drone
# saturation attack required by the design.
PRESETS: Dict[str, "callable"] = {
    "saturation_1000": _saturation_1000,
    "probe_120": _probe_120,
    "decoy_300": _decoy_300,
    "contested_500": _contested_500,
    "skirmish_80": _skirmish_80,
    "combined_saturation_strike": _combined_saturation_strike,
}


def list_scenarios() -> List[str]:
    """Return the names of all built-in presets."""
    return sorted(PRESETS.keys())


def load_scenario(name: str) -> Scenario:
    """Return a built-in preset by name. Raises KeyError if unknown."""
    builder = PRESETS.get(name)
    if builder is None:
        raise KeyError(f"unknown scenario '{name}', have {list_scenarios()}")
    return builder()


def load_scenario_file(path: Path) -> Scenario:
    """Load a Scenario from a YAML file. Raises on a missing or malformed file."""
    if not path.exists():
        raise FileNotFoundError(f"scenario file not found: {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"scenario file must be a mapping: {path}")
    return _scenario_from_dict(raw)


def _scenario_from_dict(raw: Dict[str, object]) -> Scenario:
    """Build a Scenario from a parsed YAML mapping with explicit field mapping."""
    sensors = [SensorConfig(**s) for s in raw.get("sensors", [])]  # type: ignore[arg-type]
    defenders = [
        DefenderConfig(kind=DefenderKind(d.pop("kind")), **d)  # type: ignore[arg-type]
        for d in raw.get("defenders", [])  # type: ignore[union-attr]
    ]
    site = SiteConfig(**raw["site"]) if "site" in raw else SiteConfig()
    return Scenario(
        name=str(raw["name"]),
        swarm_intent=SwarmIntent(raw["swarm_intent"]),
        swarm_count=int(raw["swarm_count"]),  # type: ignore[arg-type]
        unit_cost=float(raw.get("unit_cost", 500.0)),  # type: ignore[arg-type]
        sensors=sensors,
        defenders=defenders,
        site=site,
        tick_hz=float(raw.get("tick_hz", 5.0)),  # type: ignore[arg-type]
        max_ticks=int(raw.get("max_ticks", 600)),  # type: ignore[arg-type]
        seed=int(raw.get("seed", 7)),  # type: ignore[arg-type]
    )
