"""Tests for the BULWARK to OVERWATCH ontology bridge."""
from __future__ import annotations

import csontology as cs
import ontology_bridge as bridge
import protocol as ow


def _make_track(classification: cs.TrackClass = cs.TrackClass.HOSTILE) -> cs.Track:
    return cs.Track(
        id="trk-1",
        position=(1000.0, 2000.0, 120.0),
        velocity=(-5.0, -3.0, 0.0),
        covariance=(2.0, 2.0, 1.0),
        last_update=cs.now(),
        classification=classification,
        confidence=0.8,
    )


def _make_threat() -> cs.Threat:
    return cs.Threat(
        id="thr-1",
        score=0.92,
        time_to_impact_s=14.5,
        value_at_risk=1_000_000.0,
        priority_rank=1,
        track_id="trk-1",
        swarm_id="swm-1",
    )


def _make_swarm() -> cs.Swarm:
    return cs.Swarm(
        id="swm-1",
        member_track_ids=["trk-1", "trk-2"],
        centroid=(1000.0, 2000.0, 120.0),
        formation="DIAMOND",
        intent=cs.SwarmIntent.SATURATION,
        size=2,
        first_seen=cs.now(),
    )


def _make_defender(
    status: cs.DefenderStatus = cs.DefenderStatus.READY,
) -> cs.Defender:
    return cs.Defender(
        id="def-1",
        position=(0.0, 0.0, 0.0),
        kind=cs.DefenderKind.INTERCEPTOR,
        capacity=4,
        range_m=1500.0,
        reload_s=8.0,
        kill_prob=0.7,
        unit_cost=25000.0,
        status=status,
    )


# ---- track_to_asset_classification ----

def test_track_class_maps_to_asset_classification():
    cases = {
        cs.TrackClass.HOSTILE: ow.AssetClassification.PRIMARY,
        cs.TrackClass.FRIENDLY: ow.AssetClassification.ESCORT,
        cs.TrackClass.UNKNOWN: ow.AssetClassification.ISR,
    }
    for track_class, expected in cases.items():
        result = bridge.track_to_asset_classification(_make_track(track_class))
        assert result is expected
        assert isinstance(result, ow.AssetClassification)


def test_asset_classification_round_trips_for_known_classes():
    for track_class in (cs.TrackClass.HOSTILE, cs.TrackClass.FRIENDLY):
        asset = bridge.track_to_asset_classification(_make_track(track_class))
        assert bridge.asset_classification_to_track_class(asset) is track_class


def test_unknown_track_class_round_trips_to_unknown():
    asset = bridge.track_to_asset_classification(_make_track(cs.TrackClass.UNKNOWN))
    assert bridge.asset_classification_to_track_class(asset) is cs.TrackClass.UNKNOWN


def test_overwatch_only_classification_collapses_to_unknown():
    result = bridge.asset_classification_to_track_class(
        ow.AssetClassification.LOGISTICS
    )
    assert result is cs.TrackClass.UNKNOWN


# ---- threat_to_observation ----

def test_threat_to_observation_uses_swarm_formation_overlay():
    obs = bridge.threat_to_observation(_make_threat(), _make_track(), _make_swarm())
    assert obs["type"] == ow.MessageType.OVERLAY_UPDATE.value
    assert obs["overlay_type"] == ow.OverlayType.DIAMOND.value
    assert ow.OverlayType(obs["overlay_type"]) is ow.OverlayType.DIAMOND
    assert obs["threat_id"] == "thr-1"
    assert obs["classification"] == ow.AssetClassification.PRIMARY.value


def test_threat_to_observation_position_is_valid_geodetic():
    obs = bridge.threat_to_observation(_make_threat(), _make_track(), _make_swarm())
    pos = ow.Position(**obs["position"])
    assert abs(pos.lat - cs.ORIGIN_LAT) < 0.05
    assert abs(pos.lon - cs.ORIGIN_LON) < 0.05


def test_threat_to_observation_defaults_to_scatter_without_swarm():
    obs = bridge.threat_to_observation(_make_threat(), _make_track())
    assert obs["overlay_type"] == ow.OverlayType.SCATTER.value


def test_threat_to_observation_handles_unknown_formation():
    swarm = _make_swarm()
    swarm.formation = "MYSTERY"
    obs = bridge.threat_to_observation(_make_threat(), _make_track(), swarm)
    assert obs["overlay_type"] == ow.OverlayType.SCATTER.value


# ---- engagement_to_event ----

def _engagement(status: cs.EngagementStatus) -> cs.Engagement:
    return cs.Engagement(
        id="eng-1",
        defender_id="def-1",
        target_threat_id="thr-1",
        start_time=cs.now(),
        status=status,
        cost=25000.0,
    )


def test_engagement_to_event_matches_log_event_shape():
    event = bridge.engagement_to_event(_engagement(cs.EngagementStatus.HIT))
    assert set(event) >= {"drone_id", "severity", "message", "data", "timestamp"}
    assert event["drone_id"] == "def-1"
    assert ow.AlertSeverity(event["severity"]) is ow.AlertSeverity.INFO
    assert event["data"]["engagement_id"] == "eng-1"
    assert event["data"]["target_threat_id"] == "thr-1"


def test_engagement_leak_is_critical():
    event = bridge.engagement_to_event(_engagement(cs.EngagementStatus.LEAK))
    assert event["severity"] == ow.AlertSeverity.CRITICAL.value


def test_engagement_miss_is_warning():
    event = bridge.engagement_to_event(_engagement(cs.EngagementStatus.MISS))
    assert event["severity"] == ow.AlertSeverity.WARNING.value


# ---- defender_to_asset ----

def test_defender_to_asset_builds_valid_packet():
    asset = bridge.defender_to_asset(_make_defender())
    assert isinstance(asset, ow.AssetStatePacket)
    assert asset.drone_id == "def-1"
    assert asset.formation.role == ow.AssetClassification.OVERWATCH.value
    assert asset.status == ow.OperationalStatus.NOMINAL.value


def test_defender_status_maps_to_operational_status():
    cases = {
        cs.DefenderStatus.READY: ow.OperationalStatus.NOMINAL,
        cs.DefenderStatus.RELOADING: ow.OperationalStatus.DEGRADED,
        cs.DefenderStatus.DEPLETED: ow.OperationalStatus.RTB,
        cs.DefenderStatus.OFFLINE: ow.OperationalStatus.OFFLINE,
    }
    for defender_status, expected in cases.items():
        asset = bridge.defender_to_asset(_make_defender(defender_status))
        assert asset.status == expected.value


def test_defender_to_asset_position_round_trips_origin():
    asset = bridge.defender_to_asset(_make_defender())
    assert abs(asset.position.lat - cs.ORIGIN_LAT) < 1e-6
    assert abs(asset.position.lon - cs.ORIGIN_LON) < 1e-6
