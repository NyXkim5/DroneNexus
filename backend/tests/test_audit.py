"""Tests for the BULWARK decision audit log: persistence, lineage, and replay."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wargame import load_scenario
from wargame.audit import AuditLog, load_decisions
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
