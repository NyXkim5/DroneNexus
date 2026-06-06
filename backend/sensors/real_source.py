"""
RealSensorSource — the deployable real-data SensorSource.

This adapter feeds the same fusion and decision engine the simulator feeds, but
from real-world contact data instead of synthetic ground truth. It proves the
"one engine, two sources" thesis: the fusion engine pulls Detection events
through the exact SensorSource interface SimSensorSource exposes and cannot tell
the two apart.

Two real-data ingestion modes are supported since no physical hardware is wired
in yet:

  1. Recorded-log replay. Read a JSONL capture of timestamped contacts, one dict
     per line, and emit them as Detection objects in timestamp order through
     sample_once() and stream(). This is the realistic "play back a real radar or
     RF capture" path. Geodetic lat/lon/alt contacts are converted to the local
     ENU site frame with the csontology helpers.

  2. Live telemetry adapter. The from_telemetry classmethod maps a feed of
     telemetry.collector.DroneState (the real mavlink, msp, or usb path) into
     Detection objects, so a live sensor or feed drives the same engine. The
     coupling is loose: the source holds a callable that returns the current
     DroneStates, mirroring how SimSensorSource holds a truth callable.

Coordinate frame is local ENU meters about the site origin, the shared world
model frame. See csontology for the definition and the lat/lon converters.

Usage (recorded replay):
  source = RealSensorSource.from_jsonl("capture.jsonl", sensor_id="radar-1")
  await source.start()
  async for detection in source.stream():
      fusion.update([detection], detection.timestamp)
  await source.stop()

Usage (live telemetry):
  source = RealSensorSource.from_telemetry(collector.current_states)
  await source.start()
  dets = source.sample_once()
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Iterable, Optional, Sequence

from csontology import Detection, Vec3, latlon_to_enu, now
from sensors.base import SensorSource

logger = logging.getLogger("overwatch.sensors")

# Default confidence stamped on a real contact that declares none. Real captures
# often omit a confidence field, so we treat a reported contact as a solid look
# rather than clutter while leaving room above for richer sources.
_DEFAULT_CONFIDENCE = 0.8

# A function the live-telemetry mode calls to read the current real feed.
TelemetryFn = Callable[[], Sequence[object]]


@dataclass(frozen=True)
class _Contact:
    """One parsed real contact, already in the ENU site frame.

    Immutable. The replay path parses each JSONL line into one of these in
    timestamp order, then turns it into a Detection on demand. Position and
    velocity are ENU meters and m/s about the site origin.
    """

    detection_id: str
    timestamp: float
    position: Vec3
    velocity: Vec3
    confidence: float
    sensor_id: str
    size_rcs: Optional[float]


class RealSensorSource(SensorSource):
    """Real-data source of Detection events behind the SensorSource interface.

    Holds a pre-parsed, timestamp-ordered list of contacts and replays them
    through the same start, stop, stream, and sample_once surface SimSensorSource
    exposes. Deterministic and side-effect-free given its input. Performs no
    network calls. Construct it with from_jsonl for recorded replay or
    from_telemetry for a live DroneState feed.
    """

    def __init__(
        self,
        contacts: Sequence[_Contact],
        rate_hz: float = 10.0,
        sensor_id: str = "real",
        telemetry_fn: Optional[TelemetryFn] = None,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        super().__init__(sensor_id)
        self._contacts = sorted(contacts, key=lambda c: c.timestamp)
        self._interval = 1.0 / rate_hz
        self._telemetry_fn = telemetry_fn
        self._stop_event = asyncio.Event()
        self._cursor = 0
        self._seq = 0

    @classmethod
    def from_jsonl(
        cls, path: str | Path, rate_hz: float = 10.0, sensor_id: str = "real",
    ) -> "RealSensorSource":
        """Build a replay source from a JSONL capture of real contacts.

        Reads the file once, parses each line, skips malformed lines with a
        logged warning, and orders the survivors by timestamp. sensor_id labels
        the source and stamps any contact line that omits its own sensor_id.
        """
        contacts = list(_parse_jsonl(Path(path), sensor_id))
        return cls(contacts, rate_hz=rate_hz, sensor_id=sensor_id)

    @classmethod
    def from_telemetry(
        cls, telemetry_fn: TelemetryFn, rate_hz: float = 10.0,
        sensor_id: str = "telemetry",
    ) -> "RealSensorSource":
        """Build a live source that maps a DroneState feed into Detections.

        telemetry_fn returns the current telemetry.collector.DroneState objects
        each tick, mirroring how SimSensorSource takes a truth callable. The
        source converts each state's lat/lon/alt into the ENU site frame on every
        sample. Holds no recorded contacts.
        """
        return cls([], rate_hz=rate_hz, sensor_id=sensor_id, telemetry_fn=telemetry_fn)

    async def start(self) -> None:
        """Mark the source running. No external resource to open."""
        self._stop_event.clear()
        self._cursor = 0
        self._running = True
        logger.info(
            "RealSensorSource started with %d recorded contact(s)%s",
            len(self._contacts),
            " and a live telemetry feed" if self._telemetry_fn else "",
        )

    async def stop(self) -> None:
        """Stop the source and cause stream() to finish cleanly."""
        self._running = False
        self._stop_event.set()
        logger.info("RealSensorSource stopped")

    async def stream(self) -> AsyncIterator[Detection]:
        """Yield Detections at the configured rate until stop() or exhaustion.

        In live mode it pumps one telemetry sample per tick until stop(). In
        replay mode it walks the timestamp-ordered contacts one per tick, then
        returns cleanly once the capture is exhausted. Returns cleanly on stop.
        """
        if not self._running:
            raise RuntimeError("stream() called before start()")
        while not self._stop_event.is_set():
            for detection in self.sample_once():
                yield detection
            if self._telemetry_fn is None and self._cursor >= len(self._contacts):
                return
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def sample_once(self) -> list[Detection]:
        """Build one tick of detections now for a caller that owns the clock.

        Mirrors SimSensorSource.sample_once so the WargameRunner drives this with
        no change. In live mode it maps the current DroneState feed. In replay
        mode it returns the next contact in timestamp order, or an empty list once
        the capture is spent. Raises if called before start().
        """
        if not self._running:
            raise RuntimeError("sample_once() called before start()")
        if self._telemetry_fn is not None:
            return self._telemetry_detections()
        return self._next_replay_detections()

    def _next_replay_detections(self) -> list[Detection]:
        """Return the next recorded contact as a Detection, or an empty list."""
        if self._cursor >= len(self._contacts):
            return []
        contact = self._contacts[self._cursor]
        self._cursor += 1
        return [_contact_to_detection(contact)]

    def _telemetry_detections(self) -> list[Detection]:
        """Map the current live DroneState feed into Detection objects."""
        assert self._telemetry_fn is not None
        timestamp = now()
        out: list[Detection] = []
        for state in self._telemetry_fn():
            out.append(self._state_to_detection(state, timestamp))
        return out

    def _state_to_detection(self, state: object, timestamp: float) -> Detection:
        """Convert one DroneState into a Detection in the ENU site frame."""
        self._seq += 1
        lat = float(getattr(state, "lat"))
        lon = float(getattr(state, "lon"))
        alt = float(getattr(state, "alt_agl", 0.0) or getattr(state, "alt_msl", 0.0))
        position = latlon_to_enu(lat, lon, alt)
        velocity = _velocity_from_state(state)
        drone_id = str(getattr(state, "drone_id", self._seq))
        return Detection(
            id=f"{self.sensor_id}-{drone_id}-{self._seq}",
            timestamp=timestamp,
            position=position,
            velocity=velocity,
            confidence=_DEFAULT_CONFIDENCE,
            sensor_id=self.sensor_id,
            size_rcs=None,
        )


def _velocity_from_state(state: object) -> Vec3:
    """Derive an ENU velocity from a DroneState's speed and heading.

    heading is a compass bearing in degrees (0 North, 90 East). ground_speed is
    horizontal m/s and vertical_speed is the up component. This matches the ENU
    axes the world model uses.
    """
    import math

    speed = float(getattr(state, "ground_speed", 0.0))
    heading = float(getattr(state, "heading", 0.0))
    vertical = float(getattr(state, "vertical_speed", 0.0))
    rad = math.radians(heading)
    vx_east = speed * math.sin(rad)
    vy_north = speed * math.cos(rad)
    return (vx_east, vy_north, vertical)


def _contact_to_detection(contact: _Contact) -> Detection:
    """Stamp a parsed contact into an immutable Detection."""
    return Detection(
        id=contact.detection_id,
        timestamp=contact.timestamp,
        position=contact.position,
        velocity=contact.velocity,
        confidence=contact.confidence,
        sensor_id=contact.sensor_id,
        size_rcs=contact.size_rcs,
    )


def _parse_jsonl(path: Path, default_sensor_id: str) -> Iterable[_Contact]:
    """Parse a JSONL capture into contacts, skipping malformed lines.

    Yields one _Contact per valid line. A line that is not valid JSON, is not a
    dict, or lacks the position and timestamp fields is logged at warning and
    skipped, never raised.
    """
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            contact = _parse_line(text, line_no, default_sensor_id)
            if contact is not None:
                yield contact


def _parse_line(
    text: str, line_no: int, default_sensor_id: str,
) -> Optional[_Contact]:
    """Parse one JSONL line into a _Contact, or None if it is malformed."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("skipping malformed JSON on line %d: %s", line_no, exc)
        return None
    if not isinstance(record, dict):
        logger.warning("skipping non-object record on line %d", line_no)
        return None
    try:
        return _contact_from_record(record, line_no, default_sensor_id)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("skipping invalid contact on line %d: %s", line_no, exc)
        return None


