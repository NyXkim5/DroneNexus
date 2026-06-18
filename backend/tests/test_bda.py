"""
Tests for BDA system (backend/defense/bda.py) and
tamper-proof audit hash chain (backend/audit/hash_chain.py).

Run from backend/:
    python3 -m pytest tests/test_bda.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

import pytest

from defense.bda import BDAStatus, BDASystem
from audit.hash_chain import AuditHashChain, _compute_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bda(delay: float = 3.0) -> BDASystem:
    return BDASystem(assessment_delay_s=delay)


def _register(bda: BDASystem, eng_id: str = "eng-1", target_id: str = "T1",
               effector_id: str = "eff-1", timestamp: float = 0.0,
               initial_speed: float | None = None) -> None:
    bda.register_engagement(
        engagement_id=eng_id,
        target_id=target_id,
        effector_id=effector_id,
        timestamp=timestamp,
        initial_speed=initial_speed,
    )


# ---------------------------------------------------------------------------
# BDA tests
# ---------------------------------------------------------------------------

class TestBDAKill:
    def test_register_and_assess_kill(self) -> None:
        """Target disappears after engagement -> CONFIRMED_KILL."""
        bda = _bda(delay=3.0)
        _register(bda, timestamp=0.0)

        # Not enough time yet.
        reports = bda.assess(
            timestamp=2.0,
            track_exists=lambda tid: False,
            track_speed=lambda tid: 0.0,
            track_confidence=lambda tid: 0.0,
        )
        assert reports == []

        # Delay passed, target gone.
        reports = bda.assess(
            timestamp=4.0,
            track_exists=lambda tid: False,
            track_speed=lambda tid: 0.0,
            track_confidence=lambda tid: 0.0,
        )
        assert len(reports) == 1
        assert reports[0].status == BDAStatus.CONFIRMED_KILL
        assert reports[0].re_engage_priority == 0.0


class TestBDAMiss:
    def test_assess_miss(self) -> None:
        """Target unchanged -> MISSED."""
        bda = _bda(delay=3.0)
        _register(bda, timestamp=0.0, initial_speed=20.0)

        reports = bda.assess(
            timestamp=5.0,
            track_exists=lambda tid: True,
            track_speed=lambda tid: 20.0,
            track_confidence=lambda tid: 0.9,
        )
        assert len(reports) == 1
        assert reports[0].status == BDAStatus.MISSED
        assert reports[0].re_engage_priority > 0.0


class TestBDADamaged:
    def test_assess_damaged(self) -> None:
        """Target slowed by >50% -> DAMAGED with re-engage recommendation."""
        bda = _bda(delay=3.0)
        _register(bda, timestamp=0.0, initial_speed=20.0)

        reports = bda.assess(
            timestamp=5.0,
            track_exists=lambda tid: True,
            track_speed=lambda tid: 5.0,    # 75% reduction
            track_confidence=lambda tid: 0.85,
        )
        assert len(reports) == 1
        assert reports[0].status == BDAStatus.DAMAGED
        assert reports[0].re_engage_priority > 0.0


class TestBDADelay:
    def test_assessment_delay(self) -> None:
        """Assessment only happens after delay_s has elapsed."""
        bda = _bda(delay=5.0)
        _register(bda, timestamp=10.0)

        # 4 seconds in — not ready yet.
        early = bda.assess(
            timestamp=14.0,
            track_exists=lambda tid: False,
            track_speed=lambda tid: 0.0,
            track_confidence=lambda tid: 0.0,
        )
        assert early == []

        # 5 seconds exactly — ready.
        ready = bda.assess(
            timestamp=15.0,
            track_exists=lambda tid: False,
            track_speed=lambda tid: 0.0,
            track_confidence=lambda tid: 0.0,
        )
        assert len(ready) == 1


class TestBDAReEngage:
    def test_re_engage_targets(self) -> None:
        """Missed and damaged targets appear in get_re_engage_targets()."""
        bda = _bda(delay=1.0)

        # Miss
        _register(bda, eng_id="eng-miss", target_id="T-miss", timestamp=0.0, initial_speed=15.0)
        # Damaged
        _register(bda, eng_id="eng-dmg", target_id="T-dmg", timestamp=0.0, initial_speed=20.0)
        # Kill — should NOT appear
        _register(bda, eng_id="eng-kill", target_id="T-kill", timestamp=0.0)

        speeds = {"T-miss": 15.0, "T-dmg": 5.0, "T-kill": 0.0}
        exists = {"T-miss": True, "T-dmg": True, "T-kill": False}
        confs = {"T-miss": 0.9, "T-dmg": 0.85, "T-kill": 0.0}

        bda.assess(
            timestamp=3.0,
            track_exists=lambda tid: exists[tid],
            track_speed=lambda tid: speeds[tid],
            track_confidence=lambda tid: confs[tid],
        )

        targets = bda.get_re_engage_targets()
        target_ids = [r.target_id for r in targets]
        assert "T-miss" in target_ids
        assert "T-dmg" in target_ids
        assert "T-kill" not in target_ids

        # Sorted by priority descending — miss should outrank damaged.
        priorities = [r.re_engage_priority for r in targets]
        assert priorities == sorted(priorities, reverse=True)


# ---------------------------------------------------------------------------
# Hash chain tests
# ---------------------------------------------------------------------------

class TestHashChainAppend:
    def test_hash_chain_append(self) -> None:
        """Entries get sequential hashes and length increments correctly."""
        chain = AuditHashChain()
        e1 = chain.append("engagement", {"target": "T1"}, timestamp=1000.0)
        e2 = chain.append("bda", {"status": "confirmed"}, timestamp=1001.0)

        assert e1.sequence == 0
        assert e2.sequence == 1
        assert chain.length == 2
        assert e1.entry_hash != e2.entry_hash
        assert e2.previous_hash == e1.entry_hash
        assert chain.last_hash == e2.entry_hash


class TestHashChainVerifyValid:
    def test_hash_chain_verify_valid(self) -> None:
        """Unmodified chain verifies cleanly."""
        chain = AuditHashChain()
        for i in range(5):
            chain.append("decision", {"tick": i}, timestamp=float(i))

        valid, broken = chain.verify()
        assert valid is True
        assert broken is None


class TestHashChainTampering:
    def test_hash_chain_detect_tampering(self) -> None:
        """Modifying an entry's data breaks verification at that index."""
        chain = AuditHashChain()
        chain.append("roe_evaluation", {"authorized": True}, timestamp=1.0)
        chain.append("engagement", {"target": "T1"}, timestamp=2.0)
        chain.append("bda", {"status": "missed"}, timestamp=3.0)

        # Tamper with entry 1's data in place.
        chain._entries[1].data["target"] = "TAMPERED"

        valid, broken = chain.verify()
        assert valid is False
        assert broken == 1


