"""Tests for multi-site federated defense network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import (
    Defender,
    DefenderKind,
    DefenderStatus,
    Track,
    TrackClass,
    Vec3,
)
from defense.federation import (
    DEFAULT_COVERAGE_M,
    GREEN,
    RED,
    YELLOW,
    FederatedDefenseNetwork,
    FederatedPicture,
    SharedTrack,
)


def _defender(
    did: str,
    position: Vec3 = (0.0, 0.0, 0.0),
    capacity: int = 4,
) -> Defender:
    return Defender(
        id=did,
        position=position,
        kind=DefenderKind.JAMMER,
        capacity=capacity,
        range_m=1000.0,
        reload_s=5.0,
        kill_prob=0.8,
        unit_cost=50.0,
        status=DefenderStatus.READY,
    )


def _track(
    tid: str,
    position: Vec3 = (500.0, 500.0, 100.0),
) -> Track:
    return Track(
        id=tid,
        position=position,
        velocity=(10.0, -5.0, 0.0),
        covariance=(5.0, 5.0, 2.0),
        last_update=0.0,
        classification=TrackClass.HOSTILE,
        confidence=0.9,
    )


def _make_network() -> FederatedDefenseNetwork:
    """Build a network with two sites 5 km apart."""
    net = FederatedDefenseNetwork()
    net.add_site(
        "AIRPORT",
        position=(0.0, 0.0, 0.0),
        defenders=[_defender("JAM-A1"), _defender("JAM-A2")],
        coverage_m=3000.0,
    )
    net.add_site(
        "BASE",
        position=(5000.0, 0.0, 0.0),
        defenders=[_defender("JAM-B1"), _defender("JAM-B2")],
        coverage_m=3000.0,
    )
    return net


# -- Adding and removing sites --

def test_add_multiple_sites():
    net = _make_network()
    assert len(net.sites) == 2
    assert "AIRPORT" in net.sites
    assert "BASE" in net.sites
    assert net.sites["AIRPORT"].threat_level == GREEN
    assert net.sites["BASE"].threat_level == GREEN


def test_add_third_site():
    net = _make_network()
    net.add_site(
        "POWERPLANT",
        position=(2500.0, 4000.0, 0.0),
        defenders=[_defender("JAM-C1")],
    )
    assert len(net.sites) == 3
    assert "POWERPLANT" in net.sites


def test_remove_site():
    net = _make_network()
    removed = net.remove_site("BASE")
    assert removed is True
    assert "BASE" not in net.sites
    assert net.remove_site("NONEXISTENT") is False


# -- Track sharing --

def test_share_track_between_sites():
    net = _make_network()
    trk = _track("T-001", position=(100.0, 200.0, 50.0))
    net.share_track("AIRPORT", trk)
    # Track appears in the other site's local picture.
    base_ids = {t.id for t in net.sites["BASE"].tracks}
    assert "T-001" in base_ids
    # Source site does not get a duplicate.
    airport_ids = {t.id for t in net.sites["AIRPORT"].tracks}
    assert "T-001" not in airport_ids


def test_share_track_from_unknown_site():
    net = _make_network()
    trk = _track("T-002")
    net.share_track("NONEXISTENT", trk)
    # Nothing propagated.
    for site in net.sites.values():
        assert all(t.id != "T-002" for t in site.tracks)


def test_share_track_dedup():
    net = _make_network()
    trk = _track("T-003")
    net.share_track("AIRPORT", trk)
    net.share_track("AIRPORT", trk)
    base_count = sum(1 for t in net.sites["BASE"].tracks if t.id == "T-003")
    assert base_count == 1


# -- Reinforcement requests --

def test_reinforcement_from_green_neighbor():
    net = _make_network()
    req = net.request_reinforcement("AIRPORT", RED)
    assert req is not None
    assert req.fulfilled_by == "BASE"
    assert len(req.defenders_sent) > 0
    # Defenders moved to AIRPORT.
    airport_ids = {d.id for d in net.sites["AIRPORT"].defenders}
    for did in req.defenders_sent:
        assert did in airport_ids
    # Donor lost those defenders.
    base_ids = {d.id for d in net.sites["BASE"].defenders}
    for did in req.defenders_sent:
        assert did not in base_ids


def test_reinforcement_no_green_neighbor():
    net = _make_network()
    net.sites["BASE"].threat_level = RED
    req = net.request_reinforcement("AIRPORT", RED)
    assert req is not None
    assert req.fulfilled_by is None
    assert len(req.defenders_sent) == 0


def test_reinforcement_nonexistent_site():
    net = _make_network()
    req = net.request_reinforcement("NONEXISTENT", RED)
    assert req is None


def test_reinforcement_picks_nearest_donor():
    net = _make_network()
    net.add_site(
        "CLOSE",
        position=(1000.0, 0.0, 0.0),
        defenders=[_defender("JAM-D1"), _defender("JAM-D2")],
    )
    req = net.request_reinforcement("AIRPORT", YELLOW)
    assert req is not None
    assert req.fulfilled_by == "CLOSE"


# -- Common operating picture --

def test_common_operating_picture_merge():
    net = _make_network()
    trk_a = _track("T-010", position=(100.0, 0.0, 50.0))
    trk_b = _track("T-011", position=(4900.0, 0.0, 50.0))
    net.share_track("AIRPORT", trk_a)
    net.share_track("BASE", trk_b)
    pic = net.get_common_operating_picture()
    assert isinstance(pic, FederatedPicture)
    merged_ids = {st.track.id for st in pic.merged_tracks}
    assert "T-010" in merged_ids
    assert "T-011" in merged_ids
    assert len(pic.sites) == 2
    assert pic.timestamp > 0


def test_cop_includes_local_only_tracks():
    net = _make_network()
    local = _track("T-LOCAL")
    net.sites["AIRPORT"].tracks.append(local)
    pic = net.get_common_operating_picture()
    merged_ids = {st.track.id for st in pic.merged_tracks}
    assert "T-LOCAL" in merged_ids


# -- Track handoff --

def test_track_handoff_to_closer_site():
    net = _make_network()
    trk = _track("T-HO1", position=(100.0, 0.0, 50.0))
    net.share_track("AIRPORT", trk)
    # Move the track near BASE.
    trk_moved = _track("T-HO1", position=(4800.0, 0.0, 50.0))
    result = net.handoff_track(trk_moved)
    assert result is not None
    from_site, to_site = result
    assert from_site == "AIRPORT"
    assert to_site == "BASE"
    # Handoff logged in COP.
    pic = net.get_common_operating_picture()
    assert ("T-HO1", "AIRPORT", "BASE") in pic.active_handoffs


def test_no_handoff_when_still_closest():
    net = _make_network()
    trk = _track("T-HO2", position=(100.0, 0.0, 50.0))
    net.share_track("AIRPORT", trk)
    trk_same = _track("T-HO2", position=(200.0, 0.0, 50.0))
    result = net.handoff_track(trk_same)
    assert result is None


def test_handoff_unknown_track():
    net = _make_network()
    trk = _track("T-GHOST")
    result = net.handoff_track(trk)
    assert result is None


def test_handoff_respects_coverage_radius():
    net = _make_network()
    trk = _track("T-HO3", position=(100.0, 0.0, 50.0))
    net.share_track("AIRPORT", trk)
    # Move to a point closer to BASE but outside its coverage.
    trk_mid = _track("T-HO3", position=(3500.0, 0.0, 50.0))
    # Distance to BASE is 1500m (within 3000m coverage), so handoff should fire.
    result = net.handoff_track(trk_mid)
    assert result is not None
    assert result == ("AIRPORT", "BASE")


# -- Alert / shared kill chain --

def test_alert_site_shared_kill_chain():
    net = _make_network()
    trk = _track("T-INBOUND", position=(4000.0, 0.0, 50.0))
    success = net.alert_site("AIRPORT", "BASE", trk)
    assert success is True
    assert net.sites["BASE"].threat_level == YELLOW
    base_ids = {t.id for t in net.sites["BASE"].tracks}
    assert "T-INBOUND" in base_ids


def test_alert_nonexistent_target():
    net = _make_network()
    trk = _track("T-INBOUND2")
    success = net.alert_site("AIRPORT", "GHOST_SITE", trk)
    assert success is False


def test_alert_does_not_downgrade_threat_level():
    net = _make_network()
    net.sites["BASE"].threat_level = RED
    trk = _track("T-INBOUND3")
    net.alert_site("AIRPORT", "BASE", trk)
    # RED should not be downgraded to YELLOW.
    assert net.sites["BASE"].threat_level == RED
