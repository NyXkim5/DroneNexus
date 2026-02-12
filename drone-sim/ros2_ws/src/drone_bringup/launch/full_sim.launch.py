"""
full_sim.launch.py

Master launch file that brings up the entire NEXUS drone simulation stack:
  - Gazebo world with drone model
  - Flight controller stack (PX4 or ArduPilot via MAVROS)
  - Sensor simulation nodes
  - Navigation stack (SLAM, path planning, obstacle avoidance)
  - Control nodes (flight controller, PID tuner, mode manager, failsafe)
"""

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    GroupAction,
    LogInfo,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    # ── Declare arguments ────────────────────────────────────────────────
    pkg_bringup = FindPackageShare('drone_bringup')

    declare_world = DeclareLaunchArgument(
        'world',
        default_value='empty_field.sdf',
        description='Gazebo world file name',
    )
    declare_autopilot = DeclareLaunchArgument(
        'autopilot',
        default_value='px4',
        choices=['px4', 'ardupilot'],
        description='Autopilot stack to use: px4 or ardupilot',
    )
    declare_drone_model = DeclareLaunchArgument(
        'drone_model',
        default_value='quadcopter',
        choices=['quadcopter', 'hexacopter'],
        description='Drone model to spawn',
    )
    declare_enable_nav = DeclareLaunchArgument(
        'enable_navigation',
        default_value='true',
        description='Enable navigation stack (SLAM, path planning)',
    )
    declare_enable_perception = DeclareLaunchArgument(
        'enable_perception',
        default_value='true',
        description='Enable perception nodes (camera, detection)',
    )
    declare_drone_id = DeclareLaunchArgument(
        'drone_id',
        default_value='drone_0',
        description='Unique identifier for this drone instance',
    )

    # ── Include sub-launch files ─────────────────────────────────────────
    gazebo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([pkg_bringup, 'launch', 'gazebo.launch.py'])
        ),
        launch_arguments={
            'world': LaunchConfiguration('world'),
            'drone_model': LaunchConfiguration('drone_model'),
        }.items(),
    )

    px4_launch = GroupAction(
        actions=[
            LogInfo(msg='Launching PX4 SITL stack...'),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution([pkg_bringup, 'launch', 'px4_sitl.launch.py'])
                ),
                launch_arguments={
                    'drone_id': LaunchConfiguration('drone_id'),
                }.items(),
            ),
        ],
        condition=IfCondition(
            PythonExpression(["'", LaunchConfiguration('autopilot'), "' == 'px4'"])
        ),
    )

    ardupilot_launch = GroupAction(
        actions=[
            LogInfo(msg='Launching ArduPilot SITL stack...'),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution([pkg_bringup, 'launch', 'ardupilot.launch.py'])
                ),
                launch_arguments={
                    'drone_id': LaunchConfiguration('drone_id'),
                }.items(),
            ),
        ],
        condition=IfCondition(
            PythonExpression(["'", LaunchConfiguration('autopilot'), "' == 'ardupilot'"])
        ),
    )

    sensors_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([pkg_bringup, 'launch', 'sensors.launch.py'])
        ),
        launch_arguments={
            'drone_id': LaunchConfiguration('drone_id'),
        }.items(),
    )

    # ── Build launch description ─────────────────────────────────────────
    return LaunchDescription([
        # Arguments
        declare_world,
        declare_autopilot,
        declare_drone_model,
        declare_enable_nav,
        declare_enable_perception,
        declare_drone_id,
        # Launch groups
        LogInfo(msg='=== NEXUS Full Simulation Launch ==='),
        gazebo_launch,
        px4_launch,
        ardupilot_launch,
        sensors_launch,
    ])
