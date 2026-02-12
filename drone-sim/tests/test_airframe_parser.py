"""
Tests for airframe YAML parsing and validation.
"""
import pytest
import yaml
from pathlib import Path

AIRFRAMES_DIR = Path(__file__).parent.parent / "config" / "airframes"

# Map from frame type strings used in YAML to canonical frame families
FRAME_TYPES = ("quad_x", "quad_h", "quad_plus", "quad_deadcat",
               "hex_x", "hex_plus", "hex_y6", "hex_coaxial",
               "octo_x", "octo_plus", "octo_coaxial",
               "fixed_wing", "vtol")


def load_airframe(name: str) -> dict:
    with open(AIRFRAMES_DIR / name) as f:
        return yaml.safe_load(f)


class TestYAMLParsing:
    def test_all_files_parse(self):
        """Every YAML file must parse without errors."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            with open(path) as f:
                config = yaml.safe_load(f)
            assert isinstance(config, dict), f"{path.name} did not parse as dict"

    def test_expected_presets_exist(self):
        """All expected preset files should exist."""
        expected = [
            "5inch-fpv-freestyle.yaml",
            "3inch-cinewhoop.yaml",
            "7inch-long-range.yaml",
            "mapping-quad.yaml",
            "hex-heavy-lift.yaml",
            "octo-agricultural.yaml",
        ]
        for name in expected:
            assert (AIRFRAMES_DIR / name).exists(), f"Missing preset: {name}"


class TestAirframeSchema:
    def test_frame_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            frame = config["frame"]
            assert frame["type"] in FRAME_TYPES, \
                f"{path.name}: unknown frame type '{frame['type']}'"
            assert frame["wheelbase_mm"] > 0
            assert frame["weight_g"] > 0

    def test_motors_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            motors = config["motors"]
            assert motors["count"] >= 3
            assert motors["kv"] > 0
            assert motors["weight_g"] > 0

    def test_propellers_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            props = config["propellers"]
            assert props["static_thrust_g_per_motor"] > 0
            assert props["diameter_in"] > 0
            assert props["blades"] >= 2

    def test_battery_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            batt = config["battery"]
            assert 1 <= batt["cells_series"] <= 14
            assert batt["capacity_mah"] > 0
            assert batt["voltage_full_v"] > batt["voltage_nominal_v"] > batt["voltage_empty_v"]
            assert batt["weight_g"] > 0

    def test_sensors_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            sensors = config["sensors"]
            assert "imu" in sensors, f"{path.name} missing imu sensor"

    def test_electronics_section(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            elec = config["electronics"]
            assert "flight_controller" in elec
            assert "esc" in elec


class TestAirframeCategoryDiversity:
    def test_has_fpv_preset(self):
        configs = [load_airframe(p.name) for p in AIRFRAMES_DIR.glob("*.yaml")]
        categories = [c.get("category", "") for c in configs]
        assert any("fpv" in cat.lower() for cat in categories), \
            f"No FPV category found in {categories}"

    def test_has_commercial_preset(self):
        configs = [load_airframe(p.name) for p in AIRFRAMES_DIR.glob("*.yaml")]
        categories = [c.get("category", "") for c in configs]
        commercial_keywords = ("commercial", "industrial", "agricultural", "mapping", "heavy_lift")
        assert any(any(kw in cat.lower() for kw in commercial_keywords) for cat in categories), \
            f"No commercial/industrial category found in {categories}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
