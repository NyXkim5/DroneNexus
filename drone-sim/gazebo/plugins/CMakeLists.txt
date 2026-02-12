cmake_minimum_required(VERSION 3.10.2 FATAL_ERROR)
project(nexus_gazebo_plugins)

# ── Find dependencies ────────────────────────────────────────────────────────
find_package(gz-cmake3 REQUIRED)
find_package(gz-sim7 REQUIRED)
find_package(gz-plugin2 REQUIRED COMPONENTS register)
find_package(gz-common5 REQUIRED)
find_package(gz-math7 REQUIRED)
find_package(gz-msgs9 REQUIRED)
find_package(gz-transport12 REQUIRED)
find_package(yaml-cpp REQUIRED)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── Sensor noise plugin ──────────────────────────────────────────────────────
add_library(SensorNoisePlugin SHARED sensor_noise_plugin.cc)
target_link_libraries(SensorNoisePlugin
  gz-sim7::gz-sim7
  gz-plugin2::gz-plugin2
  gz-common5::gz-common5
  gz-math7::gz-math7
  gz-msgs9::gz-msgs9
  yaml-cpp
)
target_include_directories(SensorNoisePlugin PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
)

# ── Wind disturbance plugin ─────────────────────────────────────────────────
add_library(WindPlugin SHARED wind_plugin.cc)
target_link_libraries(WindPlugin
  gz-sim7::gz-sim7
  gz-plugin2::gz-plugin2
  gz-common5::gz-common5
  gz-math7::gz-math7
  gz-msgs9::gz-msgs9
  yaml-cpp
)
target_include_directories(WindPlugin PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
)

# ── Install ──────────────────────────────────────────────────────────────────
install(TARGETS SensorNoisePlugin WindPlugin
  LIBRARY DESTINATION lib
  ARCHIVE DESTINATION lib
  RUNTIME DESTINATION bin
)
