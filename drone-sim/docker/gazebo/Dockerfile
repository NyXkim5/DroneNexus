###############################################################################
# NEXUS Drone Simulator - Gazebo Garden Image
###############################################################################
FROM ubuntu:22.04

LABEL maintainer="NEXUS Drone Sim" \
      description="Gazebo Garden with ROS 2 Humble bridge and custom plugins"

ENV DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-c"]

# ── Base dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    wget \
    git \
    build-essential \
    cmake \
    pkg-config \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# ── Add ROS 2 Humble repository ─────────────────────────────────────────────
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/ros2.list

# ── Add Gazebo Garden repository ────────────────────────────────────────────
RUN wget https://packages.osrfoundation.org/gazebo.gpg \
    -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
    http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/gazebo-stable.list

# ── Install Gazebo Garden + ROS 2 bridge ─────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gz-garden \
    ros-humble-ros-base \
    ros-humble-ros-gz-bridge \
    ros-humble-ros-gz-sim \
    ros-humble-ros-gz-image \
    ros-humble-rmw-cyclonedds-cpp \
    python3-colcon-common-extensions \
    libyaml-cpp-dev \
    libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Build custom Gazebo plugins ─────────────────────────────────────────────
COPY gazebo/plugins /gazebo/plugins
RUN cd /gazebo/plugins && \
    mkdir -p build && cd build && \
    cmake .. \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install && \
    ldconfig

# ── Copy world and model files ──────────────────────────────────────────────
COPY gazebo/worlds /gazebo/worlds
COPY gazebo/models /gazebo/models

# ── Environment ──────────────────────────────────────────────────────────────
ENV GZ_SIM_RESOURCE_PATH=/gazebo/models:/gazebo/worlds
ENV GZ_SIM_SYSTEM_PLUGIN_PATH=/usr/local/lib

# ── Entrypoint ───────────────────────────────────────────────────────────────
COPY docker/gazebo/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gz", "sim", "-v4", "-r", "/gazebo/worlds/empty_field.sdf"]
