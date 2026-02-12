import os
from glob import glob
from setuptools import setup, find_packages

package_name = 'drone_description'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # URDF files
        (os.path.join('share', package_name, 'urdf'),
            glob('urdf/*.urdf') + glob('urdf/*.xacro')),
        # SDF files
        (os.path.join('share', package_name, 'sdf'),
            glob('sdf/*.sdf')),
        # Mesh files
        (os.path.join('share', package_name, 'meshes'),
            glob('meshes/*.stl') + glob('meshes/*.dae')),
        # Config files
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='NEXUS Dev Team',
    maintainer_email='dev@nexus.local',
    description='URDF/SDF drone model descriptions for NEXUS simulation',
    license='MIT',
    tests_require=['pytest'],
)
