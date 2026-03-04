#!/usr/bin/env bash
# ============================================================
# OVERWATCH Build Script
# Generates all deliverables into dist/
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== OVERWATCH Build System ==="
echo ""

# Ensure dist directory exists
mkdir -p "$PROJECT_ROOT/dist"

# Step 1: Install dependencies
echo "[1/3] Installing dependencies..."
cd "$PROJECT_ROOT"
npm install --silent

# Step 2: Generate architecture document
echo "[2/3] Generating architecture document..."
node src/docs/generate-doc.js
echo "      -> dist/overwatch-architecture.docx"

# Step 3: Copy HUD to dist
echo "[3/3] Packaging HUD..."
cp src/hud/index.html dist/overwatch-hud.html
echo "      -> dist/overwatch-hud.html"

echo ""
echo "=== Build complete ==="
echo "Deliverables:"
echo "  dist/overwatch-hud.html           - Tactical HUD (open in browser)"
echo "  dist/overwatch-architecture.docx  - Architecture document"
