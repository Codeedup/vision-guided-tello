cmake_minimum_required(VERSION 3.8)
project(tracking_controller)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(drone_interfaces REQUIRED)

add_executable(controller_demo src/controller_demo.cpp)

target_include_directories(controller_demo PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)

install(TARGETS controller_demo
  DESTINATION lib/${PROJECT_NAME}
)

add_executable(tracking_controller_node
  src/tracking_controller_node.cpp
)

target_include_directories(tracking_controller_node PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)

ament_target_dependencies(tracking_controller_node
  rclcpp
  drone_interfaces
)

install(TARGETS tracking_controller_node
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  ament_add_gtest(controller_tests test/controller_tests.cpp)

  target_include_directories(controller_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
  )
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
