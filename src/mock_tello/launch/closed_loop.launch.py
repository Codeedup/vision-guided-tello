"""All three real ROS nodes in an isolated software-only namespace."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    defaults = {
        'namespace': 'image_plane_mock', 'scenario': 'static',
        'initial_x': '0.6', 'initial_y': '-0.4',
        'gain_x': '0.04', 'gain_y': '0.04', 'response_tau': '0.15',
        'sensor_delay': '0.0', 'noise_stddev': '0.0', 'seed': '7',
        'fault_start': '3.0', 'log_path': '',
    }
    floats = ('initial_x', 'initial_y', 'gain_x', 'gain_y', 'response_tau',
              'sensor_delay', 'noise_stddev', 'fault_start')
    parameters = {name: ParameterValue(LaunchConfiguration(name), value_type=float)
                  for name in floats}
    parameters.update({
        'use_sim_time': False,
        'scenario': ParameterValue(LaunchConfiguration('scenario'), value_type=str),
        'seed': ParameterValue(LaunchConfiguration('seed'), value_type=int),
        'log_path': ParameterValue(LaunchConfiguration('log_path'), value_type=str),
    })
    return LaunchDescription([
        *(DeclareLaunchArgument(name, default_value=default)
          for name, default in defaults.items()),
        GroupAction([
            PushRosNamespace(LaunchConfiguration('namespace')),
            Node(package='tracking_controller', executable='tracking_controller_node',
                 parameters=[{'use_sim_time': False}],
                 arguments=['--ros-args', '--log-level', 'warn'], output='screen'),
            Node(package='mission_manager', executable='mission_manager_node',
                 parameters=[{'use_sim_time': False}],
                 arguments=['--ros-args', '--log-level', 'warn'], output='screen'),
            Node(package='mock_tello', executable='mock_tello_node',
                 parameters=[parameters], output='screen'),
        ]),
    ])
