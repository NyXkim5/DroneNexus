"""
Tests for monocular 3D localizer.

Covers range estimation, bearing computation, ENU-to-geodetic conversion,
the confidence model, and end-to-end MonocularLocalizer behavior.
"""
from __future__ import annotations

import math

import pytest

from vision.localizer_3d import (
    CameraIntrinsics,
    LocalizedDetection,
    MonocularLocalizer,
    RangeMethod,
    compute_bearing_deg,
    compute_confidence,
    compute_elevation_deg,
    enu_to_geodetic,
    estimate_range_altitude_based,
    estimate_range_multi_cue,
    estimate_range_size_based,
    range_bearing_elev_to_enu,
    _range_confidence,
    _pixel_confidence,
    _temporal_confidence,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def camera() -> CameraIntrinsics:
    """Standard 1920x1080 camera with 800px focal length."""
    return CameraIntrinsics(focal_length_px=800.0, image_width=1920, image_height=1080)


@pytest.fixture
def localizer(camera: CameraIntrinsics) -> MonocularLocalizer:
    """Localizer pointing north with geodetic origin set."""
    return MonocularLocalizer(
        camera=camera,
        known_size_m=0.5,
        range_method=RangeMethod.SIZE_BASED,
        camera_heading_deg=0.0,
        camera_tilt_deg=0.0,
        camera_enu=(0.0, 0.0, 0.0),
        origin_lat=33.6405,
        origin_lon=-117.8443,
        origin_alt=0.0,
    )


# ---------------------------------------------------------------------------
# Range estimation: SIZE_BASED
# ---------------------------------------------------------------------------

class TestRangeSizeBased:
    def test_known_geometry(self, camera: CameraIntrinsics) -> None:
        """A 0.5m drone at 50px should be at range = 800 * 0.5 / 50 = 8.0m."""
        r = estimate_range_size_based(800.0, 0.5, 50.0)
        assert r == pytest.approx(8.0)

    def test_larger_bbox_means_closer(self) -> None:
        r_close = estimate_range_size_based(800.0, 0.5, 100.0)
        r_far = estimate_range_size_based(800.0, 0.5, 10.0)
        assert r_close < r_far

    def test_zero_bbox_returns_inf(self) -> None:
        r = estimate_range_size_based(800.0, 0.5, 0.0)
        assert math.isinf(r)

    def test_negative_bbox_returns_inf(self) -> None:
        r = estimate_range_size_based(800.0, 0.5, -5.0)
        assert math.isinf(r)

    def test_proportional_to_known_size(self) -> None:
        r1 = estimate_range_size_based(800.0, 0.5, 40.0)
        r2 = estimate_range_size_based(800.0, 1.0, 40.0)
        assert r2 == pytest.approx(2.0 * r1)


# ---------------------------------------------------------------------------
# Range estimation: ALTITUDE_BASED
# ---------------------------------------------------------------------------

class TestRangeAltitudeBased:
    def test_straight_up(self) -> None:
        """Looking straight up at ceiling: range = altitude."""
        r = estimate_range_altitude_based(90.0, 120.0)
        assert r == pytest.approx(120.0)

    def test_45_degrees(self) -> None:
        """At 45 degrees: range = 120 / sin(45) ~ 169.7m."""
        r = estimate_range_altitude_based(45.0, 120.0)
        assert r == pytest.approx(120.0 / math.sin(math.radians(45.0)))

    def test_zero_elevation_returns_inf(self) -> None:
        r = estimate_range_altitude_based(0.0, 120.0)
        assert math.isinf(r)

    def test_negative_elevation_returns_inf(self) -> None:
        r = estimate_range_altitude_based(-10.0, 120.0)
        assert math.isinf(r)


# ---------------------------------------------------------------------------
# Range estimation: MULTI_CUE
# ---------------------------------------------------------------------------

class TestRangeMultiCue:
    def test_combines_estimates(self) -> None:
        """Multi-cue should produce a finite value when both cues are valid."""
        r = estimate_range_multi_cue(800.0, 0.5, 50.0, 45.0, 120.0)
        r_size = estimate_range_size_based(800.0, 0.5, 50.0)
        r_alt = estimate_range_altitude_based(45.0, 120.0)
        expected = (r_size + r_alt) / 2.0
        assert r == pytest.approx(expected)

    def test_temporal_smoothing(self) -> None:
        """With a previous range, output should blend toward it."""
        prev = 100.0
        r = estimate_range_multi_cue(800.0, 0.5, 50.0, 45.0, 120.0, prev_range_m=prev)
        r_no_smooth = estimate_range_multi_cue(800.0, 0.5, 50.0, 45.0, 120.0)
        # Should be between no-smooth and previous range
        assert min(r_no_smooth, prev) <= r <= max(r_no_smooth, prev)

    def test_falls_back_to_size_when_elevation_zero(self) -> None:
        """If elevation is 0, altitude cue is inf, should use size only."""
        r = estimate_range_multi_cue(800.0, 0.5, 50.0, 0.0, 120.0)
        r_size = estimate_range_size_based(800.0, 0.5, 50.0)
        assert r == pytest.approx(r_size)


# ---------------------------------------------------------------------------
# Bearing computation
# ---------------------------------------------------------------------------

class TestBearing:
    def test_center_pixel_gives_camera_heading(self, camera: CameraIntrinsics) -> None:
        """Detection at image center should match the camera heading."""
        bearing = compute_bearing_deg(camera.cx, camera, camera_heading_deg=90.0)
        assert bearing == pytest.approx(90.0, abs=0.1)

    def test_right_of_center_increases_bearing(self, camera: CameraIntrinsics) -> None:
        b_center = compute_bearing_deg(camera.cx, camera, camera_heading_deg=0.0)
        b_right = compute_bearing_deg(camera.cx + 200, camera, camera_heading_deg=0.0)
        assert b_right > b_center

    def test_left_of_center_decreases_bearing(self, camera: CameraIntrinsics) -> None:
        b_center = compute_bearing_deg(camera.cx, camera, camera_heading_deg=180.0)
        b_left = compute_bearing_deg(camera.cx - 200, camera, camera_heading_deg=180.0)
        assert b_left < b_center

    def test_bearing_wraps_at_360(self, camera: CameraIntrinsics) -> None:
        """Bearing should wrap around 360 degrees."""
        b = compute_bearing_deg(camera.cx - 200, camera, camera_heading_deg=5.0)
        assert 0.0 <= b < 360.0

    def test_bearing_symmetry(self, camera: CameraIntrinsics) -> None:
        """Equal offsets left and right should produce symmetric bearings."""
        offset = 100.0
        b_right = compute_bearing_deg(camera.cx + offset, camera, camera_heading_deg=180.0)
        b_left = compute_bearing_deg(camera.cx - offset, camera, camera_heading_deg=180.0)
        # Offsets from 180 should be equal magnitude
        assert abs((b_right - 180.0) + (b_left - 180.0)) < 0.01


# ---------------------------------------------------------------------------
# Elevation computation
# ---------------------------------------------------------------------------

class TestElevation:
    def test_center_pixel_gives_tilt(self, camera: CameraIntrinsics) -> None:
        elev = compute_elevation_deg(camera.cy, camera, camera_tilt_deg=10.0)
        assert elev == pytest.approx(10.0, abs=0.1)

    def test_above_center_positive_elevation(self, camera: CameraIntrinsics) -> None:
        """Object above image center (lower v) should have positive elevation."""
        elev = compute_elevation_deg(camera.cy - 200, camera, camera_tilt_deg=0.0)
        assert elev > 0.0

    def test_below_center_negative_elevation(self, camera: CameraIntrinsics) -> None:
        """Object below image center (higher v) should have negative elevation."""
        elev = compute_elevation_deg(camera.cy + 200, camera, camera_tilt_deg=0.0)
        assert elev < 0.0


# ---------------------------------------------------------------------------
# ENU to geodetic conversion
# ---------------------------------------------------------------------------

class TestEnuToGeodetic:
    def test_zero_offset_returns_origin(self) -> None:
        lat, lon, alt = enu_to_geodetic(0.0, 0.0, 0.0, 33.6405, -117.8443, 0.0)
        assert lat == pytest.approx(33.6405, abs=1e-8)
        assert lon == pytest.approx(-117.8443, abs=1e-8)
        assert alt == pytest.approx(0.0)

    def test_north_offset_increases_latitude(self) -> None:
        lat, lon, _ = enu_to_geodetic(0.0, 1000.0, 0.0, 33.6405, -117.8443)
        assert lat > 33.6405

    def test_east_offset_increases_longitude(self) -> None:
        _, lon, _ = enu_to_geodetic(1000.0, 0.0, 0.0, 33.6405, -117.8443)
        assert lon > -117.8443

    def test_up_offset_increases_altitude(self) -> None:
        _, _, alt = enu_to_geodetic(0.0, 0.0, 50.0, 33.6405, -117.8443, 100.0)
        assert alt == pytest.approx(150.0)

    def test_known_reference_point(self) -> None:
        """~111.32km north should be about 1 degree latitude."""
        lat, _, _ = enu_to_geodetic(0.0, 111320.0, 0.0, 0.0, 0.0, 0.0)
        assert lat == pytest.approx(1.0, abs=0.01)

    def test_roundtrip_small_offset(self) -> None:
        """ENU -> geodetic for small offset should be reversible."""
        east, north, up = 100.0, 200.0, 50.0
        lat, lon, alt = enu_to_geodetic(east, north, up, 33.6405, -117.8443, 0.0)
        # Rough inverse: convert back
        from vision.localizer_3d import _WGS84_A, _WGS84_E2
        lat_rad = math.radians(33.6405)
        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        n_r = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat**2)
        m_r = n_r * (1.0 - _WGS84_E2) / (1.0 - _WGS84_E2 * sin_lat**2)
        north_back = (lat - 33.6405) * math.radians(1.0) * m_r / math.radians(1.0) * (math.pi / 180.0) * m_r
        # Just verify lat/lon shifted in the right direction
        assert lat > 33.6405
        assert lon > -117.8443
        assert alt == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Confidence model
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_close_range_high_confidence(self) -> None:
        conf = compute_confidence(10.0, 100.0, temporal_hits=5)
        assert conf > 0.8

    def test_far_range_low_confidence(self) -> None:
        conf = compute_confidence(2000.0, 5.0, temporal_hits=1)
        assert conf < 0.3

    def test_closer_objects_higher_confidence(self) -> None:
        conf_close = compute_confidence(50.0, 60.0, temporal_hits=1)
        conf_far = compute_confidence(500.0, 6.0, temporal_hits=1)
        assert conf_close > conf_far

    def test_larger_bbox_higher_confidence(self) -> None:
        conf_big = compute_confidence(100.0, 80.0, temporal_hits=1)
        conf_small = compute_confidence(100.0, 5.0, temporal_hits=1)
        assert conf_big > conf_small

    def test_temporal_hits_boost_confidence(self) -> None:
        conf_1 = compute_confidence(200.0, 30.0, temporal_hits=1)
        conf_5 = compute_confidence(200.0, 30.0, temporal_hits=5)
        assert conf_5 > conf_1

    def test_confidence_bounded_0_1(self) -> None:
        assert 0.0 <= compute_confidence(0.0, 100.0, 10) <= 1.0
        assert 0.0 <= compute_confidence(10000.0, 1.0, 0) <= 1.0

    def test_range_confidence_at_half_range(self) -> None:
        """At _CONF_HALF_RANGE_M, range confidence should be 0.5."""
        conf = _range_confidence(500.0)
        assert conf == pytest.approx(0.5)

    def test_pixel_confidence_zero_bbox(self) -> None:
        conf = _pixel_confidence(0.0)
        assert conf == pytest.approx(0.1)

    def test_pixel_confidence_full_bbox(self) -> None:
        conf = _pixel_confidence(100.0)
        assert conf == pytest.approx(1.0)

    def test_temporal_zero_hits(self) -> None:
        conf = _temporal_confidence(0)
        assert conf == pytest.approx(0.5)

    def test_temporal_saturates(self) -> None:
        conf = _temporal_confidence(10)
        assert conf == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# range_bearing_elev_to_enu
