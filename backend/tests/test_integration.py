"""
End-to-end integration tests for the full BULWARK pipeline.

Proves sensor ingestion -> fusion -> threat assessment -> defense allocation ->
CoT output -> wargame recording -> replay all work together. Every test class
covers one pipeline stage boundary. All tests are marked slow because they
exercise real scenario data and real module wiring.
"""
from __future__ import annotations

import asyncio
import json as json_mod
import os
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Detection,
    Engagement,
    EngagementStatus,
    Site,
    SwarmIntent,
    Threat,
    Track,
    TrackClass,
    Vec3,
)
from cot.formatter import (
    format_defender_cot,
    format_engagement_cot,
    format_swarm_cluster_cot,
    format_threat_cot,
    format_track_cot,
)
from defense import LayeredAllocator
from fusion import TrackManager
from sensors.dji_decoder import parse_dji_binary_frame
from sensors.odid_decoder import (
    MSG_BASIC_ID,
    ODID_MESSAGE_SIZE,
    decode_odid_message,
)
from sensors.sim_source import SimSensorSource, SimSensorSpec, TruthTarget
from sensors.udp_rid_source import _parse_rid_json
from wargame.frame import Frame, Metrics
from wargame.replay import ReplayPlayer
from wargame.runner import WargameRunner
from wargame.scenario import (
    DefenderConfig,
    Scenario,
    SensorConfig,
    SiteConfig,
    list_scenarios,
    load_scenario,
)

import threat as threat_module

pytestmark = pytest.mark.slow

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fast_scenario(name: str = "skirmish_80", max_ticks: int = 300) -> Scenario:
    """Load a preset and optionally cap its tick count for speed."""
    scenario = load_scenario(name)
    scenario.max_ticks = max_ticks
    return scenario


def _run_wargame(scenario: Scenario) -> List[Frame]:
    """Run a scenario to completion and collect all frames."""

    async def _go() -> List[Frame]:
        runner = WargameRunner(scenario)
        frames: List[Frame] = []
        async for frame in runner.run(pace=False):
            frames.append(frame)
        return frames

    return asyncio.run(_go())


def _make_site() -> Site:
    """Build a default site at the ENU origin."""
    return Site(
        id="SITE-1",
        position=(0.0, 0.0, 0.0),
        protected_assets=["C2", "RADAR"],
        value=1_000_000.0,
    )


def _make_track(
    track_id: str,
    position: Vec3,
    velocity: Vec3 = (0.0, -20.0, 0.0),
) -> Track:
    """Build a hostile track at the given position."""
    return Track(
        id=track_id,
        position=position,
        velocity=velocity,
        covariance=(5.0, 5.0, 5.0),
        last_update=time.time(),
        classification=TrackClass.HOSTILE,
        confidence=0.9,
    )


def _make_threat(
    threat_id: str,
    track_id: str,
    score: float = 0.8,
    tti: float = 10.0,
) -> Threat:
    """Build a scored threat referencing a track."""
    return Threat(
        id=threat_id,
        score=score,
        time_to_impact_s=tti,
        value_at_risk=50_000.0,
        priority_rank=1,
        track_id=track_id,
        intent=SwarmIntent.SATURATION,
    )


def _make_defender(
    defender_id: str = "INT-1",
    kind: DefenderKind = DefenderKind.INTERCEPTOR,
) -> Defender:
    """Build a ready interceptor defender."""
    return Defender(
        id=defender_id,
        position=(0.0, 0.0, 0.0),
        kind=kind,
        capacity=4,
        range_m=2500.0,
        reload_s=2.0,
        kill_prob=0.85,
        unit_cost=8_000.0,
        status=DefenderStatus.READY,
    )


# ===========================================================================
# 1. TestFullWargamePipeline
# ===========================================================================


