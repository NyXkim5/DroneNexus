"""
Tests for cross-component compatibility validation.
"""
import pytest
import yaml
from pathlib import Path

AIRFRAMES_DIR = Path(__file__).parent.parent / "config" / "airframes"

# Map frame type prefixes to expected motor counts
FRAME_MOTOR_COUNT = {
    "quad": 4,
    "hex": 6,
    "octo": 8,
}


def load_airframe(name: str) -> dict:
    path = AIRFRAMES_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


class TestVoltageCompatibility:
    def test_battery_cells_in_range(self):
        """Battery cell count within compatible range."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            cells = config["battery"]["cells_series"]
            compat = config.get("compatibility", {})
            min_cells = compat.get("min_battery_cells")
            max_cells = compat.get("max_battery_cells")
            if min_cells and max_cells:
                assert min_cells <= cells <= max_cells, \
                    f"{path.name}: {cells}S outside {min_cells}-{max_cells}S range"

    def test_esc_voltage_rating(self):
        """ESC voltage range string should encompass battery cell count."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            cells = config["battery"]["cells_series"]
            esc = config["electronics"].get("esc", {})
            voltage_range = esc.get("voltage_range", "")
            if voltage_range:
                # Parse "3S-6S" format
                parts = voltage_range.replace("S", "").split("-")
                if len(parts) == 2:
                    min_s, max_s = int(parts[0]), int(parts[1])
                    assert min_s <= cells <= max_s, \
                        f"{path.name}: {cells}S battery outside ESC range {voltage_range}"


class TestMechanicalCompatibility:
    def test_prop_clearance_positive(self):
        """Prop clearance must be positive."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            clearance = config.get("compatibility", {}).get("propeller_clearance_mm")
            if clearance is not None:
                assert clearance > 0, f"{path.name}: Prop clearance {clearance}mm invalid"

    def test_motor_count_matches_frame_type(self):
        """Motor count should match frame type prefix."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            frame_type = config["frame"]["type"]
            motor_count = config["motors"]["count"]
            for prefix, expected_count in FRAME_MOTOR_COUNT.items():
                if frame_type.startswith(prefix):
                    assert motor_count == expected_count, \
                        f"{path.name}: {frame_type} should have {expected_count} motors, has {motor_count}"
                    break


class TestFirmwareCompatibility:
    def test_firmware_listed_in_compatibility(self):
        """Selected firmware should appear in compatibility firmware list."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            fc_firmware = config["electronics"]["flight_controller"].get("firmware")
            compat_firmware = config.get("compatibility", {}).get("firmware", [])
            if fc_firmware and compat_firmware:
                # The compatibility list uses strings like "Betaflight >= 4.4"
                # or "ArduCopter >= 4.3" (ArduPilot project name vs vehicle type)
                firmware_aliases = {
                    "ardupilot": ["ardupilot", "arducopter", "arduplane", "ardurover"],
                    "betaflight": ["betaflight"],
                    "inav": ["inav"],
                    "px4": ["px4"],
                }
                fw_lower = fc_firmware.lower()
                match_terms = firmware_aliases.get(fw_lower, [fw_lower])
                assert any(
                    any(term in entry.lower() for term in match_terms)
                    for entry in compat_firmware
                ), f"{path.name}: Firmware '{fc_firmware}' not mentioned in {compat_firmware}"


class TestAllPresetsValid:
    def test_all_presets_have_required_sections(self):
        """Every airframe preset must have all required sections."""
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            for section in ["name", "frame", "motors", "propellers", "battery", "electronics", "sensors"]:
                assert section in config, f"{path.name} missing required section: {section}"

    def test_all_presets_have_positive_weights(self):
        for path in sorted(AIRFRAMES_DIR.glob("*.yaml")):
            config = load_airframe(path.name)
            assert config["frame"]["weight_g"] > 0, f"{path.name}: frame weight invalid"
            assert config["motors"]["weight_g"] > 0, f"{path.name}: motor weight invalid"
            assert config["battery"]["weight_g"] > 0, f"{path.name}: battery weight invalid"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
