cmake_minimum_required(VERSION 3.8)
project(mock_tello)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/mock_tello_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  find_package(ament_cmake_ros REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)
  ament_add_pytest_test(model_tests test/test_model.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR})
  add_launch_test(test/test_closed_loop_launch.py TIMEOUT 120
    RUNNER "${ament_cmake_ros_DIR}/run_test_isolated.py")
endif()

ament_package()
