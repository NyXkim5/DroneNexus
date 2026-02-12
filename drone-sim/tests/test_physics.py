"""
Tests for the physics engine calculations.
Run: pytest tests/test_physics.py
"""
import math
import pytest
import yaml
from pathlib import Path


# Since the physics engine is in TypeScript, these tests validate
# the Python-side equivalent calculations used by validate_airframe.py
# and generate_urdf.py.


def load_airframe(name: str) -> dict:
    path = Path(__file__).parent.parent / "config" / "airframes" / name
    with open(path) as f:
        return yaml.safe_load(f)


def compute_auw(config: dict) -> float:
    frame = config["frame"]
    motors = config["motors"]
    propellers = config["propellers"]
    battery = config["battery"]
    electronics = config.get("electronics", {})

    auw = frame["weight_g"]
    auw += motors["weight_g"] * motors["count"]
    auw += propellers.get("weight_g", 4) * motors["count"]
    auw += battery["weight_g"]
    auw += electronics.get("flight_controller", {}).get("weight_g", 10)
    auw += electronics.get("esc", {}).get("weight_g", 10)
    auw += electronics.get("receiver", {}).get("weight_g", 3)
    auw += electronics.get("vtx", {}).get("weight_g", 5)
    auw += electronics.get("camera", {}).get("weight_g", 0)
    auw += config.get("sensors", {}).get("gps", {}).get("weight_g", 10)
    auw += 20  # wiring
    return auw


def compute_tw(config: dict) -> float:
    auw = compute_auw(config)
    thrust_per_motor = config["propellers"]["static_thrust_g_per_motor"]
    total_thrust = thrust_per_motor * config["motors"]["count"]
    return total_thrust / auw


class TestAUWCalculation:
    def test_fpv_quad_weight(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        auw = compute_auw(config)
        # A typical 5" quad should be 500-800g
        assert 400 < auw < 900, f"AUW {auw}g unrealistic for 5\" quad"

    def test_hex_heavy_lift_weight(self):
        config = load_airframe("hex-heavy-lift.yaml")
        auw = compute_auw(config)
        # Heavy lift hex should be 8-15kg
        assert 5000 < auw < 20000, f"AUW {auw}g unrealistic for heavy lift hex"

    def test_weight_includes_all_components(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        auw = compute_auw(config)
        # Must be heavier than frame + battery alone
        min_weight = config["frame"]["weight_g"] + config["battery"]["weight_g"]
        assert auw > min_weight


class TestThrustToWeight:
    def test_fpv_tw_ratio(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        tw = compute_tw(config)
        # FPV quads typically 5-12:1
        assert 4 < tw < 15, f"T:W {tw:.1f} unrealistic for FPV quad"

    def test_heavy_lift_tw_ratio(self):
        config = load_airframe("hex-heavy-lift.yaml")
        tw = compute_tw(config)
        # Heavy lift should be 2-5:1
        assert 1.5 < tw < 6, f"T:W {tw:.1f} unrealistic for heavy lift"

    def test_all_airframes_can_fly(self):
        """Every preset must have T:W > 1.5 to fly."""
        airframes_dir = Path(__file__).parent.parent / "config" / "airframes"
        for path in sorted(airframes_dir.glob("*.yaml")):
            config = load_airframe(path.name)
            tw = compute_tw(config)
            assert tw > 1.5, f"{path.name}: T:W ratio {tw:.1f} too low to fly"


class TestFlightTime:
    def test_fpv_flight_time(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        # FPV quads: 3-7 minutes
        hover_current = config.get("electrical", {}).get("avg_hover_current_a",
                        config.get("computed", {}).get("hover_current_a", 10))
        capacity = config["battery"]["capacity_mah"] * 0.8 / 1000
        flight_time = capacity / hover_current * 60
        assert 2 < flight_time < 15, f"Flight time {flight_time:.1f}min unrealistic"

    def test_long_range_flight_time(self):
        config = load_airframe("7inch-long-range.yaml")
        # Long range: 10-25 minutes
        hover_current = config.get("electrical", {}).get("avg_hover_current_a",
                        config.get("computed", {}).get("hover_current_a", 8))
        capacity = config["battery"]["capacity_mah"] * 0.8 / 1000
        flight_time = capacity / hover_current * 60
        assert 5 < flight_time < 40, f"Flight time {flight_time:.1f}min unrealistic for LR"


class TestBatteryPhysics:
    def test_voltage_range(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        batt = config["battery"]
        assert batt["voltage_full_v"] > batt["voltage_nominal_v"]
        assert batt["voltage_nominal_v"] > batt["voltage_empty_v"]
        # Voltage per cell should be in LiPo range
        cell_full = batt["voltage_full_v"] / batt["cells_series"]
        cell_empty = batt["voltage_empty_v"] / batt["cells_series"]
        assert 4.1 <= cell_full <= 4.35, f"Full cell voltage {cell_full}V unusual"
        assert 3.0 <= cell_empty <= 3.5, f"Empty cell voltage {cell_empty}V unusual"

    def test_energy_capacity(self):
        config = load_airframe("5inch-fpv-freestyle.yaml")
        batt = config["battery"]
        wh = batt["voltage_nominal_v"] * batt["capacity_mah"] / 1000
        assert wh > 0


class TestMotorMixing:
    def test_quad_x_mixing(self):
        """Quad X should have 4 motors."""
        config = load_airframe("5inch-fpv-freestyle.yaml")
        assert config["motors"]["count"] == 4
        assert "quad" in config["frame"]["type"]

    def test_hex_mixing(self):
        config = load_airframe("hex-heavy-lift.yaml")
        assert config["motors"]["count"] == 6

    def test_octo_mixing(self):
        config = load_airframe("octo-agricultural.yaml")
        assert config["motors"]["count"] == 8


class TestGroundEffect:
    def test_ground_effect_at_zero(self):
        """Ground effect should increase thrust near ground."""
        prop_diam = 5 * 0.0254  # 5-inch prop
        alt = 0.1  # 10cm
        ratio = alt / prop_diam
        multiplier = 1 + 0.5 * math.exp(-ratio)
        assert multiplier > 1.0

    def test_no_ground_effect_at_altitude(self):
        prop_diam = 5 * 0.0254
        alt = 10  # 10 meters
        ratio = alt / prop_diam
        multiplier = 1 + 0.5 * math.exp(-ratio)
        assert abs(multiplier - 1.0) < 0.01  # Negligible


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
