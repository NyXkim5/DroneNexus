"""Tests for the BULWARK decision audit log: persistence, lineage, and replay."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csontology as cs
from wargame import load_scenario
from wargame.audit import (
    AuditLog,
    load_decisions,
    reconstruct_chain,
)
from wargame.runner import WargameRunner


def _run_with_audit(db_path: str, ticks: int = 160) -> None:
    """Drive a short wargame with auditing enabled, no real-time pacing."""
    async def go() -> None:
        scenario = load_scenario("probe_120")
        scenario.max_ticks = ticks
        audit = AuditLog(db_path)
        runner = WargameRunner(scenario, audit=audit)
        async for _frame in runner.run(pace=False):
            pass

    asyncio.run(go())


def test_decisions_are_persisted_and_replayable(tmp_path) -> None:
    db_path = str(tmp_path / "audit.db")
    _run_with_audit(db_path)
    assert os.path.exists(db_path)
    records = load_decisions(db_path)
    assert len(records) > 0
    for rec in records:
        assert rec.engagement_id
        assert rec.defender_id
        assert rec.cost >= 0.0
        assert rec.target_threat_ids


def test_lineage_chains_to_detection_ids(tmp_path) -> None:
    db_path = str(tmp_path / "audit.db")
    _run_with_audit(db_path)
    records = load_decisions(db_path)
    # At least one recorded decision must trace a threat back to the detection
    # ids that built its track. That is the explainable-and-replayable chain.
    has_lineage = any(
        any(det_ids for det_ids in rec.lineage.values()) for rec in records
    )
    assert has_lineage


def test_killed_ids_are_subset_of_targets(tmp_path) -> None:
    db_path = str(tmp_path / "audit.db")
    _run_with_audit(db_path)
    records = load_decisions(db_path)
    for rec in records:
        assert set(rec.killed_threat_ids).issubset(set(rec.target_threat_ids))


# ---- Synthetic fixtures (no runner) for detection persistence and replay ----


def _detection(det_id: str, sensor_id: str, tick: int, ts: float) -> cs.Detection:
    """Build one synthetic detection at a deterministic position."""
    return cs.Detection(
        id=det_id,
        timestamp=ts,
        position=(float(tick), 100.0, 120.0),
        velocity=(-5.0, -3.0, 0.0),
        confidence=0.8,
        sensor_id=sensor_id,
    )


def _track(track_id: str, det_ids: list[str]) -> cs.Track:
    """Build one synthetic track carrying the given source detection ids."""
    return cs.Track(
        id=track_id,
        position=(50.0, 100.0, 120.0),
        velocity=(-5.0, -3.0, 0.0),
        covariance=(2.0, 2.0, 1.0),
        last_update=1000.0,
        classification=cs.TrackClass.HOSTILE,
        confidence=0.9,
        source_detection_ids=list(det_ids),
    )


def _threat(threat_id: str, track_id: str) -> cs.Threat:
    """Build one synthetic scored threat tied to a track and swarm."""
    return cs.Threat(
        id=threat_id,
        score=0.91,
        time_to_impact_s=12.0,
        value_at_risk=750_000.0,
        priority_rank=1,
        track_id=track_id,
        swarm_id="swm-A",
        intent=cs.SwarmIntent.SATURATION,
    )


def _engagement(eng_id: str, target_id: str, status, killed: list[str]) -> cs.Engagement:
    """Build one synthetic engagement with an explicit outcome."""
    return cs.Engagement(
        id=eng_id,
        defender_id="def-1",
        target_threat_id=target_id,
        start_time=1000.0,
        status=status,
        cost=25000.0,
        neutralized_threat_ids=list(killed),
    )


def _synthesize_run(db_path: str, sensors=("radar", "eo")) -> str:
    """Persist a >100-tick run with a track that accrues >64 detections.

    Each sensor emits one detection per tick. The detections feed one track
    whose in-memory source list is capped at 64 but whose disk lineage is the
    full set. At a decision tick we record a HIT engagement on the threat.
    """
    audit = AuditLog(db_path)
    track_id = "trk-A"
    threat = _threat("thr-A", track_id)
    all_det_ids: list[str] = []
    for tick in range(1, 121):
        dets = [
            _detection(f"det-{s}-{tick}", s, tick, 1000.0 + tick)
            for s in sensors
        ]
        audit.record_detections(tick, 1000.0 + tick, dets)
        all_det_ids.extend(d.id for d in dets)
        capped = all_det_ids[-64:]
        audit.link_tracks(tick, [_track(track_id, capped)])
    eng = _engagement("eng-A", "thr-A", cs.EngagementStatus.HIT, ["thr-A"])
    audit.record_tick(
        121, 1121.0, [eng], {"thr-A": threat}, {track_id: _track(track_id, all_det_ids[-64:])},
    )
    audit.close()
    return track_id


def test_detections_persist_to_disk(tmp_path) -> None:
    import sqlite3

    db_path = str(tmp_path / "synth.db")
    _synthesize_run(db_path)
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    finally:
        conn.close()
    # 120 ticks times 2 sensors.
    assert count == 240


def test_track_with_over_64_detections_resolves_full_set(tmp_path) -> None:
    db_path = str(tmp_path / "synth.db")
    track_id = _synthesize_run(db_path)
    chain = reconstruct_chain(db_path, "eng-A")
    assert chain is not None
    assert chain.track_id == track_id
    total = sum(len(v) for v in chain.detections_by_sensor.values())
    # 240 detections fed the track over the run, far beyond the 64 in-memory cap.
    assert total == 240


def test_decision_row_carries_threat_fields_and_actor(tmp_path) -> None:
    db_path = str(tmp_path / "synth.db")
    _synthesize_run(db_path)
    records = load_decisions(db_path)
    assert len(records) == 1
    rec = records[0]
    assert rec.score == 0.91
    assert rec.value_at_risk == 750_000.0
    assert rec.swarm_id == "swm-A"
    assert rec.intent == cs.SwarmIntent.SATURATION.value
    assert rec.time_to_impact_s == 12.0
    assert rec.priority_rank == 1
    assert rec.actor == "AUTONOMY"
    assert rec.schema_version == 1


def test_custom_actor_is_recorded(tmp_path) -> None:
    db_path = str(tmp_path / "actor.db")
    audit = AuditLog(db_path)
    threat = _threat("thr-A", "trk-A")
    eng = _engagement("eng-A", "thr-A", cs.EngagementStatus.HIT, ["thr-A"])
    audit.record_tick(
        1, 1.0, [eng], {"thr-A": threat}, {"trk-A": _track("trk-A", ["d1"])},
        actor="OPERATOR",
    )
    audit.close()
    assert load_decisions(db_path)[0].actor == "OPERATOR"


def test_miss_keeps_intended_target(tmp_path) -> None:
    db_path = str(tmp_path / "miss.db")
    audit = AuditLog(db_path)
    threat = _threat("thr-X", "trk-X")
    # A MISS neutralizes nothing but must still record what it aimed at.
    eng = _engagement("eng-miss", "thr-X", cs.EngagementStatus.MISS, [])
    audit.record_tick(
        1, 1.0, [eng], {"thr-X": threat}, {"trk-X": _track("trk-X", ["d1"])},
    )
    audit.close()
    rec = load_decisions(db_path)[0]
    assert rec.status == cs.EngagementStatus.MISS.value
    assert rec.target_threat_ids == ["thr-X"]
    assert rec.killed_threat_ids == []


def test_reconstruct_chain_returns_outcome_threat_and_sensors(tmp_path) -> None:
    db_path = str(tmp_path / "synth.db")
    _synthesize_run(db_path, sensors=("radar", "eo"))
    chain = reconstruct_chain(db_path, "eng-A")
    assert chain is not None
    assert chain.status == cs.EngagementStatus.HIT.value
    assert chain.cost == 25000.0
    assert chain.actor == "AUTONOMY"
    assert chain.score == 0.91
    assert chain.intent == cs.SwarmIntent.SATURATION.value
    assert chain.time_to_impact_s == 12.0
    assert set(chain.detections_by_sensor) == {"radar", "eo"}
    for contribs in chain.detections_by_sensor.values():
        assert all(c.sensor_id in {"radar", "eo"} for c in contribs)


def test_reconstruct_chain_unknown_engagement_returns_none(tmp_path) -> None:
    db_path = str(tmp_path / "synth.db")
    _synthesize_run(db_path)
    assert reconstruct_chain(db_path, "no-such-eng") is None
