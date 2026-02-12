# Drone Simulation Environment — Full Design

## Overview

A full-stack drone simulation environment with an Electron + React desktop app
for hardware visualization, ROS2/Gazebo physics simulation, and Docker-based
reproducibility. Designed to be intuitive for non-engineers while powerful
enough for serious development.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Electron + React App                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │  Drone   │ │  Sensor  │ │  Flight  │ │    Fleet    │ │
│  │ Builder  │ │ Config   │ │   Sim    │ │  Overview   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘ │
│       │             │            │               │        │
│  ┌────┴─────────────┴────────────┴───────────────┴──┐    │
│  │           Physics Engine (JS/WASM)                │    │
│  │   Thrust calc, battery drain, sensor noise        │    │
│  └──────────────────────┬───────────────────────────┘    │
└─────────────────────────┼────────────────────────────────┘
                          │ WebSocket / gRPC
┌─────────────────────────┼────────────────────────────────┐
│              Docker Compose Stack                         │
│  ┌──────────┐ ┌─────────┴──┐ ┌────────────┐             │
│  │  ROS2    │ │  Gazebo    │ │ PX4/Ardu   │             │
│  │  Nodes   │ │  Garden    │ │   SITL     │             │
│  └────┬─────┘ └────┬───────┘ └─────┬──────┘             │
│       │             │               │                     │
│  ┌────┴─────────────┴───────────────┴──┐                 │
│  │         MAVROS / MAVSDK Bridge       │                 │
│  └─────────────────────────────────────┘                 │
└──────────────────────────────────────────────────────────┘
```

## Project Structure

```
NEXUS/drone-sim/
├── app/                            # Electron + React desktop app
│   ├── main/
│   │   ├── main.ts                 # Electron main process
│   │   ├── preload.ts              # Context bridge
│   │   └── ipc-handlers.ts         # File I/O, YAML parsing, sim control
│   ├── renderer/
│   │   ├── App.tsx                  # Root with sidebar navigation
│   │   ├── components/
│   │   │   ├── DroneBuilder/
│   │   │   │   ├── DroneBuilder.tsx         # Main builder view
│   │   │   │   ├── ComponentPalette.tsx     # Drag source for parts
│   │   │   │   ├── DroneCanvas3D.tsx        # Three.js 3D drone model
│   │   │   │   ├── PropertiesPanel.tsx      # Edit component specs
│   │   │   │   ├── ComputedStats.tsx        # Live weight/thrust/time
│   │   │   │   └── CompatibilityChecker.tsx # Warnings display
│   │   │   ├── SensorPanel/
│   │   │   │   ├── SensorConfigurator.tsx   # Grid of sensor cards
│   │   │   │   ├── SensorCard.tsx           # Individual sensor toggle/config
│   │   │   │   ├── NoisePreview.tsx         # Live noise chart
│   │   │   │   └── SensorTooltip.tsx        # "What does this do?" explainer
│   │   │   ├── FlightSim/
│   │   │   │   ├── FlightSimView.tsx        # Main sim view
│   │   │   │   ├── TelemetryGauges.tsx      # Alt, speed, battery, etc.
│   │   │   │   ├── PIDTuner.tsx             # Sliders + response curve
│   │   │   │   ├── FlightModeSelector.tsx   # Mode buttons
│   │   │   │   ├── FailsafePanel.tsx        # Test failsafe scenarios
│   │   │   │   ├── MissionMap.tsx           # Waypoint editor
│   │   │   │   └── WorldRenderer.tsx        # 3D world (Three.js/Gazebo)
│   │   │   ├── FleetOverview/
│   │   │   │   ├── FleetGrid.tsx            # All drones grid
│   │   │   │   ├── DroneCard.tsx            # Summary card
│   │   │   │   ├── CompareView.tsx          # Side-by-side comparison
│   │   │   │   └── ImportExport.tsx         # YAML file handling
│   │   │   └── shared/
│   │   │       ├── Sidebar.tsx
│   │   │       ├── Header.tsx
│   │   │       └── StatusBar.tsx
│   │   ├── stores/
│   │   │   ├── droneStore.ts        # Current drone config state
│   │   │   ├── sensorStore.ts       # Sensor configs
│   │   │   ├── simStore.ts          # Simulation state
│   │   │   └── fleetStore.ts        # Fleet management
│   │   ├── lib/
│   │   │   ├── physics.ts           # Thrust, drag, battery models
│   │   │   ├── sensors.ts           # Noise generation functions
│   │   │   ├── compatibility.ts     # Cross-component validation
│   │   │   ├── airframe-parser.ts   # YAML <-> TypeScript types
│   │   │   └── units.ts             # Unit conversion helpers
│   │   ├── types/
│   │   │   └── airframe.ts          # TypeScript types for YAML schema
│   │   └── styles/
│   │       └── globals.css          # Tailwind + custom styles
│   ├── package.json
│   ├── tsconfig.json
│   ├── electron-builder.yml
│   ├── vite.config.ts
│   └── tailwind.config.js
│
├── docker/
│   ├── docker-compose.yml           # Full stack: ROS2 + Gazebo + SITL
│   ├── ros2/
│   │   └── Dockerfile               # ROS2 Humble + workspace
│   ├── gazebo/
│   │   └── Dockerfile               # Gazebo Garden + plugins
│   ├── px4-sitl/
│   │   └── Dockerfile               # PX4 SITL with Gazebo bridge
│   └── ardupilot-sitl/
│       └── Dockerfile               # ArduPilot SITL with Gazebo plugin
│
├── ros2_ws/src/
│   ├── drone_description/
│   │   ├── package.xml
│   │   ├── setup.py
│   │   ├── urdf/                    # URDF models generated from YAML
│   │   ├── sdf/                     # SDF models for Gazebo
│   │   ├── meshes/                  # 3D mesh files
│   │   └── config/                  # Symlinks to ../../config/airframes
│   ├── drone_bringup/
│   │   ├── package.xml
│   │   ├── setup.py
│   │   └── launch/
│   │       ├── full_sim.launch.py   # Everything
│   │       ├── gazebo.launch.py     # Gazebo world + drone spawn
│   │       ├── px4_sitl.launch.py   # PX4 SITL instances
│   │       ├── ardupilot.launch.py  # ArduPilot SITL
│   │       └── sensors.launch.py    # Sensor sim nodes
│   ├── drone_navigation/
│   │   ├── package.xml
│   │   ├── setup.py
│   │   └── drone_navigation/
│   │       ├── slam_node.py         # RTABMap wrapper
│   │       ├── path_planner.py      # A*/RRT* planner
│   │       ├── obstacle_avoidance.py
│   │       ├── vio_fallback.py      # Visual-inertial odometry
│   │       └── waypoint_executor.py # MAVLink mission execution
│   ├── drone_perception/
│   │   ├── package.xml
│   │   ├── setup.py
│   │   └── drone_perception/
│   │       ├── camera_node.py       # ROS2 camera publisher
│   │       ├── detector_node.py     # YOLO placeholder
│   │       ├── aruco_detector.py    # ArUco/landing pad detection
│   │       └── terrain_classifier.py
│   ├── drone_control/
│   │   ├── package.xml
│   │   ├── setup.py
│   │   └── drone_control/
│   │       ├── flight_controller.py # High-level flight control
│   │       ├── pid_tuner.py         # Real-time PID adjustment
│   │       ├── mode_manager.py      # Flight mode switching
│   │       └── failsafe_manager.py  # Failsafe behaviors
│   └── drone_interfaces/
│       ├── package.xml
│       ├── CMakeLists.txt
│       ├── msg/
│       │   ├── DroneState.msg
│       │   ├── SensorConfig.msg
│       │   └── AirframeConfig.msg
│       └── srv/
│           ├── SetFlightMode.srv
│           ├── UpdatePID.srv
│           └── LoadAirframe.srv
│
├── gazebo/
│   ├── worlds/
│   │   ├── empty_field.sdf          # Basic flat terrain
│   │   ├── warehouse.sdf            # Indoor warehouse
│   │   ├── urban.sdf                # City with buildings
│   │   └── agricultural.sdf         # Farm with crop rows
│   ├── models/
│   │   ├── quadcopter/              # Default quad model
│   │   ├── hexacopter/
│   │   └── obstacles/               # Trees, buildings, etc.
│   └── plugins/
│       ├── sensor_noise_plugin.cc   # Configurable noise injection
│       └── wind_plugin.cc           # Wind disturbance model
│
├── config/
│   ├── airframes/
│   │   ├── 5inch-fpv-freestyle.yaml
│   │   ├── 3inch-cinewhoop.yaml
│   │   ├── 7inch-long-range.yaml
│   │   ├── mapping-quad.yaml
│   │   ├── hex-heavy-lift.yaml
│   │   └── octo-agricultural.yaml
│   ├── sensors/
│   │   ├── imu-noise.yaml
│   │   ├── gps-noise.yaml
│   │   ├── baro-noise.yaml
│   │   ├── mag-noise.yaml
│   │   ├── lidar-noise.yaml
│   │   └── camera-noise.yaml
│   └── missions/
│       ├── simple-square.yaml
│       ├── survey-grid.yaml
│       └── orbit-poi.yaml
│
├── scripts/
│   ├── launch_sim.sh                # One-command startup
│   ├── switch_fc.sh                 # Toggle PX4 <-> ArduPilot
│   ├── generate_urdf.py             # YAML -> URDF converter
│   └── validate_airframe.py         # Check YAML + compatibility
│
└── tests/
    ├── test_physics.py
    ├── test_sensors.py
    ├── test_compatibility.py
    ├── test_airframe_parser.py
    └── test_yaml_validation.py
