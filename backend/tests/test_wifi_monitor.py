"""Unit tests for the WiFi monitor passive drone detection sensor."""
from __future__ import annotations

import asyncio

import pytest

from sensors.wifi_monitor_source import (
    DRONE_OUIS,
    WiFiMonitorSource,
    compute_confidence,
    extract_oui,
    match_oui,
    normalize_mac,
    parse_macs_from_line,
)


# ---- MAC parsing and normalization ----


class TestNormalizeMac:
    def test_uppercase(self) -> None:
        assert normalize_mac("a0:14:3d:ff:aa:bb") == "A0:14:3D:FF:AA:BB"

    def test_already_upper(self) -> None:
        assert normalize_mac("62:60:1F:11:22:33") == "62:60:1F:11:22:33"

    def test_no_colons(self) -> None:
        assert normalize_mac("62601F112233") == "62:60:1F:11:22:33"

    def test_strips_whitespace(self) -> None:
        assert normalize_mac("  a0:14:3d:ff:aa:bb  ") == "A0:14:3D:FF:AA:BB"


class TestExtractOui:
    def test_valid_mac(self) -> None:
        assert extract_oui("62:60:1F:11:22:33") == "62:60:1F"

    def test_short_mac(self) -> None:
        assert extract_oui("62:60") == ""

    def test_exact_three_octets(self) -> None:
        assert extract_oui("A0:14:3D") == "A0:14:3D"


class TestParseMacsFromLine:
    def test_single_mac(self) -> None:
        line = " 62:60:1F:AA:BB:CC  -67  2  some_ssid"
        result = parse_macs_from_line(line)
        assert result == ["62:60:1F:AA:BB:CC"]

    def test_multiple_macs(self) -> None:
        line = "62:60:1F:AA:BB:CC  A0:14:3D:11:22:33"
        result = parse_macs_from_line(line)
        assert len(result) == 2
        assert "62:60:1F:AA:BB:CC" in result
        assert "A0:14:3D:11:22:33" in result

    def test_no_macs(self) -> None:
        line = "no mac addresses here"
        assert parse_macs_from_line(line) == []

    def test_lowercase_input(self) -> None:
        line = "a0:14:3d:ff:aa:bb"
        result = parse_macs_from_line(line)
        assert result == ["A0:14:3D:FF:AA:BB"]


# ---- OUI matching ----


class TestMatchOui:
    def test_dji_match(self) -> None:
        assert match_oui("62:60:1F:AA:BB:CC") == "DJI"

    def test_parrot_match(self) -> None:
        assert match_oui("A0:14:3D:11:22:33") == "Parrot"

    def test_skydio_match(self) -> None:
        assert match_oui("38:1D:14:99:88:77") == "Skydio"

    def test_autel_match(self) -> None:
        assert match_oui("48:D6:D5:00:11:22") == "Autel"

    def test_no_match(self) -> None:
        assert match_oui("AA:BB:CC:DD:EE:FF") is None

    def test_lowercase_input(self) -> None:
        assert match_oui("62:60:1f:aa:bb:cc") == "DJI"

    def test_all_known_ouis(self) -> None:
        for oui, manufacturer in DRONE_OUIS.items():
            mac = f"{oui}:AA:BB:CC"
            assert match_oui(mac) == manufacturer


# ---- Confidence calculation ----


class TestComputeConfidence:
    def test_base_confidence(self) -> None:
        conf = compute_confidence(rssi=-70, sighting_count=1)
        assert conf == pytest.approx(0.6)

    def test_strong_rssi_bonus(self) -> None:
        conf = compute_confidence(rssi=-50, sighting_count=1)
        assert conf == pytest.approx(0.75)

    def test_multiple_sightings(self) -> None:
        conf = compute_confidence(rssi=-70, sighting_count=3)
        assert conf == pytest.approx(0.7)

    def test_strong_rssi_plus_sightings(self) -> None:
        conf = compute_confidence(rssi=-40, sighting_count=4)
        assert conf == pytest.approx(0.9)

    def test_cap_at_095(self) -> None:
        conf = compute_confidence(rssi=-30, sighting_count=20)
        assert conf == pytest.approx(0.95)

    def test_exactly_at_threshold_no_bonus(self) -> None:
        conf = compute_confidence(rssi=-60, sighting_count=1)
        assert conf == pytest.approx(0.6)

    def test_just_above_threshold(self) -> None:
        conf = compute_confidence(rssi=-59, sighting_count=1)
        assert conf == pytest.approx(0.75)

    def test_single_sighting_no_extra(self) -> None:
        conf = compute_confidence(rssi=-80, sighting_count=1)
        assert conf == pytest.approx(0.6)

    def test_two_sightings_one_extra(self) -> None:
        conf = compute_confidence(rssi=-80, sighting_count=2)
        assert conf == pytest.approx(0.65)


