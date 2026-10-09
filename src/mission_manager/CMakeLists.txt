cmake_minimum_required(VERSION 3.8)
project(mission_manager)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(drone_interfaces REQUIRED)
find_package(std_srvs REQUIRED)

add_executable(
  mission_manager_node
  src/mission_manager_node.cpp
)

target_include_directories(mission_manager_node PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)

ament_target_dependencies(mission_manager_node
  rclcpp
  drone_interfaces
  std_srvs
)

install(TARGETS mission_manager_node
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  ament_add_gtest(supervisor_tests
    test/supervisor_tests.cpp
  )

  target_include_directories(supervisor_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
  )
  find_package(ament_lint_auto REQUIRED)

  find_package(ament_cmake_ros REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)
  
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

    ament_add_gtest(
    candidate_safety_tests
    test/candidate_safety_tests.cpp
  )

  add_launch_test(
  test/test_mission_manager_launch.py
  TIMEOUT 30
  RUNNER "${ament_cmake_ros_DIR}/run_test_isolated.py"
)

  target_include_directories(candidate_safety_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
  )

endif()

ament_package()
