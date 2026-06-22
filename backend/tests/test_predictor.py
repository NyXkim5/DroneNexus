"""Tests for the predictive threat modeling module.

Covers linear extrapolation, target identification, swarm convergence
detection, threat corridor detection, and early warning generation.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Site, Swarm, SwarmIntent, Track, TrackClass, Vec3
from threat.predictor import (
    PREDICTION_STEP_S,
    ThreatCorridor,
    ThreatPrediction,
    ThreatPredictor,
    _circular_mean,
    _circular_spread,
    _normalize,
)


def _track(
    tid: str,
    pos: Vec3,
    vel: Vec3,
    cls: TrackClass = TrackClass.HOSTILE,
    cov: Vec3 = (5.0, 5.0, 5.0),
) -> Track:
    return Track(
        id=tid,
        position=pos,
        velocity=vel,
        covariance=cov,
        last_update=100.0,
        classification=cls,
    )


def _site(sid: str = "SITE-A", pos: Vec3 = (0.0, 0.0, 0.0)) -> Site:
    return Site(id=sid, position=pos, protected_assets=["HQ"], value=100_000.0)


# -- Linear extrapolation --

class TestLinearExtrapolation:
    def test_positions_at_five_second_intervals(self):
        """Predicted positions should be velocity*dt from the start."""
        t = _track("t1", (1000.0, 0.0, 50.0), (-10.0, 0.0, 0.0))
        pred = ThreatPredictor()
        results = pred.predict([t], [_site()], horizon_s=15)
        assert len(results) == 1
        p = results[0]
        assert len(p.predicted_positions) == 3
        assert len(p.predicted_times) == 3
        assert p.predicted_times == [5.0, 10.0, 15.0]
        assert p.predicted_positions[0][0] == pytest.approx(950.0, abs=0.1)
        assert p.predicted_positions[1][0] == pytest.approx(900.0, abs=0.1)
        assert p.predicted_positions[2][0] == pytest.approx(850.0, abs=0.1)

    def test_stationary_track_stays_put(self):
        t = _track("t1", (500.0, 500.0, 50.0), (0.0, 0.0, 0.0))
        results = ThreatPredictor().predict([t], [_site()], horizon_s=10)
        for pos in results[0].predicted_positions:
            assert pos[0] == pytest.approx(500.0)
            assert pos[1] == pytest.approx(500.0)

    def test_friendly_tracks_skipped(self):
        t = _track("f1", (100.0, 0.0, 50.0), (-5.0, 0.0, 0.0), TrackClass.FRIENDLY)
        results = ThreatPredictor().predict([t], [_site()], horizon_s=10)
        assert len(results) == 0


# -- Target identification --

class TestTargetIdentification:
    def test_track_heading_toward_site(self):
        """A track closing on the site should identify it as likely target."""
        site = _site("ALPHA", (0.0, 0.0, 0.0))
        t = _track("t1", (500.0, 0.0, 50.0), (-20.0, 0.0, 0.0))
        results = ThreatPredictor().predict([t], [site], horizon_s=30)
        assert results[0].likely_target == "ALPHA"
        assert results[0].impact_probability > 0.3
        assert results[0].estimated_time_to_target < 30.0

    def test_track_heading_away_gets_no_target(self):
        site = _site("ALPHA", (0.0, 0.0, 0.0))
        t = _track("t1", (500.0, 0.0, 50.0), (20.0, 0.0, 0.0))
        results = ThreatPredictor().predict([t], [site], horizon_s=30)
        assert results[0].likely_target is None
        assert results[0].impact_probability == 0.0

    def test_closest_site_selected(self):
        site_a = _site("FAR", (0.0, 2000.0, 0.0))
        site_b = _site("NEAR", (0.0, 0.0, 0.0))
        t = _track("t1", (300.0, 0.0, 50.0), (-15.0, 0.0, 0.0))
        results = ThreatPredictor().predict([t], [site_a, site_b], horizon_s=30)
        assert results[0].likely_target == "NEAR"

    def test_historical_boost(self):
        """Sites with attack history get a probability boost."""
        site = _site("BRAVO", (0.0, 0.0, 0.0))
        t = _track("t1", (600.0, 0.0, 50.0), (-10.0, 0.0, 0.0))
        p_no_hist = ThreatPredictor()
        r1 = p_no_hist.predict([t], [site], horizon_s=60)
        p_hist = ThreatPredictor(history={"BRAVO": 10})
        r2 = p_hist.predict([t], [site], horizon_s=60)
        assert r2[0].impact_probability >= r1[0].impact_probability


# -- Swarm convergence --

class TestSwarmConvergence:
    def test_swarm_blends_velocity(self):
        """Tracks in a swarm should use blended velocity for prediction."""
        site = _site("ALPHA", (0.0, 0.0, 0.0))
        t1 = _track("s1", (500.0, 10.0, 50.0), (-20.0, 5.0, 0.0))
        t2 = _track("s2", (510.0, -10.0, 50.0), (-20.0, -5.0, 0.0))
        swarm = Swarm(
            id="swarm-s1-s2",
            member_track_ids=["s1", "s2"],
            centroid=(505.0, 0.0, 50.0),
            formation="UNKNOWN",
            intent=SwarmIntent.SATURATION,
            size=2,
            first_seen=90.0,
        )
        pred = ThreatPredictor()
        results = pred.predict([t1, t2], [site], horizon_s=10, swarms=[swarm])
        assert len(results) == 2
        p1 = next(r for r in results if r.track_id == "s1")
        p2 = next(r for r in results if r.track_id == "s2")
        # Both should converge toward site (x decreasing)
        assert p1.predicted_positions[-1][0] < 500.0
        assert p2.predicted_positions[-1][0] < 510.0

    def test_swarm_boosts_confidence(self):
        site = _site("ALPHA", (0.0, 0.0, 0.0))
        lone = _track("lone", (500.0, 0.0, 50.0), (-10.0, 0.0, 0.0))
        s1 = _track("s1", (500.0, 10.0, 50.0), (-10.0, 0.0, 0.0))
        s2 = _track("s2", (510.0, -10.0, 50.0), (-10.0, 0.0, 0.0))
        swarm = Swarm(
            id="swarm-s1-s2",
            member_track_ids=["s1", "s2"],
            centroid=(505.0, 0.0, 50.0),
            formation="UNKNOWN",
            intent=SwarmIntent.PROBE,
            size=2,
            first_seen=90.0,
        )
        r_lone = ThreatPredictor().predict([lone], [site], horizon_s=10)
        r_swarm = ThreatPredictor().predict(
            [s1, s2], [site], horizon_s=10, swarms=[swarm],
        )
        swarmed = next(r for r in r_swarm if r.track_id == "s1")
        assert swarmed.confidence >= r_lone[0].confidence


# -- Corridor detection --

class TestCorridorDetection:
    def test_detects_convergence_corridor(self):
        site = _site("HQ", (0.0, 0.0, 0.0))
        tracks = [
            _track(f"c{i}", (500.0 + i * 10, 500.0, 50.0), (-15.0, -15.0, 0.0))
            for i in range(5)
        ]
        corridors = ThreatPredictor().detect_corridors(tracks, [site])
        assert len(corridors) == 1
        assert corridors[0].target_site == "HQ"
        assert corridors[0].estimated_count == 5
        assert 0 < corridors[0].origin_bearing < 360

    def test_no_corridor_when_too_few_tracks(self):
        site = _site("HQ", (0.0, 0.0, 0.0))
        tracks = [_track("c1", (500.0, 500.0, 50.0), (-10.0, -10.0, 0.0))]
        corridors = ThreatPredictor().detect_corridors(tracks, [site])
        assert len(corridors) == 0

    def test_corridor_bearing_northeast(self):
        site = _site("HQ", (0.0, 0.0, 0.0))
        # Tracks northeast of site, heading southwest
        tracks = [
            _track(f"ne{i}", (300.0 + i * 5, 300.0 + i * 5, 50.0), (-10.0, -10.0, 0.0))
            for i in range(4)
        ]
        corridors = ThreatPredictor().detect_corridors(tracks, [site])
        assert len(corridors) == 1
        # Bearing from site to northeast tracks should be ~45 degrees
        assert 30 < corridors[0].origin_bearing < 60


# -- Early warnings --

class TestEarlyWarnings:
    def test_corridor_warning_text(self):
        corridor = ThreatCorridor(
            origin_bearing=45.0,
            width_deg=20.0,
            depth_m=800.0,
            estimated_count=12,
            target_site="SITE-ALPHA",
        )
        warnings = ThreatPredictor().get_early_warnings([], corridors=[corridor])
        assert len(warnings) == 1
        assert "12 tracks" in warnings[0]
        assert "SITE-ALPHA" in warnings[0]
        assert "045" in warnings[0]

    def test_saturation_alert(self):
        preds = [
            ThreatPrediction(
                track_id=f"t{i}",
                predicted_positions=[(0.0, 0.0, 0.0)],
                predicted_times=[5.0],
                likely_target="HQ",
                impact_probability=0.9,
                estimated_time_to_target=20.0,
                confidence=0.8,
                approach_vector=(-1.0, 0.0, 0.0),
            )
            for i in range(6)
        ]
        warnings = ThreatPredictor().get_early_warnings(preds)
        sat_alerts = [w for w in warnings if "SATURATION" in w]
        assert len(sat_alerts) == 1
        assert "6 tracks" in sat_alerts[0]

    def test_critical_imminent_warning(self):
        pred = ThreatPrediction(
            track_id="t-fast",
            predicted_positions=[(10.0, 0.0, 0.0)],
            predicted_times=[5.0],
            likely_target="BASE",
            impact_probability=0.95,
            estimated_time_to_target=8.0,
            confidence=0.9,
            approach_vector=(-1.0, 0.0, 0.0),
        )
        warnings = ThreatPredictor().get_early_warnings([pred])
        crit = [w for w in warnings if "CRITICAL" in w]
        assert len(crit) == 1
        assert "t-fast" in crit[0]
        assert "BASE" in crit[0]

    def test_no_warnings_for_distant_tracks(self):
        pred = ThreatPrediction(
            track_id="t-far",
            predicted_positions=[(5000.0, 0.0, 0.0)],
            predicted_times=[60.0],
            likely_target="HQ",
            impact_probability=0.2,
            estimated_time_to_target=120.0,
            confidence=0.5,
            approach_vector=(-1.0, 0.0, 0.0),
        )
        warnings = ThreatPredictor().get_early_warnings([pred])
        assert len(warnings) == 0


# -- Utility function tests --

class TestUtilities:
    def test_normalize(self):
        v = _normalize((3.0, 4.0, 0.0))
        assert v[0] == pytest.approx(0.6, abs=0.01)
        assert v[1] == pytest.approx(0.8, abs=0.01)

    def test_normalize_zero(self):
        assert _normalize((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)

    def test_circular_mean_simple(self):
        assert _circular_mean([0.0, 90.0]) == pytest.approx(45.0, abs=1.0)

    def test_circular_mean_wrap(self):
        mean = _circular_mean([350.0, 10.0])
        assert mean == pytest.approx(0.0, abs=1.0) or mean == pytest.approx(360.0, abs=1.0)

    def test_circular_spread(self):
        spread = _circular_spread([10.0, 20.0, 30.0])
        assert spread == pytest.approx(20.0, abs=0.1)


# Allow running with pytest
import pytest
