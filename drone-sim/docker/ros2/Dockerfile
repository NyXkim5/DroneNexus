###############################################################################
# NEXUS Drone Simulator - ROS 2 Humble Base Image
###############################################################################
FROM ros:humble-ros-base-jammy

LABEL maintainer="NEXUS Drone Sim" \
      description="ROS 2 Humble base with MAVROS, SLAM, and build tools"

ENV DEBIAN_FRONTEND=noninteractive
ENV ROS_WS=/ros2_ws

SHELL ["/bin/bash", "-c"]

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    python3-colcon-common-extensions \
    python3-rosdep \
    python3-vcstool \
    python3-pip \
    build-essential \
    cmake \
    git \
    wget \
    curl \
    # ROS 2 packages
    ros-humble-mavros \
    ros-humble-mavros-extras \
    ros-humble-mavros-msgs \
    ros-humble-rtabmap-ros \
    ros-humble-navigation2 \
    ros-humble-nav2-bringup \
    ros-humble-robot-state-publisher \
    ros-humble-joint-state-publisher \
    ros-humble-xacro \
    ros-humble-tf2-ros \
    ros-humble-tf2-tools \
    ros-humble-diagnostic-updater \
    ros-humble-image-transport \
    ros-humble-cv-bridge \
    ros-humble-vision-msgs \
    ros-humble-ros2-controllers \
    ros-humble-ros2-control \
    ros-humble-rmw-cyclonedds-cpp \
    ros-humble-rosbridge-suite \
    ros-humble-geographic-msgs \
    # Python libraries
    python3-yaml \
    python3-numpy \
    python3-opencv \
    && rm -rf /var/lib/apt/lists/*

# ── Install MAVROS geographic lib datasets ───────────────────────────────────
RUN wget -q https://raw.githubusercontent.com/mavlink/mavros/master/mavros/scripts/install_geographiclib_datasets.sh \
    -O /tmp/install_geo.sh && \
    chmod +x /tmp/install_geo.sh && \
    /tmp/install_geo.sh && \
    rm /tmp/install_geo.sh

# ── Python packages ──────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    asyncio \
    websockets>=12.0 \
    aiohttp \
    pyyaml \
    numpy \
    pymavlink \
    mavsdk \
    transforms3d \
    scipy

# ── Create workspace ─────────────────────────────────────────────────────────
RUN mkdir -p ${ROS_WS}/src
WORKDIR ${ROS_WS}

# ── Copy ROS 2 packages ─────────────────────────────────────────────────────
COPY ros2_ws/src ${ROS_WS}/src

# ── Build workspace ──────────────────────────────────────────────────────────
RUN source /opt/ros/humble/setup.bash && \
    cd ${ROS_WS} && \
    rosdep update --rosdistro humble && \
    rosdep install --from-paths src --ignore-src -r -y && \
    colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# ── Entrypoint ───────────────────────────────────────────────────────────────
COPY docker/ros2/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
