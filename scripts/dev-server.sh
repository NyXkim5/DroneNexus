#!/usr/bin/env bash
# ============================================================
# OVERWATCH Dev Server
# Serves the HUD on localhost for development
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PORT="${1:-8000}"

echo "=== OVERWATCH Dev Server ==="
echo "Serving HUD at http://localhost:$PORT"
echo "Press Ctrl+C to stop"
echo ""

cd "$PROJECT_ROOT/src/hud"
python3 -m http.server "$PORT"
