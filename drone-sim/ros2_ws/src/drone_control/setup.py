from setuptools import setup, find_packages

package_name = 'drone_control'

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
    description='Flight control nodes for NEXUS drone simulation',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'flight_controller = drone_control.flight_controller:main',
            'pid_tuner = drone_control.pid_tuner:main',
            'mode_manager = drone_control.mode_manager:main',
            'failsafe_manager = drone_control.failsafe_manager:main',
        ],
    },
)
