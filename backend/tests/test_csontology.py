"""Tests for the BULWARK ontology contracts and coordinate frame."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import (
    ORIGIN_LAT, ORIGIN_LON,
    latlon_to_enu, enu_to_latlon,
    Detection, Track, Swarm, Threat, Defender, Engagement, Site,
    DefenderKind, DefenderStatus, EngagementStatus, SwarmIntent, TrackClass,
)


def test_origin_round_trips_to_zero():
    x, y, z = latlon_to_enu(ORIGIN_LAT, ORIGIN_LON, 0.0)
    assert abs(x) < 1e-6
    assert abs(y) < 1e-6
    assert abs(z) < 1e-6


def test_enu_round_trip_preserves_latlon():
    lat, lon, alt = 33.6500, -117.8300, 120.0
    x, y, z = latlon_to_enu(lat, lon, alt)
    back_lat, back_lon, back_alt = enu_to_latlon(x, y, z)
    assert abs(back_lat - lat) < 1e-9
    assert abs(back_lon - lon) < 1e-9
    assert abs(back_alt - alt) < 1e-9


def test_enu_axes_point_east_and_north():
    # A point north of origin has positive y. A point east has positive x.
    north_x, north_y, _ = latlon_to_enu(ORIGIN_LAT + 0.001, ORIGIN_LON, 0.0)
    east_x, east_y, _ = latlon_to_enu(ORIGIN_LAT, ORIGIN_LON + 0.001, 0.0)
    assert north_y > 0
    assert abs(north_x) < 1e-6
    assert east_x > 0
    assert abs(east_y) < 1e-6


def test_detection_is_frozen():
    d = Detection(
        id="det-1", timestamp=1.0, position=(10.0, 20.0, 30.0),
        velocity=(1.0, 0.0, 0.0), confidence=0.9, sensor_id="radar-1",
    )
    assert d.size_rcs is None
    try:
        d.confidence = 0.5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Detection should be frozen")


def test_track_construction_defaults():
    t = Track(
        id="trk-1", position=(0.0, 0.0, 50.0), velocity=(5.0, 0.0, 0.0),
        covariance=(2.0, 2.0, 3.0), last_update=100.0,
    )
    assert t.classification is TrackClass.UNKNOWN
    assert t.source_detection_ids == []
    assert t.history == []


def test_swarm_and_threat_construction():
    s = Swarm(
        id="swarm-1", member_track_ids=["trk-1", "trk-2"],
        centroid=(100.0, 100.0, 80.0), formation="LINE_ABREAST",
        intent=SwarmIntent.SATURATION, size=2, first_seen=10.0,
    )
    assert s.intent is SwarmIntent.SATURATION
    threat = Threat(
        id="thr-1", score=0.8, time_to_impact_s=12.5, value_at_risk=1_000_000.0,
        priority_rank=1, swarm_id=s.id,
    )
    assert threat.track_id is None
    assert threat.swarm_id == "swarm-1"


def test_defender_and_engagement_construction():
    d = Defender(
        id="def-1", position=(0.0, 0.0, 0.0), kind=DefenderKind.INTERCEPTOR,
        capacity=4, range_m=2000.0, reload_s=8.0, kill_prob=0.85, unit_cost=5000.0,
    )
    assert d.status is DefenderStatus.READY
    e = Engagement(
        id="eng-1", defender_id=d.id, target_threat_id="thr-1", start_time=20.0,
    )
    assert e.status is EngagementStatus.PENDING
    assert e.cost == 0.0


def test_site_construction():
    site = Site(
        id="site-1", position=(0.0, 0.0, 0.0),
        protected_assets=["radar", "command-post"], value=10_000_000.0,
    )
    assert "radar" in site.protected_assets
