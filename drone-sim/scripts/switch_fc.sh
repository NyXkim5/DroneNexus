#!/bin/bash
# ============================================================
# Toggle between PX4 and ArduPilot SITL
#
# Usage:
#   ./switch_fc.sh px4          # Switch to PX4
#   ./switch_fc.sh ardupilot    # Switch to ArduPilot
#   ./switch_fc.sh              # Show current FC type
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")/docker"

if [ $# -eq 0 ]; then
    echo "Current FC type: ${FC_TYPE:-px4}"
    echo ""
    echo "Usage: $0 <px4|ardupilot>"
    exit 0
fi

FC_TYPE="$1"

case $FC_TYPE in
    px4)
        echo "Switching to PX4 SITL..."
        ;;
    ardupilot)
        echo "Switching to ArduPilot SITL..."
        ;;
    *)
        echo "Error: Unknown FC type '$FC_TYPE'. Use 'px4' or 'ardupilot'."
        exit 1
        ;;
esac

cd "$DOCKER_DIR"

echo "Stopping current SITL containers..."
docker compose down

echo "Starting $FC_TYPE SITL..."
export FC_TYPE
export COMPOSE_PROFILES="$FC_TYPE"
docker compose up --build -d

echo ""
echo "Switched to $FC_TYPE SITL. Containers restarting..."
echo "View logs: docker compose logs -f"
