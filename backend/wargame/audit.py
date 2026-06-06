"""
Decision audit log for BULWARK, the explainable-and-replayable layer.

Every engagement the allocator commits is recorded to a SQLite store with its
full lineage: which detections fed which track, which track became which threat,
and which engagement neutralized it. This makes each fire decision auditable
after the fact and lets a whole wargame be replayed from the store.

The store is plain stdlib sqlite3 so it has no async or driver dependency and
persists on disk across runs. A DecisionRecord is the in-memory shape. record_tick
writes one row per engagement. record_detections persists raw contacts and
link_tracks persists the track to detection chain so the full lineage survives
even when the in-memory source_detection_ids list is capped. load_decisions reads
rows back and reconstruct_chain answers why one drone was engaged.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from csontology import Detection, Engagement, Threat, Track, Vec3

logger = logging.getLogger("overwatch.audit")

# Audit row schema version. Bump when the decisions columns change so a reader
# can branch on the stored value during after-action review.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DecisionRecord:
    """One audited fire decision with its end-to-end lineage.

    target_threat_ids are the threats the engagement aimed at and always include
    the intended primary target even on a miss. killed_threat_ids are the ones it
    neutralized. lineage maps each targeted threat id to the detection ids that
    built its track, so the detection to engagement chain is fully traceable.
    The threat-derived fields describe the primary threat at decision time.
    aim_point is set for area effectors. actor names who authorized the shot.
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
    schema_version: int = SCHEMA_VERSION
    score: Optional[float] = None
    value_at_risk: Optional[float] = None
    swarm_id: Optional[str] = None
    intent: Optional[str] = None
    time_to_impact_s: Optional[float] = None
    priority_rank: Optional[int] = None
    actor: str = "AUTONOMY"


@dataclass(frozen=True)
class DetectionContribution:
    """One detection that contributed to a reconstructed engagement chain."""

    detection_id: str
    sensor_id: str
    tick: int
    timestamp: float
    position: Vec3
    confidence: float


@dataclass(frozen=True)
class ChainReconstruction:
    """The full why-was-this-engaged answer for one engagement.

    engagement carries the outcome facts. threat carries the scored danger fields.
    track_id is the airframe estimate behind the threat. detections_by_sensor
    groups every contributing raw contact under its sensor id.
    """

    engagement_id: str
    defender_id: str
    actor: str
    timestamp: float
    status: str
    cost: float
    score: Optional[float]
    value_at_risk: Optional[float]
    intent: Optional[str]
    time_to_impact_s: Optional[float]
    track_id: Optional[str]
    detections_by_sensor: Dict[str, List[DetectionContribution]]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL DEFAULT 1,
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
    lineage TEXT NOT NULL,
    score REAL,
    value_at_risk REAL,
    swarm_id TEXT,
    intent TEXT,
    time_to_impact_s REAL,
    priority_rank INTEGER,
    actor TEXT NOT NULL DEFAULT 'AUTONOMY',
    primary_track_id TEXT
);

CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detection_id TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS track_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    track_id TEXT NOT NULL,
    detection_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_eng ON decisions(engagement_id);
