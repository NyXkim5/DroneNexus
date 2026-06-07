"""
Effector calibration loader for the BULWARK counter-swarm wargame.

This module makes effector performance data driven. It reads config/effectors.yaml
into typed dataclasses so authored kill curves can be replaced by measured data
without code changes. Scenario builders construct effectors FROM a Calibration
instead of from hard coded literals.

Each effector field carries a value plus a provenance source and a plausible
min/max band. The band records calibration uncertainty so the sensitivity sweep
can vary effectors across their measured range rather than arbitrary numbers.

Public API
----------
load_calibration(path)          read the yaml into a typed Calibration
Calibration.profile(kind)       get one EffectorProfile by DefenderKind
to_defender_kwargs(profile)     kwargs a DefenderConfig needs, built from a profile
sample_band(profile, field, f)  deterministic value within a field band

All failures raise a clear error. There are no silent fallbacks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from csontology import DefenderKind

logger = logging.getLogger("overwatch.wargame.calibration")

# Default calibration file relative to the backend cwd. Imports run from backend.
DEFAULT_CALIBRATION_PATH = Path("config") / "effectors.yaml"

# The per field parameters every effector profile must define.
REQUIRED_FIELDS: tuple[str, ...] = (
    "kill_prob",
    "effect_radius_m",
    "max_simultaneous",
    "range_m",
    "reload_s",
    "unit_cost",
)


class CalibrationError(ValueError):
    """Raised when the calibration file is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class ProvenanceBand:
    """One calibrated value with its source and a plausible min/max band.

    value is the current planning figure. min and max bound the credible range
    given uncertainty. source names where the value came from, for example
    vendor_spec, bench_test, range_trial, physics_model, or estimate. note records
    what real measurement would replace the value.
    """

    value: float
    min: float
    max: float
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.min <= self.value <= self.max:
            raise CalibrationError(
                f"value {self.value} outside band [{self.min}, {self.max}]"
            )


@dataclass(frozen=True)
class EffectorProfile:
    """A full calibrated effector, one ProvenanceBand per DefenderConfig field."""

    kind: DefenderKind
    description: str
    kill_prob: ProvenanceBand
    effect_radius_m: ProvenanceBand
    max_simultaneous: ProvenanceBand
    range_m: ProvenanceBand
    reload_s: ProvenanceBand
    unit_cost: ProvenanceBand

    def band(self, field: str) -> ProvenanceBand:
        """Return the ProvenanceBand for one field. Raises on an unknown field."""
        if field not in REQUIRED_FIELDS:
            raise CalibrationError(f"unknown effector field '{field}'")
        return getattr(self, field)


@dataclass(frozen=True)
class Calibration:
    """All effector profiles keyed by DefenderKind, loaded from one file."""

    profiles: Mapping[DefenderKind, EffectorProfile]
    schema_version: int
    source_path: str

    def profile(self, kind: DefenderKind) -> EffectorProfile:
        """Return the profile for one kind. Raises KeyError if not calibrated."""
        if kind not in self.profiles:
            raise KeyError(f"no calibration for {kind}, have {list(self.profiles)}")
        return self.profiles[kind]


def _band_from_dict(field: str, raw: Any) -> ProvenanceBand:
    """Build a ProvenanceBand from one parsed field mapping. Raises if malformed."""
    if not isinstance(raw, Mapping):
        raise CalibrationError(f"field '{field}' must be a mapping, got {type(raw)}")
    for key in ("value", "min", "max", "source"):
        if key not in raw:
            raise CalibrationError(f"field '{field}' missing '{key}'")
    return ProvenanceBand(
        value=float(raw["value"]),
        min=float(raw["min"]),
        max=float(raw["max"]),
        source=str(raw["source"]),
        note=str(raw.get("note", "")),
    )


def _profile_from_dict(kind: DefenderKind, raw: Mapping[str, Any]) -> EffectorProfile:
    """Build one EffectorProfile from a parsed effector mapping."""
    bands = {field: _band_from_dict(field, raw[field]) for field in _checked(kind, raw)}
    return EffectorProfile(
        kind=kind,
        description=str(raw.get("description", "")),
        **bands,
    )


def _checked(kind: DefenderKind, raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Return REQUIRED_FIELDS after confirming every one is present for a kind."""
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise CalibrationError(f"effector {kind.value} missing fields {missing}")
    return REQUIRED_FIELDS


def load_calibration(path: str | None = None) -> Calibration:
    """Read effectors.yaml into a typed Calibration.

    Defaults to config/effectors.yaml relative to the backend cwd. Raises
    CalibrationError on a missing or malformed file and on an unknown effector
    kind. No silent fallbacks.
    """
    file_path = Path(path) if path is not None else DEFAULT_CALIBRATION_PATH
    if not file_path.exists():
        raise CalibrationError(f"calibration file not found: {file_path}")
    raw = _parse_yaml(file_path)
    effectors = raw.get("effectors")
    if not isinstance(effectors, Mapping) or not effectors:
        raise CalibrationError(f"calibration file has no 'effectors' mapping: {file_path}")
    profiles = _build_profiles(effectors)
    logger.info("loaded calibration for %d effectors from %s", len(profiles), file_path)
    return Calibration(
        profiles=profiles,
        schema_version=int(raw.get("schema_version", 1)),
        source_path=str(file_path),
    )


def _parse_yaml(file_path: Path) -> Mapping[str, Any]:
    """Parse a yaml file into a top level mapping. Raises on a malformed file."""
    try:
        raw = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise CalibrationError(f"calibration file is not valid yaml: {file_path}") from exc
    if not isinstance(raw, Mapping):
        raise CalibrationError(f"calibration file must be a mapping: {file_path}")
    return raw


def _build_profiles(
    effectors: Mapping[str, Any],
) -> Dict[DefenderKind, EffectorProfile]:
    """Build one EffectorProfile per named effector. Raises on an unknown kind."""
    profiles: Dict[DefenderKind, EffectorProfile] = {}
    for name, raw in effectors.items():
        try:
            kind = DefenderKind(name)
        except ValueError as exc:
            raise CalibrationError(f"unknown effector kind '{name}'") from exc
        if not isinstance(raw, Mapping):
            raise CalibrationError(f"effector '{name}' must be a mapping")
        profiles[kind] = _profile_from_dict(kind, raw)
    return profiles


def to_defender_kwargs(profile: EffectorProfile) -> Dict[str, float | int]:
    """Return the kwargs a DefenderConfig needs, built from a profile.

    Maps each calibrated value onto the matching DefenderConfig field so a
    scenario builder can construct an effector FROM calibration instead of from
    hard coded literals. max_simultaneous comes back as an int.
    """
    return {
        "kill_prob": profile.kill_prob.value,
        "effect_radius_m": profile.effect_radius_m.value,
        "max_simultaneous": int(round(profile.max_simultaneous.value)),
        "range_m": profile.range_m.value,
        "reload_s": profile.reload_s.value,
        "unit_cost": profile.unit_cost.value,
    }


def sample_band(profile: EffectorProfile, field: str, fraction: float) -> float:
    """Return a value within a field band, deterministic given inputs.

    fraction 0.0 returns the band min, 1.0 returns the max, 0.5 the midpoint. It
    clamps to [0, 1] so the sensitivity sweep stays inside the calibrated range no
    matter the input. Lets the sweep vary effectors across measured uncertainty.
    """
    band = profile.band(field)
    clamped = min(1.0, max(0.0, fraction))
    return band.min + (band.max - band.min) * clamped
