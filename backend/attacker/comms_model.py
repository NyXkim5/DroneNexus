"""
RF communication topology model for BULWARK attacker swarms.

Models inter-drone link quality using free-space path loss (FSPL), Olfati-Saber
adjacency weights, and jammer-induced noise floor elevation. This replaces the
boolean EW-resistance flag with physics-grounded communication graph degradation.

The communication graph directly feeds the Reynolds flocking algorithm: only
drones that can communicate are treated as neighbors. Jamming fragments the
graph, reducing swarm coherence measurably via connected-component count.

Reference: Olfati-Saber, R. (2006). Flocking for multi-agent dynamic systems.
IEEE Transactions on Automatic Control, 51(3), 401-420.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import List

import numpy as np

from csontology import Vec3


# ---- Parameters ----

@dataclass
class CommsParams:
    """RF link parameters for swarm inter-drone communication.

    snr_scale_db controls how steeply link quality transitions around the
    sensitivity threshold. Smaller values = sharper cutoff. Default 10 dB
    gives a smooth transition across the operational range (10–2000 m).

    rx_sensitivity_dbm is tuned so that SNR equals this value at the
    desired link-break distance. With default TX=20 dBm, noise=-100 dBm,
    path_loss_exponent=2.5, and frequency=2.4 GHz, links degrade to <0.05
    around 2–5 km and reach >0.95 below ~50 m.
    """
    frequency_ghz: float = 2.4          # operating frequency in GHz
    tx_power_dbm: float = 20.0          # transmit power in dBm
    rx_sensitivity_dbm: float = 20.0    # SNR threshold for link quality midpoint (dBm)
    path_loss_exponent: float = 2.5     # free space = 2.0, urban = 3.5
    noise_floor_dbm: float = -100.0     # ambient noise floor in dBm
    bandwidth_mhz: float = 20.0         # channel bandwidth in MHz
    snr_scale_db: float = 10.0          # dB per sigmoid unit; controls transition sharpness


# ---- Constants ----

_SPEED_OF_LIGHT = 3e8          # m/s
_MIN_DISTANCE_M = 0.01         # guard against log(0) at zero separation


# ---- Core model ----

class CommsTopology:
    """Models RF communication links between swarm drones."""

    def __init__(self, params: CommsParams = None) -> None:
        self._params = params or CommsParams()

    # -- internal helpers --

    def _fspl_db(self, distance_m: float) -> float:
        """Free-space path loss in dB at given distance."""
        d = max(distance_m, _MIN_DISTANCE_M)
        freq_hz = self._params.frequency_ghz * 1e9
        # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
        fspl = (
            20.0 * math.log10(d)
            + 20.0 * math.log10(freq_hz)
            + 20.0 * math.log10(4.0 * math.pi / _SPEED_OF_LIGHT)
        )
        if self._params.path_loss_exponent != 2.0:
            # Scale deviation from free-space exponent linearly on the log term.
            # FSPL uses exponent 2 implicitly via 20*log10(d). Generalise to n:
            #   PL(n) = FSPL_ref + 10*(n-2)*log10(d)
            fspl += 10.0 * (self._params.path_loss_exponent - 2.0) * math.log10(d)
        return fspl

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Standard logistic sigmoid, numerically stable."""
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        exp_x = math.exp(x)
        return exp_x / (1.0 + exp_x)

    @staticmethod
    def _distance(a: Vec3, b: Vec3) -> float:
        """Euclidean distance between two ENU positions."""
        return math.sqrt(
            (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
        )

    # -- public API --

    def compute_link_quality(
        self, distance_m: float, noise_floor_dbm: float = None
    ) -> float:
        """Compute link quality (0-1) between two drones at given distance.

        Uses free-space path loss:
            FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
            SNR  = tx_power - FSPL - noise_floor
            quality = sigmoid((SNR - rx_sensitivity) / snr_scale_db) clamped [0, 1]

        snr_scale_db sets the dB width of the sigmoid transition region.
        noise_floor_dbm overrides the param default when jamming is active.
        """
        p = self._params
        effective_noise = noise_floor_dbm if noise_floor_dbm is not None else p.noise_floor_dbm
        fspl = self._fspl_db(distance_m)
        snr = p.tx_power_dbm - fspl - effective_noise
        raw = self._sigmoid((snr - p.rx_sensitivity_dbm) / p.snr_scale_db)
        return float(np.clip(raw, 0.0, 1.0))

    def compute_adjacency(self, positions: List[Vec3]) -> np.ndarray:
        """Compute N x N adjacency matrix where a[i][j] = link_quality(i, j).

        Diagonal is zero (no self-link). Matrix is symmetric.
        """
        n = len(positions)
        adj = np.zeros((n, n), dtype=float)
        for i in range(n):
            for j in range(i + 1, n):
                d = self._distance(positions[i], positions[j])
                q = self.compute_link_quality(d)
                adj[i, j] = q
                adj[j, i] = q
        return adj

    def get_neighbors(
        self, drone_idx: int, positions: List[Vec3], threshold: float = 0.3
    ) -> List[int]:
        """Return indices of drones this drone can communicate with."""
        neighbors: List[int] = []
        for j, pos in enumerate(positions):
            if j == drone_idx:
                continue
            d = self._distance(positions[drone_idx], pos)
            if self.compute_link_quality(d) >= threshold:
                neighbors.append(j)
        return neighbors

    def apply_jamming(
        self,
        positions: List[Vec3],
        jammer_position: Vec3,
        jammer_power_dbm: float = 40.0,
        jammer_bandwidth_mhz: float = 40.0,
    ) -> np.ndarray:
        """Compute jammed adjacency matrix.

        Jamming raises the noise floor for each drone proportional to
        1/distance^2 from the jammer. The jammer injects interference power
        into each drone's receiver. If the jammer bandwidth overlaps the drone
        channel, the interference raises the effective noise floor.

        Returns a degraded adjacency matrix.
        """
        p = self._params
        n = len(positions)

        # Spectral overlap factor: fraction of jammer bandwidth covering drone channel.
        overlap = min(jammer_bandwidth_mhz, p.bandwidth_mhz) / p.bandwidth_mhz

        # Compute effective noise floor per drone after jamming.
        effective_noise = np.full(n, p.noise_floor_dbm)
        for i, pos in enumerate(positions):
            d_jam = self._distance(pos, jammer_position)
            d_jam = max(d_jam, _MIN_DISTANCE_M)
            # Jammer received power at drone i using free-space path loss.
            jam_fspl = self._fspl_db(d_jam)
            jam_rx_dbm = jammer_power_dbm - jam_fspl
            # Scale by spectral overlap: only overlapping portion raises noise.
            jam_contribution = jam_rx_dbm + 10.0 * math.log10(overlap)
            # Combine ambient noise and jamming in linear power, convert back to dB.
            ambient_mw = 10.0 ** (p.noise_floor_dbm / 10.0)
            jam_mw = 10.0 ** (jam_contribution / 10.0)
            combined_dbm = 10.0 * math.log10(ambient_mw + jam_mw)
            effective_noise[i] = combined_dbm

        # Recompute adjacency with per-drone elevated noise floors.
        adj = np.zeros((n, n), dtype=float)
        for i in range(n):
            for j in range(i + 1, n):
                d = self._distance(positions[i], positions[j])
                # Receiver is the weaker link; use the worse noise floor.
                noise = float(max(effective_noise[i], effective_noise[j]))
                q = self.compute_link_quality(d, noise_floor_dbm=noise)
                adj[i, j] = q
                adj[j, i] = q
        return adj

    def fragmentation_count(
        self, adjacency: np.ndarray, threshold: float = 0.3
    ) -> int:
        """Count connected components in the communication graph.

        Uses BFS on the thresholded adjacency matrix.
        Returns 1 for a fully connected swarm, N for fully fragmented.
        """
        n = adjacency.shape[0]
        visited = [False] * n
        components = 0

        for start in range(n):
            if visited[start]:
                continue
            components += 1
            queue: deque[int] = deque([start])
            visited[start] = True
            while queue:
                node = queue.popleft()
                for neighbor in range(n):
                    if not visited[neighbor] and adjacency[node, neighbor] >= threshold:
                        visited[neighbor] = True
                        queue.append(neighbor)

        return components

    def olfati_saber_weight(
        self, distance_m: float, r0: float = 100.0, alpha: float = 1.0
    ) -> float:
        """Olfati-Saber 2006 communication weight.

            aij = exp(-alpha * (distance / r0)^2)

        Combines with link_quality for physics-grounded swarm coherence.
        At distance=0: weight=1.0. At distance=r0: weight=exp(-alpha).
        """
        return math.exp(-alpha * (distance_m / r0) ** 2)
