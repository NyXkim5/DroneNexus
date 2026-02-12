#!/bin/bash
set -e

cd /PX4-Autopilot

# Allow overriding the GZ_SIM_RESOURCE_PATH for Gazebo model discovery
export GZ_SIM_RESOURCE_PATH="${GZ_SIM_RESOURCE_PATH}:/PX4-Autopilot/Tools/simulation/gz/models"
export PX4_SIM_MODEL="${PX4_SIM_MODEL:-gz_x500}"
export PX4_GZ_WORLD="${PX4_GZ_WORLD:-empty_field}"

exec "$@"