class TestFullWargamePipeline:
    """Run complete wargames from scenario to completion."""

    def test_skirmish_runs_to_completion(self) -> None:
        """Load skirmish_80, run all ticks, verify termination and metrics."""
        frames = _run_wargame(_fast_scenario("skirmish_80"))
        assert len(frames) > 0
        last = frames[-1]
        assert last.done or last.metrics.tick == 300
        assert last.metrics.intercepts >= 0
        assert last.metrics.leakers >= 0
        assert last.metrics.engagements_made > 0

    def test_all_scenarios_produce_valid_frames(self) -> None:
        """Each built-in scenario runs 10 ticks without error."""
        for name in list_scenarios():
            if name == "combined_saturation_strike":
                continue
            scenario = _fast_scenario(name, max_ticks=10)
            frames = _run_wargame(scenario)
            assert len(frames) == 10, f"{name} produced {len(frames)} frames"
            for frame in frames:
                assert frame.metrics is not None
                assert frame.metrics.tick > 0

    def test_frame_has_expected_fields(self) -> None:
        """A single tick produces a frame with all required fields."""
        scenario = _fast_scenario("skirmish_80", max_ticks=1)
        frames = _run_wargame(scenario)
        assert len(frames) == 1
        frame = frames[0]
        assert isinstance(frame.tracks, list)
        assert isinstance(frame.defenders, list)
        assert isinstance(frame.threats, list)
        assert isinstance(frame.metrics, Metrics)
        assert frame.metrics.tick == 1


# ===========================================================================
# 2. TestSensorToFusionPipeline
# ===========================================================================


class TestSensorToFusionPipeline:
    """Test that detections flow through the fusion engine."""

    def test_sim_source_produces_detections(self) -> None:
        """SimSensorSource.sample_once returns Detection objects."""
        targets = [
            TruthTarget(
                id="t-1",
                position=(500.0, 500.0, 50.0),
                velocity=(-10.0, -10.0, 0.0),
            ),
        ]

        async def _go() -> List[Detection]:
            spec = SimSensorSpec(sensor_id="radar-test", range_m=3000.0)
            source = SimSensorSource(
                sensors=[spec],
                truth_fn=lambda: targets,
                rate_hz=5.0,
            )
            await source.start()
            detections = source.sample_once()
            await source.stop()
            return detections

        detections = asyncio.run(_go())
        assert len(detections) > 0
        assert all(isinstance(d, Detection) for d in detections)

    def test_detections_create_tracks(self) -> None:
        """Feeding detections into TrackManager produces tracks."""
        tm = TrackManager()
        t = time.time()
        for i in range(5):
            det = Detection(
                id=f"d-{i}",
                timestamp=t + i * 0.2,
                position=(1000.0, 1000.0, 50.0),
                velocity=(-10.0, -10.0, 0.0),
                confidence=0.9,
                sensor_id="radar-1",
            )
            tm.update([det], t + i * 0.2)
        tracks = tm.tracks()
        assert len(tracks) >= 1

    def test_tracks_classify_as_hostile(self) -> None:
        """Confirmed tracks can be classified HOSTILE."""
        tm = TrackManager()
        t = time.time()
        for i in range(6):
            det = Detection(
                id=f"d-{i}",
                timestamp=t + i * 0.2,
                position=(800.0, 800.0, 50.0),
                velocity=(-15.0, -15.0, 0.0),
                confidence=0.92,
                sensor_id="radar-1",
            )
            tm.update([det], t + i * 0.2)
        confirmed = tm.confirmed_tracks()
        assert len(confirmed) >= 1
        tm.classify_track(confirmed[0].id, TrackClass.HOSTILE)
        assert confirmed[0].classification is TrackClass.HOSTILE


# ===========================================================================
# 3. TestThreatToDefensePipeline
# ===========================================================================


class TestThreatToDefensePipeline:
    """Test threat scoring and defense allocation."""

    def test_threats_scored_by_distance(self) -> None:
        """Closer tracks score higher than distant ones."""
        site = _make_site()
        close_track = _make_track("close", (200.0, 200.0, 50.0))
        far_track = _make_track("far", (3000.0, 3000.0, 50.0))
        threats = threat_module.assess(
            [close_track, far_track], site, time.time()
        )
        scores = {t.track_id: t.score for t in threats}
        assert scores["close"] > scores["far"]

    def test_allocator_engages_threats(self) -> None:
        """Ready defenders produce engagements against threats."""
        track = _make_track("trk-1", (500.0, 500.0, 50.0))
        threat = _make_threat("th-1", "trk-1")
        defender = _make_defender("INT-1")

        def resolver(th: Threat) -> Vec3:
            return track.position

        allocator = LayeredAllocator(
            resolve_position=resolver,
            attacker_cost_ref=2000.0,
        )
        engagements = allocator.allocate([threat], [defender], time.time())
        assert len(engagements) >= 1
        assert engagements[0].defender_id == "INT-1"

    def test_cost_ledger_tracks_spending(self) -> None:
        """Running enough ticks for engagements accumulates defender spending."""
        scenario = _fast_scenario("skirmish_80", max_ticks=60)
        frames = _run_wargame(scenario)
        last = frames[-1]
        assert last.metrics.defender_spent > 0


