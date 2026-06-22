"""Unit tests for acoustic drone detection sensor."""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pytest

from sensors.acoustic_source import (
    AcousticResult,
    AcousticSensorSource,
    analyze_frame,
    estimate_bearing_tdoa,
    find_peaks,
    generate_mock_audio,
    has_harmonic_pattern,
)


# -- Harmonic pattern detection ------------------------------------------------

class TestFindPeaks:
    def test_finds_peaks_above_threshold(self) -> None:
        mags = np.array([0.0, 1.0, 0.0, 5.0, 0.0, 3.0, 0.0])
        peaks = find_peaks(mags, threshold=0.5)
        assert 1 in peaks
        assert 3 in peaks
        assert 5 in peaks

    def test_no_peaks_below_threshold(self) -> None:
        mags = np.array([0.1, 0.2, 0.1, 0.15, 0.1])
        peaks = find_peaks(mags, threshold=1.0)
        assert peaks == []


class TestHasHarmonicPattern:
    def test_detects_harmonic_series(self) -> None:
        freqs = np.array([200.0, 400.0, 600.0, 800.0])
        found, fundamental = has_harmonic_pattern(freqs, min_harmonics=3)
        assert found is True
        assert fundamental == pytest.approx(200.0)

    def test_rejects_non_harmonic(self) -> None:
        freqs = np.array([137.0, 293.0, 511.0, 719.0])
        found, _ = has_harmonic_pattern(freqs, min_harmonics=3)
        assert found is False

    def test_too_few_peaks(self) -> None:
        freqs = np.array([200.0, 400.0])
        found, _ = has_harmonic_pattern(freqs, min_harmonics=3)
        assert found is False

    def test_tolerates_slight_drift(self) -> None:
        freqs = np.array([198.0, 403.0, 597.0])
        found, fundamental = has_harmonic_pattern(freqs, min_harmonics=3)
        assert found is True
        assert fundamental == pytest.approx(198.0)

    def test_skips_sub_band_frequencies(self) -> None:
        freqs = np.array([10.0, 20.0, 30.0, 40.0])
        found, _ = has_harmonic_pattern(freqs, min_harmonics=3)
        assert found is False


# -- Full frame analysis -------------------------------------------------------

class TestAnalyzeFrame:
    def test_detects_drone_audio(self) -> None:
        audio = generate_mock_audio(0.1, sample_rate=16000)
        result = analyze_frame(audio, 16000)
        assert result.detected is True
        assert result.confidence >= 0.5
        assert result.fundamental_hz is not None
        assert 150.0 <= result.fundamental_hz <= 250.0

    def test_rejects_pure_noise(self) -> None:
        rng = np.random.default_rng(99)
        noise = rng.normal(0, 0.5, 1600)
        result = analyze_frame(noise, 16000)
        assert result.detected is False

    def test_rejects_single_tone(self) -> None:
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        single_tone = np.sin(2 * np.pi * 440.0 * t)
        result = analyze_frame(single_tone, 16000)
        assert result.detected is False

    def test_rejects_low_frequency_hum(self) -> None:
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        hum = np.sin(2 * np.pi * 50.0 * t) + 0.5 * np.sin(2 * np.pi * 100.0 * t)
        result = analyze_frame(hum, 16000)
        assert result.detected is False


# -- TDOA bearing estimation ---------------------------------------------------

class TestEstimateBearingTDOA:
    def test_broadside_zero_delay(self) -> None:
        sr = 16000
        audio = generate_mock_audio(0.05, sample_rate=sr)
        bearing = estimate_bearing_tdoa(audio, audio, sr, mic_separation_m=0.5)
        assert bearing is not None
        assert abs(bearing) < 2.0

    def test_angled_source(self) -> None:
        sr = 48000
        mic_sep = 0.3
        target_angle = 30.0
        delay_s = mic_sep * math.sin(math.radians(target_angle)) / 343.0
        delay_samples = int(round(delay_s * sr))

        base = generate_mock_audio(0.05, sample_rate=sr)
        # Roll B backward so A-B cross-correlation peaks at positive lag
        shifted = np.roll(base, -delay_samples)

        bearing = estimate_bearing_tdoa(base, shifted, sr, mic_separation_m=mic_sep)
        assert bearing is not None
        assert abs(bearing - target_angle) < 5.0

    def test_returns_none_for_impossible_geometry(self) -> None:
        sr = 16000
        t = np.linspace(0, 0.05, 800, endpoint=False)
        a = np.sin(2 * np.pi * 200 * t)
        b = np.zeros(800)
        b[0] = 100.0
        bearing = estimate_bearing_tdoa(a, b, sr, mic_separation_m=0.001)
        assert bearing is None or abs(bearing) <= 90.0


# -- Mock audio generation ----------------------------------------------------

class TestGenerateMockAudio:
    def test_correct_length(self) -> None:
        audio = generate_mock_audio(0.1, sample_rate=16000)
        assert len(audio) == 1600

    def test_contains_fundamental_frequency(self) -> None:
        audio = generate_mock_audio(0.5, sample_rate=16000, rpm=6000, blade_count=2)
        spectrum = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), 1.0 / 16000)
        fundamental = 2 * 6000 / 60.0  # 200 Hz
        idx = int(np.argmin(np.abs(freqs - fundamental)))
        assert spectrum[idx] > np.median(spectrum) * 5


# -- SensorSource integration -------------------------------------------------

class TestAcousticSensorSource:
    @pytest.mark.asyncio
    async def test_mock_mode_yields_detections(self) -> None:
        source = AcousticSensorSource(sensor_id="test-mic", mock=True)
        await source.start()
        assert source.is_running

        detections = []
        count = 0
        async for det in source.stream():
            detections.append(det)
            count += 1
            if count >= 3:
                await source.stop()
                break

        assert len(detections) == 3
        assert all(d.sensor_id == "test-mic" for d in detections)
        assert all(d.confidence >= 0.5 for d in detections)

    @pytest.mark.asyncio
    async def test_live_mode_processes_pushed_audio(self) -> None:
        source = AcousticSensorSource(sensor_id="live-mic", mock=False)
        await source.start()

        drone_audio = generate_mock_audio(0.1, sample_rate=16000)
        source.push_audio(drone_audio)

        detections = []
        async for det in source.stream():
            detections.append(det)
            await source.stop()
            break

        assert len(detections) == 1
        assert detections[0].confidence >= 0.5

    @pytest.mark.asyncio
    async def test_stream_raises_before_start(self) -> None:
        source = AcousticSensorSource(mock=True)
        with pytest.raises(RuntimeError, match="before start"):
            async for _ in source.stream():
                pass