CREATE INDEX IF NOT EXISTS idx_detections_did ON detections(detection_id);
CREATE INDEX IF NOT EXISTS idx_track_det_track ON track_detections(track_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_det_uniq
    ON track_detections(track_id, detection_id);
"""

_DECISION_COLUMNS = (
    "schema_version, tick, timestamp, engagement_id, defender_id, defender_kind, "
    "cost, status, aim_x, aim_y, aim_z, target_threat_ids, killed_threat_ids, "
    "lineage, score, value_at_risk, swarm_id, intent, time_to_impact_s, "
    "priority_rank, actor, primary_track_id"
)


class AuditLog:
    """Append-only SQLite audit store for engagements, detections, and lineage."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record_detections(
        self, tick: int, timestamp: float, detections: List[Detection],
    ) -> int:
        """Persist one tick of raw sensor contacts and return the count written.

        The lead calls this each tick before fusion so every contact survives on
        disk regardless of whether it later feeds a confirmed track.
        """
        rows = [self._detection_row(tick, timestamp, det) for det in detections]
        if rows:
            self._conn.executemany(
                "INSERT INTO detections (detection_id, sensor_id, tick, "
                "timestamp, x, y, z, confidence) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def _detection_row(
        self, tick: int, timestamp: float, det: Detection,
    ) -> tuple:
        """Flatten one Detection into an insert row."""
        px, py, pz = det.position
        ts = det.timestamp if det.timestamp else timestamp
        return (det.id, det.sensor_id, tick, ts, px, py, pz, det.confidence)

    def link_tracks(self, tick: int, tracks: List[Track]) -> int:
        """Persist the track to detection links so full lineage survives capping.

        The in-memory source_detection_ids list is capped at 64, so we append its
        current ids each tick. The unique index dedupes repeats, and the union
        across ticks reconstructs the full detection set behind a track.
        """
        rows: List[tuple] = []
        for track in tracks:
            for det_id in track.source_detection_ids:
                rows.append((tick, track.id, det_id))
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO track_detections "
                "(tick, track_id, detection_id) VALUES (?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def record_tick(
        self,
        tick: int,
        timestamp: float,
        engagements: List[Engagement],
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
        actor: str = "AUTONOMY",
    ) -> int:
        """Write one row per engagement this tick and return the count written.

        Also folds in link_tracks for the tracks behind these engagements so the
        detection chain persists even if the lead does not call link_tracks alone.
        """
        self.link_tracks(tick, list(tracks_by_id.values()))
        rows = [
            self._to_row(tick, timestamp, eng, threats_by_id, tracks_by_id, actor)
            for eng in engagements
        ]
        if rows:
            placeholders = ",".join(["?"] * 22)
            self._conn.executemany(
                f"INSERT INTO decisions ({_DECISION_COLUMNS}) "
                f"VALUES ({placeholders})",
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
        actor: str,
    ) -> tuple:
        """Flatten one engagement, its threat fields, and its lineage into a row."""
        targets = self._target_ids(eng)
        lineage = self._lineage(targets, threats_by_id, tracks_by_id)
        kind = self._defender_kind(eng, threats_by_id)
        ax, ay, az = eng.aim_point if eng.aim_point is not None else (None, None, None)
        primary = threats_by_id.get(eng.target_threat_id)
        return (
            SCHEMA_VERSION, tick, timestamp, eng.id, eng.defender_id, kind,
            eng.cost, eng.status.value, ax, ay, az,
            json.dumps(targets), json.dumps(list(eng.neutralized_threat_ids)),
            json.dumps(lineage),
            primary.score if primary else None,
            primary.value_at_risk if primary else None,
            primary.swarm_id if primary else None,
            primary.intent.value if primary else None,
            primary.time_to_impact_s if primary else None,
            primary.priority_rank if primary else None,
            actor,
            primary.track_id if primary else None,
        )

    def _target_ids(self, eng: Engagement) -> List[str]:
        """Return intended targets, always including the primary target.

        The intended primary target is recorded even on a miss. Any neutralized
        ids are added without duplicating the primary, so a missed shot still
        names what it aimed at and a multi-kill shot credits every target.
        """
        targets = [eng.target_threat_id]
        for tid in eng.neutralized_threat_ids:
            if tid not in targets:
                targets.append(tid)
        return targets

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
            f"SELECT {_DECISION_COLUMNS} FROM decisions ORDER BY id"
        )
        return [_row_to_record(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _row_to_record(row: tuple) -> DecisionRecord:
    """Rebuild a DecisionRecord from a stored row in _DECISION_COLUMNS order."""
    aim = None if row[8] is None else (row[8], row[9], row[10])
    return DecisionRecord(
        schema_version=row[0],
        tick=row[1],
        timestamp=row[2],
        engagement_id=row[3],
        defender_id=row[4],
        defender_kind=row[5],
        cost=row[6],
        status=row[7],
        aim_point=aim,
        target_threat_ids=json.loads(row[11]),
        killed_threat_ids=json.loads(row[12]),
        lineage=json.loads(row[13]),
        score=row[14],
        value_at_risk=row[15],
        swarm_id=row[16],
        intent=row[17],
        time_to_impact_s=row[18],
        priority_rank=row[19],
        actor=row[20],
    )


def reconstruct_chain(
    db_path: str, engagement_id: str,
) -> Optional[ChainReconstruction]:
    """Join decisions, track_detections, and detections for one engagement.

    Returns the why-was-this-drone-engaged answer: the engagement outcome, the
    threat fields, the track id, and every contributing detection grouped by
    sensor. Returns None when the engagement id is unknown.
    """
    conn = sqlite3.connect(db_path)
    try:
        decision = _fetch_decision(conn, engagement_id)
        if decision is None:
            return None
        track_id = decision[10]
        det_ids = _detection_ids_for_track(conn, track_id)
        grouped = _group_detections(conn, det_ids)
        return ChainReconstruction(
            engagement_id=decision[0],
            defender_id=decision[1],
            actor=decision[2],
            timestamp=decision[3],
            status=decision[4],
            cost=decision[5],
            score=decision[6],
            value_at_risk=decision[7],
            intent=decision[8],
            time_to_impact_s=decision[9],
            track_id=track_id,
            detections_by_sensor=grouped,
        )
    finally:
        conn.close()


def _fetch_decision(
    conn: sqlite3.Connection, engagement_id: str,
) -> Optional[tuple]:
    """Fetch the core decision fields for one engagement, newest row first."""
    cur = conn.execute(
        "SELECT engagement_id, defender_id, actor, timestamp, status, cost, "
        "score, value_at_risk, intent, time_to_impact_s, primary_track_id "
        "FROM decisions WHERE engagement_id = ? ORDER BY id DESC LIMIT 1",
        (engagement_id,),
    )
    return cur.fetchone()


def _detection_ids_for_track(
    conn: sqlite3.Connection, track_id: Optional[str],
) -> List[str]:
    """Return every detection id ever linked to a track across all ticks."""
    if track_id is None:
        return []
    cur = conn.execute(
        "SELECT DISTINCT detection_id FROM track_detections WHERE track_id = ?",
        (track_id,),
    )
    return [r[0] for r in cur.fetchall()]


def _group_detections(
    conn: sqlite3.Connection, detection_ids: List[str],
) -> Dict[str, List[DetectionContribution]]:
    """Load detections by id and group their contributions under each sensor."""
    grouped: Dict[str, List[DetectionContribution]] = {}
    for det_id in detection_ids:
        cur = conn.execute(
            "SELECT detection_id, sensor_id, tick, timestamp, x, y, z, "
            "confidence FROM detections WHERE detection_id = ? ORDER BY id",
            (det_id,),
        )
        for row in cur.fetchall():
            contrib = DetectionContribution(
                detection_id=row[0],
                sensor_id=row[1],
                tick=row[2],
                timestamp=row[3],
                position=(row[4], row[5], row[6]),
                confidence=row[7],
            )
            grouped.setdefault(row[1], []).append(contrib)
    return grouped
