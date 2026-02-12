from setuptools import setup, find_packages

package_name = 'drone_perception'

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
    description='Perception nodes for NEXUS drone simulation',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'camera_node = drone_perception.camera_node:main',
            'detector_node = drone_perception.detector_node:main',
            'aruco_detector = drone_perception.aruco_detector:main',
            'terrain_classifier = drone_perception.terrain_classifier:main',
        ],
    },
)
