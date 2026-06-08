"""
Decision audit log for BULWARK, the explainable-and-replayable layer.

Every engagement the allocator commits is recorded to a SQLite store with its
full lineage: which detections fed which track, which track became which threat,
and which engagement neutralized it. This makes each fire decision auditable
after the fact and lets a whole wargame be replayed from the store.

The store is a real entity-relational ontology, not one flat table. detections,
tracks, threats, and engagements are entity tables joined by declared FOREIGN
KEY relationships, with track_detections, engagement_threats as link tables.
Surrogate integer primary keys carry natural keys that repeat across ticks (a
track or threat id reappears every tick), and SQLite foreign keys are enforced
on every connection. A schema_meta table records the schema version so a reader
can branch on it during after-action review.

The store is plain stdlib sqlite3 so it has no async or driver dependency and
persists on disk across runs. record_tick writes one decision row per engagement
plus the threat, track, and engagement_threats rows behind it. record_detections
persists raw contacts and link_tracks persists the track to detection chain so
the full lineage survives even when the in-memory source_detection_ids list is
capped. load_decisions reads rows back and reconstruct_chain traverses the
foreign keys to answer why one drone was engaged.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from csontology import Detection, Engagement, Threat, Track, Vec3

logger = logging.getLogger("overwatch.audit")

# Store schema version. Bump when any table or relationship changes so a reader
# can branch on the stored value during after-action review.
SCHEMA_VERSION = 2


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
    run_id: str = ""


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
class TargetedThreat:
    """One threat an engagement targeted, with its scored danger fields.

    killed is True when this engagement neutralized the threat. The threat fields
    are read from the threats entity row reached through engagement_threats.
    """

    threat_id: str
    killed: bool
    score: Optional[float]
    value_at_risk: Optional[float]
    intent: Optional[str]
    time_to_impact_s: Optional[float]
    priority_rank: Optional[int]
    track_id: Optional[str]


@dataclass(frozen=True)
class ChainReconstruction:
    """The full why-was-this-engaged answer for one engagement.

    The fields carry the engagement outcome, the primary threat scored fields,
    the track id behind it, and every contributing raw detection grouped under
    its sensor id. targeted_threats lists every threat the engagement aimed at
    with its kill flag, traversed through the engagement_threats link table.
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
    targeted_threats: List[TargetedThreat]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL DEFAULT '',
    detection_id TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL,
    UNIQUE (track_id, tick)
);

CREATE TABLE IF NOT EXISTS threats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    threat_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    track_id TEXT,
    track_row_id INTEGER,
    score REAL,
    value_at_risk REAL,
    swarm_id TEXT,
    intent TEXT,
    time_to_impact_s REAL,
    priority_rank INTEGER,
    UNIQUE (threat_id, tick),
    FOREIGN KEY (track_row_id) REFERENCES tracks(id)
);

CREATE TABLE IF NOT EXISTS engagements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    defender_id TEXT NOT NULL,
    defender_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    cost REAL NOT NULL,
    actor TEXT NOT NULL DEFAULT 'AUTONOMY',
    primary_track_id TEXT,
    UNIQUE (engagement_id, tick)
);

CREATE TABLE IF NOT EXISTS engagement_threats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_row_id INTEGER NOT NULL,
    threat_row_id INTEGER,
    threat_id TEXT NOT NULL,
    killed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (engagement_row_id) REFERENCES engagements(id),
    FOREIGN KEY (threat_row_id) REFERENCES threats(id)
);

