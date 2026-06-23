"""
EW effects simulation for the BULWARK wargame engine.

Replaces binary probability-based EW kills with physics-grounded jamming,
spoofing, and comms disruption. Effectiveness depends on distance (inverse
square), drone EW resistance, frequency-band match, and terrain shielding.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from csontology import DefenderKind, Vec3

logger = logging.getLogger("overwatch.attacker.ew")


class EWEffect(str, Enum):
    SIGNAL_JAM = "signal_jam"
    GPS_SPOOF = "gps_spoof"
    VIDEO_DISRUPT = "video_disrupt"
    PROTOCOL_EXPLOIT = "protocol_exploit"
    FULL_DENIAL = "full_denial"
    NO_EFFECT = "no_effect"


class DroneFailsafe(str, Enum):
    HOVER = "hover"
    RTH = "return_to_home"
    LAND = "land_in_place"
    CONTINUE = "continue"
    CRASH = "crash"


@dataclass(frozen=True)
class DroneProfile:
    """Target drone RF and autonomy characteristics for EW modeling."""
    ew_resistant: bool = False
    hardened: bool = False
    has_gps_imu_fusion: bool = False
    frequency_ghz: float = 2.4
    failsafe: DroneFailsafe = DroneFailsafe.HOVER


@dataclass(frozen=True)
class Environment:
    """Propagation environment between effector and target."""
    terrain_shielding_db: float = 0.0
    atmospheric_loss_db: float = 0.0


@dataclass(frozen=True)
class EWResult:
    """Outcome of one EW engagement attempt."""
    effect: EWEffect
    failsafe: DroneFailsafe
    probability: float
    effective_power_ratio: float
    is_neutralized: bool


_EW_KINDS = {DefenderKind.EW, DefenderKind.JAMMER, DefenderKind.HPM}

# (effect, base_probability) tuples per effector kind at max power ratio.
_EFFECT_TABLE: dict[DefenderKind, List[tuple[EWEffect, float]]] = {
    DefenderKind.EW: [
        (EWEffect.FULL_DENIAL, 0.15), (EWEffect.PROTOCOL_EXPLOIT, 0.10),
        (EWEffect.GPS_SPOOF, 0.25), (EWEffect.SIGNAL_JAM, 0.40),
        (EWEffect.VIDEO_DISRUPT, 0.10),
    ],
    DefenderKind.JAMMER: [
        (EWEffect.FULL_DENIAL, 0.05), (EWEffect.SIGNAL_JAM, 0.60),
        (EWEffect.VIDEO_DISRUPT, 0.25), (EWEffect.GPS_SPOOF, 0.10),
    ],
    DefenderKind.HPM: [
        (EWEffect.FULL_DENIAL, 0.70), (EWEffect.SIGNAL_JAM, 0.20),
        (EWEffect.VIDEO_DISRUPT, 0.10),
    ],
}

_NEUTRALIZING = {
    EWEffect.SIGNAL_JAM, EWEffect.GPS_SPOOF,
    EWEffect.PROTOCOL_EXPLOIT, EWEffect.FULL_DENIAL,
}

_NO_EFFECT_RESULT = EWResult(
    EWEffect.NO_EFFECT, DroneFailsafe.CONTINUE, 0.0, 0.0, False,
)


def _distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


def _frequency_match(effector_ghz: float, drone_ghz: float) -> float:
    """0-1 factor. Perfect match = 1.0. Each GHz of separation halves it."""
    return math.exp(-0.7 * abs(effector_ghz - drone_ghz))


class EWEffectSimulator:
    """Computes realistic EW engagement outcomes from physics-based model."""

    def __init__(
        self, reference_range_m: float = 500.0, effector_frequency_ghz: float = 2.4,
    ) -> None:
        self._ref_range = max(1.0, reference_range_m)
        self._effector_freq = effector_frequency_ghz

    def compute_effect(
        self, effector_kind: DefenderKind, effector_position: Vec3,
        drone_profile: DroneProfile, drone_position: Vec3, rng,
        environment: Optional[Environment] = None,
    ) -> EWResult:
        """Determine the EW effect on a target drone."""
        if effector_kind not in _EW_KINDS:
            return _NO_EFFECT_RESULT
        power_ratio = self._power_ratio(
            effector_kind, effector_position, drone_profile,
            drone_position, environment,
        )
        roll = rng.random()
        cumulative = 0.0
        for effect, base_prob in _EFFECT_TABLE.get(effector_kind, []):
            adjusted = self._apply_resistance(
                base_prob * power_ratio, effect, drone_profile,
            )
            cumulative += adjusted
            if roll < cumulative:
                return EWResult(
                    effect=effect,
                    failsafe=self._resolve_failsafe(effect, drone_profile),
                    probability=adjusted,
                    effective_power_ratio=power_ratio,
                    is_neutralized=effect in _NEUTRALIZING,
                )
        return EWResult(
            EWEffect.NO_EFFECT, DroneFailsafe.CONTINUE,
            1.0 - cumulative, power_ratio, False,
        )

    def _power_ratio(
        self, kind: DefenderKind, effector_pos: Vec3,
        profile: DroneProfile, drone_pos: Vec3,
        environment: Optional[Environment],
    ) -> float:
        """Inverse-square power ratio with frequency match and env losses."""
        dist = max(_distance(effector_pos, drone_pos), 1.0)
        ratio = min((self._ref_range / dist) ** 2, 1.0)
        if kind is not DefenderKind.HPM:
            ratio *= _frequency_match(self._effector_freq, profile.frequency_ghz)
        env = environment or Environment()
        loss_db = env.terrain_shielding_db + env.atmospheric_loss_db
        if loss_db > 0.0:
            ratio *= 10.0 ** (-loss_db / 10.0)
        return max(0.0, min(1.0, ratio))

    @staticmethod
    def _apply_resistance(
        probability: float, effect: EWEffect, profile: DroneProfile,
    ) -> float:
        """Scale probability down based on drone resistance traits."""
        if profile.ew_resistant and effect in (
            EWEffect.SIGNAL_JAM, EWEffect.VIDEO_DISRUPT, EWEffect.PROTOCOL_EXPLOIT,
        ):
            return probability * 0.05
        if profile.hardened and effect is EWEffect.FULL_DENIAL:
            return probability * 0.15
        if profile.has_gps_imu_fusion and effect is EWEffect.GPS_SPOOF:
            return probability * 0.10
        return probability

    @staticmethod
    def _resolve_failsafe(effect: EWEffect, profile: DroneProfile) -> DroneFailsafe:
        """Determine drone response. Autonomous drones continue unless full denial."""
        if effect is EWEffect.NO_EFFECT:
            return DroneFailsafe.CONTINUE
        if profile.ew_resistant and effect is not EWEffect.FULL_DENIAL:
            return DroneFailsafe.CONTINUE
        if effect is EWEffect.FULL_DENIAL:
            return DroneFailsafe.CRASH
        return profile.failsafe
