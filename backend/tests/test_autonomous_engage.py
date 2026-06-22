"""Tests for the autonomous engagement pipeline."""
from __future__ import annotations

import asyncio
import math
import time
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Detection,
    Site,
    Threat,
    Track,
    TrackClass,
    Vec3,
)
from scripts.autonomous_engage import (
    AutonomousEngagementLoop,
    MockDetectionSource,
    SimulatedEngagement,
    TickRecord,
    build_default_corridors,
    build_default_defenders,
    build_default_site,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def site() -> Site:
    return Site(
        id="test-site",
        position=(0.0, 0.0, 0.0),
        protected_assets=["cp"],
        value=500_000.0,
    )


@pytest.fixture
def defenders() -> List[Defender]:
    return [
        Defender(
            id="DEF-001",
            position=(50.0, 50.0, 0.0),
            kind=DefenderKind.INTERCEPTOR,
            capacity=8,
            range_m=2000.0,
            reload_s=3.0,
            kill_prob=0.85,
            unit_cost=5000.0,
        ),
    ]


@pytest.fixture
def corridors(site: Site) -> List[Tuple[Vec3, float]]:
    return [(site.position, 3000.0)]


@pytest.fixture
def mock_source(site: Site) -> MockDetectionSource:
    return MockDetectionSource(site.position, count=2)


@pytest.fixture
def sim_executor() -> SimulatedEngagement:
    return SimulatedEngagement()


@pytest.fixture
def loop(
    mock_source: MockDetectionSource,
    sim_executor: SimulatedEngagement,
    site: Site,
    defenders: List[Defender],
    corridors: List[Tuple[Vec3, float]],
) -> AutonomousEngagementLoop:
    return AutonomousEngagementLoop(
        detector=mock_source,
        executor=sim_executor,
        site=site,
        defenders=defenders,
        corridors=corridors,
        tick_rate=5.0,
    )


# ---------------------------------------------------------------------------
# MockDetectionSource tests
# ---------------------------------------------------------------------------

class TestMockDetectionSource:
    @pytest.mark.asyncio
    async def test_yields_detections(self, mock_source: MockDetectionSource) -> None:
        detections = await mock_source.get_detections()
        assert len(detections) > 0
        assert all(isinstance(d, Detection) for d in detections)

    @pytest.mark.asyncio
    async def test_detections_have_valid_positions(
        self, mock_source: MockDetectionSource,
    ) -> None:
        detections = await mock_source.get_detections()
        for d in detections:
            assert len(d.position) == 3
            assert d.confidence > 0.0

    @pytest.mark.asyncio
    async def test_detections_converge_on_site(
        self, site: Site,
    ) -> None:
        source = MockDetectionSource(site.position, count=1)
        d1 = await source.get_detections()
        for _ in range(20):
            await source.get_detections()
        d2 = await source.get_detections()
        if d1 and d2:
            dist1 = math.hypot(
                d1[0].position[0] - site.position[0],
                d1[0].position[1] - site.position[1],
            )
            dist2 = math.hypot(
                d2[0].position[0] - site.position[0],
                d2[0].position[1] - site.position[1],
            )
            assert dist2 < dist1

    @pytest.mark.asyncio
    async def test_detection_ids_are_unique(
        self, mock_source: MockDetectionSource,
    ) -> None:
        d1 = await mock_source.get_detections()
        d2 = await mock_source.get_detections()
        all_ids = [d.id for d in d1 + d2]
        assert len(all_ids) == len(set(all_ids))


# ---------------------------------------------------------------------------
# SimulatedEngagement tests
# ---------------------------------------------------------------------------

class TestSimulatedEngagement:
    @pytest.mark.asyncio
    async def test_execute_returns_true(
        self, sim_executor: SimulatedEngagement,
    ) -> None:
        from defense.effector_handoff import SlewCommand
        slew = SlewCommand(
            defender_id="DEF-001",
            target_id="TRK-001",
            bearing_deg=45.0,
            elevation_deg=10.0,
            range_m=500.0,
            lead_bearing_deg=46.0,
            lead_elevation_deg=10.5,
            priority=1,
            timestamp=time.time(),
        )
        result = await sim_executor.execute(slew)
        assert result is True


# ---------------------------------------------------------------------------
# Full tick cycle tests
# ---------------------------------------------------------------------------

class TestAutonomousEngagementLoop:
    @pytest.mark.asyncio
    async def test_tick_returns_record(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        record = await loop.tick()
        assert isinstance(record, TickRecord)
        assert record.tick == 1
        assert record.detection_count > 0

    @pytest.mark.asyncio
    async def test_multi_tick_builds_tracks(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        for _ in range(5):
            record = await loop.tick()
        assert record.track_count > 0

    @pytest.mark.asyncio
    async def test_hostile_classification_after_ticks(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        for _ in range(10):
            record = await loop.tick()
        assert record.hostile_count > 0

    @pytest.mark.asyncio
    async def test_threats_scored_for_hostiles(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        record = None
        for _ in range(15):
            record = await loop.tick()
        assert record is not None
        assert record.threat_count > 0

    @pytest.mark.asyncio
    async def test_engagement_after_convergence(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        """After enough ticks, confirmed hostile tracks should trigger engagements."""
        loop._start_time = time.time()
        engaged = False
        for _ in range(30):
            record = await loop.tick()
            if record.engagement_count > 0:
                engaged = True
                break
        assert engaged, "Expected at least one engagement within 30 ticks"

    @pytest.mark.asyncio
    async def test_status_line_format(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        record = await loop.tick()
        line = record.status_line()
        assert "[T+" in line
        assert "DETECT:" in line
        assert "TRACK:" in line
        assert "THREAT:" in line
        assert "ROE:" in line
        assert "ENGAGE:" in line
        assert "STATUS:" in line

    @pytest.mark.asyncio
    async def test_tick_count_increments(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        r1 = await loop.tick()
        r2 = await loop.tick()
        assert r1.tick == 1
        assert r2.tick == 2


# ---------------------------------------------------------------------------
# ROE evaluation tests
# ---------------------------------------------------------------------------

class TestROEIntegration:
    @pytest.mark.asyncio
    async def test_low_confidence_blocks_authorization(
        self, site: Site, defenders: List[Defender],
        corridors: List[Tuple[Vec3, float]],
    ) -> None:
        """ROE with a very high min_confidence rejects even confirmed tracks."""
        from decision.roe import ROECondition, ROEEngine, ROERule

        strict_rule = ROERule(
            name="STRICT_TEST",
            conditions=[
                ROECondition.POSITIVE_ID,
                ROECondition.THREAT_IMMINENT,
                ROECondition.WITHIN_CORRIDOR,
            ],
            authorized_effectors=["any"],
            min_confidence=0.999,
            authorization_level="CO",
        )

        source = MockDetectionSource(site.position, count=1)
        loop = AutonomousEngagementLoop(
            detector=source,
            executor=SimulatedEngagement(),
            site=site,
            defenders=defenders,
            corridors=corridors,
        )
        loop._roe_engine = ROEEngine(rules=[strict_rule])
        loop._start_time = time.time()
        for _ in range(15):
            record = await loop.tick()
        assert record.authorized_count == 0
        assert record.engagement_count == 0

    @pytest.mark.asyncio
    async def test_out_of_corridor_blocks_authorization(
        self, site: Site, defenders: List[Defender],
    ) -> None:
        """Threats outside the engagement corridor must be denied by ROE."""
        tiny_corridor: List[Tuple[Vec3, float]] = [
            (site.position, 10.0),
        ]

        class FarSource(MockDetectionSource):
            async def get_detections(self) -> List[Detection]:
                self._tick += 1
                now = time.time()
                return [
                    Detection(
                        id=f"far-{self._tick}",
                        timestamp=now,
                        position=(5000.0, 5000.0, 50.0),
                        velocity=(-10.0, -10.0, 0.0),
                        confidence=0.95,
                        sensor_id="test",
                    ),
                ]

        source = FarSource(site.position, count=0)
        loop = AutonomousEngagementLoop(
            detector=source,
            executor=SimulatedEngagement(),
            site=site,
            defenders=defenders,
            corridors=tiny_corridor,
        )
        loop._start_time = time.time()
        for _ in range(15):
            record = await loop.tick()
        assert record.authorized_count == 0


# ---------------------------------------------------------------------------
# Engagement logging tests
# ---------------------------------------------------------------------------

class TestEngagementLogging:
    @pytest.mark.asyncio
    async def test_engagement_dict_has_required_fields(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        for _ in range(30):
            record = await loop.tick()
            if record.engagements:
                eng = record.engagements[0]
                assert "defender_id" in eng
                assert "target_id" in eng
                assert "bearing_deg" in eng
                assert "range_m" in eng
                return
        pytest.skip("No engagement generated within 30 ticks")

    @pytest.mark.asyncio
    async def test_audit_log_populated(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        for _ in range(15):
            await loop.tick()
        log = loop._roe_engine.audit_log
        assert len(log) > 0

    @pytest.mark.asyncio
    async def test_record_status_values(
        self, loop: AutonomousEngagementLoop,
    ) -> None:
        loop._start_time = time.time()
        statuses = set()
        for _ in range(20):
            record = await loop.tick()
            statuses.add(record.status)
        assert "DETECTING" in statuses or "TRACKING" in statuses


# ---------------------------------------------------------------------------
# Default factory tests
# ---------------------------------------------------------------------------

class TestDefaultFactories:
    def test_build_default_site(self) -> None:
        site = build_default_site(33.6405, -117.8443)
        assert site.id == "site-alpha"
        assert site.value > 0

    def test_build_default_defenders(self) -> None:
        defs = build_default_defenders()
        assert len(defs) >= 2
        kinds = {d.kind for d in defs}
        assert DefenderKind.INTERCEPTOR in kinds

    def test_build_default_corridors(self) -> None:
        pos: Vec3 = (0.0, 0.0, 0.0)
        corridors = build_default_corridors(pos)
        assert len(corridors) >= 1
        center, radius = corridors[0]
        assert radius > 0
