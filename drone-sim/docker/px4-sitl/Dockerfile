###############################################################################
# NEXUS Drone Simulator - PX4 SITL Image
###############################################################################
FROM ubuntu:22.04

LABEL maintainer="NEXUS Drone Sim" \
      description="PX4 SITL with Gazebo Garden bridge"

ENV DEBIAN_FRONTEND=noninteractive
ENV PX4_HOME=/PX4-Autopilot

SHELL ["/bin/bash", "-c"]

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    wget \
    git \
    build-essential \
    cmake \
    ninja-build \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-jinja2 \
    python3-jsonschema \
    python3-toml \
    python3-numpy \
    python3-empy \
    python3-packaging \
    python3-yaml \
    gawk \
    astyle \
    genromfs \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libopencv-dev \
    libxml2-utils \
    unzip \
    zip \
    openjdk-11-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# ── Add Gazebo Garden repository ────────────────────────────────────────────
RUN wget https://packages.osrfoundation.org/gazebo.gpg \
    -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
    http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/gazebo-stable.list && \
    apt-get update && apt-get install -y --no-install-recommends \
    gz-garden \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages ──────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    kconfiglib \
    pyros-genmsg \
    pymavlink \
    future

# ── Clone and build PX4 ─────────────────────────────────────────────────────
RUN git clone --depth 1 --branch v1.14.3 --recurse-submodules \
    https://github.com/PX4/PX4-Autopilot.git ${PX4_HOME}

WORKDIR ${PX4_HOME}

# Run PX4 setup script
RUN bash Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools 2>/dev/null || true

# Build SITL target
RUN make px4_sitl_default 2>/dev/null || \
    echo "PX4 build may require full submodule init at runtime"

# ── Expose MAVLink ports ─────────────────────────────────────────────────────
EXPOSE 14540/udp
EXPOSE 14550/udp
EXPOSE 14560/udp

# ── Entrypoint ───────────────────────────────────────────────────────────────
COPY docker/px4-sitl/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["make", "px4_sitl", "gz_x500"]