# ---- Mock mode streaming ----


class TestWiFiMonitorSourceMock:
    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        assert source.is_running
        await source.stop()
        assert not source.is_running

    @pytest.mark.asyncio
    async def test_stream_before_start_raises(self) -> None:
        source = WiFiMonitorSource(mock=True)
        with pytest.raises(RuntimeError, match="stream.*before start"):
            async for _ in source.stream():
                pass

    @pytest.mark.asyncio
    async def test_mock_yields_detections(self) -> None:
        source = WiFiMonitorSource(mock=True, sensor_id="test-wifi")
        await source.start()
        detections = []

        async def collect() -> None:
            async for det in source.stream():
                detections.append(det)
                if len(detections) >= 3:
                    await source.stop()

        await asyncio.wait_for(collect(), timeout=10.0)
        assert len(detections) >= 3
        for det in detections:
            assert det.sensor_id == "test-wifi"
            assert det.confidence >= 0.6
            assert det.confidence <= 0.95
            assert "wifi" in det.id

    @pytest.mark.asyncio
    async def test_mock_detection_has_rssi_in_size_rcs(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        det = None
        async for d in source.stream():
            det = d
            await source.stop()
            break
        assert det is not None
        assert det.size_rcs is not None
        assert -85 <= det.size_rcs <= -30

    @pytest.mark.asyncio
    async def test_sighting_count_increases_confidence(self) -> None:
        source = WiFiMonitorSource(mock=True, sensor_id="test-sighting")
        await source.start()
        detections = []

        async def collect() -> None:
            async for det in source.stream():
                detections.append(det)
                if len(detections) >= 10:
                    await source.stop()

        await asyncio.wait_for(collect(), timeout=30.0)
        mac_detections: dict[str, list[float]] = {}
        for det in detections:
            mac_key = det.id.rsplit("-", 1)[0]
            mac_detections.setdefault(mac_key, []).append(det.confidence)

        for mac_key, confs in mac_detections.items():
            if len(confs) > 1:
                assert confs[-1] >= confs[0]

    @pytest.mark.asyncio
    async def test_stop_terminates_stream(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        count = 0

        async def collect() -> None:
            nonlocal count
            async for _ in source.stream():
                count += 1
                if count >= 2:
                    await source.stop()

        await asyncio.wait_for(collect(), timeout=10.0)
        assert count >= 2

    @pytest.mark.asyncio
    async def test_sensor_id_propagated(self) -> None:
        source = WiFiMonitorSource(mock=True, sensor_id="custom-id")
        await source.start()
        async for det in source.stream():
            assert det.sensor_id == "custom-id"
            await source.stop()
            break


# ---- process_mac internal logic ----


class TestProcessMac:
    @pytest.mark.asyncio
    async def test_process_known_mac(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        det = source._process_mac("62:60:1F:AA:BB:CC", -50)
        assert det is not None
        assert det.confidence >= 0.6
        await source.stop()

    @pytest.mark.asyncio
    async def test_process_unknown_mac(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        det = source._process_mac("AA:BB:CC:DD:EE:FF", -50)
        assert det is None
        await source.stop()

    @pytest.mark.asyncio
    async def test_repeated_mac_increments_sighting(self) -> None:
        source = WiFiMonitorSource(mock=True)
        await source.start()
        mac = "62:60:1F:AA:BB:CC"
        det1 = source._process_mac(mac, -70)
        det2 = source._process_mac(mac, -70)
        assert det1 is not None
        assert det2 is not None
        assert det2.confidence > det1.confidence
        await source.stop()
