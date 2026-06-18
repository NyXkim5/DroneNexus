"""
Unit tests for the CoT/TAK bridge module.

Tests cover:
  - format_track_cot: XML structure, lat/lon conversion, type codes per TrackClass
  - format_threat_cot: threat score and intent in remarks
  - format_heartbeat_cot: sensor heartbeat format
  - CoTBridge.format_batch: bridge processes a list of tracks into CoT XML strings

All tests are pure — no network, no asyncio required for the formatter tests.
The bridge batch test also avoids I/O by calling format_batch() directly.
"""
from __future__ import annotations

import math
import time
import xml.etree.ElementTree as ET

import pytest

from csontology import (
    ORIGIN_LAT,
    ORIGIN_LON,
    SwarmIntent,
    Threat,
    Track,
    TrackClass,
    enu_to_latlon,
)
from cot.formatter import format_heartbeat_cot, format_threat_cot, format_track_cot
from cot.bridge import CoTBridge


# ---- Helpers ----

def _make_track(
    track_id: str = "T001",
    pos: tuple[float, float, float] = (100.0, 200.0, 50.0),
    vel: tuple[float, float, float] = (10.0, 0.0, 0.0),
    classification: TrackClass = TrackClass.HOSTILE,
    confidence: float = 0.85,
) -> Track:
    return Track(
        id=track_id,
        position=pos,
        velocity=vel,
        covariance=(2.0, 2.0, 1.0),
        last_update=time.time(),
        age=5.0,
        classification=classification,
        confidence=confidence,
    )


def _make_threat(
    threat_id: str = "TH001",
    track_id: str = "T001",
    score: float = 0.92,
    intent: SwarmIntent = SwarmIntent.SATURATION,
    tti: float | None = 45.0,
) -> Threat:
    return Threat(
        id=threat_id,
        score=score,
        time_to_impact_s=tti,
        value_at_risk=500_000.0,
        priority_rank=1,
        track_id=track_id,
        intent=intent,
    )


def _parse(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str)


# ---- format_track_cot ----

