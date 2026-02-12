#!/bin/bash
set -e

cd /ardupilot

export PATH="/ardupilot/Tools/autotest:$PATH"
export VEHICLE="${VEHICLE:-copter}"
export FRAME="${FRAME:-quad}"

exec "$@"
