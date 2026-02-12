"""
SQLite persistence via aiosqlite for telemetry, commands, and events.
"""
import aiosqlite
import json
import time
from datetime import datetime, timezone
from typing import List, Optional
import logging

logger = logging.getLogger("nexus.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drone_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload JSON NOT NULL,
    created_at REAL NOT NULL,
    session_name TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    params JSON,
    source TEXT DEFAULT 'operator',
    timestamp TEXT NOT NULL,
    success INTEGER,
    response TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drone_id TEXT,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    data JSON,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telem_drone ON telemetry(drone_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_telem_session ON telemetry(session_name);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
"""


class NexusDB:
    """Async SQLite database for telemetry logging and replay."""

    def __init__(self, db_path: str = "nexus.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(SCHEMA)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def log_telemetry_batch(self, packets: list,
                                    session_name: str = None) -> None:
        now = time.time()
        rows = [
            (p.get("drone_id", ""), p.get("timestamp", ""), p.get("seq", 0),
             json.dumps(p), now, session_name)
            for p in packets
        ]
        await self._db.executemany(
            "INSERT INTO telemetry (drone_id, timestamp, seq, payload, created_at, session_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._db.commit()

    async def log_command(self, command: str, params: dict,
                          success: bool = True, response: str = "") -> None:
        ts = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO commands (command, params, timestamp, success, response) "
            "VALUES (?, ?, ?, ?, ?)",
            (command, json.dumps(params), ts, int(success), response),
        )
        await self._db.commit()

    async def log_event(self, drone_id: Optional[str], severity: str,
                        message: str, data: dict = None) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO events (drone_id, severity, message, data, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (drone_id, severity, message,
             json.dumps(data) if data else None, ts),
        )
        await self._db.commit()

    async def get_commands(self, limit: int = 100) -> List[dict]:
        cursor = await self._db.execute(
            "SELECT id, command, params, timestamp, success, response "
            "FROM commands ORDER BY id DESC LIMIT ?", (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "command": r[1], "params": json.loads(r[2] or "{}"),
             "timestamp": r[3], "success": bool(r[4]), "response": r[5]}
            for r in rows
        ]

    async def get_events(self, limit: int = 100, severity: str = None) -> List[dict]:
        if severity:
            cursor = await self._db.execute(
                "SELECT id, drone_id, severity, message, data, timestamp "
                "FROM events WHERE severity = ? ORDER BY id DESC LIMIT ?",
                (severity, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, drone_id, severity, message, data, timestamp "
                "FROM events ORDER BY id DESC LIMIT ?", (limit,),
            )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "drone_id": r[1], "severity": r[2], "message": r[3],
             "data": json.loads(r[4]) if r[4] else None, "timestamp": r[5]}
            for r in rows
        ]

    async def get_telemetry_replay(self, drone_id: str,
                                    start_time: str, end_time: str) -> List[dict]:
        cursor = await self._db.execute(
            "SELECT payload FROM telemetry "
            "WHERE drone_id = ? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY seq ASC",
            (drone_id, start_time, end_time),
        )
        rows = await cursor.fetchall()
        return [json.loads(r[0]) for r in rows]

    async def get_session_packets(self, session_name: str) -> List[dict]:
        """Return all telemetry packets for a recording session, ordered by time."""
        cursor = await self._db.execute(
            "SELECT payload, created_at FROM telemetry "
            "WHERE session_name = ? ORDER BY created_at ASC, seq ASC",
            (session_name,),
        )
        rows = await cursor.fetchall()
        return [{"payload": json.loads(r[0]), "created_at": r[1]} for r in rows]

    async def get_session_count(self, session_name: str) -> int:
        """Return number of packets in a session."""
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_name = ?",
            (session_name,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def list_sessions(self) -> List[dict]:
        """Return metadata for all recorded sessions."""
        cursor = await self._db.execute(
            "SELECT session_name, COUNT(*) as packet_count, "
            "MIN(created_at) as started_at, MAX(created_at) as ended_at "
            "FROM telemetry WHERE session_name IS NOT NULL "
            "GROUP BY session_name ORDER BY started_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "session_name": r[0],
                "packet_count": r[1],
                "started_at": r[2],
                "ended_at": r[3],
                "duration_s": round(r[3] - r[2], 2) if r[2] and r[3] else 0,
            }
            for r in rows
        ]

    async def get_telemetry_range(self, drone_id: str,
                                   start_time: str, end_time: str) -> List[dict]:
        """Return raw telemetry payloads filtered by drone_id and time range."""
        cursor = await self._db.execute(
            "SELECT payload FROM telemetry "
            "WHERE drone_id = ? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC",
            (drone_id, start_time, end_time),
        )
        rows = await cursor.fetchall()
        return [json.loads(r[0]) for r in rows]

    async def ensure_session_column(self) -> None:
        """Add session_name column if migrating from older schema."""
        try:
            await self._db.execute(
                "ALTER TABLE telemetry ADD COLUMN session_name TEXT DEFAULT NULL"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists
