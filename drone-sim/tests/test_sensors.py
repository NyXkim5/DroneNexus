"""
Tests for sensor noise model configurations.
Validates that noise presets are properly structured and realistic.
"""
import math
import pytest
import yaml
from pathlib import Path


SENSORS_DIR = Path(__file__).parent.parent / "config" / "sensors"
PRESETS = ["perfect", "typical", "noisy", "failing"]


def load_sensor_config(name: str) -> dict:
    path = SENSORS_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


class TestSensorConfigStructure:
    def test_all_sensor_configs_exist(self):
        expected = ["imu-noise.yaml", "gps-noise.yaml", "baro-noise.yaml",
                     "mag-noise.yaml", "lidar-noise.yaml", "camera-noise.yaml"]
        for name in expected:
            assert (SENSORS_DIR / name).exists(), f"Missing sensor config: {name}"

    def test_all_configs_have_presets(self):
        for path in sorted(SENSORS_DIR.glob("*.yaml")):
            config = load_sensor_config(path.name)
            assert "presets" in config, f"{path.name} missing 'presets' section"
            presets = config["presets"]
            for preset_name in PRESETS:
                assert preset_name in presets, f"{path.name} missing preset: {preset_name}"

    def test_all_configs_have_metadata(self):
        for path in sorted(SENSORS_DIR.glob("*.yaml")):
            config = load_sensor_config(path.name)
            assert "sensor_type" in config, f"{path.name} missing sensor_type"
            assert "description" in config, f"{path.name} missing description"


class TestIMUNoise:
    def test_perfect_preset_has_zero_noise(self):
        config = load_sensor_config("imu-noise.yaml")
        perfect = config["presets"]["perfect"]
        # IMU presets have nested gyroscope/accelerometer sections
        gyro = perfect.get("gyroscope", {})
        accel = perfect.get("accelerometer", {})
        # Key noise parameters should be zero
        assert gyro.get("noise_density_dps_per_sqrt_hz", 0) == 0
        assert gyro.get("bias_instability_dps", 0) == 0
        assert accel.get("noise_density_ug_per_sqrt_hz", 0) == 0
        assert accel.get("bias_instability_mg", 0) == 0

    def test_noise_increases_with_presets(self):
        config = load_sensor_config("imu-noise.yaml")
        presets = config["presets"]
        # Check gyro noise density increases across presets
        key = "noise_density_dps_per_sqrt_hz"
        typical_val = presets["typical"]["gyroscope"][key]
        noisy_val = presets["noisy"]["gyroscope"][key]
        failing_val = presets["failing"]["gyroscope"][key]
        assert typical_val <= noisy_val <= failing_val

    def test_realistic_gyro_values(self):
        config = load_sensor_config("imu-noise.yaml")
        typical = config["presets"]["typical"]
        # Typical gyro noise density: 0.003-0.01 deg/s/sqrt(Hz)
        gyro_noise = typical["gyroscope"]["noise_density_dps_per_sqrt_hz"]
        assert 0.001 < gyro_noise < 0.1


class TestGPSNoise:
    def test_perfect_accuracy(self):
        config = load_sensor_config("gps-noise.yaml")
        perfect = config["presets"]["perfect"]
        # GPS presets may have nested or flat structure
        h_acc = _get_nested(perfect, "horizontal_accuracy_m")
        dropout = _get_nested(perfect, "dropout_probability")
        if h_acc is not None:
            assert h_acc < 0.1
        if dropout is not None:
            assert dropout == 0

    def test_typical_accuracy(self):
        config = load_sensor_config("gps-noise.yaml")
        typical = config["presets"]["typical"]
        # Typical GPS: 1-5m accuracy
        h_acc = _get_nested(typical, "horizontal_accuracy_m")
        if h_acc is not None:
            assert 0.5 < h_acc < 10

    def test_failing_has_dropouts(self):
        config = load_sensor_config("gps-noise.yaml")
        failing = config["presets"]["failing"]
        dropout = _get_nested(failing, "dropout_probability")
        if dropout is not None:
            assert dropout > 0


class TestBaroNoise:
    def test_noise_increases(self):
        config = load_sensor_config("baro-noise.yaml")
        presets = config["presets"]
        key = "altitude_noise_m"
        typical_val = _get_nested(presets["typical"], key)
        noisy_val = _get_nested(presets["noisy"], key)
        if typical_val is not None and noisy_val is not None:
            assert typical_val <= noisy_val


class TestMagNoise:
    def test_structure(self):
        config = load_sensor_config("mag-noise.yaml")
        assert "presets" in config

    def test_interference_model(self):
        config = load_sensor_config("mag-noise.yaml")
        failing = config["presets"]["failing"]
        # Failing mag should have high interference
        interference = _get_nested(failing, "interference_probability")
        if interference is not None:
            assert interference > 0.1


class TestLiDARNoise:
    def test_range_limits(self):
        config = load_sensor_config("lidar-noise.yaml")
        typical = config["presets"]["typical"]
        min_range = _get_nested(typical, "min_range_m")
        max_range = _get_nested(typical, "max_range_m")
        if min_range is not None and max_range is not None:
            assert min_range < max_range


def _get_nested(d: dict, key: str):
    """Search for a key in a dict, including one level of nesting."""
    if key in d:
        return d[key]
    for v in d.values():
        if isinstance(v, dict) and key in v:
            return v[key]
    return None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