def _contact_from_record(
    record: dict, line_no: int, default_sensor_id: str,
) -> _Contact:
    """Build a _Contact from a parsed record, raising on missing fields.

    Accepts either geodetic lat/lon/alt or direct ENU x/y/z position. Velocity,
    confidence, and rcs are optional. Raises KeyError, TypeError, or ValueError
    on a malformed record so the caller can skip the line.
    """
    timestamp = float(record["timestamp"])
    position = _position_from_record(record)
    velocity = _velocity_from_record(record)
    confidence = float(record.get("confidence", _DEFAULT_CONFIDENCE))
    rcs = record.get("rcs", record.get("size_rcs"))
    size_rcs = float(rcs) if rcs is not None else None
    sensor_id = str(record.get("sensor_id", default_sensor_id))
    detection_id = str(record.get("id", f"{sensor_id}-{line_no}"))
    return _Contact(
        detection_id=detection_id,
        timestamp=timestamp,
        position=position,
        velocity=velocity,
        confidence=confidence,
        sensor_id=sensor_id,
        size_rcs=size_rcs,
    )


def _position_from_record(record: dict) -> Vec3:
    """Resolve an ENU position from either lat/lon/alt or x/y/z fields.

    Prefers geodetic lat/lon when present and converts to the ENU site frame.
    Falls back to direct ENU x/y/z. Raises KeyError if neither is present.
    """
    if "lat" in record and "lon" in record:
        alt = float(record.get("alt", 0.0))
        return latlon_to_enu(float(record["lat"]), float(record["lon"]), alt)
    if "x" in record and "y" in record:
        return (float(record["x"]), float(record["y"]), float(record.get("z", 0.0)))
    raise KeyError("record needs lat/lon or x/y position fields")


def _velocity_from_record(record: dict) -> Vec3:
    """Resolve an ENU velocity from optional vx/vy/vz fields, defaulting to zero."""
    return (
        float(record.get("vx", 0.0)),
        float(record.get("vy", 0.0)),
        float(record.get("vz", 0.0)),
    )
