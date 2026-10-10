#!/usr/bin/env bash
# Source WSL ROS, then let the Python launcher manage signals and cleanup.
set -eo pipefail
pipeline_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/jazzy/setup.bash
source "$pipeline_root/install/local_setup.bash"
exec python3 "$pipeline_root/tools/start_webcam_pipeline.py" "$@"
