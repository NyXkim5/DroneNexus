###############################################################################
# NEXUS Drone Simulator - ArduPilot SITL Image
###############################################################################
FROM ubuntu:22.04

LABEL maintainer="NEXUS Drone Sim" \
      description="ArduPilot SITL with MAVProxy for copter simulation"

ENV DEBIAN_FRONTEND=noninteractive
ENV ARDUPILOT_HOME=/ardupilot

SHELL ["/bin/bash", "-c"]

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    build-essential \
    cmake \
    g++ \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-dev \
    python3-lxml \
    python3-matplotlib \
    python3-numpy \
    python3-yaml \
    python3-opencv \
    libxml2-dev \
    libxslt1-dev \
    wget \
    xterm \
    screen \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages ──────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    future \
    pymavlink \
    MAVProxy \
    pexpect \
    dronekit \
    empy==3.3.4 \
    pyserial

# ── Clone ArduPilot ──────────────────────────────────────────────────────────
RUN git clone --depth 1 --branch Copter-4.5 --recurse-submodules \
    https://github.com/ArduPilot/ardupilot.git ${ARDUPILOT_HOME}

WORKDIR ${ARDUPILOT_HOME}

# ── Build SITL ───────────────────────────────────────────────────────────────
RUN ./waf configure --board sitl && \
    ./waf copter

# ── Expose MAVLink ports ─────────────────────────────────────────────────────
EXPOSE 5760/tcp
EXPOSE 14550/udp
EXPOSE 14551/udp
EXPOSE 14560/udp
EXPOSE 14570/udp

# ── Entrypoint ───────────────────────────────────────────────────────────────
COPY docker/ardupilot-sitl/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["Tools/autotest/sim_vehicle.py", "-v", "ArduCopter", "--frame", "quad", "-I0", "--no-rebuild"]
