"""Existing image-plane plant plus the independent FAKE command boundary."""
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='offline_demo'),
        DeclareLaunchArgument('scenario', default_value='static'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(
            get_package_share_directory('mock_tello') + '/launch/closed_loop.launch.py'),
            launch_arguments={'namespace': namespace,
                              'scenario': LaunchConfiguration('scenario')}.items()),
        Node(package='tello_bridge', executable='tello_bridge_node', namespace=namespace,
             parameters=[{'use_sim_time': False}], output='screen'),
    ])