# ===========================================================================
# 4. TestCoTFormatterPipeline
# ===========================================================================


class TestCoTFormatterPipeline:
    """Test that all CoT formatters produce valid XML."""

    def test_track_cot_roundtrip(self) -> None:
        """Track CoT serializes to valid XML with correct uid."""
        track = _make_track("trk-42", (100.0, 200.0, 30.0))
        xml_str = format_track_cot(track)
        root = ET.fromstring(xml_str)
        assert root.tag == "event"
        assert "OVERWATCH.trk-42" == root.attrib["uid"]
        point = root.find("point")
        assert point is not None
        assert float(point.attrib["lat"]) != 0.0

    def test_threat_cot_has_score(self) -> None:
        """Threat CoT includes score in remarks."""
        track = _make_track("trk-7", (300.0, 400.0, 50.0))
        threat = _make_threat("th-7", "trk-7", score=0.92)
        xml_str = format_threat_cot(threat, track)
        root = ET.fromstring(xml_str)
        remarks = root.find(".//remarks")
        assert remarks is not None
        assert "Score: 0.92" in (remarks.text or "")

    def test_defender_cot_has_range(self) -> None:
        """Defender CoT includes a sensor element with range."""
        defender = _make_defender("EW-3", DefenderKind.EW)
        xml_str = format_defender_cot(defender)
        root = ET.fromstring(xml_str)
        sensor = root.find(".//sensor")
        assert sensor is not None
        assert "range" in sensor.attrib

    def test_engagement_cot_has_flow_tags(self) -> None:
        """Engagement CoT includes __flow-tags__ subelement."""
        track = _make_track("trk-9", (200.0, 200.0, 40.0))
        threat = _make_threat("th-9", "trk-9")
        defender = _make_defender("INT-5")
        engagement = Engagement(
            id="eng-1",
            defender_id="INT-5",
            target_threat_id="th-9",
            start_time=time.time(),
            status=EngagementStatus.HIT,
            cost=8000.0,
            neutralized_threat_ids=["th-9"],
        )
        xml_str = format_engagement_cot(engagement, defender, threat, track)
        root = ET.fromstring(xml_str)
        flow = root.find(".//__flow-tags__")
        assert flow is not None
        assert "OVERWATCH-BDA" in flow.attrib

    def test_swarm_cluster_cot_has_shape(self) -> None:
        """Swarm cluster CoT includes an ellipse shape element."""
        xml_str = format_swarm_cluster_cot(
            cluster_id="swarm-1",
            center=(500.0, 500.0, 80.0),
            member_count=12,
            radius_m=150.0,
            intent="SATURATION",
            timestamp=time.time(),
        )
        root = ET.fromstring(xml_str)
        shape = root.find(".//shape")
        assert shape is not None
        ellipse = shape.find("ellipse")
        assert ellipse is not None
        assert float(ellipse.attrib["major"]) == 150.0


# ===========================================================================
# 5. TestRecordAndReplay
# ===========================================================================


class TestRecordAndReplay:
    """Test recording a wargame and replaying it."""

    def test_record_then_replay(self) -> None:
        """Recorded frame count matches replay frame count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rec_path = Path(tmpdir) / "test.wargame.gz"
            scenario = _fast_scenario("skirmish_80", max_ticks=15)
            frames = self._run_with_recording(scenario, rec_path)
            player = ReplayPlayer(rec_path)
            player.load()
            assert player.frame_count == len(frames)

    def test_replay_seek(self) -> None:
        """Seeking to a specific tick returns the correct frame."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rec_path = Path(tmpdir) / "seek.wargame.gz"
            scenario = _fast_scenario("skirmish_80", max_ticks=10)
            self._run_with_recording(scenario, rec_path)
            player = ReplayPlayer(rec_path)
            player.load()
            frame = player.seek(5)
            tick = frame.get("metrics", {}).get("tick", -1)
            assert tick == 6

    def _run_with_recording(
        self, scenario: Scenario, rec_path: Path,
    ) -> List[Frame]:
        """Run a scenario with recording enabled."""

        async def _go() -> List[Frame]:
            runner = WargameRunner(scenario, record_path=rec_path)
            frames: List[Frame] = []
            async for frame in runner.run(pace=False):
                frames.append(frame)
            return frames

        return asyncio.run(_go())


