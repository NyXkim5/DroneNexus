"""
OVERWATCH Demo Walkthrough -- timed narration for video recording.

Run alongside bulwark.html for a guided demo. Prints narration with
timing markers and optionally exports SRT subtitles.

Usage from cwd=backend:
  python -m scripts.demo_walkthrough
  python -m scripts.demo_walkthrough --srt demo.srt
  python -m scripts.demo_walkthrough --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SCRIPT: List[tuple[int, str]] = [
    (0,  "OVERWATCH is a software-defined counter-drone defense engine."),
    (4,  "It fuses any sensor -- RF, visual, thermal, acoustic -- into one real-time picture."),
    (9,  "Starting wargame: 120 hostile drones approaching a defended site."),
    (14, "The fusion engine builds tracks from raw sensor detections."),
    (19, "Threat classifier identifies intent: PROBE pattern detected."),
    (24, "ROE engine evaluates each threat. 6 conditions checked. Authorization granted."),
    (30, "Layered allocator assigns defenders: EW jammers first, kinetic as backup."),
    (36, "Cost exchange ratio: 0.42. Defense is winning on cost."),
    (42, "YOLOv11 drone detector runs at 94.9% accuracy on 75,000 training images."),
    (48, "Kill chain averages under 5 seconds from detection to engagement."),
    (55, "20 hostiles neutralized. Zero leakers. Defense holding."),
    (62, "OVERWATCH handles 1,000+ simultaneous tracks at 60 frames per second."),
    (69, "Multi-sensor fusion: RF detection confirmed by AI visual classification."),
    (76, "Wargame complete. Full after-action review with audit trail."),
    (82, "Every engagement decision is traceable. Full ROE compliance."),
    (88, "OVERWATCH. The software brain for counter-drone defense."),
]


def _fmt_ts(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _srt_ts(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"00:{m:02d}:{s:02d},000"


def export_srt(path: Path) -> None:
    """Write SCRIPT as an SRT subtitle file."""
    lines: list[str] = []
    for idx, (start, text) in enumerate(SCRIPT, 1):
        end = SCRIPT[idx][0] if idx < len(SCRIPT) else start + 5
        lines.append(str(idx))
        lines.append(f"{_srt_ts(start)} --> {_srt_ts(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"SRT written to {path}")


async def run_walkthrough(*, dry_run: bool = False) -> None:
    """Print narration lines at timed intervals."""
    t0 = time.monotonic()
    print("\n=== OVERWATCH DEMO WALKTHROUGH ===\n")

    for delay, text in SCRIPT:
        if not dry_run:
            elapsed = time.monotonic() - t0
            wait = delay - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        print(f"[{_fmt_ts(delay)}]  {text}")

    print("\n=== END ===\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="OVERWATCH demo narration")
    parser.add_argument("--srt", type=Path, help="Export SRT subtitle file")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print all lines instantly"
    )
    args = parser.parse_args()

    if args.srt:
        export_srt(args.srt)
        return

    try:
        asyncio.run(run_walkthrough(dry_run=args.dry_run))
    except KeyboardInterrupt:
        print("\nDemo interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
