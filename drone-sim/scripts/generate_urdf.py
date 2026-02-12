#!/usr/bin/env python3
"""
Generate URDF/SDF drone model from airframe YAML config.
Converts hardware specifications into a simulation-ready robot description.

Usage:
    python generate_urdf.py config/airframes/5inch-fpv-freestyle.yaml
    python generate_urdf.py config/airframes/5inch-fpv-freestyle.yaml --format sdf
"""

import argparse
import math
import sys
from pathlib import Path

import yaml


def load_airframe(yaml_path: str) -> dict:
    with open(yaml_path) as f:
        return yaml.safe_load(f)


def generate_urdf(config: dict) -> str:
    """Generate URDF XML from airframe config."""
    name = config.get("name", "drone").replace(" ", "_").lower()
    frame = config["frame"]
    motors = config["motors"]
    propellers = config["propellers"]
    battery = config["battery"]
    sensors = config.get("sensors", {})

    arm_length_m = frame["arm_length_mm"] / 1000
    body_mass = frame["weight_g"] / 1000
    motor_mass = motors["weight_g"] / 1000
    prop_diameter_m = propellers["size_inch"] * 0.0254
    total_mass = body_mass + motors["count"] * motor_mass + battery["weight_g"] / 1000

    # Approximate inertia for a flat plate body
    body_size = frame["wheelbase_mm"] / 1000 * 0.3
    ixx = (total_mass / 12) * (body_size**2 + 0.03**2)
    iyy = ixx
    izz = (total_mass / 12) * (body_size**2 + body_size**2)

    # Compute motor positions
    motor_count = motors["count"]
    motor_positions = []
    layout = frame.get("layout", "X")

    for i in range(motor_count):
        if layout in ("X", "H"):
            angle = (2 * math.pi * i / motor_count) + math.pi / motor_count
        else:
            angle = 2 * math.pi * i / motor_count
        x = arm_length_m * math.cos(angle)
        y = arm_length_m * math.sin(angle)
        motor_positions.append((x, y, 0.02))

    urdf = f"""<?xml version="1.0"?>
<robot name="{name}" xmlns:xacro="http://www.ros.org/wiki/xacro">

  <!-- Base link (body) -->
  <link name="base_link">
    <visual>
      <geometry>
        <box size="{body_size:.4f} {body_size:.4f} 0.03"/>
      </geometry>
      <material name="dark_gray">
        <color rgba="0.2 0.2 0.2 1"/>
      </material>
    </visual>
    <collision>
      <geometry>
        <box size="{body_size:.4f} {body_size:.4f} 0.03"/>
      </geometry>
    </collision>
    <inertial>
      <mass value="{total_mass:.4f}"/>
      <inertia ixx="{ixx:.6f}" ixy="0" ixz="0" iyy="{iyy:.6f}" iyz="0" izz="{izz:.6f}"/>
    </inertial>
  </link>

  <!-- Base footprint (ground projection) -->
  <link name="base_footprint"/>
  <joint name="base_footprint_joint" type="fixed">
    <parent link="base_footprint"/>
    <child link="base_link"/>
    <origin xyz="0 0 0.05" rpy="0 0 0"/>
  </joint>
"""

    # Generate motor/arm links
    for i, (mx, my, mz) in enumerate(motor_positions):
        direction = "cw" if i % 2 == 0 else "ccw"
        arm_length = math.sqrt(mx**2 + my**2)
        arm_angle = math.atan2(my, mx)

        urdf += f"""
  <!-- Arm {i+1} -->
  <link name="arm_{i+1}">
    <visual>
      <origin xyz="{arm_length/2:.4f} 0 0" rpy="0 0 0"/>
      <geometry>
        <box size="{arm_length:.4f} 0.015 0.008"/>
      </geometry>
      <material name="carbon">
        <color rgba="0.1 0.1 0.1 1"/>
      </material>
    </visual>
    <collision>
      <origin xyz="{arm_length/2:.4f} 0 0" rpy="0 0 0"/>
      <geometry>
        <box size="{arm_length:.4f} 0.015 0.008"/>
      </geometry>
    </collision>
    <inertial>
      <mass value="0.01"/>
      <inertia ixx="0.000001" ixy="0" ixz="0" iyy="0.000001" iyz="0" izz="0.000001"/>
    </inertial>
  </link>
  <joint name="arm_{i+1}_joint" type="fixed">
    <parent link="base_link"/>
    <child link="arm_{i+1}"/>
    <origin xyz="0 0 0" rpy="0 0 {arm_angle:.4f}"/>
  </joint>

  <!-- Motor {i+1} ({direction}) -->
  <link name="motor_{i+1}">
    <visual>
      <geometry>
        <cylinder radius="0.015" length="0.015"/>
      </geometry>
      <material name="motor_gray">
        <color rgba="0.4 0.4 0.4 1"/>
      </material>
    </visual>
    <inertial>
      <mass value="{motor_mass:.4f}"/>
      <inertia ixx="0.000005" ixy="0" ixz="0" iyy="0.000005" iyz="0" izz="0.000003"/>
    </inertial>
  </link>
  <joint name="motor_{i+1}_joint" type="fixed">
    <parent link="base_link"/>
    <child link="motor_{i+1}"/>
    <origin xyz="{mx:.4f} {my:.4f} {mz:.4f}" rpy="0 0 0"/>
  </joint>

  <!-- Propeller {i+1} -->
  <link name="prop_{i+1}">
    <visual>
      <geometry>
        <cylinder radius="{prop_diameter_m/2:.4f}" length="0.002"/>
      </geometry>
      <material name="prop_transparent">
        <color rgba="0.5 0.5 0.5 0.3"/>
      </material>
    </visual>
    <inertial>
      <mass value="{propellers['weight_g']/1000:.4f}"/>
      <inertia ixx="0.000002" ixy="0" ixz="0" iyy="0.000002" iyz="0" izz="0.000004"/>
    </inertial>
  </link>
  <joint name="prop_{i+1}_joint" type="continuous">
    <parent link="motor_{i+1}"/>
    <child link="prop_{i+1}"/>
    <origin xyz="0 0 0.01" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
  </joint>
"""

    # Add sensor links
    if sensors.get("gps", {}).get("present", False):
        urdf += """
  <!-- GPS Module -->
  <link name="gps_link">
    <visual>
      <geometry><cylinder radius="0.015" length="0.005"/></geometry>
      <material name="gps_green"><color rgba="0 0.6 0.4 1"/></material>
    </visual>
    <inertial>
      <mass value="0.015"/>
      <inertia ixx="0.000001" ixy="0" ixz="0" iyy="0.000001" iyz="0" izz="0.000001"/>
    </inertial>
  </link>
  <joint name="gps_joint" type="fixed">
    <parent link="base_link"/>
    <child link="gps_link"/>
    <origin xyz="0 0 0.04" rpy="0 0 0"/>
  </joint>
"""

    urdf += """
  <!-- IMU (integrated in FC) -->
  <link name="imu_link"/>
  <joint name="imu_joint" type="fixed">
    <parent link="base_link"/>
    <child link="imu_link"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
  </joint>

  <!-- Camera -->
  <link name="camera_link">
    <visual>
      <geometry><box size="0.02 0.025 0.02"/></geometry>
      <material name="camera_dark"><color rgba="0.15 0.15 0.15 1"/></material>
    </visual>
    <inertial>
      <mass value="0.03"/>
      <inertia ixx="0.000001" ixy="0" ixz="0" iyy="0.000001" iyz="0" izz="0.000001"/>
    </inertial>
  </link>
  <joint name="camera_joint" type="fixed">
    <parent link="base_link"/>
    <child link="camera_link"/>
    <origin xyz="0.05 0 -0.005" rpy="0 0.5 0"/>
  </joint>

</robot>
"""
    return urdf


def main():
    parser = argparse.ArgumentParser(description="Generate URDF from airframe YAML")
    parser.add_argument("yaml_file", help="Path to airframe YAML file")
    parser.add_argument("--format", choices=["urdf", "sdf"], default="urdf", help="Output format")
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")
    args = parser.parse_args()

    config = load_airframe(args.yaml_file)
    result = generate_urdf(config)

    if args.output:
        Path(args.output).write_text(result)
        print(f"Written to {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
