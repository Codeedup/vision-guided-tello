"""UDP receiver, production policies and fake transport; autonomy starts disabled."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='webcam_test'),
        DeclareLaunchArgument('bind', default_value='127.0.0.1'),
        DeclareLaunchArgument('port', default_value='5005'),
        Node(package='tello_bridge', executable='ros_hand_receiver.py', namespace=namespace,
             arguments=['--bind', LaunchConfiguration('bind'),
                        '--port', LaunchConfiguration('port'),
                        '--namespace', namespace], output='screen'),
        *(Node(package=package, executable=executable, namespace=namespace,
               parameters=[{'use_sim_time': False}],
               arguments=['--ros-args', '--log-level', 'warn'], output='screen')
          for package, executable in (
              ('tracking_controller', 'tracking_controller_node'),
              ('mission_manager', 'mission_manager_node'),
              ('tello_bridge', 'tello_bridge_node'))),
    ])