```

## Airframe YAML Schema (Complete)

See the expanded schema with frame, motors, propellers, battery, electronics,
sensors, thermal, electrical, compatibility, mechanical, and computed sections.
All presets ship with full data. The `computed` section is auto-calculated by
the physics engine and displayed in the UI.

## Electron App Views

### 1. Drone Builder
- Left panel: Component palette with drag-and-drop
- Center: Three.js 3D drone model, rotatable, components labeled
- Right: Properties editor for selected component
- Bottom bar: Live computed stats (AUW, T:W, flight time, warnings)

### 2. Sensor Configurator
- Grid of toggleable sensor cards (IMU, GPS, Baro, Mag, LiDAR, Camera, Depth, Optical Flow)
- Each card: noise parameter sliders, live preview chart
- Preset profiles: Perfect / Typical / Noisy / Failing
- Plain-English tooltips for non-engineers

### 3. Flight Simulator
- 3D world view (Gazebo connection or built-in Three.js renderer)
- Telemetry gauges: altitude, speed, battery, GPS, signal
- PID tuner with live response curves
- Flight mode selector
- Failsafe test buttons
- Mission waypoint map

### 4. Fleet Overview
- Grid of configured drone cards
- Side-by-side spec comparison
- YAML import/export

## Implementation Plan

### Phase 1: Foundation (config + physics + types)
1. Create project structure (drone-sim/ directory tree)
2. Write all 6 airframe YAML presets with full hardware profiles
3. Write sensor noise model YAML configs
4. Write TypeScript types matching the YAML schema
5. Write physics engine (thrust calculation, battery drain, drag model)
6. Write sensor noise generators
7. Write compatibility checker
8. Write YAML parser/validator
9. Write unit tests for physics, sensors, compatibility

### Phase 2: Electron App Shell
10. Initialize Electron + Vite + React + Tailwind project
11. Set up Electron main process with IPC handlers
12. Create sidebar navigation + app layout
13. Implement Zustand stores

### Phase 3: Drone Builder View
14. Build ComponentPalette with categorized parts
15. Build DroneCanvas3D with Three.js (simple drone model)
16. Build PropertiesPanel form editor
17. Build ComputedStats live dashboard
18. Build CompatibilityChecker warnings
19. Wire drag-and-drop to update config

### Phase 4: Sensor Configurator View
20. Build SensorCard component
21. Build SensorConfigurator grid
22. Build NoisePreview chart (Canvas or Chart.js)
23. Add preset profiles
24. Add tooltips for non-engineers

### Phase 5: Flight Simulator View
25. Build TelemetryGauges
26. Build FlightModeSelector
27. Build PIDTuner with response curves
28. Build FailsafePanel
29. Build MissionMap (Leaflet)
30. Build WorldRenderer (Three.js basic world)
31. Wire to simStore for state management

### Phase 6: Fleet Overview
32. Build FleetGrid + DroneCard
33. Build CompareView
34. Build ImportExport

### Phase 7: Docker + ROS2 + Gazebo Infrastructure
35. Write Dockerfiles for ROS2, Gazebo, PX4 SITL, ArduPilot SITL
36. Write docker-compose.yml for full stack
37. Write ROS2 package manifests + setup.py files
38. Write drone_interfaces (custom msgs/srvs)
39. Write drone_bringup launch files
40. Write drone_description URDF generator from YAML
41. Write Gazebo world files
42. Write Gazebo sensor noise plugin (C++)
43. Write Gazebo wind plugin (C++)

### Phase 8: ROS2 Nodes
44. Write drone_control/flight_controller.py
45. Write drone_control/pid_tuner.py
46. Write drone_control/mode_manager.py
47. Write drone_control/failsafe_manager.py
48. Write drone_navigation/slam_node.py
49. Write drone_navigation/path_planner.py
50. Write drone_navigation/obstacle_avoidance.py
51. Write drone_navigation/vio_fallback.py
52. Write drone_navigation/waypoint_executor.py
53. Write drone_perception/camera_node.py
54. Write drone_perception/detector_node.py
55. Write drone_perception/aruco_detector.py
56. Write drone_perception/terrain_classifier.py

### Phase 9: Scripts + Integration
57. Write launch_sim.sh
58. Write switch_fc.sh
59. Write generate_urdf.py
60. Write validate_airframe.py
61. Write mission YAML files
62. Integration tests
