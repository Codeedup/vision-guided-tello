#!/usr/bin/env bash
# All registered regression checks; never starts a camera or aircraft connection.
set -eo pipefail
checks_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$checks_root"
source /opt/ros/jazzy/setup.bash
colcon build --event-handlers console_direct+
source install/local_setup.bash
colcon test --event-handlers console_direct+
colcon test-result --verbose
git diff --check
