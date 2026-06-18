# OVERWATCH ISR Asset Coordination Platform
# See .dockerignore for build context exclusions

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

LABEL maintainer="jay@archv.dev"
LABEL description="OVERWATCH ISR Asset Coordination Platform"
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

# Copy application code
COPY --chown=overwatch:overwatch backend/ ./backend/
COPY --chown=overwatch:overwatch src/ ./src/
COPY --chown=overwatch:overwatch config/ ./config/

# Create writable directories for read-only filesystem
RUN mkdir -p /data /tmp \
    && chown overwatch:overwatch /data /tmp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

USER overwatch

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
