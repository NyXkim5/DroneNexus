#!/usr/bin/env bash
# ============================================================
# OVERWATCH Wargame Demo Launcher
# Starts Backend + HUD only (no webcam). Use for wargame simulations.
#
# Services:
#   1. Backend API (FastAPI)   - port 8765
#   2. HUD file server         - port 8888
#
# Usage:
#   ./scripts/demo-wargame.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# -- Colors --
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# -- Log files --
LOG_BACKEND="/tmp/overwatch_backend.log"
LOG_HUD="/tmp/overwatch_hud.log"

# -- Ports --
PORT_BACKEND=8765
PORT_HUD=8888

# -- Child PIDs --
PIDS=()

cleanup() {
    echo ""
    echo -e "${YELLOW}[OVERWATCH] Shutting down...${NC}"
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    sleep 1
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}[OVERWATCH] All services stopped.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

check_port() {
    local port=$1
    local name=$2
    if lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] Port $port is already in use ($name). Run scripts/demo-stop.sh first.${NC}"
        exit 1
    fi
}

wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        if curl -s -o /dev/null -w '' "$url" 2>/dev/null; then
            echo -e "  ${GREEN}[OK]${NC} $name is ready"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    echo -e "  ${RED}[FAIL]${NC} $name did not start within ${max_attempts}s"
    return 1
}

# ============================================================
echo -e "${BOLD}${CYAN}"
echo "  ___  _   _ ___ _____      ___ _____ ___ _  _"
echo " / _ \\| | | | __| _ \\ \\    / /_\\_   _/ __| || |"
echo "| (_) | \\_/ | _||   /\\ \\/\\/ / _ \\| || (__| __ |"
echo " \\___/ \\___/|___|_|_\\ \\_/\\_/_/ \\_\\_| \\___|_||_|"
echo ""
echo -e "${NC}${BOLD}  ISR Asset Coordination Platform - Wargame Mode${NC}"
echo ""

# -- Pre-flight: check ports --
echo -e "${CYAN}[1/4] Checking ports...${NC}"
check_port $PORT_BACKEND "Backend API"
check_port $PORT_HUD "HUD Server"
echo -e "  ${GREEN}[OK]${NC} All ports available"

# -- Start Backend --
echo -e "${CYAN}[2/4] Starting Backend API on port $PORT_BACKEND...${NC}"
cd "$PROJECT_ROOT/backend"
python3 main.py > "$LOG_BACKEND" 2>&1 &
PIDS+=($!)
echo -e "  ${GREEN}[OK]${NC} Backend PID: ${PIDS[-1]} (log: $LOG_BACKEND)"

# -- Start HUD Server --
echo -e "${CYAN}[3/4] Starting HUD file server on port $PORT_HUD...${NC}"
cd "$PROJECT_ROOT/src/hud"
python3 -m http.server $PORT_HUD > "$LOG_HUD" 2>&1 &
PIDS+=($!)
echo -e "  ${GREEN}[OK]${NC} HUD Server PID: ${PIDS[-1]} (log: $LOG_HUD)"

# -- Wait for services --
echo -e "${CYAN}[4/4] Waiting for services to be ready...${NC}"
wait_for_service "http://localhost:$PORT_BACKEND/api/v1/ontology/taskforce/health" "Backend API"
wait_for_service "http://localhost:$PORT_HUD/bulwark.html" "HUD Server"

# -- Open HUD --
echo ""
open "http://localhost:$PORT_HUD/bulwark.html"

# -- Summary --
echo -e "${BOLD}${GREEN}============================================================${NC}"
echo -e "${BOLD}  OVERWATCH Wargame Mode Running${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "  ${BOLD}Backend API:${NC}       http://localhost:$PORT_BACKEND"
echo -e "  ${BOLD}HUD (BULWARK):${NC}     http://localhost:$PORT_HUD/bulwark.html"
echo ""
echo -e "  ${YELLOW}Webcam Detector:   SKIPPED (wargame mode)${NC}"
echo ""
echo -e "  ${BOLD}Logs:${NC}"
echo -e "    Backend:  $LOG_BACKEND"
echo -e "    HUD:      $LOG_HUD"
echo ""
echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all services."
echo -e "${GREEN}============================================================${NC}"
echo ""

# -- Wait for children --
wait
