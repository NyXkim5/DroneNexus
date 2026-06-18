"""
CLI script for replaying saved wargame recordings.

Usage:
    python -m scripts.replay_wargame \\
        --recording path/to/recording.json.gz \\
        [--speed 2.0] [--start-tick 0] [--end-tick 100] \\
        [--format table|json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from wargame.replay import ReplayPlayer

logger = logging.getLogger("overwatch.replay_cli")

# Metric keys displayed in table mode
TABLE_COLUMNS = [
    "tick",
    "sim_time_s",
    "active_hostiles",
    "leakers",
    "intercepts",
    "intercept_rate",
    "cost_exchange_ratio",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a saved wargame recording.",
    )
    parser.add_argument(
        "--recording", required=True, type=Path,
        help="Path to the recording JSON (gzipped).",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Playback speed multiplier (default: 1.0).",
    )
    parser.add_argument(
        "--start-tick", type=int, default=0,
        help="First tick to play (default: 0).",
    )
    parser.add_argument(
        "--end-tick", type=int, default=None,
        help="Last tick to play (exclusive). Omit for all.",
    )
    parser.add_argument(
        "--format", dest="fmt", choices=["table", "json"],
        default="table", help="Output format (default: table).",
    )
    return parser.parse_args()


def _print_table_header() -> None:
    header = " | ".join(f"{col:>22s}" for col in TABLE_COLUMNS)
    print(header)
    print("-" * len(header))


def _print_table_row(metrics: dict) -> None:
    cells: list[str] = []
    for col in TABLE_COLUMNS:
        val = metrics.get(col)
        if val is None:
            cells.append(f"{'--':>22s}")
        elif isinstance(val, float):
            cells.append(f"{val:>22.4f}")
        else:
            cells.append(f"{str(val):>22s}")
    print(" | ".join(cells))


def _progress_bar(current: int, total: int, width: int = 30) -> str:
    frac = current / max(total, 1)
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{total}"


def _print_summary(frames: list[dict], meta: dict) -> None:
    print("\n=== Replay Summary ===")
    print(f"Scenario:     {meta.get('scenario_name', 'unknown')}")
    print(f"Total frames: {meta.get('total_frames', len(frames))}")
    duration = meta.get("end_time", 0) - meta.get("start_time", 0)
    print(f"Duration:     {duration:.1f}s")
    if not frames:
        return
    last = (frames[-1].get("metrics") or {})
    print(f"Final leakers:           {last.get('leakers', '--')}")
    print(f"Final intercepts:        {last.get('intercepts', '--')}")
    print(f"Final intercept rate:    {last.get('intercept_rate', '--')}")
    print(f"Cost exchange ratio:     {last.get('cost_exchange_ratio', '--')}")


async def _run(args: argparse.Namespace) -> None:
    player = ReplayPlayer(args.recording)
    meta = player.load()
    player.speed = args.speed

    total = player.frame_count
    end = args.end_tick if args.end_tick is not None else total

    print(f"Replaying {args.recording.name} "
          f"(ticks {args.start_tick}-{end}, speed {args.speed}x)")

    if args.fmt == "table":
        _print_table_header()

    collected: list[dict] = []
    async for frame in player.play_range(
        args.start_tick, end, pace=True,
    ):
        collected.append(frame)
        metrics = frame.get("metrics") or {}
        tick = metrics.get("tick", "?")

        if args.fmt == "json":
            print(json.dumps(frame))
        else:
            _print_table_row(metrics)

        progress = _progress_bar(len(collected), end - args.start_tick)
        sys.stderr.write(f"\r{progress}")
        sys.stderr.flush()

    sys.stderr.write("\n")
    _print_summary(collected, meta)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