CREATE TABLE IF NOT EXISTS track_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL DEFAULT '',
    tick INTEGER NOT NULL,
    track_id TEXT NOT NULL,
    detection_id TEXT NOT NULL,
    detection_row_id INTEGER,
    FOREIGN KEY (detection_row_id) REFERENCES detections(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 2,
    tick INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    engagement_id TEXT NOT NULL,
    engagement_row_id INTEGER,
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
    primary_track_id TEXT,
    FOREIGN KEY (engagement_row_id) REFERENCES engagements(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_eng ON decisions(engagement_id);
CREATE INDEX IF NOT EXISTS idx_detections_did ON detections(detection_id);
CREATE INDEX IF NOT EXISTS idx_track_det_track ON track_detections(track_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_det_uniq
    ON track_detections(run_id, track_id, detection_id);
CREATE INDEX IF NOT EXISTS idx_threats_tid ON threats(threat_id);
CREATE INDEX IF NOT EXISTS idx_eng_eid ON engagements(engagement_id);
CREATE INDEX IF NOT EXISTS idx_eng_threats_eng
    ON engagement_threats(engagement_row_id);
"""

_DECISION_COLUMNS = (
    "schema_version, tick, timestamp, engagement_id, defender_id, defender_kind, "
    "cost, status, aim_x, aim_y, aim_z, target_threat_ids, killed_threat_ids, "
    "lineage, score, value_at_risk, swarm_id, intent, time_to_impact_s, "
    "priority_rank, actor, primary_track_id, run_id"
)


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with foreign key enforcement turned on."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class AuditLog:
    """Append-only SQLite audit store for the counter-swarm entity ontology."""

    def __init__(
        self,
        db_path: str,
        run_id: Optional[str] = None,
        scenario: str = "",
        label: str = "",
    ) -> None:
        """Open the store and register this run.

        run_id identifies the run that produced the decisions written through this
        instance, so several runs can share one store without their after-action
        logs colliding. A generated id is used when none is given. The FK lineage
        already uses globally unique surrogate row ids, so reconstruct_chain stays
        collision-safe across runs; run_id scopes the natural-key decision log.
        """
        self._conn = _connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, scenario, label) VALUES (?, ?, ?)",
            (self._run_id, scenario, label),
        )
        self._conn.commit()

    @property
    def run_id(self) -> str:
        """The run identifier stamped on every decision this instance writes."""
        return self._run_id

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
                "INSERT INTO detections (run_id, detection_id, sensor_id, tick, "
                "timestamp, x, y, z, confidence) VALUES (?,?,?,?,?,?,?,?,?)",
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
        return (self._run_id, det.id, det.sensor_id, tick, ts, px, py, pz, det.confidence)

    def link_tracks(self, tick: int, tracks: List[Track]) -> int:
        """Persist the track to detection links so full lineage survives capping.

        The in-memory source_detection_ids list is capped at 64, so we append its
        current ids each tick. The unique index dedupes repeats, and the union
        across ticks reconstructs the full detection set behind a track. Each link
        resolves a foreign key to the detection entity row when one exists on disk.
        """
        rows: List[tuple] = []
        for track in tracks:
            for det_id in track.source_detection_ids:
                det_row_id = self._latest_detection_row_id(det_id)
                rows.append((self._run_id, tick, track.id, det_id, det_row_id))
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO track_detections "
                "(run_id, tick, track_id, detection_id, detection_row_id) "
                "VALUES (?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def _latest_detection_row_id(self, detection_id: str) -> Optional[int]:
        """Return the newest stored detection row id for this run, if any.

        Scoped to this run so a repeated detection id from another run in the same
        store never mislinks a track to the wrong run's detection.
        """
        cur = self._conn.execute(
            "SELECT id FROM detections WHERE detection_id = ? AND run_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (detection_id, self._run_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def record_tracks(self, tick: int, tracks: List[Track]) -> int:
        """Persist one snapshot row per track this tick and return the count.

        Each row is a per-tick estimate of one airframe. The (track_id, tick)
        unique index makes the write idempotent within a tick.
        """
        rows = [self._track_row(tick, tr) for tr in tracks]
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO tracks (track_id, tick, classification, "
                "confidence, x, y, z) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def _track_row(self, tick: int, track: Track) -> tuple:
        """Flatten one Track snapshot into an insert row."""
        tx, ty, tz = track.position
        return (
            track.id, tick, track.classification.value, track.confidence,
            tx, ty, tz,
        )

    def record_threats(
        self,
        tick: int,
        threats: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
    ) -> int:
        """Persist one threat entity row per threat this tick and return the count.

        Each threat row carries a foreign key to its track snapshot row for this
        tick so the threat to track relationship is declared, not hand-joined.
        """
        self.record_tracks(tick, list(tracks_by_id.values()))
        rows = [self._threat_row(tick, th) for th in threats.values()]
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO threats (threat_id, tick, track_id, "
                "track_row_id, score, value_at_risk, swarm_id, intent, "
                "time_to_impact_s, priority_rank) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def _threat_row(self, tick: int, threat: Threat) -> tuple:
        """Flatten one Threat into an insert row with its track foreign key."""
        track_row_id = self._track_row_id(threat.track_id, tick)
        return (
            threat.id, tick, threat.track_id, track_row_id, threat.score,
            threat.value_at_risk, threat.swarm_id, threat.intent.value,
            threat.time_to_impact_s, threat.priority_rank,
        )

    def _track_row_id(
        self, track_id: Optional[str], tick: int,
    ) -> Optional[int]:
        """Return the surrogate id of a track snapshot for this tick, if any."""
        if track_id is None:
            return None
        cur = self._conn.execute(
            "SELECT id FROM tracks WHERE track_id = ? AND tick = ?",
            (track_id, tick),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def record_tick(
        self,
        tick: int,
        timestamp: float,
        engagements: List[Engagement],
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
        actor: str = "AUTONOMY",
    ) -> int:
        """Write this tick's entity rows and one decision per engagement.

        Populates the tracks, threats, engagements, engagement_threats link, and
        decisions tables and folds in link_tracks so the detection chain persists.
        Returns the number of decision rows written. Signature is stable for the
        runner.
        """
        self.link_tracks(tick, list(tracks_by_id.values()))
        self.record_threats(tick, threats_by_id, tracks_by_id)
        count = 0
        for eng in engagements:
            self._record_engagement(tick, timestamp, eng, threats_by_id,
                                    tracks_by_id, actor)
            count += 1
        self._conn.commit()
        return count

    def _record_engagement(
        self,
        tick: int,
        timestamp: float,
        eng: Engagement,
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
        actor: str,
    ) -> None:
        """Insert one engagement entity, its threat links, and its decision row."""
        kind = self._defender_kind(eng, threats_by_id)
        primary = threats_by_id.get(eng.target_threat_id)
        primary_track = primary.track_id if primary else None
        eng_row_id = self._insert_engagement(
            tick, eng, kind, actor, primary_track,
        )
        targets = self._target_ids(eng)
        self._link_engagement_threats(eng_row_id, eng, targets, tick)
        self._insert_decision(
            tick, timestamp, eng, eng_row_id, kind, primary, actor,
            targets, threats_by_id, tracks_by_id,
        )

    def _insert_engagement(
        self, tick: int, eng: Engagement, kind: str, actor: str,
        primary_track: Optional[str],
    ) -> int:
        """Insert one engagement entity row and return its surrogate id."""
        cur = self._conn.execute(
            "INSERT INTO engagements (engagement_id, tick, defender_id, "
            "defender_kind, status, cost, actor, primary_track_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eng.id, tick, eng.defender_id, kind, eng.status.value, eng.cost,
             actor, primary_track),
        )
        return int(cur.lastrowid)

    def _link_engagement_threats(
        self, eng_row_id: int, eng: Engagement, targets: List[str], tick: int,
    ) -> None:
        """Link this engagement to every threat it targeted, flagging the kills."""
        killed = set(eng.neutralized_threat_ids)
        rows = [
            (eng_row_id, self._threat_row_id(tid, tick), tid,
             1 if tid in killed else 0)
            for tid in targets
        ]
        self._conn.executemany(
            "INSERT INTO engagement_threats (engagement_row_id, threat_row_id, "
            "threat_id, killed) VALUES (?,?,?,?)",
            rows,
        )

    def _threat_row_id(self, threat_id: str, tick: int) -> Optional[int]:
        """Return the surrogate id of a threat entity for this tick, if any."""
        cur = self._conn.execute(
            "SELECT id FROM threats WHERE threat_id = ? AND tick = ?",
            (threat_id, tick),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def _insert_decision(
        self,
        tick: int,
        timestamp: float,
        eng: Engagement,
        eng_row_id: int,
        kind: str,
        primary: Optional[Threat],
        actor: str,
        targets: List[str],
        threats_by_id: Dict[str, Threat],
        tracks_by_id: Dict[str, Track],
    ) -> None:
        """Insert the denormalized decision row keyed to its engagement entity."""
        lineage = self._lineage(targets, threats_by_id, tracks_by_id)
        ax, ay, az = eng.aim_point if eng.aim_point is not None else (None, None, None)
        row = (
            self._run_id,
            SCHEMA_VERSION, tick, timestamp, eng.id, eng_row_id, eng.defender_id,
            kind, eng.cost, eng.status.value, ax, ay, az,
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
        self._conn.execute(
            "INSERT INTO decisions (run_id, schema_version, tick, timestamp, "
            "engagement_id, engagement_row_id, defender_id, defender_kind, cost, "
            "status, aim_x, aim_y, aim_z, target_threat_ids, killed_threat_ids, "
            "lineage, score, value_at_risk, swarm_id, intent, time_to_impact_s, "
            "priority_rank, actor, primary_track_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
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


def schema_version(db_path: str) -> Optional[int]:
    """Read the recorded schema version from the store, or None if absent."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def load_decisions(
    db_path: str, run_id: Optional[str] = None,
) -> List[DecisionRecord]:
    """Read recorded decisions for replay or after-action review.

    With run_id given, only that run's decisions are returned, so several runs in
    one store do not mix. Without it, every run's decisions are returned in write
    order.
    """
    conn = _connect(db_path)
    try:
        if run_id is None:
            cur = conn.execute(f"SELECT {_DECISION_COLUMNS} FROM decisions ORDER BY id")
        else:
            cur = conn.execute(
                f"SELECT {_DECISION_COLUMNS} FROM decisions WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
        return [_row_to_record(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_runs(db_path: str) -> List[Tuple[str, str, str]]:
    """Return the recorded runs as (run_id, scenario, label) tuples."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT run_id, scenario, label FROM runs ORDER BY rowid")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
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
        run_id=row[22],
    )


def reconstruct_chain(
    db_path: str, engagement_id: str,
) -> Optional[ChainReconstruction]:
    """Traverse the entity foreign keys for one engagement and return the chain.

    Walks engagement -> engagement_threats -> threats -> tracks ->
    track_detections -> detections, returning the why-was-this-drone-engaged
    answer: the outcome, the primary threat fields, every targeted threat with
    its kill flag, and every contributing detection grouped by sensor. Returns
    None when the engagement id is unknown.
    """
    conn = _connect(db_path)
    try:
        eng = _fetch_engagement(conn, engagement_id)
        if eng is None:
            return None
        eng_row_id, primary_track = eng[0], eng[7]
        targeted = _targeted_threats(conn, eng_row_id)
        det_ids = _detection_ids_for_track(conn, primary_track)
        grouped = _group_detections(conn, det_ids)
        primary = _primary_threat(conn, primary_track, eng[1])
        return _build_reconstruction(eng, primary, primary_track, grouped, targeted)
    finally:
        conn.close()


def _fetch_engagement(
    conn: sqlite3.Connection, engagement_id: str,
) -> Optional[tuple]:
    """Fetch the engagement entity row for one id, newest tick first."""
    cur = conn.execute(
        "SELECT e.id, e.tick, e.engagement_id, e.defender_id, e.actor, "
        "e.status, e.cost, e.primary_track_id, "
        "(SELECT d.timestamp FROM decisions d WHERE d.engagement_row_id = e.id "
        " ORDER BY d.id DESC LIMIT 1) AS ts "
        "FROM engagements e WHERE e.engagement_id = ? "
        "ORDER BY e.tick DESC, e.id DESC LIMIT 1",
        (engagement_id,),
    )
    return cur.fetchone()


def _build_reconstruction(
    eng: tuple,
    primary: Optional[tuple],
    primary_track: Optional[str],
    grouped: Dict[str, List[DetectionContribution]],
    targeted: List[TargetedThreat],
) -> ChainReconstruction:
    """Assemble a ChainReconstruction from the traversed entity rows."""
    return ChainReconstruction(
        engagement_id=eng[2],
        defender_id=eng[3],
        actor=eng[4],
        timestamp=_engagement_timestamp(eng),
        status=eng[5],
        cost=eng[6],
        score=primary[0] if primary else None,
        value_at_risk=primary[1] if primary else None,
        intent=primary[2] if primary else None,
        time_to_impact_s=primary[3] if primary else None,
        track_id=primary_track,
        detections_by_sensor=grouped,
        targeted_threats=targeted,
    )


def _engagement_timestamp(eng: tuple) -> float:
    """Return the recorded decision timestamp, falling back to the tick number."""
    return float(eng[8]) if eng[8] is not None else float(eng[1])


def _primary_threat(
    conn: sqlite3.Connection, track_id: Optional[str], tick: int,
) -> Optional[tuple]:
    """Fetch the scored fields of the primary threat for this track and tick."""
    if track_id is None:
        return None
    cur = conn.execute(
        "SELECT score, value_at_risk, intent, time_to_impact_s FROM threats "
        "WHERE track_id = ? ORDER BY ABS(tick - ?), tick DESC LIMIT 1",
        (track_id, tick),
    )
    return cur.fetchone()


def _targeted_threats(
    conn: sqlite3.Connection, eng_row_id: int,
) -> List[TargetedThreat]:
    """Read every threat this engagement targeted through the link table."""
    cur = conn.execute(
        "SELECT et.threat_id, et.killed, t.score, t.value_at_risk, t.intent, "
        "t.time_to_impact_s, t.priority_rank, t.track_id "
        "FROM engagement_threats et "
        "LEFT JOIN threats t ON t.id = et.threat_row_id "
        "WHERE et.engagement_row_id = ? ORDER BY et.id",
        (eng_row_id,),
    )
    return [
        TargetedThreat(
            threat_id=row[0],
            killed=bool(row[1]),
            score=row[2],
            value_at_risk=row[3],
            intent=row[4],
            time_to_impact_s=row[5],
            priority_rank=row[6],
            track_id=row[7],
        )
        for row in cur.fetchall()
    ]


def _detection_ids_for_track(
    conn: sqlite3.Connection,
    track_id: Optional[str],
    run_id: Optional[str] = None,
) -> List[str]:
    """Return every detection id ever linked to a track across all ticks.

    When run_id is given, only that run's links are returned.
    """
    if track_id is None:
        return []
    if run_id is None:
        cur = conn.execute(
            "SELECT DISTINCT detection_id FROM track_detections WHERE track_id = ?",
            (track_id,),
        )
    else:
        cur = conn.execute(
            "SELECT DISTINCT detection_id FROM track_detections "
            "WHERE track_id = ? AND run_id = ?",
            (track_id, run_id),
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


def threats_for_engagement(
    db_path: str, engagement_id: str,
) -> List[TargetedThreat]:
    """Return every threat an engagement targeted, traversed through the links."""
    conn = _connect(db_path)
    try:
        eng = _fetch_engagement(conn, engagement_id)
        if eng is None:
            return []
        return _targeted_threats(conn, eng[0])
    finally:
        conn.close()


def detections_for_track(
    db_path: str, track_id: str, run_id: Optional[str] = None,
) -> Dict[str, List[DetectionContribution]]:
    """Return every detection behind a track, grouped under its sensor id.

    With run_id given, only links from that run are followed, so a track id reused
    by another run in the same store does not pull in foreign detections.
    """
    conn = _connect(db_path)
    try:
        det_ids = _detection_ids_for_track(conn, track_id, run_id)
        return _group_detections(conn, det_ids)
    finally:
        conn.close()
