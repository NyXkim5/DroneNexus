"""After-action review analyzer for OVERWATCH.

Usage:
    python3 -m scripts.after_action_review --recording path/to/recording.json.gz
    python3 -m scripts.after_action_review --scenario probe_120
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("overwatch.aar")


@dataclass
class AARReport:
    """Structured after-action review output."""

    total_duration_s: float = 0.0
    total_ticks: int = 0
    first_detection_time_s: float = 0.0
    avg_detection_to_track_s: float = 0.0
    false_positive_count: int = 0
    total_engagements: int = 0
    hits: int = 0
    misses: int = 0
    leaks: int = 0
    hit_rate: float = 0.0
    avg_kill_chain_s: float = 0.0
    fastest_kill_chain_s: float = 0.0
    slowest_kill_chain_s: float = 0.0
    defender_spent: float = 0.0
    attacker_destroyed: float = 0.0
    cost_exchange_ratio: float = 0.0
    total_roe_evaluations: int = 0
    roe_denials: int = 0
    unauthorized_engagements: int = 0
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AARAnalyzer:
    """Analyzes completed wargame frames or autonomous engagement tick logs."""

    def analyze_recording(self, frames: List[Dict[str, Any]]) -> AARReport:
        """Analyze wargame frame dicts from WargameRecorder."""
        if not frames:
            return AARReport()
        report = AARReport(total_ticks=len(frames))
        report.total_duration_s = self._t(frames[-1]) - self._t(frames[0])
        report.first_detection_time_s = next(
            (self._t(f) for f in frames if f.get("tracks")), 0.0,
        )
        report.avg_detection_to_track_s = self._det_to_track(frames)
        report.false_positive_count = sum(
            1 for f in frames for t in f.get("tracks", [])
            if t.get("classification", "") not in ("HOSTILE", "hostile")
            and t.get("confidence", 1.0) > 0.5
        )
        self._engagement_stats(frames, report)
        self._cost_stats(frames, report)
        self._roe_stats(frames, report)
        report.recommendations = _recommend(report)
        return report

    def analyze_tick_records(self, records: List[Any]) -> AARReport:
        """Analyze TickRecord objects from autonomous_engage."""
        if not records:
            return AARReport()
        rpt = AARReport(total_ticks=len(records), total_duration_s=records[-1].elapsed_s)
        rpt.first_detection_time_s = next(
            (r.elapsed_s for r in records if r.detection_count > 0), 0.0)
        det = [r.elapsed_s for r in records if r.detection_count > 0]
        trk = [r.elapsed_s for r in records if r.track_count > 0]
        if det and trk:
            rpt.avg_detection_to_track_s = trk[0] - det[0]
        chain: List[float] = []
        for r in records:
            rpt.total_engagements += r.engagement_count
            if r.engagement_count > 0 and rpt.first_detection_time_s > 0:
                ct = r.elapsed_s - rpt.first_detection_time_s
                if ct > 0:
                    chain.append(ct)
        rpt.hits, rpt.hit_rate = rpt.total_engagements, (1.0 if rpt.total_engagements else 0.0)
        if chain:
            rpt.avg_kill_chain_s = sum(chain) / len(chain)
            rpt.fastest_kill_chain_s, rpt.slowest_kill_chain_s = min(chain), max(chain)
        rpt.recommendations = _recommend(rpt)
        return rpt

    def _t(self, frame: Dict[str, Any]) -> float:
        return float((frame.get("metrics") or {}).get("sim_time_s", 0.0))

    def _det_to_track(self, frames: List[Dict[str, Any]]) -> float:
        ft, fd = 0.0, 0.0
        for f in frames:
            t = self._t(f)
            if f.get("tracks") and ft == 0.0:
                ft = t
            if (f.get("metrics") or {}).get("tracks_held", 0) > 0 and fd == 0.0:
                fd = t
        return abs(ft - fd) if ft > 0 and fd > 0 else 0.0

    def _engagement_stats(self, frames: List[Dict[str, Any]], r: AARReport) -> None:
        chain: List[float] = []
        seen_eng = False
        for f in frames:
            if f.get("assignments"):
                if not seen_eng:
                    seen_eng = True
                ct = self._t(f) - r.first_detection_time_s
                if ct > 0:
                    chain.append(ct)
        m = frames[-1].get("metrics") or {}
        r.total_engagements = int(m.get("engagements_made", 0))
        r.leaks = int(m.get("leakers", 0))
        if r.total_engagements > 0:
            r.hits = int(m.get("intercepts", 0))
            r.misses = max(0, r.total_engagements - r.hits)
            r.hit_rate = r.hits / r.total_engagements
        if chain:
            r.avg_kill_chain_s = sum(chain) / len(chain)
            r.fastest_kill_chain_s, r.slowest_kill_chain_s = min(chain), max(chain)

    def _cost_stats(self, frames: List[Dict[str, Any]], r: AARReport) -> None:
        m = frames[-1].get("metrics") or {}
        r.defender_spent = float(m.get("defender_spent", 0.0))
        r.attacker_destroyed = float(m.get("attacker_destroyed", 0.0))
        if r.attacker_destroyed > 0:
            r.cost_exchange_ratio = r.defender_spent / r.attacker_destroyed

    def _roe_stats(self, frames: List[Dict[str, Any]], r: AARReport) -> None:
        for f in frames:
            for ev in f.get("roe_evaluations", []):
                r.total_roe_evaluations += 1
                if not ev.get("authorized", True):
                    r.roe_denials += 1


_RULES: List[tuple] = [
    (lambda r: r.total_engagements > 0 and r.hit_rate < 0.8,
     "Hit rate below 80%. Consider adding area-effect EW defenders."),
    (lambda r: r.avg_kill_chain_s > 10,
     "Kill chain averaging >10s. Sensor fusion latency may need optimization."),
    (lambda r: r.cost_exchange_ratio > 1.0,
     "Cost exchange unfavorable. Reduce kinetic interceptor use against decoys."),
    (lambda r: r.leaks > 0,
     "{leaks} leakers detected. Add defender capacity or extend engagement range."),
    (lambda r: r.unauthorized_engagements > 0,
     "{unauthorized_engagements} unauthorized engagements. Review ROE configuration immediately."),
    (lambda r: r.total_engagements == 0 and r.total_ticks > 10,
     "No engagements fired. Check defender readiness and threat classification."),
]


def _recommend(r: AARReport) -> List[str]:
    return [
        msg.format(leaks=r.leaks, unauthorized_engagements=r.unauthorized_engagements)
        for pred, msg in _RULES if pred(r)
    ]


def format_terminal(report: AARReport, title: str = "AAR") -> str:
    """Render an AAR report as a terminal table."""
    r = report
    bar = "=" * 60
    lines = [
        bar, f"  AFTER-ACTION REVIEW: {title}", bar, "",
        "  TIMELINE",
        f"    Duration:            {r.total_duration_s:.1f} s",
        f"    Total ticks:         {r.total_ticks}",
        f"    First detection:     {r.first_detection_time_s:.1f} s",
        f"    Avg detect-to-track: {r.avg_detection_to_track_s:.1f} s", "",
        "  ENGAGEMENT",
        f"    Engagements:         {r.total_engagements}",
        f"    Hits / Misses:       {r.hits} / {r.misses}",
        f"    Leakers:             {r.leaks}",
        f"    Hit rate:            {r.hit_rate:.1%}",
        f"    Kill chain (avg):    {r.avg_kill_chain_s:.1f} s",
        f"    Kill chain (range):  {r.fastest_kill_chain_s:.1f} - {r.slowest_kill_chain_s:.1f} s", "",
        "  COST",
        f"    Defender spent:      ${r.defender_spent:,.0f}",
        f"    Attacker destroyed:  ${r.attacker_destroyed:,.0f}",
        f"    Cost exchange ratio: {r.cost_exchange_ratio:.2f}", "",
        "  ROE COMPLIANCE",
        f"    ROE evaluations:     {r.total_roe_evaluations}",
        f"    ROE denials:         {r.roe_denials}",
        f"    Unauthorized:        {r.unauthorized_engagements}", "",
    ]
    if r.recommendations:
        lines.append("  RECOMMENDATIONS")
        for i, rec in enumerate(r.recommendations, 1):
            lines.append(f"    {i}. {rec}")
        lines.append("")
    lines.append(bar)
    return "\n".join(lines)


async def _run_and_analyze(name: str) -> AARReport:
    from wargame.runner import WargameRunner
    from wargame.scenario import load_scenario
    runner = WargameRunner(load_scenario(name))
    frames: List[Dict[str, Any]] = []
    async for frame in runner.run(pace=False):
        frames.append(frame.to_dict())
    return AARAnalyzer().analyze_recording(frames)


def _load_and_analyze(path: Path) -> AARReport:
    from wargame.recorder import WargameRecorder
    return AARAnalyzer().analyze_recording(WargameRecorder.load(path)[1])


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="OVERWATCH After-Action Review")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--recording", type=str, help="Path to recording .json.gz")
    g.add_argument("--scenario", type=str, help="Run built-in scenario")
    p.add_argument("--output", type=str, default="", help="JSON output path")
    args = p.parse_args()
    if args.recording:
        title, report = Path(args.recording).stem, _load_and_analyze(Path(args.recording))
    else:
        title, report = args.scenario, asyncio.run(_run_and_analyze(args.scenario))
    print(format_terminal(report, title))
    out = Path(args.output) if args.output else Path(f"aar_{title}.json")
    out.write_text(json.dumps(report.to_dict(), indent=2))
    logger.info("JSON report saved to %s", out)


if __name__ == "__main__":
    main()
