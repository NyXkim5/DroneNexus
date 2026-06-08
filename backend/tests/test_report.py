"""Tests for the after-action report CLI formatters."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csontology as cs
from wargame.audit import AuditLog
from wargame.report import format_chain, format_run_summary, format_runs


def _track(track_id, det_ids):
    return cs.Track(
        id=track_id, position=(10.0, 20.0, 80.0), velocity=(-5.0, 0.0, 0.0),
        covariance=(2.0, 2.0, 1.0), last_update=1.0, classification=cs.TrackClass.HOSTILE,
        confidence=0.9, source_detection_ids=list(det_ids),
    )


def _threat(threat_id, track_id):
    return cs.Threat(
        id=threat_id, score=0.8, time_to_impact_s=9.0, value_at_risk=50_000.0,
        priority_rank=1, track_id=track_id, intent=cs.SwarmIntent.SATURATION,
    )


def _eng(eng_id, target, status, killed):
    return cs.Engagement(
        id=eng_id, defender_id="HPM-1", target_threat_id=target, start_time=1.0,
        status=status, cost=8.0, neutralized_threat_ids=list(killed),
    )


def _seed(db_path) -> None:
    """Write one run with a detection-backed track and one HIT engagement."""
    audit = AuditLog(db_path, run_id="run1", scenario="saturation_1000")
    det = cs.Detection(
        id="d1", timestamp=1.0, position=(10.0, 20.0, 80.0), velocity=(0.0, 0.0, 0.0),
        confidence=0.9, sensor_id="radar-1",
    )
    audit.record_detections(1, 1.0, [det])
    audit.link_tracks(1, [_track("trk-1", ["d1"])])
    audit.record_tick(
        2, 2.0, [_eng("eng-1", "thr-1", cs.EngagementStatus.HIT, ["thr-1"])],
        {"thr-1": _threat("thr-1", "trk-1")}, {"trk-1": _track("trk-1", ["d1"])},
    )
    audit.close()


def test_format_runs_lists_the_run(tmp_path):
    db = str(tmp_path / "a.db")
    _seed(db)
    out = format_runs(db)
    assert "run1" in out and "saturation_1000" in out


def test_format_run_summary_counts_outcomes(tmp_path):
    db = str(tmp_path / "a.db")
    _seed(db)
    out = format_run_summary(db, "run1")
    assert "1 engagements" in out
    assert "1 hit" in out
    assert "1 kills credited" in out


def test_format_chain_renders_lineage(tmp_path):
    db = str(tmp_path / "a.db")
    _seed(db)
    out = format_chain(db, "eng-1")
    assert "eng-1" in out
    assert "SATURATION" in out
    assert "radar-1" in out


def test_format_chain_unknown_engagement(tmp_path):
    db = str(tmp_path / "a.db")
    _seed(db)
    assert "not found" in format_chain(db, "nope")
