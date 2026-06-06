"""
BULWARK wargame package — the end-to-end counter-swarm integration loop.

Public surface:
  WargameRunner(scenario)        -> async generator of Frame per tick
  Scenario, load_scenario(name)  -> scenario config and presets
  Frame, Metrics                 -> renderable snapshot and live scoreboard
  build_world(scenario)          -> assembled WorldModel
"""
from wargame.frame import Frame, Metrics
from wargame.runner import WargameRunner
from wargame.scenario import (
    Scenario,
    list_scenarios,
    load_scenario,
    load_scenario_file,
)
from wargame.world import WorldModel, build_world
from wargame.sweep import SweepResult, crossover_report, run_sweep

__all__ = [
    "WargameRunner",
    "SweepResult",
    "crossover_report",
    "run_sweep",
    "Frame",
    "Metrics",
    "Scenario",
    "WorldModel",
    "build_world",
    "list_scenarios",
    "load_scenario",
    "load_scenario_file",
]
