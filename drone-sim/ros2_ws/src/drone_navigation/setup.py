from setuptools import setup, find_packages

package_name = 'drone_navigation'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='NEXUS Dev Team',
    maintainer_email='dev@nexus.local',
    description='Navigation nodes for NEXUS drone simulation',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'slam_node = drone_navigation.slam_node:main',
            'path_planner = drone_navigation.path_planner:main',
            'obstacle_avoidance = drone_navigation.obstacle_avoidance:main',
            'vio_fallback = drone_navigation.vio_fallback:main',
            'waypoint_executor = drone_navigation.waypoint_executor:main',
        ],
    },
)
