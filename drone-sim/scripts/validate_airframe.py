#!/usr/bin/env python3
"""
Validate airframe YAML files and report computed specifications.

Usage:
    python validate_airframe.py config/airframes/5inch-fpv-freestyle.yaml
    python validate_airframe.py config/airframes/  # Validate all in directory
"""

import argparse
import math
import sys
from pathlib import Path

import yaml

REQUIRED_SECTIONS = ["name", "frame", "motors", "propellers", "battery", "electronics", "sensors"]
REQUIRED_FRAME = ["type", "layout", "weight_g", "wheelbase_mm"]
REQUIRED_MOTORS = ["count", "kv", "max_thrust_g", "weight_g"]
REQUIRED_BATTERY = ["cells", "voltage_nominal", "capacity_mah", "weight_g"]


def validate_file(filepath: Path) -> list[str]:
    """Validate a single airframe YAML. Returns list of errors."""
    errors = []

    try:
        with open(filepath) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(config, dict):
        return ["File does not contain a YAML mapping"]

    # Check required sections
    for section in REQUIRED_SECTIONS:
        if section not in config:
            errors.append(f"Missing required section: {section}")

    # Validate frame
    if "frame" in config:
        for field in REQUIRED_FRAME:
            if field not in config["frame"]:
                errors.append(f"Missing frame.{field}")
        if config["frame"].get("weight_g", 0) <= 0:
            errors.append("frame.weight_g must be positive")

    # Validate motors
    if "motors" in config:
        for field in REQUIRED_MOTORS:
            if field not in config["motors"]:
                errors.append(f"Missing motors.{field}")
        if config["motors"].get("count", 0) < 3:
            errors.append("motors.count must be at least 3")

    # Validate battery
    if "battery" in config:
        for field in REQUIRED_BATTERY:
            if field not in config["battery"]:
                errors.append(f"Missing battery.{field}")
        cells = config["battery"].get("cells", 0)
        if cells < 1 or cells > 14:
            errors.append(f"battery.cells ({cells}) out of range 1-14")

    return errors


def compute_specs(config: dict) -> dict:
    """Compute performance specifications from config."""
    frame = config["frame"]
    motors = config["motors"]
    propellers = config["propellers"]
    battery = config["battery"]
    electronics = config.get("electronics", {})
    electrical = config.get("electrical", {})

    # All-up weight
    auw = frame["weight_g"]
    auw += motors["weight_g"] * motors["count"]
    auw += propellers.get("weight_g", 4) * motors["count"]
    auw += battery["weight_g"]
    auw += electronics.get("flight_controller", {}).get("weight_g", 10)
    auw += electronics.get("esc", {}).get("weight_g", 10)
    auw += electronics.get("receiver", {}).get("weight_g", 3)
    auw += electronics.get("vtx", {}).get("weight_g", 5)
    auw += config.get("sensors", {}).get("camera", {}).get("weight_g", 30)
    auw += 20  # wiring estimate

    # Thrust-to-weight
    total_thrust = motors["max_thrust_g"] * motors["count"]
    tw = total_thrust / auw if auw > 0 else 0

    # Flight time (simplified)
    hover_throttle = 1 / tw if tw > 0 else 1
    hover_current = electrical.get("avg_hover_current_a", auw * 0.012)
    usable_mah = battery["capacity_mah"] * 0.8
    flight_time_min = (usable_mah / 1000 / hover_current * 60) if hover_current > 0 else 0

    # Max payload (T:W >= 2:1)
    max_payload = total_thrust / 2 - auw

    # Max speed estimate
    wheelbase_m = frame["wheelbase_mm"] / 1000
    frontal_area = wheelbase_m * 0.05
    excess_thrust_n = ((total_thrust - auw) / 1000) * 9.81
    max_speed = math.sqrt(2 * max(0, excess_thrust_n) / (1.225 * 1.2 * frontal_area)) if excess_thrust_n > 0 else 0

    # Energy capacity
    energy_wh = battery["voltage_nominal"] * battery["capacity_mah"] / 1000

    return {
        "all_up_weight_g": round(auw),
        "total_thrust_g": round(total_thrust),
        "thrust_to_weight": round(tw, 2),
        "hover_throttle_pct": round(hover_throttle * 100),
        "max_flight_time_min": round(flight_time_min, 1),
        "max_payload_g": round(max(0, max_payload)),
        "max_speed_ms": round(max_speed),
        "energy_capacity_wh": round(energy_wh, 1),
    }


def print_report(filepath: Path, config: dict, errors: list[str]):
    """Print validation report."""
    name = config.get("name", filepath.stem)
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  {filepath}")
    print(f"{'='*60}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")
    else:
        print("\n  VALID")

    if not errors:
        specs = compute_specs(config)
        print(f"\n  Computed Specifications:")
        print(f"    All-Up Weight:   {specs['all_up_weight_g']:>8} g")
        print(f"    Total Thrust:    {specs['total_thrust_g']:>8} g")
        print(f"    Thrust:Weight:   {specs['thrust_to_weight']:>8}:1")
        print(f"    Hover Throttle:  {specs['hover_throttle_pct']:>8}%")
        print(f"    Max Flight Time: {specs['max_flight_time_min']:>8} min")
        print(f"    Max Payload:     {specs['max_payload_g']:>8} g")
        print(f"    Max Speed:       {specs['max_speed_ms']:>8} m/s")
        print(f"    Energy:          {specs['energy_capacity_wh']:>8} Wh")

        # Warnings
        tw = specs["thrust_to_weight"]
        if tw < 2:
            print(f"\n  WARNING: T:W ratio ({tw}:1) below safe minimum (2:1)")
        elif tw < 3:
            print(f"\n  WARNING: T:W ratio ({tw}:1) is marginal")

        if specs["max_flight_time_min"] < 3:
            print(f"  WARNING: Flight time ({specs['max_flight_time_min']}min) very short")


def main():
    parser = argparse.ArgumentParser(description="Validate airframe YAML files")
    parser.add_argument("path", help="YAML file or directory of YAML files")
    args = parser.parse_args()

    target = Path(args.path)
    files = list(target.glob("*.yaml")) + list(target.glob("*.yml")) if target.is_dir() else [target]

    if not files:
        print(f"No YAML files found at {target}")
        sys.exit(1)

    total_errors = 0
    for filepath in sorted(files):
        errors = validate_file(filepath)
        total_errors += len(errors)
        with open(filepath) as f:
            config = yaml.safe_load(f)
        print_report(filepath, config, errors)

    print(f"\n{'='*60}")
    print(f"  Validated {len(files)} file(s), {total_errors} error(s)")
    print(f"{'='*60}")

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