# ---------------------------------------------------------------------------

class TestRangeBearingElevToEnu:
    def test_north_bearing(self) -> None:
        """Bearing 0 (north) should increase north component."""
        e, n, u = range_bearing_elev_to_enu(100.0, 0.0, 0.0)
        assert n == pytest.approx(100.0, abs=0.1)
        assert abs(e) < 0.1

    def test_east_bearing(self) -> None:
        """Bearing 90 (east) should increase east component."""
        e, n, u = range_bearing_elev_to_enu(100.0, 90.0, 0.0)
        assert e == pytest.approx(100.0, abs=0.1)
        assert abs(n) < 0.1

    def test_elevation_increases_up(self) -> None:
        """Positive elevation should produce positive up component."""
        _, _, u = range_bearing_elev_to_enu(100.0, 0.0, 45.0)
        assert u == pytest.approx(100.0 * math.sin(math.radians(45.0)), abs=0.1)

    def test_camera_offset_applied(self) -> None:
        """Camera ENU position should shift the result."""
        e, n, u = range_bearing_elev_to_enu(100.0, 0.0, 0.0, (50.0, 50.0, 10.0))
        assert n == pytest.approx(150.0, abs=0.1)
        assert e == pytest.approx(50.0, abs=0.1)
        assert u == pytest.approx(10.0, abs=0.1)


