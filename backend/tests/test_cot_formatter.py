"""Unit tests for CoT XML formatters.

Covers the existing format_track_cot and format_threat_cot for regression
safety, plus the new format_defender_cot, format_engagement_cot, and
format_swarm_cluster_cot formatters.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Engagement,
    EngagementStatus,
    SwarmIntent,
    Threat,
    Track,
    TrackClass,
    Vec3,
    enu_to_latlon,
)
from cot.formatter import (
    format_defender_cot,
    format_engagement_cot,
    format_heartbeat_cot,
    format_swarm_cluster_cot,
    format_threat_cot,
    format_track_cot,
)


# -- Fixtures --


def _make_track(
    track_id: str = "T001",
    position: Vec3 = (100.0, 200.0, 50.0),
    velocity: Vec3 = (5.0, 10.0, 0.0),
    classification: TrackClass = TrackClass.HOSTILE,
    confidence: float = 0.92,
    last_update: float = 1700000000.0,
) -> Track:
    return Track(
        id=track_id,
        position=position,
        velocity=velocity,
        covariance=(1.0, 1.0, 1.0),
        last_update=last_update,
        classification=classification,
        confidence=confidence,
    )


def _make_threat(
    threat_id: str = "TH001",
    track_id: str = "T001",
    score: float = 0.85,
    tti: float = 12.5,
) -> Threat:
    return Threat(
        id=threat_id,
        score=score,
        time_to_impact_s=tti,
        value_at_risk=50000.0,
        priority_rank=1,
        track_id=track_id,
        intent=SwarmIntent.SATURATION,
    )


def _make_defender(
    defender_id: str = "D001",
    position: Vec3 = (50.0, 50.0, 0.0),
    kind: DefenderKind = DefenderKind.JAMMER,
    capacity: int = 10,
    range_m: float = 500.0,
    kill_prob: float = 0.75,
    status: DefenderStatus = DefenderStatus.READY,
) -> Defender:
    return Defender(
        id=defender_id,
        position=position,
        kind=kind,
        capacity=capacity,
        range_m=range_m,
        reload_s=5.0,
        kill_prob=kill_prob,
        unit_cost=100.0,
        status=status,
    )


def _make_engagement(
    eng_id: str = "E001",
    defender_id: str = "D001",
    threat_id: str = "TH001",
    status: EngagementStatus = EngagementStatus.HIT,
    cost: float = 100.0,
    neutralized: list[str] | None = None,
) -> Engagement:
    return Engagement(
        id=eng_id,
        defender_id=defender_id,
        target_threat_id=threat_id,
        start_time=1700000000.0,
        status=status,
        cost=cost,
        neutralized_threat_ids=neutralized if neutralized is not None else ["TH001"],
    )


# -- Existing formatter regression tests --


class TestFormatTrackCot:
    def test_well_formed_xml(self) -> None:
        track = _make_track()
        xml = format_track_cot(track)
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_cot_type_hostile(self) -> None:
        track = _make_track(classification=TrackClass.HOSTILE)
        root = ET.fromstring(format_track_cot(track))
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_cot_type_unknown(self) -> None:
        track = _make_track(classification=TrackClass.UNKNOWN)
        root = ET.fromstring(format_track_cot(track))
        assert root.attrib["type"] == "a-u-A-M-H-Q"

    def test_cot_type_friendly(self) -> None:
        track = _make_track(classification=TrackClass.FRIENDLY)
        root = ET.fromstring(format_track_cot(track))
        assert root.attrib["type"] == "a-f-A-M-H-Q"

    def test_uid_format(self) -> None:
        track = _make_track(track_id="X42")
        root = ET.fromstring(format_track_cot(track))
        assert root.attrib["uid"] == "OVERWATCH.X42"

    def test_position_conversion(self) -> None:
        track = _make_track(position=(0.0, 0.0, 0.0))
        root = ET.fromstring(format_track_cot(track))
        point = root.find("point")
        assert point is not None
        lat = float(point.attrib["lat"])
        lon = float(point.attrib["lon"])
        assert abs(lat - 33.6405) < 0.001
        assert abs(lon - (-117.8443)) < 0.001

    def test_remarks_contains_confidence(self) -> None:
        track = _make_track(confidence=0.92)
        root = ET.fromstring(format_track_cot(track))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "0.92" in remarks.text


class TestFormatThreatCot:
    def test_well_formed_xml(self) -> None:
        threat = _make_threat()
        track = _make_track()
        xml = format_threat_cot(threat, track)
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_always_hostile_type(self) -> None:
        threat = _make_threat()
        track = _make_track(classification=TrackClass.UNKNOWN)
        root = ET.fromstring(format_threat_cot(threat, track))
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_remarks_contains_score(self) -> None:
        threat = _make_threat(score=0.85)
        track = _make_track()
        root = ET.fromstring(format_threat_cot(threat, track))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "0.85" in remarks.text

    def test_remarks_contains_tti(self) -> None:
        threat = _make_threat(tti=12.5)
        track = _make_track()
        root = ET.fromstring(format_threat_cot(threat, track))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "12.5" in remarks.text

    def test_none_tti_shows_na(self) -> None:
        threat = _make_threat()
        threat.time_to_impact_s = None
        track = _make_track()
        root = ET.fromstring(format_threat_cot(threat, track))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "N/A" in remarks.text


# -- New formatter tests --


class TestFormatDefenderCot:
    def test_well_formed_xml(self) -> None:
        defender = _make_defender()
        xml = format_defender_cot(defender)
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_cot_type(self) -> None:
        defender = _make_defender()
        root = ET.fromstring(format_defender_cot(defender))
        assert root.attrib["type"] == "a-f-G-E-W"

    def test_uid_format(self) -> None:
        defender = _make_defender(defender_id="LASER-01")
        root = ET.fromstring(format_defender_cot(defender))
        assert root.attrib["uid"] == "OVERWATCH.DEF.LASER-01"

    def test_callsign(self) -> None:
        defender = _make_defender(kind=DefenderKind.LASER, defender_id="L1")
        root = ET.fromstring(format_defender_cot(defender))
        contact = root.find(".//contact")
        assert contact is not None
        assert contact.attrib["callsign"] == "LASER-L1"

    def test_position_conversion(self) -> None:
        defender = _make_defender(position=(0.0, 0.0, 0.0))
        root = ET.fromstring(format_defender_cot(defender))
        point = root.find("point")
        assert point is not None
        lat = float(point.attrib["lat"])
        lon = float(point.attrib["lon"])
        assert abs(lat - 33.6405) < 0.001
        assert abs(lon - (-117.8443)) < 0.001

    def test_remarks_contains_key_fields(self) -> None:
        defender = _make_defender(
            kind=DefenderKind.HPM,
            status=DefenderStatus.ENGAGING,
            capacity=3,
            range_m=800.0,
            kill_prob=0.90,
        )
        root = ET.fromstring(format_defender_cot(defender))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "HPM" in remarks.text
        assert "ENGAGING" in remarks.text
        assert "Capacity: 3" in remarks.text
        assert "800m" in remarks.text
        assert "0.90" in remarks.text

    def test_sensor_subelement(self) -> None:
        defender = _make_defender(range_m=1200.0)
        root = ET.fromstring(format_defender_cot(defender))
        sensor = root.find(".//sensor")
        assert sensor is not None
        assert sensor.attrib["range"] == "1200"

    def test_zero_position(self) -> None:
        defender = _make_defender(position=(0.0, 0.0, 0.0))
        xml = format_defender_cot(defender)
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_depleted_status(self) -> None:
        defender = _make_defender(status=DefenderStatus.DEPLETED, capacity=0)
        root = ET.fromstring(format_defender_cot(defender))
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "DEPLETED" in remarks.text
        assert "Capacity: 0" in remarks.text


class TestFormatEngagementCot:
    def test_well_formed_xml(self) -> None:
        eng = _make_engagement()
        xml = format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_hit_type(self) -> None:
        eng = _make_engagement(status=EngagementStatus.HIT)
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        assert root.attrib["type"] == "a-h-A-M-H-Q"

    def test_miss_type(self) -> None:
        eng = _make_engagement(status=EngagementStatus.MISS)
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        assert root.attrib["type"] == "a-u-A-M-H-Q"

    def test_leak_type(self) -> None:
        eng = _make_engagement(status=EngagementStatus.LEAK)
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        assert root.attrib["type"] == "a-u-A-M-H-Q"

    def test_uid_format(self) -> None:
        eng = _make_engagement(eng_id="ENG-42")
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        assert root.attrib["uid"] == "OVERWATCH.ENG.ENG-42"

    def test_position_from_track(self) -> None:
        track = _make_track(position=(100.0, 200.0, 50.0))
        expected_lat, expected_lon, _ = enu_to_latlon(100.0, 200.0, 50.0)
        eng = _make_engagement()
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), track)
        )
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - expected_lat) < 0.0001
        assert abs(float(point.attrib["lon"]) - expected_lon) < 0.0001

    def test_remarks_contains_key_fields(self) -> None:
        eng = _make_engagement(
            status=EngagementStatus.HIT,
            cost=250.0,
            neutralized=["TH001", "TH002"],
        )
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(defender_id="D99"), _make_threat(), _make_track())
        )
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "HIT" in remarks.text
        assert "D99" in remarks.text
        assert "$250.00" in remarks.text
        assert "TH001,TH002" in remarks.text

    def test_flow_tags(self) -> None:
        eng = _make_engagement()
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        flow = root.find(".//__flow-tags__")
        assert flow is not None
        assert "OVERWATCH-BDA" in flow.attrib

    def test_empty_neutralized(self) -> None:
        eng = _make_engagement(neutralized=[])
        root = ET.fromstring(
            format_engagement_cot(eng, _make_defender(), _make_threat(), _make_track())
        )
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "Neutralized: none" in remarks.text


class TestFormatSwarmClusterCot:
    def test_well_formed_xml(self) -> None:
        xml = format_swarm_cluster_cot(
            cluster_id="C1",
            center=(0.0, 0.0, 50.0),
            member_count=8,
            radius_m=150.0,
            intent="SATURATION",
            timestamp=1700000000.0,
        )
        root = ET.fromstring(xml)
        assert root.tag == "event"

    def test_cot_type(self) -> None:
        xml = format_swarm_cluster_cot(
            "C1", (0.0, 0.0, 0.0), 5, 100.0, "PROBE", 1700000000.0,
        )
        root = ET.fromstring(xml)
        assert root.attrib["type"] == "a-h-G"

    def test_uid_format(self) -> None:
        xml = format_swarm_cluster_cot(
            "ALPHA", (0.0, 0.0, 0.0), 5, 100.0, "PROBE", 1700000000.0,
        )
        root = ET.fromstring(xml)
        assert root.attrib["uid"] == "OVERWATCH.SWARM.ALPHA"

    def test_callsign(self) -> None:
        xml = format_swarm_cluster_cot(
            "BRAVO", (0.0, 0.0, 0.0), 5, 100.0, "WAVES", 1700000000.0,
        )
        root = ET.fromstring(xml)
        contact = root.find(".//contact")
        assert contact is not None
        assert contact.attrib["callsign"] == "SWARM-BRAVO"

    def test_remarks_content(self) -> None:
        xml = format_swarm_cluster_cot(
            "C1", (0.0, 0.0, 0.0), 12, 300.0, "DECOY", 1700000000.0,
        )
        root = ET.fromstring(xml)
        remarks = root.find(".//remarks")
        assert remarks is not None and remarks.text is not None
        assert "Members: 12" in remarks.text
        assert "300m" in remarks.text
        assert "DECOY" in remarks.text

    def test_shape_ellipse(self) -> None:
        xml = format_swarm_cluster_cot(
            "C1", (0.0, 0.0, 0.0), 5, 200.0, "UNKNOWN", 1700000000.0,
        )
        root = ET.fromstring(xml)
        ellipse = root.find(".//shape/ellipse")
        assert ellipse is not None
        assert ellipse.attrib["major"] == "200.0"
        assert ellipse.attrib["minor"] == "200.0"

    def test_position_conversion(self) -> None:
        expected_lat, expected_lon, _ = enu_to_latlon(500.0, 300.0, 100.0)
        xml = format_swarm_cluster_cot(
            "C1", (500.0, 300.0, 100.0), 5, 100.0, "PROBE", 1700000000.0,
        )
        root = ET.fromstring(xml)
        point = root.find("point")
        assert point is not None
        assert abs(float(point.attrib["lat"]) - expected_lat) < 0.0001
        assert abs(float(point.attrib["lon"]) - expected_lon) < 0.0001

    def test_zero_position(self) -> None:
        xml = format_swarm_cluster_cot(
            "C1", (0.0, 0.0, 0.0), 1, 0.0, "UNKNOWN", 1700000000.0,
        )
        root = ET.fromstring(xml)
        assert root.tag == "event"
