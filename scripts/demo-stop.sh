#!/usr/bin/env bash
# ============================================================
# OVERWATCH Demo Stop
# Kills all Python processes listening on demo ports (8765, 8766, 8888).
#
# Usage:
#   ./scripts/demo-stop.sh
# ============================================================
set -euo pipefail

# -- Colors --
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PORTS=(8765 8766 8888)
NAMES=("Backend API" "Webcam Detector" "HUD Server")

echo -e "${BOLD}[OVERWATCH] Stopping demo services...${NC}"
echo ""

all_stopped=true

for i in "${!PORTS[@]}"; do
    port="${PORTS[$i]}"
    name="${NAMES[$i]}"
    pids=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)

    if [ -n "$pids" ]; then
        for pid in $pids; do
            echo -e "  Killing ${BOLD}$name${NC} (port $port, PID $pid)..."
            kill "$pid" 2>/dev/null || true
        done
    else
        echo -e "  ${YELLOW}$name${NC} (port $port) - not running"
    fi
done

# Wait a moment then verify
sleep 2

echo ""
echo -e "${BOLD}[OVERWATCH] Verifying...${NC}"
echo ""

for i in "${!PORTS[@]}"; do
    port="${PORTS[$i]}"
    name="${NAMES[$i]}"
    if lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        # Force kill
        pids=$(lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
        echo -e "  ${YELLOW}$name${NC} (port $port) - force killed"
        all_stopped=false
    else
        echo -e "  ${GREEN}[OK]${NC} $name (port $port) - stopped"
    fi
done

echo ""
if [ "$all_stopped" = true ]; then
    echo -e "${GREEN}[OVERWATCH] All services stopped.${NC}"
else
    echo -e "${YELLOW}[OVERWATCH] Some services required force kill. All stopped now.${NC}"
fi
