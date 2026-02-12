"""
gazebo.launch.py

Launches Gazebo Garden with a specified world file and spawns a drone model.
Starts the ROS-Gazebo bridge for topic communication.
"""

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    LogInfo,
    SetEnvironmentVariable,
)
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    EnvironmentVariable,
)
from launch_ros.actions import Node


def generate_launch_description():
    # ── Arguments ────────────────────────────────────────────────────────
    declare_world = DeclareLaunchArgument(
        'world',
        default_value='empty_field.sdf',
        description='SDF world file to load',
    )
    declare_drone_model = DeclareLaunchArgument(
        'drone_model',
        default_value='quadcopter',
        choices=['quadcopter', 'hexacopter'],
        description='Drone model to spawn in the world',
    )
    declare_spawn_x = DeclareLaunchArgument('spawn_x', default_value='0.0')
    declare_spawn_y = DeclareLaunchArgument('spawn_y', default_value='0.0')
    declare_spawn_z = DeclareLaunchArgument('spawn_z', default_value='0.2')
    declare_verbose = DeclareLaunchArgument(
        'verbose', default_value='4', description='Gazebo verbosity level 0-4'
    )

    # ── Environment ──────────────────────────────────────────────────────
    set_resource_path = SetEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH',
        '/gazebo/models:/gazebo/worlds',
    )

    # ── Launch Gazebo server ─────────────────────────────────────────────
    gz_sim = ExecuteProcess(
        cmd=[
            'gz', 'sim', '-v', LaunchConfiguration('verbose'),
            '-r',
            PathJoinSubstitution(['/gazebo/worlds', LaunchConfiguration('world')]),
        ],
        output='screen',
        name='gz_sim',
    )

    # ── Spawn drone model ────────────────────────────────────────────────
    spawn_drone = ExecuteProcess(
        cmd=[
            'gz', 'service',
            '-s', '/world/default/create',
            '--reqtype', 'gz.msgs.EntityFactory',
            '--reptype', 'gz.msgs.Boolean',
            '--timeout', '5000',
            '--req',
            'sdf_filename: "/gazebo/models/'
            + 'quadcopter'  # Will be parameterized at runtime
            + '/model.sdf"'
            + ' name: "drone_0"'
            + ' pose: {position: {x: 0, y: 0, z: 0.2}}',
        ],
        output='screen',
        name='spawn_drone',
    )

    # ── ROS <-> Gazebo bridge ────────────────────────────────────────────
    ros_gz_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        name='ros_gz_bridge',
        output='screen',
        parameters=[{
            'use_sim_time': True,
        }],
        arguments=[
            # IMU
            '/imu@sensor_msgs/msg/Imu[gz.msgs.IMU',
            # GPS / NavSat
            '/navsat@sensor_msgs/msg/NavSatFix[gz.msgs.NavSat',
            # Camera image
            '/camera@sensor_msgs/msg/Image[gz.msgs.Image',
            # Depth camera
            '/depth_camera@sensor_msgs/msg/Image[gz.msgs.Image',
            # LiDAR
            '/lidar@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan',
            # LiDAR point cloud
            '/lidar/points@sensor_msgs/msg/PointCloud2[gz.msgs.PointCloudPacked',
            # Barometer
            '/air_pressure@sensor_msgs/msg/FluidPressure[gz.msgs.FluidPressure',
            # Magnetometer
            '/magnetometer@sensor_msgs/msg/MagneticField[gz.msgs.Magnetometer',
            # Clock
            '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock',
            # Joint states
            '/joint_states@sensor_msgs/msg/JointState[gz.msgs.Model',
            # Odometry
            '/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry',
            # Cmd vel (bidirectional)
            '/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist',
        ],
    )

    return LaunchDescription([
        declare_world,
        declare_drone_model,
        declare_spawn_x,
        declare_spawn_y,
        declare_spawn_z,
        declare_verbose,
        set_resource_path,
        LogInfo(msg='=== Launching Gazebo Garden ==='),
        gz_sim,
        spawn_drone,
        ros_gz_bridge,
    ])