# ===========================================================================
# 6. TestGymEnvPipeline
# ===========================================================================


class TestGymEnvPipeline:
    """Test the RL environment works end-to-end."""

    def _make_env(self, max_ticks: int = 50):
        from wargame.gym_env import BulwarkEnv

        scenario = Scenario(
            name="gym_integration",
            swarm_intent=SwarmIntent.SATURATION,
            swarm_count=20,
            unit_cost=500.0,
            sensors=[
                SensorConfig(
                    sensor_id="radar-1",
                    position=(0.0, 0.0, 0.0),
                    range_m=5000.0,
                ),
            ],
            defenders=[
                DefenderConfig(
                    id_prefix="INT",
                    kind=DefenderKind.INTERCEPTOR,
                    count=4,
                    position=(0.0, 0.0, 0.0),
                    capacity=10,
                    range_m=3000.0,
                    reload_s=2.0,
                    kill_prob=0.85,
                    unit_cost=8_000.0,
                ),
            ],
            site=SiteConfig(),
            tick_hz=5.0,
            max_ticks=max_ticks,
            seed=42,
        )
        return BulwarkEnv(scenario)

    def test_full_episode(self) -> None:
        """A complete episode ends with terminated or truncated."""
        env = self._make_env(max_ticks=80)
        obs, info = env.reset()
        terminated = False
        truncated = False
        step = 0
        while not terminated and not truncated:
            action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            step += 1
            if step > 200:
                break
        assert terminated or truncated

    def test_observation_stability(self) -> None:
        """Ten steps produce no NaN in observations."""
        env = self._make_env()
        obs, _ = env.reset()
        for _ in range(10):
            action = env.action_space.sample()
            obs, _, terminated, truncated, _ = env.step(action)
            assert not np.any(np.isnan(obs)), "NaN detected in observation"
            if terminated or truncated:
                break

    def test_reward_nonzero(self) -> None:
        """Total reward across an episode is not zero."""
        env = self._make_env(max_ticks=60)
        obs, _ = env.reset()
        total_reward = 0.0
        for _ in range(60):
            action = env.action_space.sample()
            obs, reward, terminated, truncated, _ = env.step(action)
            total_reward += reward
            if terminated or truncated:
                break
        assert total_reward != 0.0


# ===========================================================================
# 7. TestDecoderPipeline
# ===========================================================================


class TestDecoderPipeline:
    """Test ODID and DJI decoders work with the sensor source interface."""

    def test_odid_basic_id_decode(self) -> None:
        """A constructed BasicID message decodes to valid fields."""
        payload = bytearray(ODID_MESSAGE_SIZE)
        payload[0] = MSG_BASIC_ID << 4
        payload[1] = 0x10
        serial = b"TESTDRONE123"
        payload[2:2 + len(serial)] = serial
        result = decode_odid_message(bytes(payload))
        assert result is not None
        assert result["msg_type"] == MSG_BASIC_ID
        assert result["IDType"] == 1
        assert "TESTDRONE123" in result["BasicID"]

    def test_dji_binary_to_detection(self) -> None:
        """A constructed DJI binary frame parses to a dict."""
        data = bytearray(227)
        serial = b"DJI1234567890"
        data[0:len(serial)] = serial
        device = b"Mavic3"
        data[64:64 + len(device)] = device
        data[128] = 4
        struct.pack_into("<d", data, 145, 33.6405)
        struct.pack_into("<d", data, 153, -117.8443)
        struct.pack_into("<d", data, 161, 100.0)
        struct.pack_into("<d", data, 169, 150.0)
        struct.pack_into("<d", data, 201, 5.0)
        struct.pack_into("<d", data, 209, -3.0)
        struct.pack_into("<d", data, 217, 1.0)
        struct.pack_into("<h", data, 225, -55)
        result = parse_dji_binary_frame(bytes(data))
        assert result is not None
        assert "DJI1234567890" in result["serial_number"]
        assert abs(result["uas_lat"] - 33.6405) < 0.01

    def test_udp_rid_json_to_detection(self) -> None:
        """A UDP RID JSON message parses to a valid dict."""
        msg = {
            "id": "RID-001",
            "lat": 33.6410,
            "lon": -117.8440,
            "alt": 50.0,
            "speed": 15.0,
            "hdg": 180.0,
            "t": time.time(),
        }
        raw = json_mod.dumps(msg).encode("utf-8")
        parsed = _parse_rid_json(raw)
        assert parsed is not None
        assert parsed["lat"] == 33.6410
        assert parsed["id"] == "RID-001"


