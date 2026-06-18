# OVERWATCH Backend Service
# See .dockerignore for build context exclusions

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

LABEL maintainer="jay@archv.dev"
LABEL description="OVERWATCH Backend WebSocket Service"
LABEL version="1.0.0"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd --gid 1000 overwatch \
    && useradd --uid 1000 --gid overwatch --shell /bin/false --create-home overwatch

WORKDIR /app

COPY --chown=overwatch:overwatch . .

# Create writable directories for read-only filesystem
RUN mkdir -p /data /tmp \
    && chown overwatch:overwatch /data /tmp

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

USER overwatch

CMD ["python3", "main.py"]
