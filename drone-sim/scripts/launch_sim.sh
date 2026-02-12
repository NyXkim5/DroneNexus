#!/bin/bash
# ============================================================
# NEXUS Drone Simulator — One-Command Launch
# Starts the full simulation stack via Docker Compose.
#
# Usage:
#   ./launch_sim.sh                     # Default (PX4 SITL)
#   ./launch_sim.sh --fc ardupilot      # Use ArduPilot SITL
#   ./launch_sim.sh --no-gui            # Headless (no Gazebo GUI)
#   ./launch_sim.sh --drones 3          # Spawn 3 drones
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_DIR/docker"

# Defaults
FC_TYPE="px4"
GUI_ENABLED="true"
DRONE_COUNT=1
WORLD="empty_field"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --fc)
            FC_TYPE="$2"
            shift 2
            ;;
        --no-gui)
            GUI_ENABLED="false"
            shift
            ;;
        --drones)
            DRONE_COUNT="$2"
            shift 2
            ;;
        --world)
            WORLD="$2"
            shift 2
            ;;
        --help|-h)
            echo "NEXUS Drone Simulator Launcher"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --fc <px4|ardupilot>    Flight controller SITL (default: px4)"
            echo "  --no-gui               Disable Gazebo GUI (headless)"
            echo "  --drones <n>           Number of drone instances (default: 1)"
            echo "  --world <name>         Gazebo world (empty_field, warehouse, urban, agricultural)"
            echo "  -h, --help             Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "============================================"
echo "  NEXUS Drone Simulator"
echo "============================================"
echo "  Flight Controller:  $FC_TYPE"
echo "  Gazebo GUI:         $GUI_ENABLED"
echo "  Drone Count:        $DRONE_COUNT"
echo "  World:              $WORLD"
echo "============================================"
echo ""

# Export environment variables for docker-compose
export FC_TYPE
export GUI_ENABLED
export DRONE_COUNT
export WORLD

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed."
    echo "Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "Error: Docker daemon is not running."
    exit 1
fi

# Build and launch
echo "Building containers (this may take a while on first run)..."
cd "$DOCKER_DIR"

# Select compose profile based on FC type
if [ "$FC_TYPE" = "ardupilot" ]; then
    COMPOSE_PROFILES="ardupilot"
else
    COMPOSE_PROFILES="px4"
fi

export COMPOSE_PROFILES

docker compose up --build -d

echo ""
echo "============================================"
echo "  Simulation is running!"
echo "============================================"
echo ""
echo "  Services:"
echo "    ROS2:       Running in container"
echo "    Gazebo:     ${GUI_ENABLED == 'true' && 'http://localhost:8080 (web)' || 'headless'}"
echo "    $FC_TYPE SITL: UDP ports 14540-$((14540 + DRONE_COUNT - 1))"
echo "    MAVROS:     Running in container"
echo ""
echo "  To view logs:    docker compose logs -f"
echo "  To stop:         docker compose down"
echo "  To restart:      docker compose restart"
echo ""
echo "  Launch the Electron app to connect to the simulation."
echo "============================================"