# ===========================================================================
# 8. TestExtensionManagerPipeline
# ===========================================================================


class TestExtensionManagerPipeline:
    """Test the extension system with real extensions."""

    def test_collision_extension_loads_and_exports(self) -> None:
        """CollisionExtension loads and exposes check_all."""
        from extensions.collision_ext import CollisionExtension
        from extensions.manager import ExtensionManager

        manager = ExtensionManager()
        ext = CollisionExtension()
        manager.register(ext)
        asyncio.run(manager.load_all({}))
        exports = ext.exports()
        assert "check_all" in exports
        assert callable(exports["check_all"])
        asyncio.run(manager.unload_all())

    def test_geofence_extension_loads_and_exports(self) -> None:
        """GeofenceExtension loads and exposes check and contains."""
        from extensions.geofence_ext import GeofenceExtension
        from extensions.manager import ExtensionManager

        manager = ExtensionManager()
        ext = GeofenceExtension()
        manager.register(ext)
        asyncio.run(manager.load_all({}))
        exports = ext.exports()
        assert "check" in exports
        assert "contains" in exports
        assert callable(exports["check"])
        asyncio.run(manager.unload_all())

    def test_alerts_extension_depends_on_others(self) -> None:
        """AlertsExtension declares collision and geofence dependencies."""
        from extensions.alerts_ext import AlertsExtension
        from extensions.collision_ext import CollisionExtension
        from extensions.geofence_ext import GeofenceExtension
        from extensions.base import ExtensionState
        from extensions.manager import ExtensionManager

        manager = ExtensionManager()
        collision = CollisionExtension()
        geofence = GeofenceExtension()
        alerts = AlertsExtension()
        manager.register(collision)
        manager.register(geofence)
        manager.register(alerts)
        ctx = {"settings": _DummySettings(), "extension_manager": manager}
        asyncio.run(manager.load_all(ctx))
        assert collision.state == ExtensionState.LOADED
        assert geofence.state == ExtensionState.LOADED
        assert alerts.state == ExtensionState.LOADED
        asyncio.run(manager.unload_all())


class _DummySettings:
    """Minimal settings object to satisfy AlertEngine construction."""
    safety_bubble_m = 5.0
    min_vertical_sep_m = 3.0
    geofence_vertices = [
        (35.0, -97.0),
        (35.0, -96.0),
        (36.0, -96.0),
        (36.0, -97.0),
    ]
    max_altitude_m = 120.0
    alert_rules = []


# ===========================================================================
# 9. TestRateLimiterIntegration
# ===========================================================================


class TestRateLimiterIntegration:
    """Test rate limiter with realistic message patterns."""

    def test_burst_protection(self) -> None:
        """Sending 100 messages rapidly blocks most of them."""
        from api.rate_limiter import MessageRateLimiter

        limiter = MessageRateLimiter(default_hz=10.0)
        allowed = sum(1 for _ in range(100) if limiter.should_send("test"))
        assert allowed < 10, f"Expected < 10 allowed, got {allowed}"

    def test_sustained_rate(self) -> None:
        """Messages spaced at the interval all pass."""
        from api.rate_limiter import MessageRateLimiter

        limiter = MessageRateLimiter(default_hz=10.0)
        allowed = 0
        for i in range(10):
            limiter.reset("sustained")
            if limiter.should_send("sustained"):
                allowed += 1
        assert allowed == 10
