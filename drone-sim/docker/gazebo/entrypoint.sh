#!/bin/bash
set -e

# Source ROS 2 for the Gazebo-ROS bridge
source /opt/ros/humble/setup.bash

export RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}
export GZ_SIM_RESOURCE_PATH=${GZ_SIM_RESOURCE_PATH:-/gazebo/models:/gazebo/worlds}
export GZ_SIM_SYSTEM_PLUGIN_PATH=${GZ_SIM_SYSTEM_PLUGIN_PATH:-/usr/local/lib}

exec "$@"
