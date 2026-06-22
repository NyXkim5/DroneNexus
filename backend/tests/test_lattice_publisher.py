"""Tests for the Lattice-compatible entity publisher."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

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
)
from sinks.lattice_publisher import (
    LatticePublisher,
    _RateLimiter,
    build_defender_entity,
    build_engagement_entity,
    build_track_entity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_track(
    track_id: str = "t-001",
    position: Vec3 = (100.0, 200.0, 50.0),
    velocity: Vec3 = (5.0, -3.0, 0.0),
    classification: TrackClass = TrackClass.HOSTILE,
    confidence: float = 0.92,
) -> Track:
    return Track(
        id=track_id,
        position=position,
        velocity=velocity,
        covariance=(1.0, 1.0, 1.0),
        last_update=time.time(),
        classification=classification,
        confidence=confidence,
    )


def _make_threat(
    threat_id: str = "th-001",
    track_id: str = "t-001",
    score: float = 0.85,
    intent: SwarmIntent = SwarmIntent.SATURATION,
    time_to_impact: Optional[float] = 12.5,
) -> Threat:
    return Threat(
        id=threat_id,
        score=score,
        time_to_impact_s=time_to_impact,
        value_at_risk=5000.0,
        priority_rank=1,
        track_id=track_id,
        intent=intent,
    )


def _make_defender(
    defender_id: str = "d-001",
    kind: DefenderKind = DefenderKind.JAMMER,
    position: Vec3 = (0.0, 0.0, 0.0),
    status: DefenderStatus = DefenderStatus.READY,
) -> Defender:
    return Defender(
        id=defender_id,
        position=position,
        kind=kind,
        capacity=10,
        range_m=500.0,
        reload_s=3.0,
        kill_prob=0.8,
        unit_cost=100.0,
        status=status,
    )


def _make_engagement(
    eng_id: str = "e-001",
    defender_id: str = "d-001",
    threat_id: str = "th-001",
    status: EngagementStatus = EngagementStatus.HIT,
) -> Engagement:
    return Engagement(
        id=eng_id,
        defender_id=defender_id,
        target_threat_id=threat_id,
        start_time=time.time(),
        status=status,
        cost=100.0,
        neutralized_threat_ids=[threat_id],
    )


# ---------------------------------------------------------------------------
# Track entity tests
# ---------------------------------------------------------------------------

class TestTrackEntity:
    def test_hostile_track_produces_correct_entity(self) -> None:
        track = _make_track()
        threat = _make_threat()
        entity = build_track_entity(track, threat, "TEST", "2026-06-22T12:00:00Z")

        assert entity["entity_id"] == "overwatch-track-t-001"
        assert entity["entity_type"] == "TRACK"
        assert entity["disposition"] == "DISPOSITION_HOSTILE"
        assert entity["confidence"] == 0.92
        assert entity["ontology"]["template"] == "TEMPLATE_TRACK"
        assert entity["ontology"]["platform_type"] == "PLATFORM_TYPE_UAV"
        assert entity["threat_score"] == 0.85
        assert entity["intent"] == "SATURATION"
        assert entity["time_to_impact_s"] == 12.5

    def test_unknown_track_has_unknown_disposition(self) -> None:
        track = _make_track(classification=TrackClass.UNKNOWN)
        entity = build_track_entity(track, None, "TEST", "2026-06-22T12:00:00Z")

        assert entity["disposition"] == "DISPOSITION_UNKNOWN"
        assert "threat_score" not in entity

    def test_friendly_track_disposition(self) -> None:
        track = _make_track(classification=TrackClass.FRIENDLY)
        entity = build_track_entity(track, None, "TEST", "2026-06-22T12:00:00Z")

        assert entity["disposition"] == "DISPOSITION_FRIENDLY"

    def test_location_has_lat_lon_alt(self) -> None:
        track = _make_track(position=(0.0, 0.0, 100.0))
        entity = build_track_entity(track, None, "TEST", "2026-06-22T12:00:00Z")

        loc = entity["location"]
        assert "lat" in loc
        assert "lon" in loc
        assert "alt" in loc
        assert loc["alt"] == 100.0

    def test_velocity_present(self) -> None:
        track = _make_track(velocity=(10.0, -5.0, 2.0))
        entity = build_track_entity(track, None, "TEST", "2026-06-22T12:00:00Z")

        vel = entity["velocity"]
        assert vel["east_mps"] == 10.0
        assert vel["north_mps"] == -5.0
        assert vel["up_mps"] == 2.0

    def test_no_threat_omits_threat_fields(self) -> None:
        track = _make_track()
        entity = build_track_entity(track, None, "TEST", "2026-06-22T12:00:00Z")

        assert "threat_score" not in entity
        assert "intent" not in entity
        assert "time_to_impact_s" not in entity


# ---------------------------------------------------------------------------
# Defender entity tests
# ---------------------------------------------------------------------------

class TestDefenderEntity:
    def test_defender_entity_fields(self) -> None:
        defender = _make_defender()
        entity = build_defender_entity(defender, "TEST", "2026-06-22T12:00:00Z")

        assert entity["entity_id"] == "overwatch-defender-d-001"
        assert entity["entity_type"] == "ASSET"
        assert entity["disposition"] == "DISPOSITION_FRIENDLY"
        assert entity["ontology"]["template"] == "TEMPLATE_ASSET"
        assert entity["ontology"]["platform_type"] == "PLATFORM_TYPE_COUNTER_UAS"
        assert entity["confidence"] == 1.0

    def test_effector_detail(self) -> None:
        defender = _make_defender(kind=DefenderKind.HPM)
        entity = build_defender_entity(defender, "TEST", "2026-06-22T12:00:00Z")

        eff = entity["effector"]
        assert eff["kind"] == "HPM"
        assert eff["detail"] == "HPM_EMITTER"
        assert eff["status"] == "READY"
        assert eff["capacity"] == 10
        assert eff["range_m"] == 500.0

    def test_all_defender_kinds_map(self) -> None:
        for kind in DefenderKind:
            defender = _make_defender(kind=kind)
            entity = build_defender_entity(defender, "TEST", "2026-06-22T12:00:00Z")
            assert entity["effector"]["kind"] == kind.value

    def test_location_geodetic(self) -> None:
        defender = _make_defender(position=(50.0, 50.0, 10.0))
        entity = build_defender_entity(defender, "TEST", "2026-06-22T12:00:00Z")

        loc = entity["location"]
        assert isinstance(loc["lat"], float)
        assert isinstance(loc["lon"], float)
        assert loc["alt"] == 10.0


# ---------------------------------------------------------------------------
# Engagement entity tests
# ---------------------------------------------------------------------------

class TestEngagementEntity:
    def test_engagement_entity_relationships(self) -> None:
        track = _make_track()
        threat = _make_threat()
        defender = _make_defender()
        eng = _make_engagement()

        entity = build_engagement_entity(
            eng, defender, threat, track, "TEST", "2026-06-22T12:00:00Z",
        )

        assert entity["entity_id"] == "overwatch-engagement-e-001"
        assert entity["entity_type"] == "ENGAGEMENT"
        rels = entity["relationships"]
        assert len(rels) == 2
        assert rels[0]["related_entity_id"] == "overwatch-defender-d-001"
        assert rels[0]["relationship_type"] == "ENGAGED_BY"
        assert rels[1]["related_entity_id"] == "overwatch-track-t-001"
        assert rels[1]["relationship_type"] == "ENGAGED_TARGET"

    def test_engagement_detail(self) -> None:
        track = _make_track()
        threat = _make_threat()
        defender = _make_defender()
        eng = _make_engagement(status=EngagementStatus.HIT)

        entity = build_engagement_entity(
            eng, defender, threat, track, "TEST", "2026-06-22T12:00:00Z",
        )

        detail = entity["engagement_detail"]
        assert detail["status"] == "HIT"
        assert detail["cost"] == 100.0
        assert detail["neutralized_count"] == 1

    def test_miss_engagement(self) -> None:
        track = _make_track()
        threat = _make_threat()
        defender = _make_defender()
        eng = _make_engagement(status=EngagementStatus.MISS)
        eng.neutralized_threat_ids = []

        entity = build_engagement_entity(
            eng, defender, threat, track, "TEST", "2026-06-22T12:00:00Z",
        )

        assert entity["engagement_detail"]["status"] == "MISS"
        assert entity["engagement_detail"]["neutralized_count"] == 0


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_first_call_always_allowed(self) -> None:
        limiter = _RateLimiter(default_period_s=1.0)
        assert limiter.allow("track") is True

    def test_second_call_blocked_within_period(self) -> None:
        limiter = _RateLimiter(default_period_s=10.0)
        assert limiter.allow("track") is True
        assert limiter.allow("track") is False

    def test_different_keys_independent(self) -> None:
        limiter = _RateLimiter(default_period_s=10.0)
        assert limiter.allow("track") is True
        assert limiter.allow("defender") is True

    def test_custom_period_respected(self) -> None:
        limiter = _RateLimiter(default_period_s=10.0)
        limiter.set_period("fast", 0.0)
        assert limiter.allow("fast") is True
        assert limiter.allow("fast") is True

    def test_reset_clears_state(self) -> None:
        limiter = _RateLimiter(default_period_s=10.0)
        assert limiter.allow("track") is True
        assert limiter.allow("track") is False
        limiter.reset()
        assert limiter.allow("track") is True


# ---------------------------------------------------------------------------
# LatticePublisher integration tests
# ---------------------------------------------------------------------------

class TestLatticePublisher:
    def test_publish_tracks_returns_entities(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        tracks = [_make_track("t-1"), _make_track("t-2")]
        threats = [_make_threat("th-1", "t-1")]

        entities = pub.publish_tracks(tracks, threats)
        assert len(entities) == 2
        assert entities[0]["entity_id"] == "overwatch-track-t-1"
        assert entities[0]["threat_score"] == 0.85
        assert "threat_score" not in entities[1]

    def test_publish_tracks_rate_limited(self) -> None:
        pub = LatticePublisher(source_name="TEST", track_hz=0.1)
        tracks = [_make_track()]
        threats: List[Threat] = []

        first = pub.publish_tracks(tracks, threats)
        second = pub.publish_tracks(tracks, threats)
        assert len(first) == 1
        assert len(second) == 0

    def test_publish_defenders_returns_entities(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        defenders = [_make_defender("d-1"), _make_defender("d-2")]

        entities = pub.publish_defenders(defenders)
        assert len(entities) == 2
        assert entities[0]["entity_id"] == "overwatch-defender-d-1"

    def test_publish_defenders_rate_limited(self) -> None:
        pub = LatticePublisher(source_name="TEST", defender_hz=0.1)
        defenders = [_make_defender()]

        first = pub.publish_defenders(defenders)
        second = pub.publish_defenders(defenders)
        assert len(first) == 1
        assert len(second) == 0

    def test_publish_engagements_no_rate_limit(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        track = _make_track()
        threat = _make_threat()
        defender = _make_defender()
        eng = _make_engagement()

        first = pub.publish_engagements([eng], [defender], [threat], [track])
        second = pub.publish_engagements([eng], [defender], [threat], [track])
        assert len(first) == 1
        assert len(second) == 1

    def test_publish_engagements_skips_missing_refs(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        eng = _make_engagement(defender_id="missing", threat_id="missing")

        result = pub.publish_engagements([eng], [], [], [])
        assert len(result) == 0

    def test_publish_empty_engagements(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        result = pub.publish_engagements([], [], [], [])
        assert result == []

    def test_reset_rate_limits(self) -> None:
        pub = LatticePublisher(source_name="TEST", track_hz=0.1)
        tracks = [_make_track()]

        first = pub.publish_tracks(tracks, [])
        assert len(first) == 1

        pub.reset_rate_limits()
        after_reset = pub.publish_tracks(tracks, [])
        assert len(after_reset) == 1


# ---------------------------------------------------------------------------
# Frame integration test
# ---------------------------------------------------------------------------

class TestPublishFrame:
    def _make_frame(self) -> Any:
        """Build a minimal Frame-like object for testing."""
        @dataclass
        class FakeMetrics:
            tick: int = 5

        @dataclass
        class FakeFrame:
            tracks: List[Track] = field(default_factory=list)
            threats: List[Threat] = field(default_factory=list)
            defenders: List[Defender] = field(default_factory=list)
            engagements: List[Engagement] = field(default_factory=list)
            metrics: FakeMetrics = field(default_factory=FakeMetrics)

        return FakeFrame(
            tracks=[_make_track("t-1"), _make_track("t-2")],
            threats=[_make_threat("th-1", "t-1")],
            defenders=[_make_defender("d-1")],
            engagements=[_make_engagement("e-1", "d-1", "th-1")],
        )

    def test_publish_frame_returns_all_entity_types(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        frame = self._make_frame()

        entities = pub.publish_frame_to_lattice(frame)
        types = {e["entity_type"] for e in entities}

        assert "TRACK" in types
        assert "ASSET" in types
        assert "ENGAGEMENT" in types

    def test_publish_frame_entity_count(self) -> None:
        pub = LatticePublisher(source_name="TEST")
        frame = self._make_frame()

        entities = pub.publish_frame_to_lattice(frame)
        # 2 tracks + 1 defender + 1 engagement = 4
        assert len(entities) == 4

    def test_json_output_format(self) -> None:
        """Verify entities are JSON-serializable and have required fields."""
        import json

        pub = LatticePublisher(source_name="TEST")
        frame = self._make_frame()
        entities = pub.publish_frame_to_lattice(frame)

        for entity in entities:
            serialized = json.dumps(entity)
            parsed = json.loads(serialized)
            assert "entity_id" in parsed
            assert "entity_type" in parsed
            assert "location" in parsed
            assert "timestamp" in parsed
            assert "ontology" in parsed
            assert "disposition" in parsed
            assert "relationships" in parsed

    def test_repeated_frame_respects_rate_limits(self) -> None:
        pub = LatticePublisher(source_name="TEST", track_hz=0.1, defender_hz=0.1)
        frame = self._make_frame()

        first = pub.publish_frame_to_lattice(frame)
        second = pub.publish_frame_to_lattice(frame)

        # First call: 2 tracks + 1 defender + 1 engagement = 4
        assert len(first) == 4
        # Second call: tracks and defenders rate-limited, only engagement passes
        assert len(second) == 1
        assert second[0]["entity_type"] == "ENGAGEMENT"
