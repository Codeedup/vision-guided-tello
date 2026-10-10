cmake_minimum_required(VERSION 3.8)
project(tello_bridge)
find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/tello_bridge_node scripts/tello_operator
  DESTINATION lib/${PROJECT_NAME})
set(WEBCAM_TOOLS ${CMAKE_CURRENT_SOURCE_DIR}/../../tools/webcam_perception)
install(PROGRAMS ${WEBCAM_TOOLS}/ros_hand_receiver.py ${WEBCAM_TOOLS}/measure_pipeline.py
  DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})
if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  find_package(ament_cmake_ros REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)
  find_package(ament_cmake_flake8 REQUIRED)
  find_package(ament_cmake_pep257 REQUIRED)
  find_package(ament_cmake_xmllint REQUIRED)
  find_package(ament_cmake_lint_cmake REQUIRED)
  ament_flake8()
  ament_flake8(${WEBCAM_TOOLS}/.. TESTNAME tools_flake8)
  ament_pep257()
  ament_pep257(${WEBCAM_TOOLS}/.. TESTNAME tools_pep257)
  ament_xmllint()
  ament_lint_cmake()
  ament_add_pytest_test(boundary_tests test/test_boundary.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR})
  ament_add_pytest_test(timing_tests test/test_timing.py
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR})
  ament_add_pytest_test(webcam_tools_tests ${WEBCAM_TOOLS}
    APPEND_ENV PYTHONPATH=${WEBCAM_TOOLS} TIMEOUT 60)
  ament_add_pytest_test(diagnostic_tests ${WEBCAM_TOOLS}/../tello_diagnostics
    APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}:${WEBCAM_TOOLS}/../tello_diagnostics
    TIMEOUT 60)
  ament_add_pytest_test(synthetic_demo_tests test/test_demo.py TIMEOUT 120)
  add_launch_test(test/test_loopback_launch.py TIMEOUT 120
    RUNNER "${ament_cmake_ros_DIR}/run_test_isolated.py")
endif()
ament_package()

