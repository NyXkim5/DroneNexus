"""
Unit tests for CoTReceiver, BiDirectionalCoTBridge, and IncomingCoTEvent parsing.

Tests are pure (no network, no asyncio required) except where explicitly marked.
All XML is constructed in-process so tests run offline with zero external deps.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import List
from unittest.mock import MagicMock

import pytest

from cot.receiver import CoTReceiver, IncomingCoTEvent
from cot.bidirectional import BiDirectionalCoTBridge
from csontology import TrackClass, latlon_to_enu, ORIGIN_LAT, ORIGIN_LON


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cot_xml(
    uid: str = "ATAK-001",
    cot_type: str = "a-h-A-M-H-Q",
    lat: float = 33.6410,
    lon: float = -117.8440,
    hae: float = 100.0,
    callsign: str = "GHOST1",
    remarks: str = "Hostile drone spotted",
    time_str: str = "2024-01-15T12:00:00.000Z",
    stale_str: str = "2024-01-15T12:01:00.000Z",
) -> bytes:
    event = ET.Element("event", {
        "version": "2.0",
        "uid": uid,
        "type": cot_type,
        "time": time_str,
        "start": time_str,
        "stale": stale_str,
        "how": "h-e",
    })
    ET.SubElement(event, "point", {
        "lat": str(lat),
        "lon": str(lon),
        "hae": str(hae),
        "ce": "5",
        "le": "5",
    })
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign})
    ET.SubElement(detail, "remarks").text = remarks
    return ET.tostring(event, encoding="unicode").encode("utf-8")


def _receiver() -> CoTReceiver:
    return CoTReceiver()


def _bridge() -> BiDirectionalCoTBridge:
    sender = MagicMock()
    receiver = CoTReceiver()
    return BiDirectionalCoTBridge(sender=sender, receiver=receiver)


# ---------------------------------------------------------------------------
# test_parse_hostile_report
# ---------------------------------------------------------------------------

class TestParseHostileReport:
    def test_uid_extracted(self) -> None:
        xml = _make_cot_xml(uid="HOSTILE-99", cot_type="a-h-A-M-H-Q")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.uid == "HOSTILE-99"

    def test_event_type_extracted(self) -> None:
        xml = _make_cot_xml(cot_type="a-h-A-M-H-Q")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.event_type == "a-h-A-M-H-Q"

    def test_lat_lon_extracted(self) -> None:
        xml = _make_cot_xml(lat=33.6410, lon=-117.8440)
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert abs(event.lat - 33.6410) < 1e-6
        assert abs(event.lon - (-117.8440)) < 1e-6

    def test_altitude_extracted(self) -> None:
        xml = _make_cot_xml(hae=250.0)
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert abs(event.altitude - 250.0) < 0.01

    def test_callsign_extracted(self) -> None:
        xml = _make_cot_xml(callsign="ALPHA-1")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.callsign == "ALPHA-1"

    def test_remarks_extracted(self) -> None:
        xml = _make_cot_xml(remarks="Multiple drones inbound")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.remarks == "Multiple drones inbound"

    def test_raw_xml_stored(self) -> None:
        xml = _make_cot_xml(uid="RAW-001")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert "RAW-001" in event.raw_xml

    def test_timestamp_is_float(self) -> None:
        xml = _make_cot_xml()
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert isinstance(event.timestamp, float)
        assert event.timestamp > 0

    def test_stale_time_after_timestamp(self) -> None:
        xml = _make_cot_xml(
            time_str="2024-01-15T12:00:00.000Z",
            stale_str="2024-01-15T12:01:00.000Z",
        )
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.stale_time > event.timestamp


# ---------------------------------------------------------------------------
# test_parse_friendly_position
# ---------------------------------------------------------------------------

class TestParseFriendlyPosition:
    def test_friendly_type_extracted(self) -> None:
        xml = _make_cot_xml(cot_type="a-f-G-U-C", callsign="BLUE-2")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.event_type == "a-f-G-U-C"

    def test_friendly_callsign(self) -> None:
        xml = _make_cot_xml(cot_type="a-f-G", callsign="BRAVO-3")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.callsign == "BRAVO-3"

    def test_friendly_position_values(self) -> None:
        xml = _make_cot_xml(cot_type="a-f-G", lat=33.6500, lon=-117.8500, hae=50.0)
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert abs(event.lat - 33.6500) < 1e-6
        assert abs(event.lon - (-117.8500)) < 1e-6
        assert abs(event.altitude - 50.0) < 0.01


# ---------------------------------------------------------------------------
# test_parse_marker
# ---------------------------------------------------------------------------

class TestParseMarker:
    def test_marker_type_extracted(self) -> None:
        xml = _make_cot_xml(cot_type="b-m-p-s-m", callsign="WP-ALPHA")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.event_type == "b-m-p-s-m"

    def test_marker_callsign(self) -> None:
        xml = _make_cot_xml(cot_type="b-m-p-s-m", callsign="OBJ-HOTEL")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.callsign == "OBJ-HOTEL"

    def test_marker_remarks(self) -> None:
        xml = _make_cot_xml(cot_type="b-m-p-s-m", remarks="Attack by fire position")
        event = _receiver().parse_cot_xml(xml)
        assert event is not None
        assert event.remarks == "Attack by fire position"


# ---------------------------------------------------------------------------
# test_malformed_xml_returns_none
# ---------------------------------------------------------------------------

class TestMalformedXml:
    def test_garbage_bytes(self) -> None:
        result = _receiver().parse_cot_xml(b"\x00\xff\xfe garbage not xml")
        assert result is None

    def test_empty_bytes(self) -> None:
        result = _receiver().parse_cot_xml(b"")
        assert result is None

    def test_valid_xml_wrong_root_tag(self) -> None:
        result = _receiver().parse_cot_xml(b"<notanevent><point/></notanevent>")
        assert result is None

    def test_missing_uid(self) -> None:
        xml = b'<event type="a-h-A" time="" stale="" how="h-e"><point lat="33" lon="-117" hae="0" ce="1" le="1"/></event>'
        result = _receiver().parse_cot_xml(xml)
        assert result is None

    def test_missing_type(self) -> None:
        xml = b'<event uid="X" time="" stale="" how="h-e"><point lat="33" lon="-117" hae="0" ce="1" le="1"/></event>'
        result = _receiver().parse_cot_xml(xml)
        assert result is None

    def test_missing_point_element(self) -> None:
        xml = b'<event uid="X" type="a-h-A" time="" stale="" how="h-e"><detail/></event>'
        result = _receiver().parse_cot_xml(xml)
        assert result is None

    def test_unclosed_tag(self) -> None:
        result = _receiver().parse_cot_xml(b"<event uid='x' type='a-h-A'><point")
        assert result is None

    def test_random_string(self) -> None:
        result = _receiver().parse_cot_xml(b"hello world this is not xml at all")
        assert result is None


# ---------------------------------------------------------------------------
# classify_event tests
# ---------------------------------------------------------------------------

class TestClassifyEvent:
    def _event_with_type(self, cot_type: str) -> IncomingCoTEvent:
        return IncomingCoTEvent(
            uid="X",
            event_type=cot_type,
            lat=33.0,
            lon=-117.0,
            altitude=0.0,
            callsign="TEST",
            remarks="",
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )

    def test_classify_hostile(self) -> None:
        r = _receiver()
        event = self._event_with_type("a-h-A-M-H-Q")
        assert r.classify_event(event) == "hostile_report"

    def test_classify_hostile_variant(self) -> None:
        r = _receiver()
        event = self._event_with_type("a-h-G-U-C-I")
        assert r.classify_event(event) == "hostile_report"

    def test_classify_friendly(self) -> None:
        r = _receiver()
        event = self._event_with_type("a-f-G")
        assert r.classify_event(event) == "friendly_position"

    def test_classify_friendly_air(self) -> None:
        r = _receiver()
        event = self._event_with_type("a-f-A-M-H-Q")
        assert r.classify_event(event) == "friendly_position"

    def test_classify_marker(self) -> None:
        r = _receiver()
        event = self._event_with_type("b-m-p-s-m")
        assert r.classify_event(event) == "marker"

    def test_classify_marker_waypoint(self) -> None:
        r = _receiver()
        event = self._event_with_type("b-m-p-w-GOTO")
        assert r.classify_event(event) == "marker"

    def test_classify_medevac(self) -> None:
        r = _receiver()
        event = self._event_with_type("b-r-f-h-c")
        assert r.classify_event(event) == "medevac_request"

    def test_classify_unknown_contact(self) -> None:
        r = _receiver()
        event = self._event_with_type("a-u-A-M-H-Q")
        assert r.classify_event(event) == "unknown_contact"

    def test_classify_other(self) -> None:
        r = _receiver()
        event = self._event_with_type("t-x-takp-v")
        assert r.classify_event(event) == "other"


# ---------------------------------------------------------------------------
# test_inject_hostile_creates_track
# ---------------------------------------------------------------------------

class TestInjectHostileCreatesTrack:
    def _hostile_event(
        self,
        lat: float = 33.6420,
        lon: float = -117.8430,
        hae: float = 150.0,
        uid: str = "ATAK-HOSTILE-01",
        callsign: str = "TANGO-1",
    ) -> IncomingCoTEvent:
        return IncomingCoTEvent(
            uid=uid,
            event_type="a-h-A-M-H-Q",
            lat=lat,
            lon=lon,
            altitude=hae,
            callsign=callsign,
            remarks="Hostile UAV",
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )

    def test_returns_track(self) -> None:
        bridge = _bridge()
        event = self._hostile_event()
        track = bridge.inject_hostile_report(event)
        assert track is not None

    def test_track_classification_hostile(self) -> None:
        bridge = _bridge()
        track = bridge.inject_hostile_report(self._hostile_event())
        assert track is not None
        assert track.classification == TrackClass.HOSTILE

    def test_track_id_contains_uid(self) -> None:
        bridge = _bridge()
        event = self._hostile_event(uid="ATAK-XYZ-99")
        track = bridge.inject_hostile_report(event)
        assert track is not None
        assert "ATAK-XYZ-99" in track.id

    def test_track_position_is_enu(self) -> None:
        lat, lon, hae = 33.6420, -117.8430, 150.0
        expected = latlon_to_enu(lat, lon, hae)
        bridge = _bridge()
        event = self._hostile_event(lat=lat, lon=lon, hae=hae)
        track = bridge.inject_hostile_report(event)
        assert track is not None
        assert abs(track.position[0] - expected[0]) < 0.01
        assert abs(track.position[1] - expected[1]) < 0.01
        assert abs(track.position[2] - expected[2]) < 0.01

    def test_track_at_origin_has_zero_enu(self) -> None:
        bridge = _bridge()
        event = self._hostile_event(lat=ORIGIN_LAT, lon=ORIGIN_LON, hae=0.0)
        track = bridge.inject_hostile_report(event)
        assert track is not None
        assert abs(track.position[0]) < 0.1
        assert abs(track.position[1]) < 0.1
        assert abs(track.position[2]) < 0.1

    def test_non_hostile_event_returns_none(self) -> None:
        bridge = _bridge()
        event = IncomingCoTEvent(
            uid="F1",
            event_type="a-f-G",
            lat=33.0,
            lon=-117.0,
            altitude=0.0,
            callsign="BLUE",
            remarks="",
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )
        result = bridge.inject_hostile_report(event)
        assert result is None

    def test_track_confidence_set(self) -> None:
        bridge = _bridge()
        track = bridge.inject_hostile_report(self._hostile_event())
        assert track is not None
        assert 0.0 < track.confidence <= 1.0

    def test_track_last_update_matches_event_timestamp(self) -> None:
        bridge = _bridge()
        ts = time.time() - 5.0
        event = IncomingCoTEvent(
            uid="H2",
            event_type="a-h-A",
            lat=33.64,
            lon=-117.84,
            altitude=50.0,
            callsign="TANGO",
            remarks="",
            timestamp=ts,
            stale_time=ts + 60,
            raw_xml="",
        )
        track = bridge.inject_hostile_report(event)
        assert track is not None
        assert abs(track.last_update - ts) < 0.001


# ---------------------------------------------------------------------------
# test_inject_friendly_updates_registry
# ---------------------------------------------------------------------------

class TestInjectFriendlyUpdatesRegistry:
    def _friendly_event(
        self,
        uid: str = "FRIENDLY-01",
        callsign: str = "ALPHA-1",
        lat: float = 33.6400,
        lon: float = -117.8443,
        hae: float = 10.0,
    ) -> IncomingCoTEvent:
        return IncomingCoTEvent(
            uid=uid,
            event_type="a-f-G-U-C",
            lat=lat,
            lon=lon,
            altitude=hae,
            callsign=callsign,
            remarks="",
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )

    def test_registry_entry_created(self) -> None:
        bridge = _bridge()
        event = self._friendly_event(uid="F-100")
        bridge.inject_friendly_position(event)
        assert "F-100" in bridge.friendly_registry

    def test_registry_contains_callsign(self) -> None:
        bridge = _bridge()
        event = self._friendly_event(uid="F-200", callsign="CHARLIE-4")
        bridge.inject_friendly_position(event)
        assert bridge.friendly_registry["F-200"]["callsign"] == "CHARLIE-4"

    def test_registry_contains_enu_position(self) -> None:
        bridge = _bridge()
        lat, lon, hae = 33.6400, -117.8443, 10.0
        event = self._friendly_event(lat=lat, lon=lon, hae=hae)
        bridge.inject_friendly_position(event)
        entry = bridge.friendly_registry[event.uid]
        expected = latlon_to_enu(lat, lon, hae)
        pos = entry["position"]
        assert abs(pos[0] - expected[0]) < 0.01
        assert abs(pos[1] - expected[1]) < 0.01

    def test_registry_updates_on_second_call(self) -> None:
        bridge = _bridge()
        uid = "F-UPDATE"
        bridge.inject_friendly_position(self._friendly_event(uid=uid, lat=33.640))
        bridge.inject_friendly_position(self._friendly_event(uid=uid, lat=33.650))
        entry = bridge.friendly_registry[uid]
        assert abs(entry["lat"] - 33.650) < 1e-6

    def test_non_friendly_event_does_not_pollute_registry(self) -> None:
        bridge = _bridge()
        event = IncomingCoTEvent(
            uid="H-99",
            event_type="a-h-A-M-H-Q",
            lat=33.0,
            lon=-117.0,
            altitude=0.0,
            callsign="TANGO",
            remarks="",
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )
        bridge.inject_friendly_position(event)
        assert "H-99" not in bridge.friendly_registry

    def test_multiple_friendlies_stored(self) -> None:
        bridge = _bridge()
        for i in range(5):
            bridge.inject_friendly_position(self._friendly_event(uid=f"F-{i}"))
        assert len(bridge.friendly_registry) == 5


# ---------------------------------------------------------------------------
# test_recent_events_filters_old
# ---------------------------------------------------------------------------

class TestRecentEventsFiltersOld:
    def _event_at_time(self, ts: float, uid: str = "X") -> IncomingCoTEvent:
        return IncomingCoTEvent(
            uid=uid,
            event_type="a-h-A",
            lat=33.0,
            lon=-117.0,
            altitude=0.0,
            callsign="TEST",
            remarks="",
            timestamp=ts,
            stale_time=ts + 60,
            raw_xml="",
        )

    def test_recent_event_included(self) -> None:
        r = CoTReceiver()
        event = self._event_at_time(time.time() - 10.0)
        r._events.append(event)
        assert event in r.recent_events

    def test_old_event_excluded(self) -> None:
        r = CoTReceiver()
        old_event = self._event_at_time(time.time() - 120.0, uid="OLD")
        r._events.append(old_event)
        assert old_event not in r.recent_events

    def test_exactly_at_boundary_excluded(self) -> None:
        r = CoTReceiver()
        boundary_event = self._event_at_time(time.time() - 60.1, uid="BOUNDARY")
        r._events.append(boundary_event)
        assert boundary_event not in r.recent_events

    def test_mixed_ages_only_recent_returned(self) -> None:
        r = CoTReceiver()
        now = time.time()
        old = self._event_at_time(now - 90.0, uid="OLD")
        recent = self._event_at_time(now - 5.0, uid="RECENT")
        r._events.extend([old, recent])
        result = r.recent_events
        assert recent in result
        assert old not in result

    def test_empty_store_returns_empty(self) -> None:
        r = CoTReceiver()
        assert r.recent_events == []

    def test_all_recent_all_returned(self) -> None:
        r = CoTReceiver()
        now = time.time()
        events = [self._event_at_time(now - i, uid=f"E{i}") for i in range(5)]
        r._events.extend(events)
        assert len(r.recent_events) == 5


# ---------------------------------------------------------------------------
# test_on_event_callback
# ---------------------------------------------------------------------------

class TestOnEventCallback:
    def test_handler_called_on_parse(self) -> None:
        r = CoTReceiver()
        received: List[IncomingCoTEvent] = []
        r.on_event(received.append)

        xml = _make_cot_xml(uid="CB-001", cot_type="a-h-A-M-H-Q", callsign="CB-GHOST")
        event = r.parse_cot_xml(xml)
        assert event is not None

        # Simulate the protocol's datagram_received dispatch path.
        for handler in r._handlers:
            handler(event)

        assert len(received) == 1
        assert received[0].uid == "CB-001"

    def test_multiple_handlers_all_called(self) -> None:
        r = CoTReceiver()
        results_a: List[IncomingCoTEvent] = []
        results_b: List[IncomingCoTEvent] = []
        r.on_event(results_a.append)
        r.on_event(results_b.append)

        xml = _make_cot_xml(uid="MULTI-01")
        event = r.parse_cot_xml(xml)
        assert event is not None

        for handler in r._handlers:
            handler(event)

        assert len(results_a) == 1
        assert len(results_b) == 1

    def test_handler_receives_correct_event_type(self) -> None:
        r = CoTReceiver()
        received: List[IncomingCoTEvent] = []
        r.on_event(received.append)

        xml = _make_cot_xml(cot_type="b-m-p-s-m")
        event = r.parse_cot_xml(xml)
        assert event is not None

        for handler in r._handlers:
            handler(event)

        assert received[0].event_type == "b-m-p-s-m"

    def test_no_handler_registered_does_not_raise(self) -> None:
        r = CoTReceiver()
        xml = _make_cot_xml()
        event = r.parse_cot_xml(xml)
        assert event is not None
        # No handlers registered — iterating _handlers should be a no-op.
        for handler in r._handlers:
            handler(event)

    def test_malformed_xml_handler_not_called(self) -> None:
        r = CoTReceiver()
        called = [False]
        r.on_event(lambda _: called.__setitem__(0, True))

        result = r.parse_cot_xml(b"not xml at all")
        assert result is None
        assert not called[0]


# ---------------------------------------------------------------------------
# inject_marker tests
# ---------------------------------------------------------------------------

class TestInjectMarker:
    def _marker_event(
        self,
        uid: str = "MARKER-01",
        callsign: str = "WP-ALPHA",
        lat: float = 33.6415,
        lon: float = -117.8435,
        hae: float = 20.0,
        remarks: str = "Objective hotel",
    ) -> IncomingCoTEvent:
        return IncomingCoTEvent(
            uid=uid,
            event_type="b-m-p-s-m",
            lat=lat,
            lon=lon,
            altitude=hae,
            callsign=callsign,
            remarks=remarks,
            timestamp=time.time(),
            stale_time=time.time() + 60,
            raw_xml="",
        )

    def test_returns_dict(self) -> None:
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event())
        assert isinstance(result, dict)

    def test_dict_contains_uid(self) -> None:
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event(uid="MRK-99"))
        assert result["uid"] == "MRK-99"

    def test_dict_contains_enu_position(self) -> None:
        lat, lon, hae = 33.6415, -117.8435, 20.0
        expected = latlon_to_enu(lat, lon, hae)
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event(lat=lat, lon=lon, hae=hae))
        pos = result["enu_position"]
        assert abs(pos[0] - expected[0]) < 0.01
        assert abs(pos[1] - expected[1]) < 0.01

    def test_dict_contains_callsign(self) -> None:
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event(callsign="OBJ-BRAVO"))
        assert result["callsign"] == "OBJ-BRAVO"

    def test_dict_contains_remarks(self) -> None:
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event(remarks="Breach point"))
        assert result["remarks"] == "Breach point"

    def test_dict_contains_cot_type(self) -> None:
        bridge = _bridge()
        result = bridge.inject_marker(self._marker_event())
        assert result["cot_type"] == "b-m-p-s-m"

    def test_dict_has_unique_id(self) -> None:
        bridge = _bridge()
        r1 = bridge.inject_marker(self._marker_event(uid="M1"))
        r2 = bridge.inject_marker(self._marker_event(uid="M2"))
        assert r1["id"] != r2["id"]
