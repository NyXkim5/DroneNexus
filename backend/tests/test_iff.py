"""
Tests for IFF system and airspace deconfliction.

Covers:
- Registered drone returns FRIENDLY via transponder.
- Unregistered track returns UNKNOWN.
- Track with HOSTILE TrackClass returns HOSTILE.
- No friendlies nearby -> engagement safe.
- Friendly within blast radius -> engagement blocked.
- Friendly between effector and target -> line-of-fire blocked.
- Nearest friendly distance computation.
- Friendlies-in-radius count.
- Zone add / presence.
- Altitude deconfliction (friendly at different altitude is safe).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csontology import Track, TrackClass, Vec3
from defense.iff import (
    AirspaceDeconflictor,
    DeconflictionZone,
    IFFMode,
    IFFSystem,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track(
    track_id: str,
    classification: TrackClass = TrackClass.UNKNOWN,
    confidence: float = 0.5,
    velocity: Vec3 = (0.0, 0.0, 0.0),
    position: Vec3 = (100.0, 100.0, 50.0),
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


# ---------------------------------------------------------------------------
# IFFSystem tests
# ---------------------------------------------------------------------------

def test_register_and_interrogate_friendly() -> None:
    """Registered drone with a transponder code returns FRIENDLY at high confidence."""
    iff = IFFSystem()
    iff.register_friendly("alpha-1", transponder_code="IFF-7742")

    resp = iff.interrogate(_track("alpha-1"))

    assert resp.mode == IFFMode.FRIENDLY
    assert resp.method == "transponder"
    assert resp.confidence >= 0.9
    assert resp.track_id == "alpha-1"


def test_unknown_track_returns_unknown() -> None:
    """An unregistered track with no hostile classification defaults to UNKNOWN."""
    iff = IFFSystem()
    resp = iff.interrogate(_track("ghost-99"))

    assert resp.mode == IFFMode.UNKNOWN
    assert resp.method == "default"
    assert resp.confidence == 0.0


def test_hostile_classification() -> None:
    """A track classified HOSTILE by the fusion engine returns IFFMode.HOSTILE."""
    iff = IFFSystem()
    resp = iff.interrogate(
        _track("enemy-55", classification=TrackClass.HOSTILE, confidence=0.92)
    )

    assert resp.mode == IFFMode.HOSTILE
    assert resp.method == "track_class"
    assert resp.confidence == 0.92


def test_rf_signature_match() -> None:
    """Registered drone with rf_signature but no transponder uses rf_signature method."""
    iff = IFFSystem()
    iff.register_friendly("bravo-2", rf_signature="SIG-ABC123")

    resp = iff.interrogate(_track("bravo-2"))

    assert resp.mode == IFFMode.FRIENDLY
    assert resp.method == "rf_signature"
    assert resp.confidence >= 0.85


def test_is_engagement_safe_hostile() -> None:
    """is_engagement_safe returns True for a confirmed hostile track."""
    iff = IFFSystem()
    iff.interrogate(
        _track("enemy-1", classification=TrackClass.HOSTILE, confidence=0.95)
    )
    assert iff.is_engagement_safe("enemy-1") is True


def test_is_engagement_safe_unknown_returns_false() -> None:
    """is_engagement_safe returns False for UNKNOWN tracks (safe-side default)."""
    iff = IFFSystem()
    iff.interrogate(_track("mystery-7"))
    assert iff.is_engagement_safe("mystery-7") is False


def test_is_engagement_safe_friendly_returns_false() -> None:
    """is_engagement_safe returns False for confirmed friendly tracks."""
    iff = IFFSystem()
    iff.register_friendly("friendly-drone", transponder_code="CODE-001")
    iff.interrogate(_track("friendly-drone"))
    assert iff.is_engagement_safe("friendly-drone") is False


# ---------------------------------------------------------------------------
# AirspaceDeconflictor tests
# ---------------------------------------------------------------------------

def test_engagement_safe_no_friendlies() -> None:
    """With no friendlies registered, every engagement is safe."""
    dc = AirspaceDeconflictor()
    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 0.0),
        target_position=(500.0, 0.0, 50.0),
        effect_radius_m=10.0,
    )
    assert safe is True
    assert reason == "clear"


def test_engagement_blocked_friendly_nearby() -> None:
    """Friendly within the blast radius of the target blocks the engagement."""
    dc = AirspaceDeconflictor()
    dc.update_friendly("charlie-3", (505.0, 0.0, 50.0))  # 5 m from target

    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 0.0),
        target_position=(500.0, 0.0, 50.0),
        effect_radius_m=10.0,
    )
    assert safe is False
    assert "charlie-3" in reason
    assert "blast radius" in reason


def test_line_of_fire_blocked() -> None:
    """Friendly sitting directly between effector and target blocks the shot."""
    dc = AirspaceDeconflictor()
    # Effector at origin, target at (1000, 0, 50). Friendly at midpoint.
    dc.update_friendly("delta-4", (500.0, 0.0, 50.0))

    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 50.0),
        target_position=(1000.0, 0.0, 50.0),
        effect_radius_m=0.0,   # no blast radius — pure LOF block
        lof_corridor_m=10.0,
    )
    assert safe is False
    assert "delta-4" in reason
    assert "line of fire" in reason


def test_line_of_fire_clear_offset_friendly() -> None:
    """Friendly far to the side of the firing line does not block the engagement."""
    dc = AirspaceDeconflictor()
    dc.update_friendly("echo-5", (500.0, 200.0, 50.0))  # 200 m lateral offset

    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 50.0),
        target_position=(1000.0, 0.0, 50.0),
        effect_radius_m=0.0,
        lof_corridor_m=10.0,
    )
    assert safe is True
    assert reason == "clear"


def test_nearest_friendly_distance() -> None:
    """nearest_friendly_distance returns the correct (id, distance) pair."""
    dc = AirspaceDeconflictor()
    dc.update_friendly("far-drone", (1000.0, 0.0, 0.0))
    dc.update_friendly("near-drone", (30.0, 40.0, 0.0))  # distance = 50 m

    drone_id, dist = dc.nearest_friendly_distance((0.0, 0.0, 0.0))

    assert drone_id == "near-drone"
    assert abs(dist - 50.0) < 0.01


def test_nearest_friendly_distance_no_friendlies() -> None:
    """nearest_friendly_distance with no friendlies returns (None, inf)."""
    dc = AirspaceDeconflictor()
    drone_id, dist = dc.nearest_friendly_distance((0.0, 0.0, 0.0))
    assert drone_id is None
    assert dist == float("inf")


def test_friendlies_in_radius() -> None:
    """friendlies_in_radius returns exactly the drones within the radius."""
    dc = AirspaceDeconflictor()
    dc.update_friendly("close-1", (10.0, 0.0, 0.0))
    dc.update_friendly("close-2", (0.0, 10.0, 0.0))
    dc.update_friendly("far-1", (500.0, 0.0, 0.0))

    inside = dc.friendlies_in_radius((0.0, 0.0, 0.0), radius_m=20.0)

    assert set(inside) == {"close-1", "close-2"}
    assert "far-1" not in inside


def test_zone_management() -> None:
    """add_zone stores zones; restricted zones block engagements that cross them."""
    dc = AirspaceDeconflictor()
    zone = DeconflictionZone(
        center=(500.0, 0.0, 50.0),
        radius_m=50.0,
        altitude_floor_m=0.0,
        altitude_ceiling_m=200.0,
        zone_type="restricted",
    )
    dc.add_zone(zone)

    # Engagement path passes straight through the zone center.
    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 50.0),
        target_position=(1000.0, 0.0, 50.0),
        effect_radius_m=0.0,
    )
    assert safe is False
    assert "restricted zone" in reason


def test_zone_non_restricted_does_not_block() -> None:
    """A transit zone on the engagement path does not block the engagement."""
    dc = AirspaceDeconflictor()
    zone = DeconflictionZone(
        center=(500.0, 0.0, 50.0),
        radius_m=50.0,
        altitude_floor_m=0.0,
        altitude_ceiling_m=200.0,
        zone_type="transit",
    )
    dc.add_zone(zone)

    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 50.0),
        target_position=(1000.0, 0.0, 50.0),
        effect_radius_m=0.0,
    )
    assert safe is True
    assert reason == "clear"


def test_deconfliction_respects_altitude() -> None:
    """
    Friendly at a very different altitude from the target is not within the
    blast radius and does not block the engagement.
    """
    dc = AirspaceDeconflictor()
    # Target is at 50 m altitude; friendly is at 500 m altitude, same x/y.
    dc.update_friendly("high-drone", (500.0, 0.0, 500.0))

    safe, reason = dc.check_engagement_safe(
        effector_position=(0.0, 0.0, 50.0),
        target_position=(500.0, 0.0, 50.0),
        effect_radius_m=20.0,
    )
    assert safe is True
    assert reason == "clear"
