"""
Telemetry Replay System — record live sessions and replay them at variable speed.

Records telemetry packets with a session tag and replays them through the
SwarmAggregator WebSocket broadcast pipeline at the original timing scaled
by a configurable speed factor.
"""
import asyncio
import json
import time
import logging
from typing import List, Optional

from db.models import OverwatchDB
from telemetry.aggregator import SwarmAggregator

logger = logging.getLogger("overwatch.replay")


class ReplayEngine:
    """Records and replays telemetry sessions via the database."""

    def __init__(self, db: OverwatchDB, aggregator: SwarmAggregator):
        self._db = db
        self._aggregator = aggregator

        # Recording state
        self._recording = False
        self._recording_session: Optional[str] = None
        self._recording_start: float = 0.0
        self._recording_count: int = 0
        self._record_task: Optional[asyncio.Task] = None

        # Replay state
        self._replaying = False
        self._replay_task: Optional[asyncio.Task] = None
        self._replay_session: Optional[str] = None
        self._replay_speed: float = 1.0

    # ---- Properties ----

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_replaying(self) -> bool:
        return self._replaying

    # ---- Recording ----

    def start_recording(self, session_name: str) -> None:
        """Begin logging all telemetry packets to DB with a session tag.

        Starts a background task that snapshots drone states from the
        aggregator at 10Hz and writes them to the telemetry table with
        the given session_name.
        """
        if self._recording:
            raise RuntimeError(
                f"Already recording session '{self._recording_session}'"
            )

        self._recording = True
        self._recording_session = session_name
        self._recording_start = time.time()
        self._recording_count = 0
        self._record_task = asyncio.create_task(
            self._recording_loop(session_name)
        )
        logger.info(f"Recording started: '{session_name}'")

    async def _recording_loop(self, session_name: str) -> None:
        """Snapshot aggregator states at 10Hz and batch-write to DB."""
        interval = 1.0 / 10  # 10Hz
        try:
            while self._recording:
                t0 = time.monotonic()

                packets = []
                for state in self._aggregator.drone_states.values():
                    pkt = state.to_telemetry_packet()
                    packets.append(pkt.model_dump(mode="json"))

                if packets:
                    await self._db.log_telemetry_batch(
                        packets, session_name=session_name
                    )
                    self._recording_count += len(packets)

                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(0, interval - elapsed))
        except asyncio.CancelledError:
            logger.info(f"Recording task cancelled for session '{session_name}'")

    async def stop_recording(self) -> dict:
        """Stop the active recording and return session summary."""
        if not self._recording:
            raise RuntimeError("No active recording")

        self._recording = False
        if self._record_task and not self._record_task.done():
            self._record_task.cancel()
            try:
                await self._record_task
            except asyncio.CancelledError:
                pass

        duration = time.time() - self._recording_start
        summary = {
            "session_name": self._recording_session,
            "packet_count": self._recording_count,
            "duration_s": round(duration, 2),
        }

        logger.info(
            f"Recording stopped: '{self._recording_session}' "
            f"({self._recording_count} packets, {duration:.1f}s)"
        )

        self._recording_session = None
        self._record_task = None
        return summary

    # ---- Session queries ----

    async def list_sessions(self) -> List[dict]:
        """Query DB for all recorded sessions with metadata."""
        return await self._db.list_sessions()

    # ---- Replay ----

    async def start_replay(self, session_name: str, speed: float = 1.0) -> None:
        """Start an async replay of a recorded session.

        Reads packets from the DB for the given session and broadcasts
        them through the aggregator's WebSocket clients at the original
        timing scaled by the speed factor (2.0 = double speed, 0.5 = half).
        """
        if self._replaying:
            raise RuntimeError(
                f"Already replaying session '{self._replay_session}'"
            )

        # Verify session exists
        count = await self._db.get_session_count(session_name)
        if count == 0:
            raise ValueError(f"Session '{session_name}' not found or empty")

        self._replaying = True
        self._replay_session = session_name
        self._replay_speed = speed
        self._replay_task = asyncio.create_task(
            self._replay_loop(session_name, speed)
        )
        logger.info(
            f"Replay started: '{session_name}' ({count} packets, {speed}x speed)"
        )

    async def _replay_loop(self, session_name: str, speed: float) -> None:
        """Read packets from DB and broadcast at scaled timing."""
        try:
            rows = await self._db.get_session_packets(session_name)
            if not rows:
                logger.warning(f"No packets found for session '{session_name}'")
                self._replaying = False
                return

            prev_ts = rows[0]["created_at"]

            for row in rows:
                if not self._replaying:
                    break

                # Calculate delay based on original timing
                delta = row["created_at"] - prev_ts
                prev_ts = row["created_at"]

                if delta > 0 and speed > 0:
                    await asyncio.sleep(delta / speed)

                # Broadcast the packet to all WebSocket clients
                payload = row["payload"]
                # Wrap in array to match the aggregator's broadcast format
                if isinstance(payload, dict):
                    payload_list = [payload]
                elif isinstance(payload, list):
                    payload_list = payload
                else:
                    continue

                message = json.dumps(payload_list)
                dead = []
                for ws in self._aggregator.ws_clients:
                    try:
                        await ws.send_text(message)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._aggregator.ws_clients.discard(ws)

            logger.info(f"Replay completed: '{session_name}'")

        except asyncio.CancelledError:
            logger.info(f"Replay cancelled: '{session_name}'")
        finally:
            self._replaying = False
            self._replay_session = None

    async def stop_replay(self) -> None:
        """Cancel the active replay task."""
        if not self._replaying:
            raise RuntimeError("No active replay")

        self._replaying = False
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
            try:
                await self._replay_task
            except asyncio.CancelledError:
                pass

        self._replay_task = None
        logger.info("Replay stopped")