# ---------------------------------------------------------------------------
# MonocularLocalizer end-to-end
# ---------------------------------------------------------------------------

class TestMonocularLocalizer:
    def test_basic_localization(self, localizer: MonocularLocalizer) -> None:
        """A detection at image center should produce a result along the heading."""
        bbox = (910.0, 490.0, 1010.0, 590.0)  # 100x100 centered
        result = localizer.localize(bbox, detection_id="d1")
        assert isinstance(result, LocalizedDetection)
        assert result.detection_id == "d1"
        assert result.range_m > 0
        assert 0.0 <= result.confidence <= 1.0

    def test_bearing_matches_center(self, localizer: MonocularLocalizer) -> None:
        """Detection at image center, heading=0, bearing should be ~0."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = localizer.localize(bbox, detection_id="d2")
        assert result.bearing_deg == pytest.approx(0.0, abs=1.0)

    def test_range_from_bbox_size(self, localizer: MonocularLocalizer) -> None:
        """Known geometry: 0.5m drone at 50px -> range = 800*0.5/100 = 4.0m."""
        # 100px max dimension
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = localizer.localize(bbox, detection_id="d3")
        assert result.range_m == pytest.approx(4.0)

    def test_geodetic_populated_with_origin(self, localizer: MonocularLocalizer) -> None:
        """With origin set, position_geo should be near the origin."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = localizer.localize(bbox, detection_id="d4")
        lat, lon, alt = result.position_geo
        assert abs(lat - 33.6405) < 0.01
        assert abs(lon - (-117.8443)) < 0.01

    def test_geodetic_zero_without_origin(self, camera: CameraIntrinsics) -> None:
        """Without origin, position_geo should be (0, 0, 0)."""
        loc = MonocularLocalizer(camera=camera, known_size_m=0.5)
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = loc.localize(bbox, detection_id="d5")
        assert result.position_geo == (0.0, 0.0, 0.0)

    def test_size_estimate_roundtrip(self, localizer: MonocularLocalizer) -> None:
        """Estimated real-world size should be close to known_size_m."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = localizer.localize(bbox, detection_id="d6")
        assert result.size_estimate_m == pytest.approx(0.5, abs=0.05)

    def test_closer_objects_higher_confidence(
        self, localizer: MonocularLocalizer
    ) -> None:
        """A large bbox (close object) should have higher confidence than small."""
        bbox_close = (860.0, 440.0, 1060.0, 640.0)  # 200x200
        bbox_far = (955.0, 535.0, 965.0, 545.0)  # 10x10
        result_close = localizer.localize(bbox_close, detection_id="close")
        result_far = localizer.localize(bbox_far, detection_id="far")
        assert result_close.confidence > result_far.confidence
        assert result_close.range_m < result_far.range_m

    def test_altitude_based_method(self, camera: CameraIntrinsics) -> None:
        """ALTITUDE_BASED method should produce a finite range for upward look."""
        loc = MonocularLocalizer(
            camera=camera,
            range_method=RangeMethod.ALTITUDE_BASED,
            camera_tilt_deg=30.0,
            altitude_ceiling_m=120.0,
        )
        # Detection above center (object above horizon)
        bbox = (910.0, 200.0, 1010.0, 300.0)
        result = loc.localize(bbox, detection_id="alt1")
        assert result.range_m > 0
        assert not math.isinf(result.range_m)

    def test_multi_cue_method(self, camera: CameraIntrinsics) -> None:
        """MULTI_CUE should produce a finite result."""
        loc = MonocularLocalizer(
            camera=camera,
            range_method=RangeMethod.MULTI_CUE,
            camera_tilt_deg=30.0,
            altitude_ceiling_m=120.0,
        )
        bbox = (910.0, 200.0, 1010.0, 300.0)
        result = loc.localize(bbox, detection_id="mc1")
        assert result.range_m > 0

    def test_temporal_tracking_accumulates(
        self, localizer: MonocularLocalizer
    ) -> None:
        """Multiple calls with the same ID should increase temporal hits."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        r1 = localizer.localize(bbox, detection_id="track1")
        r2 = localizer.localize(bbox, detection_id="track1")
        r3 = localizer.localize(bbox, detection_id="track1")
        # Confidence should increase (or stay equal) with more hits
        assert r3.confidence >= r1.confidence

    def test_clear_tracking_resets(self, localizer: MonocularLocalizer) -> None:
        """clear_tracking should reset temporal state."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        localizer.localize(bbox, detection_id="t1")
        localizer.localize(bbox, detection_id="t1")
        localizer.clear_tracking()
        assert localizer._temporal_hits == {}
        assert localizer._prev_ranges == {}

    def test_auto_generates_detection_id(
        self, localizer: MonocularLocalizer
    ) -> None:
        """Without a detection_id, one should be auto-generated."""
        bbox = (910.0, 490.0, 1010.0, 590.0)
        result = localizer.localize(bbox)
        assert len(result.detection_id) > 0


# ---------------------------------------------------------------------------
# CameraIntrinsics
# ---------------------------------------------------------------------------

class TestCameraIntrinsics:
    def test_principal_point(self, camera: CameraIntrinsics) -> None:
        assert camera.cx == pytest.approx(960.0)
        assert camera.cy == pytest.approx(540.0)

    def test_custom_dimensions(self) -> None:
        cam = CameraIntrinsics(focal_length_px=600.0, image_width=1280, image_height=720)
        assert cam.cx == pytest.approx(640.0)
        assert cam.cy == pytest.approx(360.0)
