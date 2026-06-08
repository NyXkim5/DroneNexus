"""
After-action report CLI for the BULWARK decision audit store.

Reads the audit SQLite written during a wargame and prints a human-readable
summary: the runs in the store, a per-run decision summary, and the full
why-was-this-drone-engaged chain for a single engagement. It uses only the
read-side audit helpers, so it never mutates the store.

Usage:
  python -m wargame.report --db audit.db                      list runs
  python -m wargame.report --db audit.db --run <id>           summarize one run
  python -m wargame.report --db audit.db --engagement <id>    reconstruct a chain
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from wargame.audit import (
    DecisionRecord,
    list_runs,
    load_decisions,
    reconstruct_chain,
)


def format_runs(db_path: str) -> str:
    """List every run recorded in the store as id, scenario, and label."""
    runs = list_runs(db_path)
    if not runs:
        return "no runs recorded"
    lines = ["runs:"]
    for run_id, scenario, label in runs:
        tail = f"  {label}" if label else ""
        lines.append(f"  {run_id}  {scenario}{tail}")
    return "\n".join(lines)


def format_run_summary(db_path: str, run_id: Optional[str] = None) -> str:
    """Summarize the decisions for one run: counts, kills, and spend."""
    decisions = load_decisions(db_path, run_id=run_id)
    scope = run_id if run_id is not None else "all runs"
    if not decisions:
        return f"{scope}: no decisions"
    hits = sum(1 for d in decisions if d.status == "HIT")
    misses = sum(1 for d in decisions if d.status == "MISS")
    leaks = sum(1 for d in decisions if d.status == "LEAK")
    killed = sum(len(d.killed_threat_ids) for d in decisions)
    spent = sum(d.cost for d in decisions)
    return (
        f"{scope}: {len(decisions)} engagements, {hits} hit, {misses} miss, "
        f"{leaks} leak, {killed} kills credited, ${spent:,.0f} spent"
    )


def format_chain(db_path: str, engagement_id: str) -> str:
    """Render the full lineage chain for one engagement, or a not-found note."""
    chain = reconstruct_chain(db_path, engagement_id)
    if chain is None:
        return f"engagement {engagement_id} not found"
    lines = [
        f"engagement {chain.engagement_id} by {chain.defender_id} "
        f"[{chain.actor}] -> {chain.status} (${chain.cost:,.0f})",
        f"  primary threat: score={_fmt(chain.score)} "
        f"intent={chain.intent} tti={_fmt(chain.time_to_impact_s)} "
        f"track={chain.track_id}",
    ]
    for tgt in chain.targeted_threats:
        flag = "killed" if tgt.killed else "survived"
        lines.append(f"  target {tgt.threat_id}: {flag} (track {tgt.track_id})")
    total = sum(len(v) for v in chain.detections_by_sensor.values())
    lines.append(f"  lineage: {total} detections across {len(chain.detections_by_sensor)} sensors")
    for sensor, dets in sorted(chain.detections_by_sensor.items()):
        lines.append(f"    {sensor}: {len(dets)} detections")
    return "\n".join(lines)


def _fmt(value: Optional[float]) -> str:
    """Format an optional float for the report, or a dash when absent."""
    return f"{value:.2f}" if value is not None else "-"


def _render(args: argparse.Namespace) -> str:
    """Pick the report to render from the parsed arguments."""
    if args.engagement:
        return format_chain(args.db, args.engagement)
    if args.run:
        return format_run_summary(args.db, args.run)
    return format_runs(args.db) + "\n" + format_run_summary(args.db, None)


def main() -> None:
    """CLI entrypoint for the after-action report."""
    parser = argparse.ArgumentParser(description="BULWARK after-action report")
    parser.add_argument("--db", required=True, help="path to the audit SQLite store")
    parser.add_argument("--run", help="summarize one run by id")
    parser.add_argument("--engagement", help="reconstruct one engagement chain by id")
    print(_render(parser.parse_args()))


if __name__ == "__main__":
    main()