class TestFormatTrackCot:
    def test_root_element_is_event(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        assert root.tag == "event"

    def test_uid_contains_track_id(self) -> None:
        xml = format_track_cot(_make_track(track_id="ABC123"))
        root = _parse(xml)
        assert root.attrib["uid"] == "OVERWATCH.ABC123"

    def test_version_is_2_0(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        assert root.attrib["version"] == "2.0"

    def test_how_is_machine_generated(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        assert root.attrib["how"] == "m-g"

    def test_hostile_type_code(self) -> None:
        xml = format_track_cot(_make_track(classification=TrackClass.HOSTILE))
        root = _parse(xml)
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_unknown_type_code(self) -> None:
        xml = format_track_cot(_make_track(classification=TrackClass.UNKNOWN))
        root = _parse(xml)
        assert root.attrib["type"] == "a-u-A-M-H-Q"

    def test_friendly_type_code(self) -> None:
        xml = format_track_cot(_make_track(classification=TrackClass.FRIENDLY))
        root = _parse(xml)
        assert root.attrib["type"] == "a-f-A-M-H-Q"

    def test_point_element_present(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        point = root.find("point")
        assert point is not None

    def test_lat_lon_conversion_accuracy(self) -> None:
        pos = (500.0, 300.0, 80.0)
        expected_lat, expected_lon, expected_alt = enu_to_latlon(*pos)
        xml = format_track_cot(_make_track(pos=pos))
        root = _parse(xml)
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - expected_lat) < 1e-5
        assert abs(float(point.attrib["lon"]) - expected_lon) < 1e-5
        assert abs(float(point.attrib["hae"]) - expected_alt) < 0.01

    def test_point_at_origin_maps_to_site_origin(self) -> None:
        xml = format_track_cot(_make_track(pos=(0.0, 0.0, 0.0)))
        root = _parse(xml)
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - ORIGIN_LAT) < 1e-5
        assert abs(float(point.attrib["lon"]) - ORIGIN_LON) < 1e-5

    def test_track_speed_and_course_present(self) -> None:
        vel = (10.0, 0.0, 0.0)  # pure east = course 90 deg
        xml = format_track_cot(_make_track(vel=vel))
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        track_el = detail.find("track")
        assert track_el is not None
        speed = float(track_el.attrib["speed"])
        course = float(track_el.attrib["course"])
        assert abs(speed - 10.0) < 0.01
        assert abs(course - 90.0) < 0.1

    def test_north_velocity_gives_zero_course(self) -> None:
        vel = (0.0, 10.0, 0.0)  # pure north
        xml = format_track_cot(_make_track(vel=vel))
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        track_el = detail.find("track")
        assert track_el is not None
        assert abs(float(track_el.attrib["course"]) - 0.0) < 0.1

    def test_contact_callsign_present(self) -> None:
        xml = format_track_cot(_make_track(track_id="X99", classification=TrackClass.HOSTILE))
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        contact = detail.find("contact")
        assert contact is not None
        assert "X99" in contact.attrib["callsign"]

    def test_remarks_contains_track_id(self) -> None:
        xml = format_track_cot(_make_track(track_id="T007"))
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "T007" in (remarks.text or "")

    def test_stale_is_after_time(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        # Both are ISO strings — lexicographic comparison works for UTC ISO-8601.
        assert root.attrib["stale"] > root.attrib["time"]

    def test_ce_and_le_on_point(self) -> None:
        xml = format_track_cot(_make_track())
        root = _parse(xml)
        point = root.find("point")
        assert point is not None
        assert point.attrib["ce"] == "10"
        assert point.attrib["le"] == "10"


# ---- format_threat_cot ----

class TestFormatThreatCot:
    def test_root_is_event(self) -> None:
        track = _make_track()
        threat = _make_threat()
        xml = format_threat_cot(threat, track)
        assert _parse(xml).tag == "event"

    def test_always_hostile_type(self) -> None:
        track = _make_track(classification=TrackClass.UNKNOWN)
        threat = _make_threat()
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_callsign_prefixed_hostile(self) -> None:
        track = _make_track(track_id="T002")
        threat = _make_threat(track_id="T002")
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        contact = detail.find("contact")
        assert contact is not None
        assert contact.attrib["callsign"] == "HOSTILE-T002"

    def test_remarks_contains_threat_score(self) -> None:
        track = _make_track()
        threat = _make_threat(score=0.77)
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "0.77" in (remarks.text or "")

    def test_remarks_contains_intent(self) -> None:
        track = _make_track()
        threat = _make_threat(intent=SwarmIntent.SATURATION)
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "SATURATION" in (remarks.text or "")

    def test_remarks_contains_tti(self) -> None:
        track = _make_track()
        threat = _make_threat(tti=30.0)
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "30.0" in (remarks.text or "")

    def test_remarks_tti_none_shows_na(self) -> None:
        track = _make_track()
        threat = _make_threat(tti=None)
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "N/A" in (remarks.text or "")

    def test_uid_matches_track_id(self) -> None:
        track = _make_track(track_id="T999")
        threat = _make_threat(track_id="T999")
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        assert root.attrib["uid"] == "OVERWATCH.T999"

    def test_lat_lon_accuracy(self) -> None:
        pos = (250.0, -100.0, 30.0)
        expected_lat, expected_lon, _ = enu_to_latlon(*pos)
        track = _make_track(pos=pos)
        threat = _make_threat()
        xml = format_threat_cot(threat, track)
        root = _parse(xml)
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - expected_lat) < 1e-5
        assert abs(float(point.attrib["lon"]) - expected_lon) < 1e-5


# ---- format_heartbeat_cot ----

class TestHeartbeatXml:
    def _heartbeat(
        self,
        n_tracks: int = 5,
        n_threats: int = 2,
        lat: float = ORIGIN_LAT,
        lon: float = ORIGIN_LON,
    ) -> ET.Element:
        xml = format_heartbeat_cot(
            site_lat=lat,
            site_lon=lon,
            n_tracks=n_tracks,
            n_threats=n_threats,
            now_ts=time.time(),
        )
        return _parse(xml)

    def test_root_is_event(self) -> None:
        assert self._heartbeat().tag == "event"

    def test_uid_is_overwatch_sensor(self) -> None:
        assert self._heartbeat().attrib["uid"] == "OVERWATCH.SENSOR"

    def test_type_is_friendly_ground_sensor(self) -> None:
        assert self._heartbeat().attrib["type"] == "a-f-G-E-S"

    def test_callsign_is_overwatch_gcs(self) -> None:
        root = self._heartbeat()
        detail = root.find("detail")
        assert detail is not None
        contact = detail.find("contact")
        assert contact is not None
        assert contact.attrib["callsign"] == "OVERWATCH-GCS"

    def test_remarks_contains_track_and_threat_counts(self) -> None:
        root = self._heartbeat(n_tracks=7, n_threats=3)
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        text = remarks.text or ""
        assert "7" in text
        assert "3" in text

    def test_point_at_site_coords(self) -> None:
        root = self._heartbeat(lat=34.0, lon=-118.0)
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - 34.0) < 1e-5
        assert abs(float(point.attrib["lon"]) - (-118.0)) < 1e-5

    def test_point_hae_is_zero(self) -> None:
        root = self._heartbeat()
        point = root.find("point")
        assert point is not None
        assert point.attrib["hae"] == "0"

    def test_stale_is_after_time(self) -> None:
        root = self._heartbeat()
        assert root.attrib["stale"] > root.attrib["time"]

    def test_how_is_machine_generated(self) -> None:
        assert self._heartbeat().attrib["how"] == "m-g"

    def test_ce_and_le_are_one(self) -> None:
        root = self._heartbeat()
        point = root.find("point")
        assert point is not None
        assert point.attrib["ce"] == "1"
        assert point.attrib["le"] == "1"


# ---- CoTBridge.format_batch ----

class TestCoTBridgeBatch:
    def test_batch_returns_one_xml_per_track(self) -> None:
        tracks = [_make_track(track_id=f"T{i:03d}") for i in range(5)]
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=tracks, threats=[])
        assert len(results) == 5

    def test_batch_all_parseable_xml(self) -> None:
        tracks = [_make_track(track_id=f"T{i:03d}") for i in range(3)]
        bridge = CoTBridge()
        for xml in bridge.format_batch(tracks=tracks, threats=[]):
            root = _parse(xml)
            assert root.tag == "event"

    def test_batch_threat_track_uses_threat_formatter(self) -> None:
        track = _make_track(track_id="T001", classification=TrackClass.UNKNOWN)
        threat = _make_threat(track_id="T001", score=0.88)
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=[track], threats=[threat])
        assert len(results) == 1
        root = _parse(results[0])
        # Threat formatter always emits hostile type regardless of track class.
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_batch_non_threat_track_uses_track_formatter(self) -> None:
        track = _make_track(track_id="T002", classification=TrackClass.FRIENDLY)
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=[track], threats=[])
        assert len(results) == 1
        root = _parse(results[0])
        assert root.attrib["type"] == "a-f-A-M-H-Q"

    def test_batch_threat_score_in_remarks(self) -> None:
        track = _make_track(track_id="T003")
        threat = _make_threat(track_id="T003", score=0.55)
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=[track], threats=[threat])
        root = _parse(results[0])
        detail = root.find("detail")
        assert detail is not None
        remarks = detail.find("remarks")
        assert remarks is not None
        assert "0.55" in (remarks.text or "")

    def test_batch_empty_tracks_returns_empty(self) -> None:
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=[], threats=[])
        assert results == []

    def test_batch_mixed_track_classes(self) -> None:
        tracks = [
            _make_track(track_id="H1", classification=TrackClass.HOSTILE),
            _make_track(track_id="F1", classification=TrackClass.FRIENDLY),
            _make_track(track_id="U1", classification=TrackClass.UNKNOWN),
        ]
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=tracks, threats=[])
        types = [_parse(xml).attrib["type"] for xml in results]
        assert "a-h-A-M-H-Q" in types
        assert "a-f-A-M-H-Q" in types
        assert "a-u-A-M-H-Q" in types

    def test_batch_uid_prefix_overwatch(self) -> None:
        tracks = [_make_track(track_id="T010")]
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=tracks, threats=[])
        root = _parse(results[0])
        assert root.attrib["uid"].startswith("OVERWATCH.")

    def test_batch_threat_without_matching_track_ignored(self) -> None:
        track = _make_track(track_id="T001")
        # Threat references a different track_id not in the batch.
        orphan_threat = _make_threat(track_id="T999", score=0.99)
        bridge = CoTBridge()
        results = bridge.format_batch(tracks=[track], threats=[orphan_threat])
        # The track should still be emitted, just without threat formatting.
        assert len(results) == 1
        root = _parse(results[0])
        # T001 is HOSTILE, so type stays hostile regardless.
        assert root.attrib["uid"] == "OVERWATCH.T001"
