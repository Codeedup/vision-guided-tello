"""UDP receiver, production policies and fake transport; autonomy starts disabled."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    nodes = [
        Node(package='tello_bridge', executable='ros_hand_receiver.py', namespace=namespace,
             arguments=['--bind', LaunchConfiguration('bind'),
                        '--port', LaunchConfiguration('port'),
                        '--namespace', namespace], output='screen'),
        *(Node(package=package, executable=executable, namespace=namespace,
               parameters=[{'use_sim_time': False}, *(
                   [{'timing_trace_path': LaunchConfiguration('bridge_trace'),
                     'timing_trace_label': LaunchConfiguration('trace_label')}]
                   if package == 'tello_bridge' else [])],
               arguments=['--ros-args', '--log-level', 'warn'], output='screen')
          for package, executable in (
              ('tracking_controller', 'tracking_controller_node'),
              ('mission_manager', 'mission_manager_node'),
              ('tello_bridge', 'tello_bridge_node'))),
    ]
    # Register before starting children so even immediate startup failures stop
    # the group. A zero exit is also unexpected while the pipeline is running.
    handlers = [RegisterEventHandler(OnProcessExit(
        target_action=node,
        on_exit=[EmitEvent(event=Shutdown(reason='Required webcam pipeline process exited'))]))
        for node in nodes]
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='webcam_test'),
        DeclareLaunchArgument('bind', default_value='127.0.0.1'),
        DeclareLaunchArgument('port', default_value='5005'),
        DeclareLaunchArgument('bridge_trace', default_value=''),
        DeclareLaunchArgument('trace_label', default_value=''),
        *handlers, *nodes,
    ])
