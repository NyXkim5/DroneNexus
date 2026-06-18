"""
After-action report (AAR) generation from wargame recordings.

AARExporter takes the metadata and frame list produced by WargameRecorder.load()
and derives engagement statistics, a formatted text report, a JSON summary, and
a CSV timeline suitable for external analysis.

All monetary values are in USD as recorded by the wargame ledger.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List, Optional

from wargame.recorder import RecordingMetadata


class AARExporter:
    """Generates after-action reports from wargame recordings."""

    def __init__(self, metadata: RecordingMetadata, frames: List[dict]) -> None:
        self._metadata = metadata
        self._frames = frames

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_summary(self) -> dict:
        """Analyze frames and produce summary statistics.

        Returns a dict containing:
        - total_engagements, hits, misses, leakers
        - cost_exchange_ratio
        - peak_threat_count and peak_threat_time_s
        - cascade_outcomes (count of cascade results across all frames)
        - engagement_orders_made (count of non-null engagement orders)
        - timeline of key events (first engagement, peak threat, end)
        """
        if not self._frames:
            return self._empty_summary()

        total_engagements = 0
        hits = 0
        misses = 0
        leakers = 0
        peak_threat_count = 0
        peak_threat_time_s = 0.0
        cascade_outcomes = 0
        engagement_orders_made = 0
        final_metrics: dict = {}
        timeline: List[dict] = []
        first_engagement_recorded = False

        for frame in self._frames:
            metrics = frame.get("metrics") or {}
            t = metrics.get("sim_time_s", 0.0)

            # Accumulate per-frame engagement deltas by diffing engagements_made.
            frame_engagements = metrics.get("engagements_made", 0)
            frame_intercepts = metrics.get("intercepts", 0)
            active = metrics.get("active_hostiles", 0)
            frame_leakers = metrics.get("leakers", 0)

            # Track peak threat moment.
            if active > peak_threat_count:
                peak_threat_count = active
                peak_threat_time_s = t

            # Cascade results.
            cascade_outcomes += len(frame.get("cascade_results") or [])

            # Engagement orders.
            if frame.get("engagement_order") is not None:
                engagement_orders_made += 1

            # Record first engagement event.
            if frame_engagements > 0 and not first_engagement_recorded:
                first_engagement_recorded = True
                timeline.append({"time_s": round(t, 2), "event": "First engagement"})

            final_metrics = metrics

        # Final totals come from the last frame's cumulative metrics.
        total_engagements = final_metrics.get("engagements_made", 0)
        hits = final_metrics.get("intercepts", 0)
        misses = max(0, total_engagements - hits)
        leakers = final_metrics.get("leakers", 0)
        cost_exchange_ratio = final_metrics.get("cost_exchange_ratio")
        defender_spent = final_metrics.get("defender_spent", 0.0)
        attacker_destroyed = final_metrics.get("attacker_destroyed", 0.0)
        duration_s = self._metadata.end_time - self._metadata.start_time

        # Add peak and end events to timeline.
        timeline.append(
            {"time_s": round(peak_threat_time_s, 2), "event": f"Peak threat: {peak_threat_count} active hostiles"}
        )
        if self._frames:
            last_metrics = self._frames[-1].get("metrics") or {}
            timeline.append(
                {"time_s": round(last_metrics.get("sim_time_s", 0.0), 2), "event": "Scenario end"}
            )

        timeline.sort(key=lambda e: e["time_s"])

        return {
            "scenario_name": self._metadata.scenario_name,
            "duration_s": round(duration_s, 2),
            "total_frames": self._metadata.total_frames,
            "total_engagements": total_engagements,
            "hits": hits,
            "misses": misses,
            "leakers": leakers,
            "cost_exchange_ratio": cost_exchange_ratio,
            "defender_spent": round(defender_spent, 2),
            "attacker_destroyed": round(attacker_destroyed, 2),
            "peak_threat_count": peak_threat_count,
            "peak_threat_time_s": round(peak_threat_time_s, 2),
            "cascade_outcomes": cascade_outcomes,
            "engagement_orders_made": engagement_orders_made,
            "timeline": timeline,
        }

    def generate_text_report(self) -> str:
        """Produce a formatted text after-action report."""
        s = self.generate_summary()
        ratio_str = f"{s['cost_exchange_ratio']:.3f}" if s["cost_exchange_ratio"] is not None else "N/A"
        intercepts = s["hits"]
        leakers = s["leakers"]

        lines: List[str] = [
            "OVERWATCH/BULWARK AFTER-ACTION REPORT",
            "=====================================",
            f"Scenario: {s['scenario_name']}",
            f"Duration: {s['duration_s']}s ({s['total_frames']} frames)",
            "",
            "EXECUTIVE SUMMARY",
            f"- Threat: {s['peak_threat_count']} hostile tracks at peak (T+{s['peak_threat_time_s']}s)",
            f"- Result: {leakers} leaker(s), {intercepts} intercept(s)",
            f"- Cost Exchange: {ratio_str}",
            f"- Defender Spent: ${s['defender_spent']:,.2f}",
            f"- Attacker Destroyed: ${s['attacker_destroyed']:,.2f}",
            "",
            "ENGAGEMENT TIMELINE",
        ]

        for event in s["timeline"]:
            lines.append(f"  T+{event['time_s']}s: {event['event']}")

        lines += [
            "",
            "CASCADE ANALYSIS",
            f"  Total cascade outcomes scored: {s['cascade_outcomes']}",
        ]

        if s["cascade_outcomes"] == 0:
            lines.append("  No cascade dependency data present in recording.")

        lines += [
            "",
            "ROE AUDIT",
            f"  Total engagements authorized: {s['total_engagements']}",
            f"  Engagement orders (decision engine): {s['engagement_orders_made']}",
            f"  Hit rate: {self._hit_rate(s['hits'], s['total_engagements'])}",
            "",
        ]

        return "\n".join(lines)

    def export_json(self, path: Path) -> None:
        """Export the summary dict as a JSON file."""
        summary = self.generate_summary()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)

    def export_csv_timeline(self, path: Path) -> None:
        """Export frame-by-frame metrics as a CSV file for analysis."""
        headers = [
            "frame_index",
            "sim_time_s",
            "active_hostiles",
            "tracks_held",
            "leakers",
            "engagements_made",
            "intercepts",
            "intercept_rate",
            "defender_spent",
            "attacker_destroyed",
            "cost_exchange_ratio",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for i, frame in enumerate(self._frames):
                metrics = frame.get("metrics") or {}
                writer.writerow(
                    {
                        "frame_index": i,
                        "sim_time_s": metrics.get("sim_time_s", ""),
                        "active_hostiles": metrics.get("active_hostiles", ""),
                        "tracks_held": metrics.get("tracks_held", ""),
                        "leakers": metrics.get("leakers", ""),
                        "engagements_made": metrics.get("engagements_made", ""),
                        "intercepts": metrics.get("intercepts", ""),
                        "intercept_rate": metrics.get("intercept_rate", ""),
                        "defender_spent": metrics.get("defender_spent", ""),
                        "attacker_destroyed": metrics.get("attacker_destroyed", ""),
                        "cost_exchange_ratio": metrics.get("cost_exchange_ratio", ""),
                    }
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _empty_summary(self) -> dict:
        return {
            "scenario_name": self._metadata.scenario_name,
            "duration_s": 0.0,
            "total_frames": 0,
            "total_engagements": 0,
            "hits": 0,
            "misses": 0,
            "leakers": 0,
            "cost_exchange_ratio": None,
            "defender_spent": 0.0,
            "attacker_destroyed": 0.0,
            "peak_threat_count": 0,
            "peak_threat_time_s": 0.0,
            "cascade_outcomes": 0,
            "engagement_orders_made": 0,
            "timeline": [],
        }

    @staticmethod
    def _hit_rate(hits: int, total: int) -> str:
        if total == 0:
            return "N/A"
        return f"{hits / total * 100:.1f}%"
