"""
openvins.launch.py

Launch file that starts OpenVINS (ov_msckf) and the OpenVINS bridge node
for DroneNexus VIO integration.
"""

from __future__ import annotations

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


launch_args = [
    DeclareLaunchArgument(
        name="drone_id",
        default_value="drone_0",
        description="Drone identifier",
    ),
    DeclareLaunchArgument(
        name="config_path",
        default_value="",
        description="Path to OpenVINS estimator_config.yaml",
    ),
    DeclareLaunchArgument(
        name="use_stereo",
        default_value="false",
        description="Enable stereo tracking",
    ),
    DeclareLaunchArgument(
        name="max_cameras",
        default_value="1",
        description="Number of cameras (1=mono, 2=stereo)",
    ),
    DeclareLaunchArgument(
        name="openvins_ns",
        default_value="ov_msckf",
        description="OpenVINS node namespace",
    ),
    DeclareLaunchArgument(
        name="ov_enable",
        default_value="true",
        description="Enable the OpenVINS estimator node",
    ),
    DeclareLaunchArgument(
        name="covariance_scale",
        default_value="1.0",
        description="Scale factor for covariance republish",
    ),
    DeclareLaunchArgument(
        name="max_age_sec",
        default_value="0.5",
        description="Max age before OpenVINS data is considered stale",
    ),
    DeclareLaunchArgument(
        name="auto_fallback",
        default_value="true",
        description="Fall back to dead reckoning on timeout",
    ),
    DeclareLaunchArgument(
        name="verbosity",
        default_value="INFO",
        description="OpenVINS verbosity: ALL, DEBUG, INFO, WARNING, ERROR, SILENT",
    ),
]


def launch_setup(context):  # noqa: ANN001
    config_path = LaunchConfiguration("config_path").perform(context)
    openvins_ns = LaunchConfiguration("openvins_ns").perform(context)

    if not config_path:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
            "openvins_drone_sim.yaml",
        )

    openvins_node = Node(
        package="ov_msckf",
        executable="run_subscribe_msckf",
        condition=IfCondition(LaunchConfiguration("ov_enable")),
        namespace=LaunchConfiguration("openvins_ns"),
        output="screen",
        parameters=[
            {"verbosity": LaunchConfiguration("verbosity")},
            {"use_stereo": LaunchConfiguration("use_stereo")},
            {"max_cameras": LaunchConfiguration("max_cameras")},
            {"config_path": config_path},
        ],
        remappings=[
            ("/imu0", "mavros/imu/data"),
            ("/cam0/image_raw", "camera/image_raw"),
        ],
    )

    bridge_node = Node(
        package="drone_navigation",
        executable="openvins_bridge",
        name="openvins_bridge",
        output="screen",
        parameters=[
            {"drone_id": LaunchConfiguration("drone_id")},
            {"openvins_ns": openvins_ns},
            {"covariance_scale": LaunchConfiguration("covariance_scale")},
            {"max_age_sec": LaunchConfiguration("max_age_sec")},
            {"auto_fallback": LaunchConfiguration("auto_fallback")},
        ],
    )

    return [openvins_node, bridge_node]


def generate_launch_description() -> LaunchDescription:
    opfunc = OpaqueFunction(function=launch_setup)
    ld = LaunchDescription(launch_args)
    ld.add_action(opfunc)
    return ld