class TestHashChainExportLoad:
    def test_hash_chain_export_load_roundtrip(self, tmp_path: Path) -> None:
        """Export, load, and verify produces an identical valid chain."""
        chain = AuditHashChain()
        chain.append("roe_evaluation", {"target": "T1", "authorized": True}, timestamp=100.0)
        chain.append("engagement", {"effector": "eff-1"}, timestamp=101.0)
        chain.append("bda", {"status": "confirmed"}, timestamp=104.0)

        export_path = tmp_path / "audit.jsonl"
        chain.export(export_path)
        assert export_path.exists()

        loaded = AuditHashChain.load(export_path)
        assert loaded.length == chain.length
        assert loaded.last_hash == chain.last_hash

        valid, broken = loaded.verify()
        assert valid is True
        assert broken is None

    def test_load_detects_tampered_file(self, tmp_path: Path) -> None:
        """A tampered export file raises ValueError on load."""
        chain = AuditHashChain()
        chain.append("engagement", {"target": "T1"}, timestamp=1.0)
        chain.append("bda", {"status": "missed"}, timestamp=4.0)

        export_path = tmp_path / "tampered.jsonl"
        chain.export(export_path)

        # Read lines, corrupt line 0, write back.
        lines = export_path.read_text(encoding="utf-8").splitlines()
        obj = json.loads(lines[0])
        obj["data"]["target"] = "HACKED"
        lines[0] = json.dumps(obj)
        export_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(ValueError, match="integrity check failed"):
            AuditHashChain.load(export_path)


class TestHashChainGenesis:
    def test_hash_chain_genesis(self) -> None:
        """First entry's previous_hash is the all-zeros genesis hash."""
        chain = AuditHashChain()
        entry = chain.append("decision", {}, timestamp=0.0)
        assert entry.previous_hash == "0" * 64
        assert entry.sequence == 0

    def test_empty_chain_verifies(self) -> None:
        """An empty chain is trivially valid."""
        chain = AuditHashChain()
        valid, broken = chain.verify()
        assert valid is True
        assert broken is None
