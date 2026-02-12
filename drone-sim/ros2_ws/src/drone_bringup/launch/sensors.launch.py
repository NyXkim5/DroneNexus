"""
sensors.launch.py

Launches all sensor simulation nodes:
  - Camera image publisher
  - Object detection (YOLO placeholder)
  - ArUco marker detection
  - Terrain classifier
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
    declare_enable_detection = DeclareLaunchArgument(
        'enable_detection',
        default_value='true',
        description='Enable object detection node',
    )

    # ── Camera node ──────────────────────────────────────────────────────
    camera_node = Node(
        package='drone_perception',
        executable='camera_node',
        name='camera_node',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'camera_topic': '/camera',
            'depth_topic': '/depth_camera',
            'image_width': 640,
            'image_height': 480,
            'publish_rate': 30.0,
        }],
    )

    # ── Detector node (YOLO placeholder) ─────────────────────────────────
    detector_node = Node(
        package='drone_perception',
        executable='detector_node',
        name='detector_node',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'confidence_threshold': 0.5,
            'model_path': '',
        }],
    )

    # ── ArUco detector ───────────────────────────────────────────────────
    aruco_node = Node(
        package='drone_perception',
        executable='aruco_detector',
        name='aruco_detector',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'marker_size_m': 0.15,
            'dictionary_id': 0,
        }],
    )

    # ── Terrain classifier ───────────────────────────────────────────────
    terrain_node = Node(
        package='drone_perception',
        executable='terrain_classifier',
        name='terrain_classifier',
        namespace=LaunchConfiguration('drone_id'),
        output='screen',
        parameters=[{
            'drone_id': LaunchConfiguration('drone_id'),
            'classify_rate': 1.0,
        }],
    )

    return LaunchDescription([
        declare_drone_id,
        declare_enable_detection,
        LogInfo(msg='=== Launching Sensor Simulation Nodes ==='),
        camera_node,
        detector_node,
        aruco_node,
        terrain_node,
    ])
