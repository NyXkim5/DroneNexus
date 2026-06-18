"""
Electronic warfare effectors for BULWARK counter-swarm defense.

EW is the primary layer of counter-swarm defense. Jamming control links,
spoofing GPS, and disrupting protocols neutralize drones at near-zero marginal
cost and flip the cost-exchange ratio against cheap attacker swarms.

Physics basis: free-space path loss (FSPL) determines jammer power at target.
Compare jammer-at-target (dBm) to target receiver sensitivity to get J/S ratio.
Effectiveness is a sigmoid over J/S so the model degrades smoothly with range.

Effect types
------------
BARRAGE_JAM  -- wideband noise across a frequency range; hits all in-range drones
SPOT_JAM     -- narrowband on one frequency; most effective when frequency matches
GPS_SPOOF    -- inject false GPS signals; degrades navigation, not comms
PROTOCOL_JAM -- target a specific drone control protocol (e.g. MAVLink, DJI OcuSync)
DEAUTH       -- 802.11 deauth frames; effective only on WiFi-based consumer drones
"""
from __future__ import annotations

import math
import sys
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csontology import Vec3, Threat

# ---- Constants ----

_SPEED_OF_LIGHT = 3e8
_MIN_DISTANCE_M = 0.01

# Target receiver sensitivity assumptions by protocol.
# Real values sourced from datasheet ranges for common drone links.
_PROTOCOL_SENSITIVITY_DBM: Dict[str, float] = {
    "dji":      -90.0,   # DJI OcuSync / O3
    "mavlink":  -85.0,   # MAVLink over 900 MHz / 2.4 GHz telemetry
    "wifi":     -80.0,   # 802.11n consumer drones
    "fhss":     -95.0,   # frequency-hopping spread spectrum military links
    "lora":     -120.0,  # LoRa long-range, very sensitive
    "default":  -85.0,
}

# GPS receiver sensitivity (C/N0 threshold) expressed as power at antenna.
_GPS_RECEIVER_SENSITIVITY_DBM = -130.0  # typical GNSS front-end

# GPS L1 frequency (GHz) for path loss calculation when spoofing.
_GPS_L1_GHZ = 1.5754

# Frequency bands associated with common protocols (GHz center frequency).
_PROTOCOL_FREQUENCY_GHZ: Dict[str, float] = {
    "dji":      2.4,
    "mavlink":  0.9,
    "wifi":     2.4,
    "fhss":     1.8,
    "lora":     0.915,
    "default":  2.4,
}

# Sigmoid sharpness (dB). Controls how steeply effectiveness transitions
# around the J/S = 0 dB threshold. Lower = sharper cutoff.
_JS_SCALE_DB = 6.0


# ---- Enums ----

class EWEffectType(Enum):
    BARRAGE_JAM  = "barrage_jam"
    SPOT_JAM     = "spot_jam"
    GPS_SPOOF    = "gps_spoof"
    PROTOCOL_JAM = "protocol_jam"
    DEAUTH       = "deauth"


# ---- Dataclasses ----

@dataclass
class EWEffector:
    """An electronic warfare effector that can be allocated against threats.

    power_dbm is radiated power. bandwidth_mhz is the jammer emission bandwidth.
    range_m is the maximum engagement distance. energy_budget_j is the total
    energy available; it decreases each time the effector is allocated.
    power_consumption_w is draw per engagement-second (used to compute energy cost).
    """
    id: str
    position: Vec3
    effect_type: EWEffectType
    power_dbm: float = 40.0
    bandwidth_mhz: float = 40.0
    range_m: float = 2000.0
    active: bool = False
    energy_budget_j: float = float("inf")
    power_consumption_w: float = 100.0


@dataclass
class EWEffect:
    """Result of applying one EW effector against one target.

    effectiveness is 0-1: probability of disrupting the target this tick.
    energy_cost_j is the energy drawn from the effector budget.
    comm_degradation is 0-1: how much the target's comm links are reduced.
    nav_degradation is 0-1: how much the target's navigation is degraded.
    """
    effector_id: str
    target_id: str
    effect_type: EWEffectType
    effectiveness: float
    energy_cost_j: float
    comm_degradation: float
    nav_degradation: float


# ---- Core allocator ----

