"""
Tests for effector calibration loading.

These assert the shipped effectors.yaml loads into the typed model, that every
effector defines the required params with consistent bands, that to_defender_kwargs
returns the exact keys a DefenderConfig needs, that a missing or malformed file
raises a clear error, and that sample_band stays inside the band.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses
import inspect

import pytest

from csontology import DefenderKind
from wargame.calibration import (
    REQUIRED_FIELDS,
    Calibration,
    CalibrationError,
    EffectorProfile,
    ProvenanceBand,
    load_calibration,
    sample_band,
    to_defender_kwargs,
)
from wargame.scenario import DefenderConfig

# The kinds the shipped file must calibrate. LASER and NET are optional extras.
_CORE_KINDS = (DefenderKind.HPM, DefenderKind.EW, DefenderKind.INTERCEPTOR)


def test_default_file_loads_into_typed_model() -> None:
    calib = load_calibration()
    assert isinstance(calib, Calibration)
    assert calib.schema_version >= 1
    for kind in _CORE_KINDS:
        profile = calib.profile(kind)
        assert isinstance(profile, EffectorProfile)
        assert profile.kind is kind


def test_explicit_path_loads() -> None:
    calib = load_calibration("config/effectors.yaml")
    assert isinstance(calib, Calibration)
    assert calib.source_path.endswith("effectors.yaml")


def test_every_effector_has_required_params_with_ordered_bands() -> None:
    calib = load_calibration()
    for kind, profile in calib.profiles.items():
        for field in REQUIRED_FIELDS:
            band = profile.band(field)
            assert isinstance(band, ProvenanceBand)
            assert band.source, f"{kind} {field} missing provenance source"
            assert band.min <= band.value <= band.max, f"{kind} {field} band order"


def test_to_defender_kwargs_has_exact_defenderconfig_keys() -> None:
    calib = load_calibration()
    kwargs = to_defender_kwargs(calib.profile(DefenderKind.HPM))
    assert set(kwargs) == set(REQUIRED_FIELDS)
    # max_simultaneous must be an int for the DefenderConfig field.
    assert isinstance(kwargs["max_simultaneous"], int)


def test_to_defender_kwargs_builds_a_defenderconfig() -> None:
    calib = load_calibration()
    kwargs = to_defender_kwargs(calib.profile(DefenderKind.HPM))
    # The kwargs are a subset of DefenderConfig fields and construct cleanly.
    config = DefenderConfig(
        id_prefix="HPM",
        kind=DefenderKind.HPM,
        count=1,
        position=(0.0, 0.0, 0.0),
        capacity=18,
        **kwargs,
    )
    assert config.kill_prob == kwargs["kill_prob"]
    assert config.unit_cost == kwargs["unit_cost"]
    # Every kwarg key names a real DefenderConfig field.
    config_fields = {f.name for f in dataclasses.fields(DefenderConfig)}
    assert set(kwargs).issubset(config_fields)


def test_missing_file_raises_clear_error() -> None:
    with pytest.raises(CalibrationError, match="not found"):
        load_calibration("config/does_not_exist.yaml")


def test_malformed_yaml_raises_clear_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("effectors: [this is not a mapping\n")
    with pytest.raises(CalibrationError):
        load_calibration(str(bad))


def test_missing_effectors_key_raises(tmp_path) -> None:
    bad = tmp_path / "empty.yaml"
    bad.write_text("schema_version: 1\n")
    with pytest.raises(CalibrationError, match="effectors"):
        load_calibration(str(bad))


def test_missing_required_field_raises(tmp_path) -> None:
    bad = tmp_path / "partial.yaml"
    bad.write_text(
        "effectors:\n"
        "  HPM:\n"
        "    kill_prob: {value: 0.7, min: 0.4, max: 0.9, source: estimate}\n"
    )
    with pytest.raises(CalibrationError, match="missing fields"):
        load_calibration(str(bad))


def test_unknown_effector_kind_raises(tmp_path) -> None:
    bad = tmp_path / "unknown.yaml"
    bad.write_text(
        "effectors:\n"
        "  PLASMA:\n"
        "    kill_prob: {value: 0.7, min: 0.4, max: 0.9, source: estimate}\n"
    )
    with pytest.raises(CalibrationError, match="unknown effector kind"):
        load_calibration(str(bad))


def test_value_outside_band_raises(tmp_path) -> None:
    bad = tmp_path / "outofband.yaml"
    lines = ["effectors:", "  HPM:"]
    for field in REQUIRED_FIELDS:
        # kill_prob value sits above its max to trip the band check.
        if field == "kill_prob":
            lines.append(f"    {field}: {{value: 5.0, min: 0.4, max: 0.9, source: estimate}}")
        else:
            lines.append(f"    {field}: {{value: 1.0, min: 0.0, max: 2.0, source: estimate}}")
    bad.write_text("\n".join(lines) + "\n")
    with pytest.raises(CalibrationError, match="outside band"):
        load_calibration(str(bad))


def test_sample_band_stays_within_band() -> None:
    calib = load_calibration()
    profile = calib.profile(DefenderKind.HPM)
    for field in REQUIRED_FIELDS:
        band = profile.band(field)
        for fraction in (-1.0, 0.0, 0.25, 0.5, 1.0, 2.0):
            sampled = sample_band(profile, field, fraction)
            assert band.min <= sampled <= band.max


def test_sample_band_endpoints_and_determinism() -> None:
    calib = load_calibration()
    profile = calib.profile(DefenderKind.EW)
    band = profile.band("range_m")
    assert sample_band(profile, "range_m", 0.0) == band.min
    assert sample_band(profile, "range_m", 1.0) == band.max
    # Deterministic: same inputs give the same output.
    assert sample_band(profile, "range_m", 0.3) == sample_band(profile, "range_m", 0.3)


def test_sample_band_unknown_field_raises() -> None:
    calib = load_calibration()
    profile = calib.profile(DefenderKind.HPM)
    with pytest.raises(CalibrationError, match="unknown effector field"):
        sample_band(profile, "not_a_field", 0.5)


def test_profile_missing_kind_raises() -> None:
    calib = load_calibration()
    # JAMMER is a valid DefenderKind but not calibrated in the shipped file.
    if DefenderKind.JAMMER not in calib.profiles:
        with pytest.raises(KeyError):
            calib.profile(DefenderKind.JAMMER)


def test_public_signatures_are_stable() -> None:
    # Guard the public API the lead wires into scenario builders.
    assert str(inspect.signature(load_calibration)) == "(path: 'str | None' = None) -> 'Calibration'"
    assert (
        str(inspect.signature(sample_band))
        == "(profile: 'EffectorProfile', field: 'str', fraction: 'float') -> 'float'"
    )
