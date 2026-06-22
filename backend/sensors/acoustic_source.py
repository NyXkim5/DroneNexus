"""AcousticSensorSource -- detect drone propeller noise via frequency analysis.

Fiber-optic drones emit zero RF. This module detects them acoustically:
propellers produce harmonics at blade_count * RPM / 60 Hz. Two-blade props
at 6000 RPM give 200 Hz fundamental with harmonics at 400, 600, 800 Hz.
Single mic = detection only. Two mics = TDOA bearing estimation.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import numpy as np

from csontology import Detection
from sensors.base import SensorSource

logger = logging.getLogger(__name__)

_SPEED_OF_SOUND, _DRONE_FREQ_LOW, _DRONE_FREQ_HIGH = 343.0, 80.0, 900.0
_MIN_HARMONICS, _HARMONIC_TOLERANCE = 3, 0.08
_DEFAULT_FRAME_MS, _MOCK_SAMPLE_RATE = 100, 16000
_MOCK_RPM, _MOCK_BLADE_COUNT = 6000, 2


@dataclass(frozen=True)
class AcousticResult:
    """Intermediate result from a single analysis frame."""
    detected: bool
    confidence: float
    fundamental_hz: Optional[float]
    bearing_deg: Optional[float]


def find_peaks(magnitudes: np.ndarray, threshold: float) -> list[int]:
    """Return indices of local maxima above threshold."""
    peaks: list[int] = []
    for i in range(1, len(magnitudes) - 1):
        above = magnitudes[i] > threshold
        local_max = magnitudes[i] > magnitudes[i - 1] and magnitudes[i] > magnitudes[i + 1]
        if above and local_max:
            peaks.append(i)
    return peaks


def has_harmonic_pattern(
    peak_freqs: np.ndarray,
    min_harmonics: int = _MIN_HARMONICS,
) -> tuple[bool, Optional[float]]:
    """Return (is_harmonic, fundamental_hz) for the given peak frequencies."""
    if len(peak_freqs) < min_harmonics:
        return False, None
    for candidate in peak_freqs:
        if candidate < _DRONE_FREQ_LOW:
            continue
        count = 0
        for pf in peak_freqs:
            ratio = pf / candidate
            nearest_int = round(ratio)
            if nearest_int >= 1 and abs(ratio - nearest_int) < _HARMONIC_TOLERANCE:
                count += 1
        if count >= min_harmonics:
            return True, float(candidate)
    return False, None


def analyze_frame(
    audio_samples: np.ndarray, sample_rate: int, noise_floor_mult: float = 5.0,
) -> AcousticResult:
    """Analyze one audio frame for drone propeller harmonics."""
    _no_det = AcousticResult(False, 0.0, None, None)
    spectrum = np.fft.rfft(audio_samples)
    freqs = np.fft.rfftfreq(len(audio_samples), 1.0 / sample_rate)
    magnitudes = np.abs(spectrum)
    band_mask = (freqs >= _DRONE_FREQ_LOW) & (freqs <= _DRONE_FREQ_HIGH)
    if not np.any(band_mask):
        return _no_det
    band_mags = magnitudes[band_mask]
    threshold = float(np.median(band_mags) * noise_floor_mult)
    peak_indices = find_peaks(band_mags, threshold)
    if len(peak_indices) < _MIN_HARMONICS:
        return _no_det
    peak_freqs = freqs[band_mask][peak_indices]
    is_harmonic, fundamental = has_harmonic_pattern(peak_freqs)
    if not is_harmonic:
        return _no_det
    peak_power = float(np.max(band_mags[peak_indices]))
    snr = peak_power / (float(np.median(band_mags)) + 1e-12)
    return AcousticResult(True, min(0.95, 0.5 + 0.05 * snr), fundamental, None)


def estimate_bearing_tdoa(
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    sample_rate: int,
    mic_separation_m: float,
) -> Optional[float]:
    """TDOA bearing from two mics. Returns degrees or None if invalid."""
    corr = np.correlate(samples_a, samples_b, mode="full")
    center = len(samples_b) - 1
    lag = int(np.argmax(corr)) - center
    delay_s = lag / sample_rate
    sin_val = delay_s * _SPEED_OF_SOUND / mic_separation_m
    if abs(sin_val) > 1.0:
        return None
    return float(math.degrees(math.asin(sin_val)))


def generate_mock_audio(
    duration_s: float, sample_rate: int = _MOCK_SAMPLE_RATE,
    rpm: int = _MOCK_RPM, blade_count: int = _MOCK_BLADE_COUNT,
    num_harmonics: int = 4, noise_level: float = 0.05,
) -> np.ndarray:
    """Generate synthetic audio with drone propeller harmonic signature."""
    n = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    f0 = blade_count * rpm / 60.0
    signal = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, num_harmonics + 1))
    return signal + np.random.default_rng(42).normal(0, noise_level, n)


class AcousticSensorSource(SensorSource):
    """SensorSource for drone detection via propeller acoustics.

    Mock mode generates synthetic audio. Live mode reads from push_audio().
    """
    def __init__(
        self,
        sensor_id: str = "acoustic-1",
        sample_rate: int = _MOCK_SAMPLE_RATE,
        frame_ms: int = _DEFAULT_FRAME_MS,
        mock: bool = True,
        mic_separation_m: Optional[float] = None,
    ) -> None:
        super().__init__(sensor_id)
        self._sample_rate = sample_rate
        self._frame_samples = int(sample_rate * frame_ms / 1000)
        self._mock = mock
        self._mic_sep = mic_separation_m
        self._stop_event = asyncio.Event()
        self._audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=64)
        self._seq = 0

    async def start(self) -> None:
        self._stop_event.clear()
        self._running = True
        logger.info("Acoustic source %s started (mock=%s)", self.sensor_id, self._mock)

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        logger.info("Acoustic source %s stopped", self.sensor_id)

    def push_audio(self, samples: np.ndarray) -> None:
        """Push an audio frame for live-mode processing."""
        try:
            self._audio_queue.put_nowait(samples)
        except asyncio.QueueFull:
            logger.warning("Acoustic queue full, dropping frame")

    async def stream(self) -> AsyncIterator[Detection]:
        if not self._running:
            raise RuntimeError("stream() called before start()")
        while not self._stop_event.is_set():
            frame = await self._next_frame()
            if frame is None:
                continue
            result = analyze_frame(frame, self._sample_rate)
            if result.detected:
                self._seq += 1
                yield Detection(
                    id=f"{self.sensor_id}-{self._seq}",
                    timestamp=time.time(),
                    position=(0.0, 0.0, 0.0),
                    velocity=(0.0, 0.0, 0.0),
                    confidence=result.confidence,
                    sensor_id=self.sensor_id,
                    size_rcs=None,
                )

    async def _next_frame(self) -> Optional[np.ndarray]:
        """Get the next audio frame from mock generator or live queue."""
        if self._mock:
            await asyncio.sleep(self._frame_samples / self._sample_rate)
            if self._stop_event.is_set():
                return None
            return generate_mock_audio(
                self._frame_samples / self._sample_rate,
                self._sample_rate,
            )
        try:
            return await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            return None
