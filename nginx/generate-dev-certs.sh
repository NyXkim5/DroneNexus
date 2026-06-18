#!/usr/bin/env bash
# Generate self-signed TLS certificates for OVERWATCH dev environment
# Usage: ./generate-dev-certs.sh
# Output: ./certs/server.crt and ./certs/server.key

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/../certs"

mkdir -p "${CERTS_DIR}"

echo "Generating self-signed TLS certificate for OVERWATCH dev..."

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "${CERTS_DIR}/server.key" \
    -out "${CERTS_DIR}/server.crt" \
    -subj "/C=US/ST=California/L=Irvine/O=OVERWATCH/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:overwatch,IP:127.0.0.1"

chmod 600 "${CERTS_DIR}/server.key"
chmod 644 "${CERTS_DIR}/server.crt"

echo "Certificates generated:"
echo "  ${CERTS_DIR}/server.crt"
echo "  ${CERTS_DIR}/server.key"
echo ""
echo "To use with docker-compose, the certs volume mount will pick these up automatically."
