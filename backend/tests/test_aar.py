"""Tests for the after-action review analyzer."""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from scripts.after_action_review import AARAnalyzer, AARReport, format_terminal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _frame(
    tick: int,
    sim_time_s: float,
    tracks: Optional[List[dict]] = None,
    assignments: Optional[List[dict]] = None,
    engagements_made: int = 0,
    intercepts: int = 0,
    leakers: int = 0,
    defender_spent: float = 0.0,
    attacker_destroyed: float = 0.0,
    roe_evaluations: Optional[List[dict]] = None,
    tracks_held: int = 0,
) -> Dict[str, Any]:
    """Build a minimal wargame frame dict."""
    return {
        "metrics": {
            "tick": tick,
            "sim_time_s": sim_time_s,
            "active_hostiles": 0,
            "tracks_held": tracks_held or len(tracks or []),
            "leakers": leakers,
            "engagements_made": engagements_made,
            "intercepts": intercepts,
            "intercept_rate": intercepts / engagements_made if engagements_made else 0.0,
            "defender_spent": defender_spent,
            "attacker_destroyed": attacker_destroyed,
            "cost_exchange_ratio": (
                defender_spent / attacker_destroyed
                if attacker_destroyed > 0 else None
            ),
        },
        "tracks": tracks or [],
        "assignments": assignments or [],
        "roe_evaluations": roe_evaluations or [],
    }


@dataclass
class FakeTickRecord:
    """Mimics the TickRecord from autonomous_engage."""
    tick: int = 0
    elapsed_s: float = 0.0
    detection_count: int = 0
    track_count: int = 0
    hostile_count: int = 0
    threat_count: int = 0
    authorized_count: int = 0
    engagement_count: int = 0
    engagements: List[Dict[str, Any]] = field(default_factory=list)
    top_threat_tti: Optional[float] = None
    status: str = "IDLE"


# ---------------------------------------------------------------------------
# AARReport dataclass
# ---------------------------------------------------------------------------

class TestAARReport:

    def test_defaults(self) -> None:
        r = AARReport()
        assert r.total_ticks == 0
        assert r.recommendations == []

    def test_to_dict_round_trip(self) -> None:
        r = AARReport(total_ticks=10, hits=5, misses=2)
        d = r.to_dict()
        assert d["total_ticks"] == 10
        assert d["hits"] == 5
        assert json.dumps(d)


# ---------------------------------------------------------------------------
# Recording analysis
# ---------------------------------------------------------------------------

