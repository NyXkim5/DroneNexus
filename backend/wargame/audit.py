"""
Decision audit log for BULWARK, the explainable-and-replayable layer.

Every engagement the allocator commits is recorded to a SQLite store with its
full lineage: which detections fed which track, which track became which threat,
and which engagement neutralized it. This makes each fire decision auditable
after the fact and lets a whole wargame be replayed from the store.

The store is plain stdlib sqlite3 so it has no async or driver dependency and
persists on disk across runs. A DecisionRecord is the in-memory shape. record_tick
writes one row per engagement. load_decisions reads them back for replay.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from csontology import Engagement, Threat, Track, Vec3

logger = logging.getLogger("overwatch.audit")


@dataclass(frozen=True)
class DecisionRecord:
    """One audited fire decision with its end-to-end lineage.

    target_threat_ids are the threats the engagement aimed at. killed_threat_ids
    are the ones it neutralized. lineage maps each targeted threat id to the
    detection ids that built its track, so the detection to engagement chain is
    fully traceable. aim_point is set for area effectors.
    """

    tick: int
    timestamp: float
    engagement_id: str
    defender_id: str
    defender_kind: str
    cost: float
    status: str
    target_threat_ids: List[str]
    killed_threat_ids: List[str]
    lineage: Dict[str, List[str]]
    aim_point: Optional[Vec3] = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    engagement_id TEXT NOT NULL,
    defender_id TEXT NOT NULL,
    defender_kind TEXT NOT NULL,
    cost REAL NOT NULL,
    status TEXT NOT NULL,
    aim_x REAL, aim_y REAL, aim_z REAL,
    target_threat_ids TEXT NOT NULL,
    killed_threat_ids TEXT NOT NULL,
    lineage TEXT NOT NULL
);
"""


class AuditLog:
    """Append-only SQLite audit store for engagement decisions."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record_tick(
        self,
        tick: int,
        timestamp: float,
        engagements: List[Engagement],
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
    ) -> int:
        """Write one row per engagement this tick and return the count written."""
        rows = [
            self._to_row(tick, timestamp, eng, threats_by_id, tracks_by_id)
            for eng in engagements
        ]
        if rows:
            self._conn.executemany(
                "INSERT INTO decisions (tick, timestamp, engagement_id, "
                "defender_id, defender_kind, cost, status, aim_x, aim_y, aim_z, "
                "target_threat_ids, killed_threat_ids, lineage) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def _to_row(
        self,
        tick: int,
        timestamp: float,
        eng: Engagement,
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
    ) -> tuple:
        """Flatten one engagement and its lineage into an insert row."""
        targets = eng.neutralized_threat_ids or [eng.target_threat_id]
        lineage = self._lineage(targets, threats_by_id, tracks_by_id)
        kind = self._defender_kind(eng, threats_by_id)
        ax, ay, az = eng.aim_point if eng.aim_point is not None else (None, None, None)
        return (
            tick, timestamp, eng.id, eng.defender_id, kind,
            eng.cost, eng.status.value, ax, ay, az,
            json.dumps(targets), json.dumps(eng.neutralized_threat_ids),
            json.dumps(lineage),
        )

    def _lineage(
        self,
        target_ids: List[str],
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
    ) -> Dict[str, List[str]]:
        """Map each targeted threat to the detection ids behind its track."""
        chain: Dict[str, List[str]] = {}
        for tid in target_ids:
            threat = threats_by_id.get(tid)
            if threat is None or threat.track_id is None:
                chain[tid] = []
                continue
            track = tracks_by_id.get(threat.track_id)
            chain[tid] = list(track.source_detection_ids) if track else []
        return chain

    def _defender_kind(
        self, eng: Engagement, threats_by_id: Dict[str, Threat],
    ) -> str:
        """Best-effort defender kind label from the engagement id prefix."""
        return eng.defender_id.split("-")[0]

    def close(self) -> None:
        """Flush and close the underlying connection."""
        self._conn.commit()
        self._conn.close()


def load_decisions(db_path: str) -> List[DecisionRecord]:
    """Read every recorded decision back for replay or after-action review."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT tick, timestamp, engagement_id, defender_id, defender_kind, "
            "cost, status, aim_x, aim_y, aim_z, target_threat_ids, "
            "killed_threat_ids, lineage FROM decisions ORDER BY id"
        )
        return [_row_to_record(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _row_to_record(row: tuple) -> DecisionRecord:
    """Rebuild a DecisionRecord from a stored row."""
    aim = None if row[7] is None else (row[7], row[8], row[9])
    return DecisionRecord(
        tick=row[0], timestamp=row[1], engagement_id=row[2], defender_id=row[3],
        defender_kind=row[4], cost=row[5], status=row[6], aim_point=aim,
        target_threat_ids=json.loads(row[10]),
        killed_threat_ids=json.loads(row[11]),
        lineage=json.loads(row[12]),
    )
