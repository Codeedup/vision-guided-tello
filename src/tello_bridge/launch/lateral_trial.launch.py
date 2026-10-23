"""Real flight trial ROS path: one mission manager and one data-only forwarder."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    source_frame_id = LaunchConfiguration('source_frame_id')
    manager = Node(
        package='mission_manager', executable='mission_manager_node',
        namespace=namespace,
        parameters=[{'use_sim_time': False,
                     'required_candidate_frame_id': source_frame_id}],
        output='screen')
    forwarder = Node(
        package='tello_bridge', executable='tello_approved_forwarder',
        namespace=namespace,
        arguments=['--namespace', namespace,
                   '--windows-ip', LaunchConfiguration('windows_ip'),
                   '--port', LaunchConfiguration('port'),
                   '--flight-session-id', LaunchConfiguration('flight_session_id'),
                   '--flight-source-frame-id', source_frame_id],
        output='screen')
    handlers = [RegisterEventHandler(OnProcessExit(
        target_action=node,
        on_exit=[EmitEvent(event=Shutdown(reason='Required flight trial process exited'))]))
        for node in (manager, forwarder)]
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='lateral_trial'),
        DeclareLaunchArgument('windows_ip'),
        DeclareLaunchArgument('port', default_value='5011'),
        DeclareLaunchArgument('flight_session_id'),
        DeclareLaunchArgument('source_frame_id'),
        *handlers, manager, forwarder,
    ])