class TestAnalyzeRecording:

    def test_empty_frames(self) -> None:
        report = AARAnalyzer().analyze_recording([])
        assert report.total_ticks == 0

    def test_timeline(self) -> None:
        frames = [
            _frame(1, 0.2),
            _frame(2, 0.4),
            _frame(3, 0.6),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.total_ticks == 3
        assert report.total_duration_s == pytest.approx(0.4, abs=0.01)

    def test_first_detection(self) -> None:
        frames = [
            _frame(1, 0.2),
            _frame(2, 0.4, tracks=[{"id": "t1", "classification": "HOSTILE", "confidence": 0.9}]),
            _frame(3, 0.6),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.first_detection_time_s == pytest.approx(0.4)

    def test_engagement_stats_from_metrics(self) -> None:
        frames = [
            _frame(1, 0.2, tracks=[{"id": "t1", "classification": "HOSTILE", "confidence": 0.9}]),
            _frame(
                2, 0.4,
                assignments=[{"defender_id": "D1", "track_id": "t1", "status": "HIT"}],
                engagements_made=3, intercepts=2,
            ),
            _frame(3, 0.6, engagements_made=3, intercepts=2, leakers=1),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.total_engagements == 3
        assert report.hits == 2
        assert report.misses == 1
        assert report.leaks == 1
        assert report.hit_rate == pytest.approx(2 / 3)

    def test_cost_analysis(self) -> None:
        frames = [
            _frame(
                1, 0.2,
                defender_spent=16000.0,
                attacker_destroyed=50000.0,
                engagements_made=2,
                intercepts=2,
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.defender_spent == 16000.0
        assert report.attacker_destroyed == 50000.0
        assert report.cost_exchange_ratio == pytest.approx(0.32)

    def test_roe_compliance(self) -> None:
        frames = [
            _frame(
                1, 0.2,
                roe_evaluations=[
                    {"target_id": "t1", "authorized": True, "reason": "ok"},
                    {"target_id": "t2", "authorized": False, "reason": "denied"},
                ],
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.total_roe_evaluations == 2
        assert report.roe_denials == 1
        assert report.unauthorized_engagements == 0

    def test_false_positives_counted(self) -> None:
        frames = [
            _frame(
                1, 0.2,
                tracks=[
                    {"id": "t1", "classification": "UNKNOWN", "confidence": 0.8},
                    {"id": "t2", "classification": "HOSTILE", "confidence": 0.9},
                ],
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.false_positive_count == 1


# ---------------------------------------------------------------------------
# TickRecord analysis
# ---------------------------------------------------------------------------

class TestAnalyzeTickRecords:

    def test_empty_records(self) -> None:
        report = AARAnalyzer().analyze_tick_records([])
        assert report.total_ticks == 0

    def test_timeline_from_ticks(self) -> None:
        records = [
            FakeTickRecord(tick=1, elapsed_s=0.2, detection_count=3),
            FakeTickRecord(tick=2, elapsed_s=0.4, detection_count=2, track_count=2),
            FakeTickRecord(tick=3, elapsed_s=0.6, engagement_count=1),
        ]
        report = AARAnalyzer().analyze_tick_records(records)
        assert report.total_ticks == 3
        assert report.total_duration_s == pytest.approx(0.6)
        assert report.first_detection_time_s == pytest.approx(0.2)
        assert report.total_engagements == 1

    def test_detection_to_track_gap(self) -> None:
        records = [
            FakeTickRecord(tick=1, elapsed_s=1.0, detection_count=5),
            FakeTickRecord(tick=2, elapsed_s=2.0),
            FakeTickRecord(tick=3, elapsed_s=3.0, track_count=2),
        ]
        report = AARAnalyzer().analyze_tick_records(records)
        assert report.avg_detection_to_track_s == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:

    def test_low_hit_rate(self) -> None:
        frames = [
            _frame(
                1, 10.0,
                engagements_made=10, intercepts=5, leakers=0,
                tracks=[{"id": "t1", "classification": "HOSTILE", "confidence": 0.9}],
                assignments=[{"defender_id": "D1", "track_id": "t1", "status": "HIT"}],
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.hit_rate == 0.5
        assert any("Hit rate below 80%" in r for r in report.recommendations)

    def test_high_cost_ratio(self) -> None:
        frames = [
            _frame(
                1, 5.0,
                defender_spent=20000.0,
                attacker_destroyed=5000.0,
                engagements_made=2,
                intercepts=2,
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert any("Cost exchange unfavorable" in r for r in report.recommendations)

    def test_leakers_recommendation(self) -> None:
        frames = [
            _frame(1, 5.0, leakers=3, engagements_made=1, intercepts=1),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert any("3 leakers" in r for r in report.recommendations)

    def test_no_engagements_recommendation(self) -> None:
        frames = [_frame(i, float(i)) for i in range(1, 15)]
        report = AARAnalyzer().analyze_recording(frames)
        assert any("No engagements fired" in r for r in report.recommendations)

    def test_clean_run_no_recommendations(self) -> None:
        frames = [
            _frame(
                1, 5.0,
                engagements_made=10,
                intercepts=10,
                leakers=0,
                defender_spent=1000.0,
                attacker_destroyed=5000.0,
                tracks=[{"id": "t1", "classification": "HOSTILE", "confidence": 0.9}],
                assignments=[{"defender_id": "D1", "track_id": "t1", "status": "HIT"}],
            ),
        ]
        report = AARAnalyzer().analyze_recording(frames)
        assert report.recommendations == []


# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------

class TestFormatTerminal:

    def test_contains_sections(self) -> None:
        report = AARReport(
            total_ticks=100,
            total_duration_s=20.0,
            total_engagements=5,
            hits=4,
            misses=1,
            hit_rate=0.8,
            defender_spent=8000.0,
            attacker_destroyed=25000.0,
            cost_exchange_ratio=0.32,
        )
        text = format_terminal(report, "test_scenario")
        assert "AFTER-ACTION REVIEW" in text
        assert "TIMELINE" in text
        assert "ENGAGEMENT" in text
        assert "COST" in text
        assert "ROE COMPLIANCE" in text
        assert "test_scenario" in text

    def test_recommendations_shown(self) -> None:
        report = AARReport(recommendations=["Fix it", "Check it"])
        text = format_terminal(report)
        assert "RECOMMENDATIONS" in text
        assert "1. Fix it" in text
        assert "2. Check it" in text


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

class TestJSONOutput:

    def test_report_serializes(self) -> None:
        report = AARReport(
            total_ticks=50,
            hits=10,
            recommendations=["one", "two"],
        )
        raw = json.dumps(report.to_dict())
        loaded = json.loads(raw)
        assert loaded["total_ticks"] == 50
        assert loaded["hits"] == 10
        assert loaded["recommendations"] == ["one", "two"]

    def test_write_json_file(self, tmp_path: Path) -> None:
        report = AARReport(total_ticks=5)
        out = tmp_path / "aar.json"
        out.write_text(json.dumps(report.to_dict(), indent=2))
        loaded = json.loads(out.read_text())
        assert loaded["total_ticks"] == 5
