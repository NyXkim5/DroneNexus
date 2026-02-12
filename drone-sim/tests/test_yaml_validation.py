"""
Tests for YAML structure validation — ensures all config files
are well-formed and internally consistent.
"""
import pytest
import yaml
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


class TestMissionFiles:
    def test_all_missions_parse(self):
        missions_dir = CONFIG_DIR / "missions"
        for path in sorted(missions_dir.glob("*.yaml")):
            with open(path) as f:
                config = yaml.safe_load(f)
            assert isinstance(config, dict), f"{path.name} not a dict"

    def test_missions_have_waypoints(self):
        missions_dir = CONFIG_DIR / "missions"
        for path in sorted(missions_dir.glob("*.yaml")):
            with open(path) as f:
                config = yaml.safe_load(f)
            assert "waypoints" in config, f"{path.name} missing waypoints"
            assert len(config["waypoints"]) > 0, f"{path.name} has no waypoints"

    def test_waypoints_have_coordinates(self):
        """Waypoints must have position coordinates (either lat/lon/alt or x_m/y_m/z_m)."""
        missions_dir = CONFIG_DIR / "missions"
        for path in sorted(missions_dir.glob("*.yaml")):
            with open(path) as f:
                config = yaml.safe_load(f)
            for i, wp in enumerate(config["waypoints"]):
                # Support both GPS (lat/lon/alt) and local ENU (position.x_m/y_m/z_m)
                has_gps = "lat" in wp and "lon" in wp and "alt" in wp
                has_local = "position" in wp and isinstance(wp["position"], dict)
                assert has_gps or has_local, \
                    f"{path.name} waypoint {i} missing coordinates (need lat/lon/alt or position)"


class TestSensorConfigConsistency:
    def test_sensor_configs_internally_consistent(self):
        """Noise levels should increase: perfect < typical < noisy < failing."""
        sensors_dir = CONFIG_DIR / "sensors"
        for path in sorted(sensors_dir.glob("*.yaml")):
            with open(path) as f:
                config = yaml.safe_load(f)

            presets = config.get("presets", {})
            if not presets:
                continue

            # Collect all numeric leaf values per preset for comparison
            perfect_vals = _collect_numeric_leaves(presets.get("perfect", {}))
            failing_vals = _collect_numeric_leaves(presets.get("failing", {}))

            # Find common numeric keys
            common_keys = set(perfect_vals.keys()) & set(failing_vals.keys())

            for key in common_keys:
                pv = perfect_vals[key]
                fv = failing_vals[key]
                # For noise/drift/error/dropout params, perfect should not exceed failing
                # Skip keys where higher = better (e.g., full_well_capacity, range, bandwidth)
                skip_keywords = ("capacity", "range", "bandwidth", "resolution",
                                 "saturation", "well", "quantization_bits")
                if any(sk in key for sk in skip_keywords):
                    continue
                if any(kw in key for kw in ("noise", "drift", "dropout", "error",
                                            "bias", "interference", "spike")):
                    # Compare absolute values since error/bias can be negative
                    assert abs(pv) <= abs(fv), \
                        f"{path.name}.{key}: perfect ({pv}) > failing ({fv})"


class TestConfigDirectoryStructure:
    def test_airframes_dir_exists(self):
        assert (CONFIG_DIR / "airframes").is_dir()

    def test_sensors_dir_exists(self):
        assert (CONFIG_DIR / "sensors").is_dir()

    def test_missions_dir_exists(self):
        assert (CONFIG_DIR / "missions").is_dir()

    def test_minimum_airframe_count(self):
        airframes = list((CONFIG_DIR / "airframes").glob("*.yaml"))
        assert len(airframes) >= 6, f"Expected at least 6 airframes, found {len(airframes)}"

    def test_minimum_sensor_config_count(self):
        sensors = list((CONFIG_DIR / "sensors").glob("*.yaml"))
        assert len(sensors) >= 6, f"Expected at least 6 sensor configs, found {len(sensors)}"


def _collect_numeric_leaves(d: dict, prefix: str = "") -> dict:
    """Recursively collect all numeric leaf values from a nested dict."""
    result = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            result[full_key] = v
        elif isinstance(v, dict):
            result.update(_collect_numeric_leaves(v, full_key))
        elif isinstance(v, list):
            # Skip lists (e.g., bias_initial arrays)
            pass
    return result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
