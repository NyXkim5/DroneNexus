"""
Tests for the swarm-aware threat classifier and prioritizer.

Scenarios are labeled by the intent or ranking property they assert. The site
sits at the ENU origin. Tracks approach from the south (negative y) moving north
(positive y velocity) toward the site, which is the natural attack axis here.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Site, Track, TrackClass, SwarmIntent, Vec3

from threat.classifier import assess, detect_swarms
from threat.clustering import cluster_tracks
from threat.intent import infer_intent, closing_speed_to_site
from threat.scoring import time_to_impact


SITE = Site(id="site-1", position=(0.0, 0.0, 0.0), protected_assets=["cmd"], value=1_000_000.0)


def make_track(
    track_id: str,
    position: Vec3,
    velocity: Vec3,
    classification: TrackClass = TrackClass.HOSTILE,
) -> Track:
    """Build a hostile track at a position with a velocity for tests."""
    return Track(
        id=track_id,
        position=position,
        velocity=velocity,
        covariance=(2.0, 2.0, 2.0),
        last_update=100.0,
        age=10.0,
        classification=classification,
        confidence=0.9,
    )


# ---- closing speed and time to impact ----

def test_closing_speed_positive_when_moving_toward_site():
    # South of site moving north closes on it.
    t = make_track("a", (0.0, -500.0, 50.0), (0.0, 20.0, 0.0))
    assert closing_speed_to_site(t, SITE) > 19.9


def test_closing_speed_negative_when_receding():
    t = make_track("a", (0.0, -500.0, 50.0), (0.0, -20.0, 0.0))
    assert closing_speed_to_site(t, SITE) < 0.0


def test_time_to_impact_none_when_not_closing():
    t = make_track("a", (0.0, -500.0, 50.0), (0.0, -20.0, 0.0))
    assert time_to_impact(t, SITE) is None


def test_time_to_impact_matches_range_over_speed():
    t = make_track("a", (0.0, -400.0, 0.0), (0.0, 20.0, 0.0))
    tti = time_to_impact(t, SITE)
    assert tti is not None
    assert abs(tti - 20.0) < 0.1


# ---- clustering ----

def test_clustering_groups_nearby_tracks():
    tracks = [
        make_track("a", (0.0, -300.0, 50.0), (0.0, 10.0, 0.0)),
        make_track("b", (20.0, -310.0, 50.0), (0.0, 10.0, 0.0)),
        make_track("c", (-15.0, -305.0, 50.0), (0.0, 10.0, 0.0)),
    ]
    swarms = cluster_tracks(tracks, timestamp=100.0)
    assert len(swarms) == 1
    assert swarms[0].size == 3


def test_clustering_splits_far_apart_tracks():
    tracks = [
        make_track("a", (0.0, -300.0, 50.0), (0.0, 10.0, 0.0)),
        make_track("b", (10.0, -305.0, 50.0), (0.0, 10.0, 0.0)),
        make_track("c", (2000.0, -300.0, 50.0), (0.0, 10.0, 0.0)),
        make_track("d", (2010.0, -305.0, 50.0), (0.0, 10.0, 0.0)),
    ]
    swarms = cluster_tracks(tracks, timestamp=100.0)
    assert len(swarms) == 2


def test_singletons_do_not_form_a_swarm():
    tracks = [make_track("lone", (0.0, -300.0, 50.0), (0.0, 10.0, 0.0))]
    assert cluster_tracks(tracks, timestamp=100.0) == []


# ---- intent classification ----

def test_intent_saturation_large_tight_mass_closing():
    # Seven tightly packed drones all rushing the site together.
    tracks = []
    for i in range(7):
        x = -40.0 + i * 12.0
        tracks.append(make_track(f"s{i}", (x, -400.0, 50.0), (0.0, 25.0, 0.0)))
    swarms = detect_swarms(tracks, SITE, timestamp=100.0)
    assert len(swarms) == 1
    assert swarms[0].intent == SwarmIntent.SATURATION


def test_intent_waves_separated_range_bands():
    # Two ranks closing from distinct distance bands.
    tracks = [
        make_track("w1", (-10.0, -200.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("w2", (10.0, -210.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("w3", (-10.0, -400.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("w4", (10.0, -410.0, 50.0), (0.0, 20.0, 0.0)),
    ]
    # Use a wide cluster radius so both bands group into one swarm.
    swarms = detect_swarms(tracks, SITE, timestamp=100.0, radius_m=400.0)
    assert len(swarms) == 1
    assert swarms[0].intent == SwarmIntent.WAVES


def test_intent_decoy_loiterers_with_a_few_committing():
    # Three loiter, one commits fast. Most are not closing.
    tracks = [
        make_track("d1", (-20.0, -300.0, 50.0), (5.0, 0.0, 0.0)),
        make_track("d2", (20.0, -305.0, 50.0), (-5.0, 0.0, 0.0)),
        make_track("d3", (0.0, -310.0, 50.0), (3.0, 0.0, 0.0)),
        make_track("d4", (5.0, -300.0, 50.0), (0.0, 30.0, 0.0)),
    ]
    swarms = detect_swarms(tracks, SITE, timestamp=100.0)
    assert len(swarms) == 1
    assert swarms[0].intent == SwarmIntent.DECOY


def test_intent_probe_small_group_closing_slowly():
    # A pair creeping toward the site to scout.
    tracks = [
        make_track("p1", (-10.0, -300.0, 50.0), (0.0, 3.0, 0.0)),
        make_track("p2", (10.0, -305.0, 50.0), (0.0, 3.0, 0.0)),
    ]
    swarms = detect_swarms(tracks, SITE, timestamp=100.0)
    assert len(swarms) == 1
    assert swarms[0].intent == SwarmIntent.PROBE


# ---- ranking ----

def test_assess_skips_friendly_and_unknown():
    tracks = [
        make_track("hostile", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("friend", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0), TrackClass.FRIENDLY),
        make_track("blip", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0), TrackClass.UNKNOWN),
    ]
    threats = assess(tracks, SITE, timestamp=100.0)
    assert len(threats) == 1
    assert threats[0].track_id == "hostile"


def test_assess_empty_when_no_hostiles():
    tracks = [make_track("friend", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0), TrackClass.FRIENDLY)]
    assert assess(tracks, SITE, timestamp=100.0) == []


def test_closer_threat_outranks_farther_one():
    # Same speed, both closing straight up the y axis. The nearer ranks first.
    near = make_track("near", (0.0, -100.0, 50.0), (0.0, 20.0, 0.0))
    far = make_track("far", (3000.0, -3000.0, 50.0), (0.0, 20.0, 0.0))
    threats = assess([near, far], SITE, timestamp=100.0)
    ranks = {t.track_id: t.priority_rank for t in threats}
    assert ranks["near"] < ranks["far"]


def test_faster_threat_outranks_slower_one_at_same_range():
    # Both 500m due south closing straight north. Faster ranks first.
    fast = make_track("fast", (0.0, -500.0, 50.0), (0.0, 40.0, 0.0))
    slow = make_track("slow", (3000.0, -500.0, 50.0), (0.0, 8.0, 0.0))
    threats = assess([fast, slow], SITE, timestamp=100.0)
    ranks = {t.track_id: t.priority_rank for t in threats}
    assert ranks["fast"] < ranks["slow"]


def test_ranks_are_dense_and_one_based():
    tracks = [
        make_track("a", (0.0, -100.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("b", (3000.0, -500.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("c", (-3000.0, -900.0, 50.0), (0.0, 20.0, 0.0)),
    ]
    threats = assess(tracks, SITE, timestamp=100.0)
    ranks = sorted(t.priority_rank for t in threats)
    assert ranks == [1, 2, 3]


def test_clustered_threats_keep_track_id_and_carry_swarm_context():
    tracks = [
        make_track("a", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("b", (20.0, -310.0, 50.0), (0.0, 20.0, 0.0)),
        make_track("c", (-15.0, -305.0, 50.0), (0.0, 20.0, 0.0)),
    ]
    threats = assess(tracks, SITE, timestamp=100.0)
    # Each airframe is its own threat so an effector can target it directly.
    assert len(threats) == 3
    # Every threat keeps its track_id and carries the swarm as context.
    assert all(t.track_id is not None for t in threats)
    assert all(t.swarm_id is not None for t in threats)
    assert len({t.swarm_id for t in threats}) == 1


def test_swarm_outranks_a_distant_lone_track():
    # A committed swarm near the site beats a far lone drone.
    swarm = [
        make_track("s1", (0.0, -150.0, 50.0), (0.0, 25.0, 0.0)),
        make_track("s2", (20.0, -160.0, 50.0), (0.0, 25.0, 0.0)),
        make_track("s3", (-15.0, -155.0, 50.0), (0.0, 25.0, 0.0)),
    ]
    lone = make_track("lone", (0.0, -2500.0, 50.0), (0.0, 5.0, 0.0))
    threats = assess(swarm + [lone], SITE, timestamp=100.0)
    top = threats[0]
    assert top.swarm_id is not None
    assert top.priority_rank == 1


# ---- uncertainty-aware scoring ----

def test_confident_track_outranks_an_uncertain_twin():
    # Two identical closing threats; the one with tighter covariance must rank
    # higher because the defense is more certain of it.
    tight = make_track("tight", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0))
    loose = make_track("loose", (0.0, -300.0, 50.0), (0.0, 20.0, 0.0))
    tight.covariance = (3.0, 3.0, 2.0)
    loose.covariance = (250.0, 250.0, 50.0)
    threats = assess([tight, loose], SITE, timestamp=100.0)
    ranks = {t.track_id: t.priority_rank for t in threats}
    assert ranks["tight"] < ranks["loose"]
    scores = {t.track_id: t.score for t in threats}
    assert scores["tight"] > scores["loose"]
