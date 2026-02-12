"""
px4_sitl.launch.py

Launches PX4 SITL instances with MAVROS bridge.
Configures the MAVLink connection between PX4 and ROS 2.
"""

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    LogInfo,
)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # ── Arguments ────────────────────────────────────────────────────────
    declare_drone_id = DeclareLaunchArgument(
        'drone_id',
        default_value='drone_0',
        description='Unique drone identifier',
    )
    declare_fcu_url = DeclareLaunchArgument(
        'fcu_url',
        default_value='udp://:14540@172.20.0.30:14540',
        description='MAVLink FCU connection URL',
    )
    declare_tgt_system = DeclareLaunchArgument(
        'tgt_system',
        default_value='1',
        description='MAVLink target system ID',
    )
    declare_tgt_component = DeclareLaunchArgument(
        'tgt_component',
        default_value='1',
        description='MAVLink target component ID',
    )

    # ── MAVROS node ──────────────────────────────────────────────────────
    mavros_node = Node(
        package='mavros',
        executable='mavros_node',
        name='mavros',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'fcu_url': LaunchConfiguration('fcu_url'),
            'gcs_url': '',
            'target_system_id': 1,
            'target_component_id': 1,
            'fcu_protocol': 'v2.0',
            'system_id': 255,
            'component_id': 240,
            # Plugin whitelist for performance
            'plugin_allowlist': [
                'sys_status',
                'sys_time',
                'command',
                'global_position',
                'local_position',
                'setpoint_position',
                'setpoint_velocity',
                'setpoint_attitude',
                'setpoint_raw',
                'rc_io',
                'imu',
                'altitude',
                'battery',
                'waypoint',
                'param',
                'home_position',
                'mission',
            ],
        }],
        remappings=[
            ('mavros/state', 'mavros/state'),
            ('mavros/local_position/pose', 'mavros/local_position/pose'),
            ('mavros/global_position/global', 'mavros/global_position/global'),
        ],
    )

    # ── Flight controller bridge node ────────────────────────────────────
    flight_controller = Node(
        package='drone_control',
        executable='flight_controller',
        name='flight_controller',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'autopilot_type': 'px4',
            'state_publish_rate': 20.0,
        }],
    )

    # ── Mode manager ─────────────────────────────────────────────────────
    mode_manager = Node(
        package='drone_control',
        executable='mode_manager',
        name='mode_manager',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'autopilot_type': 'px4',
        }],
    )

    # ── Failsafe manager ─────────────────────────────────────────────────
    failsafe_manager = Node(
        package='drone_control',
        executable='failsafe_manager',
        name='failsafe_manager',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'battery_failsafe_threshold': 20.0,
            'gps_loss_timeout_sec': 5.0,
        }],
    )

    return LaunchDescription([
        declare_drone_id,
        declare_fcu_url,
        declare_tgt_system,
        declare_tgt_component,
        LogInfo(msg='=== Launching PX4 SITL with MAVROS ==='),
        mavros_node,
        flight_controller,
        mode_manager,
        failsafe_manager,
    ])
