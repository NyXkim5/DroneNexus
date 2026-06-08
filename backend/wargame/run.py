"""
CLI entrypoint for the BULWARK wargame.

Run a scenario end to end and print live metrics per tick plus a final summary:
  python -m wargame.run --scenario saturation_1000
  python -m wargame.run --list
  python -m wargame.run --file wargame/scenarios/probe_120.yaml --max-ticks 50

Prints one line of metrics per tick and a summary block at the end. The headline
figure is the cost-exchange ratio, defender dollars spent per attacker dollar
destroyed. Below 1.0 means defense wins on cost.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from wargame.audit import AuditLog
from wargame.frame import Frame
from wargame.runner import WargameRunner
from wargame.scenario import (
    Scenario,
    list_scenarios,
    load_scenario,
    load_scenario_file,
)

logger = logging.getLogger("overwatch.wargame")


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the wargame entrypoint."""
    parser = argparse.ArgumentParser(description="BULWARK counter-swarm wargame")
    parser.add_argument("--scenario", help="preset scenario name")
    parser.add_argument("--file", help="path to a YAML scenario file")
    parser.add_argument("--list", action="store_true", help="list preset scenarios")
    parser.add_argument("--max-ticks", type=int, help="override scenario max_ticks")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    parser.add_argument(
        "--fast", action="store_true",
        help="run with no real-time pacing, for fast batch runs",
    )
    parser.add_argument(
        "--audit", help="write a decision audit log to this SQLite path",
    )
    return parser.parse_args()


def _resolve_scenario(args: argparse.Namespace) -> Scenario:
    """Build the scenario from a preset name or a YAML file path."""
    if args.file:
        scenario = load_scenario_file(Path(args.file))
    elif args.scenario:
        scenario = load_scenario(args.scenario)
    else:
        raise SystemExit("provide --scenario <name> or --file <path>, or --list")
    if args.max_ticks is not None:
        scenario.max_ticks = args.max_ticks
    return scenario


def _format_tick(frame: Frame) -> str:
    """Format one tick of metrics as a single fixed-width line."""
    m = frame.metrics
    ratio = "n/a" if m.cost_exchange_ratio is None else f"{m.cost_exchange_ratio:.2f}"
    return (
        f"t={m.tick:4d} sim={m.sim_time_s:6.1f}s "
        f"hostiles={m.active_hostiles:4d} tracks={m.tracks_held:4d} "
        f"leak={m.leakers:3d} eng={m.engagements_made:4d} "
        f"hit={m.intercepts:4d} rate={m.intercept_rate:5.2f} "
        f"def=${m.defender_spent:,.0f} kill=${m.attacker_destroyed:,.0f} "
        f"CER={ratio}"
    )


def _print_summary(frame: Frame, scenario: Scenario) -> None:
    """Print the final scoreboard block for the run."""
    m = frame.metrics
    ratio = "n/a" if m.cost_exchange_ratio is None else f"{m.cost_exchange_ratio:.3f}"
    print("=" * 64)
    print(f"WARGAME COMPLETE: {scenario.name} ({scenario.swarm_intent.value})")
    print("=" * 64)
    print(f"  ticks run            {m.tick}")
    print(f"  swarm size           {scenario.swarm_count}")
    print(f"  hostiles remaining   {m.active_hostiles}")
    print(f"  leakers (impacted)   {m.leakers}")
    print(f"  engagements made     {m.engagements_made}")
    print(f"  intercepts           {m.intercepts}")
    print(f"  intercept rate       {m.intercept_rate:.2%}")
    print(f"  defender spent       ${m.defender_spent:,.0f}")
    print(f"  attacker destroyed   ${m.attacker_destroyed:,.0f}")
    print(f"  COST-EXCHANGE RATIO  {ratio}  (defender $ per attacker $ killed)")
    print("=" * 64)


async def _run(
    scenario: Scenario, quiet: bool, pace: bool, audit_path: str | None,
) -> None:
    """Drive the runner to completion, printing metrics along the way."""
    audit = AuditLog(audit_path, scenario=scenario.name) if audit_path else None
    runner = WargameRunner(scenario, audit=audit)
    last: Frame | None = None
    async for frame in runner.run(pace=pace):
        last = frame
        if not quiet:
            print(_format_tick(frame))
    if last is not None:
        _print_summary(last, scenario)
    if audit_path:
        print(f"  audit log written     {audit_path}")


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args = _parse_args()
    if args.list:
        print("scenarios:", ", ".join(list_scenarios()))
        return
    scenario = _resolve_scenario(args)
    asyncio.run(_run(scenario, args.quiet, not args.fast, args.audit))


if __name__ == "__main__":
    main()