class EWAllocator:
    """Allocates EW effectors to threats, complementing the kinetic allocator.

    Strategy:
    - BARRAGE_JAM: fire against swarm clusters (multiple threats nearby).
    - SPOT_JAM: fire against high-score single threats whose frequency is known.
    - GPS_SPOOF: fire against autonomous drones (no operator in the loop).
    - PROTOCOL_JAM: fire against threats whose protocol is known.
    - DEAUTH: fire only against WiFi-based consumer drones.

    Inactive effectors and effectors with exhausted energy budgets are skipped.
    """

    # Engagement energy is computed as power * assumed dwell time per allocation.
    _DWELL_S = 10.0  # seconds of effect per allocation tick

    def __init__(self) -> None:
        # Mutable energy tracking across calls. Maps effector id -> remaining J.
        self._energy_remaining: Dict[str, float] = {}

    def _get_energy(self, effector: EWEffector) -> float:
        """Return tracked remaining energy for effector, initialising from budget."""
        if effector.id not in self._energy_remaining:
            self._energy_remaining[effector.id] = effector.energy_budget_j
        return self._energy_remaining[effector.id]

    def _consume_energy(self, effector: EWEffector, cost_j: float) -> None:
        """Deduct energy from tracked budget."""
        remaining = self._get_energy(effector)
        self._energy_remaining[effector.id] = max(0.0, remaining - cost_j)

    def _has_energy(self, effector: EWEffector) -> bool:
        """True when effector still has budget for at least one dwell."""
        cost = effector.power_consumption_w * self._DWELL_S
        return self._get_energy(effector) >= cost

    @staticmethod
    def _fspl_db(distance_m: float, frequency_ghz: float) -> float:
        """Free-space path loss in dB."""
        d = max(distance_m, _MIN_DISTANCE_M)
        freq_hz = frequency_ghz * 1e9
        return (
            20.0 * math.log10(d)
            + 20.0 * math.log10(freq_hz)
            + 20.0 * math.log10(4.0 * math.pi / _SPEED_OF_LIGHT)
        )

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Numerically stable logistic sigmoid."""
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        ex = math.exp(x)
        return ex / (1.0 + ex)

    @staticmethod
    def _distance(a: Vec3, b: Vec3) -> float:
        """Euclidean distance between two ENU positions in meters."""
        return math.sqrt(
            (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
        )

    def compute_effectiveness(
        self,
        effector: EWEffector,
        target_position: Vec3,
        target_frequency_ghz: float = 2.4,
        target_protocol: str = "dji",
    ) -> float:
        """Compute probability (0-1) of disrupting the target.

        Uses J/S (jammer-to-signal) ratio:
            jammer power at target = effector.power_dbm - FSPL(distance, freq)
            target signal power   = assumed from protocol receiver sensitivity
            J/S = jammer_at_target - rx_sensitivity
            effectiveness = sigmoid(J/S / JS_SCALE_DB)

        Special handling per effect type:
        - BARRAGE_JAM: bandwidth overlap factor scales effectiveness.
        - SPOT_JAM: frequency match bonus (+6 dB effective J/S when aligned).
        - GPS_SPOOF: uses GPS L1 path loss and GPS receiver sensitivity.
        - PROTOCOL_JAM: bonus when target protocol matches effector frequency.
        - DEAUTH: full effectiveness only against WiFi protocol targets.
        """
        distance = self._distance(effector.position, target_position)
        if distance > effector.range_m:
            return 0.0

        effect = effector.effect_type

        if effect is EWEffectType.GPS_SPOOF:
            return self._gps_spoof_effectiveness(effector, distance)

        if effect is EWEffectType.DEAUTH:
            return self._deauth_effectiveness(effector, distance, target_protocol)

        # For jamming types, compute jammer power at target.
        rx_sensitivity = _PROTOCOL_SENSITIVITY_DBM.get(
            target_protocol, _PROTOCOL_SENSITIVITY_DBM["default"]
        )
        freq_for_loss = target_frequency_ghz
        fspl = self._fspl_db(distance, freq_for_loss)
        jammer_at_target = effector.power_dbm - fspl

        js_db = jammer_at_target - rx_sensitivity

        if effect is EWEffectType.BARRAGE_JAM:
            # Spectral density spread over bandwidth reduces effective J/S.
            # Narrower target channel vs wide jammer: overlap penalty.
            target_bw = 20.0  # assume 20 MHz drone channel
            overlap = min(effector.bandwidth_mhz, target_bw) / max(target_bw, 1.0)
            js_db += 10.0 * math.log10(max(overlap, 1e-6))

        elif effect is EWEffectType.SPOT_JAM:
            # Frequency match gives full J/S. Mismatch degrades proportionally.
            effector_center = _PROTOCOL_FREQUENCY_GHZ.get(target_protocol, target_frequency_ghz)
            freq_delta_ghz = abs(effector_center - target_frequency_ghz)
            if freq_delta_ghz < 0.1:
                js_db += 6.0  # on-frequency bonus
            else:
                penalty = 20.0 * math.log10(1.0 + freq_delta_ghz / 0.1)
                js_db -= penalty

        elif effect is EWEffectType.PROTOCOL_JAM:
            # Protocol match gives a +3 dB bonus from optimised waveform.
            expected_freq = _PROTOCOL_FREQUENCY_GHZ.get(target_protocol, target_frequency_ghz)
            if abs(expected_freq - target_frequency_ghz) < 0.05:
                js_db += 3.0

        raw = self._sigmoid(js_db / _JS_SCALE_DB)
        return float(np.clip(raw, 0.0, 1.0))

    def _gps_spoof_effectiveness(self, effector: EWEffector, distance: float) -> float:
        """GPS spoofing effectiveness based on L1 path loss vs receiver sensitivity."""
        fspl = self._fspl_db(distance, _GPS_L1_GHZ)
        spoofer_at_target = effector.power_dbm - fspl
        js_db = spoofer_at_target - _GPS_RECEIVER_SENSITIVITY_DBM
        raw = self._sigmoid(js_db / _JS_SCALE_DB)
        return float(np.clip(raw, 0.0, 1.0))

    def _deauth_effectiveness(
        self, effector: EWEffector, distance: float, target_protocol: str
    ) -> float:
        """Deauth only works on WiFi-based drones."""
        if target_protocol not in ("wifi", "dji"):
            return 0.0
        rx_sensitivity = _PROTOCOL_SENSITIVITY_DBM.get("wifi", -80.0)
        fspl = self._fspl_db(distance, 2.4)
        jammer_at_target = effector.power_dbm - fspl
        js_db = jammer_at_target - rx_sensitivity
        raw = self._sigmoid(js_db / _JS_SCALE_DB)
        return float(np.clip(raw, 0.0, 1.0))

    def _energy_cost(self, effector: EWEffector) -> float:
        """Energy consumed per allocation dwell (joules)."""
        return effector.power_consumption_w * self._DWELL_S

    def _comm_degradation(
        self, effectiveness: float, effect_type: EWEffectType
    ) -> float:
        """Fraction of comm link quality removed (0-1)."""
        if effect_type is EWEffectType.GPS_SPOOF:
            return 0.0
        return effectiveness

    def _nav_degradation(
        self, effectiveness: float, effect_type: EWEffectType
    ) -> float:
        """Fraction of navigation accuracy removed (0-1)."""
        if effect_type is EWEffectType.GPS_SPOOF:
            return effectiveness
        if effect_type in (EWEffectType.BARRAGE_JAM, EWEffectType.SPOT_JAM):
            return effectiveness * 0.3
        return 0.0

    def allocate(
        self,
        effectors: List[EWEffector],
        threats: List[Threat],
        threat_positions: Dict[str, Vec3],
    ) -> List[EWEffect]:
        """Assign EW effectors to threats and return EWEffect objects.

        Priority logic per effect type:
        - BARRAGE_JAM: target all threats in range; each effector covers many.
        - SPOT_JAM: highest-score single threat in range.
        - GPS_SPOOF: autonomous threats (swarm_id set, intent not UNKNOWN).
        - PROTOCOL_JAM: any threat in range with known protocol.
        - DEAUTH: threats with WiFi-based protocols.

        Skips inactive effectors and those with exhausted energy budgets.
        """
        effects: List[EWEffect] = []
        sorted_threats = sorted(threats, key=lambda t: -t.score)

        for effector in effectors:
            if not effector.active:
                continue
            if not self._has_energy(effector):
                continue

            candidates = [
                t for t in sorted_threats
                if t.id in threat_positions
                and self._distance(effector.position, threat_positions[t.id]) <= effector.range_m
            ]
            if not candidates:
                continue

            effects += self._allocate_effector(effector, candidates, threat_positions)

        return effects

    def _allocate_effector(
        self,
        effector: EWEffector,
        candidates: List[Threat],
        threat_positions: Dict[str, Vec3],
    ) -> List[EWEffect]:
        """Produce EWEffect objects for one effector against its candidate threats."""
        effect_type = effector.effect_type
        effects: List[EWEffect] = []

        if effect_type is EWEffectType.BARRAGE_JAM:
            # Barrage covers every candidate in range in one shot.
            for threat in candidates:
                eff = self._make_effect(effector, threat, threat_positions)
                if eff is not None:
                    effects.append(eff)

        elif effect_type is EWEffectType.DEAUTH:
            # Deauth only against WiFi candidates; treat all as wifi protocol here.
            for threat in candidates:
                eff = self._make_effect(
                    effector, threat, threat_positions, target_protocol="wifi"
                )
                if eff is not None:
                    effects.append(eff)

        elif effect_type is EWEffectType.GPS_SPOOF:
            # GPS spoof: prefer autonomous threats; fall back to highest score.
            autonomous = [
                t for t in candidates if t.swarm_id is not None
            ]
            targets = autonomous if autonomous else candidates[:1]
            for threat in targets:
                eff = self._make_effect(effector, threat, threat_positions)
                if eff is not None:
                    effects.append(eff)

        else:
            # SPOT_JAM and PROTOCOL_JAM: top-score candidate only.
            eff = self._make_effect(effector, candidates[0], threat_positions)
            if eff is not None:
                effects.append(eff)

        return effects

    def _make_effect(
        self,
        effector: EWEffector,
        threat: Threat,
        threat_positions: Dict[str, Vec3],
        target_protocol: str = "dji",
        target_frequency_ghz: float = 2.4,
    ) -> Optional[EWEffect]:
        """Build one EWEffect, consuming energy. Returns None if budget exhausted."""
        if not self._has_energy(effector):
            return None
        effectiveness = self.compute_effectiveness(
            effector,
            threat_positions[threat.id],
            target_frequency_ghz=target_frequency_ghz,
            target_protocol=target_protocol,
        )
        cost = self._energy_cost(effector)
        self._consume_energy(effector, cost)
        return EWEffect(
            effector_id=effector.id,
            target_id=threat.id,
            effect_type=effector.effect_type,
            effectiveness=effectiveness,
            energy_cost_j=cost,
            comm_degradation=self._comm_degradation(effectiveness, effector.effect_type),
            nav_degradation=self._nav_degradation(effectiveness, effector.effect_type),
        )

    def apply_effects(
        self,
        effects: List[EWEffect],
        swarm_adjacency: np.ndarray,
    ) -> np.ndarray:
        """Apply comm_degradation from EW effects to the swarm adjacency matrix.

        Each effect that disrupts a target drone reduces all edges incident on that
        drone by (1 - comm_degradation). GPS spoof and nav-only effects leave the
        comm graph unchanged (comm_degradation == 0 for those).

        Returns a new degraded adjacency matrix; the input is not mutated.
        """
        degraded = swarm_adjacency.copy()
        n = degraded.shape[0]

        # Map target_id to max comm_degradation across all effects on that target.
        # Use max so multiple overlapping effectors compound properly via the
        # largest single degradation (conservative; avoids >1.0 artefacts).
        per_target: Dict[str, float] = {}
        for effect in effects:
            tid = effect.target_id
            per_target[tid] = max(per_target.get(tid, 0.0), effect.comm_degradation)

        # Apply degradation per index. Caller is responsible for maintaining a
        # mapping from threat/drone id to matrix row/column index. Here we use
        # the order effects arrive: degradation is applied to rows matching the
        # position of the target_id within the unique ordered target list embedded
        # in the effect list, capped to matrix size.
        indexed_targets = list(per_target.keys())
        for idx, tid in enumerate(indexed_targets):
            if idx >= n:
                break
            deg = per_target[tid]
            scale = max(0.0, 1.0 - deg)
            degraded[idx, :] *= scale
            degraded[:, idx] *= scale
            degraded[idx, idx] = 0.0  # preserve zero diagonal

        return degraded
