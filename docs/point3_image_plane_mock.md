# Point 3: approved-boundary image-plane mock

## Status and evidence — 9 October 2026

Implementation started from GitHub commit
`98eb4b9c6ab254cc6253371266feeb735a4f82b4`, a clean remote clone of
`Codeedup/vision-guided-tello`, on branch `feature/image-plane-mock`.
The user confirmed points 1 and 2 were implemented. That commit contains
`test_supervisor_edges_launch.py`, `test_supervisor_clock_launch.py` and
`test_controller_supervisor_launch.py` and their CMake/manifest wiring. It does
not contain a completion report or captured fresh test results. Those earlier
ROS tests were inspected, not rerun in this environment.

Point 3 implementation is present. Local verification passes **24 pure model /
approved-boundary tests and 11 deterministic policy/model scenarios**. The
scenario harness compiles and calls the actual existing C++ controller and
supervisor headers. It supplies its own scheduling and input cache, so it is
not evidence for the production ROS wrappers, watchdog threads or DDS.

**ROS build and the new real-chain launch tests remain unverified here** because
this environment has no ROS, colcon, rclpy or generated drone interfaces.
Run the WSL validation below before marking this milestone complete. Do not
add these counts to earlier colcon totals or claim independent flight behaviors
from an aggregate test count.

No existing controller, supervisor, message, or test was changed. No SDK,
network socket, takeoff, real landing command, or aircraft connection was added.

## Requirements and architecture

The selected platform remains a standard Ryze/DJI Tello with laptop compute,
WSL Ubuntu 24.04 and ROS 2 Jazzy. The current scope is lateral/vertical visual
centering with exactly one usable target. Forward motion, yaw, autonomous
distance hold and automatic takeoff remain out of scope. Real perception must
eventually establish open-palm eligibility; synthetic one-hand detection does
not implement that rule.

`mock_tello` consumes **ApprovedCommand only** for motion and publishes synthetic
`HandTarget`. Its launch file runs the existing real controller and supervisor.
The default namespace is `/image_plane_mock`. It never subscribes to raw
candidates. A `std_msgs/String` JSON state topic is diagnostic output only.

| Interface | Meaning |
|---|---|
| `hand_target` | Synthetic camera observations, stamped at acquisition |
| `approved_command` | Independent supervisor decisions; the only motion input |
| `mock_tello/state` | JSON state: true errors, response velocities, applied inputs, consumer mode, terminal flag, frame counts and run index |
| `mission_manager/set_autonomy` | Existing explicit enable / ordinary disable |
| `mission_manager/manual_takeover` | Existing explicit authority takeover and supervisor latch reset |
| `mock_tello/reset` | Explicit mock-world reset, requiring a fresh disabled supervisor decision |

The world starts **prepositioned in stabilized hover**. This is an initial
simulation condition, not a takeoff action. Disabled authority yields control;
the unmodelled manual pilot contributes zero motion in this mock. Residual
response velocity decays instead of stopping instantly.

## Model and assumptions

Image x is positive right; image y is positive below the centre. Let `u_l` and
`u_v` be bounded lateral/right and vertical/up command scalars. The pure model is:

```text
tau * dv_l/dt + v_l = gain_x * u_l
tau * dv_v/dt + v_v = gain_y * u_v
dx/dt = target_image_velocity_x - v_l
dy/dt = target_image_velocity_y + v_v
```

Velocity and displacement use the exact first-order solution for each held
input. Target rates are held for the step. `tau=0` selects an instantaneous
response. True errors are not clipped: leaving the image loses detection.
Noise that pushes a measurement out of bounds also loses detection rather than
being clipped into an apparently valid target.

| Parameter | Default | Interpretation |
|---|---:|---|
| `initial_x`, `initial_y` | 0.6, -0.4 | Initial normalized image errors |
| `gain_x`, `gain_y` | 0.04 | Normalized image units / second / scalar |
| `response_tau` | 0.15 s | Assumed response time constant |
| `sensor_hz` | 20 | Acquisition rate; configurable from 1 to 120 |
| `step_seconds` | 0.01 s | Requested model update period; 0.001 to 0.05 |
| `sensor_delay` | 0 s | Fixed acquisition-to-delivery delay, including transport; 0 to 2 |
| `noise_stddev` | 0 | Independent Gaussian error noise, per axis |
| `seed` | 7 | Repeatable sensor random sequence |
| `state_hz` | 10 | Diagnostic / CSV rate |
| `scenario` | static | Scenario listed below |
| `fault_start` | 3 s | Fault start measured from mock startup or reset |
| `fault_duration` | 0.6 s | Fault duration; `loss` instead defaults to 2.5 s |
| `log_path` | empty | Optional new CSV file; an existing file is never overwritten |

At scalar 10 the assumed apparent steady motion is `0.04 * 10 = 0.4` image
units/s. With the existing P gain 20, the small-error gain product is
`0.04 * 20 = 0.8 /s`. These are mock assumptions, **not Tello velocity
calibration or flight-safe gain tuning**. The zero-delay linearized response
has characteristic polynomial `0.15*s^2 + s + 0.8`; real sensing and transport
delay alter that behavior. A passing 100 ms example does not establish a
universal latency or noise tolerance.

Acquisition and delivery use separate timers. Delivery retains the original
stamp and measured values; it never restamps an old frame. The queue is FIFO
and bounded at 512 frames. At the maximum supported 120 Hz and 2 s delay, the
nominal backlog is about 240 frames. Queue overflow drops the oldest frame.

The ROS node uses steady timers and monotonic elapsed time with ROS source
stamps. This launch requires `use_sim_time=False`. Callback intervals exceeding
100 ms revoke the cached input (`STEP_OVERRUN`); the model conservatively applies
neutral across that gap and integrates residual lag. This is a soft timing
model, not a hard real-time reconstruction of stalled scheduling.

## Independent consumer safety

The mock separately checks the supervisor decision age, original source age
when tracking, and local monotonic receipt silence. Each freshness limit is
strictly less than 250 ms. Finite scalars within ±10 and consistent permission
flags are required; unauthorized nonzero payloads fail closed. Fresh supervisor
heartbeats can repeat a source stamp, but cannot renew that source's age.
Duplicate/older decision stamps cannot replace the payload or renew receipt time.

Expired/invalid input selects neutral. This mock does not independently request
landing when the supervisor dies; it demonstrates a neutral consumer watchdog.
Real bridge lost-link and flight-state policy still needs its own design.

A fresh, consistent `request_land` latches **LANDING_REQUESTED** in the mock.
It selects neutral through the response lag and makes new observations absent.
Repeated requests are idempotent. No descent, ground contact or landing
completion is simulated. Manual takeover clears the supervisor's latch but
does not reset the mock terminal condition. Resetting the mock is a separate
explicit action and requires a fresh disabled decision. Pending frames are
cleared; decision replay protection is retained. Each reset increments the
diagnostic/CSV `run_index` and restarts scenario time.

## Build and validate in WSL

Keep the drone powered off for this software-only milestone. From the actual
repository root in WSL (normally `/home/dylan/vision_guided_tello_ws`):

```bash
source /opt/ros/jazzy/setup.bash
cd /home/dylan/vision_guided_tello_ws
colcon build --packages-up-to mock_tello
source install/local_setup.bash
colcon test --packages-select mock_tello \
  --event-handlers console_direct+ --ctest-args --output-on-failure
colcon test-result --all --verbose
```

If newly declared dependencies are missing, use the workspace's normal rosdep
dependency installation before rebuilding:

```bash
rosdep install --from-paths src --ignore-src --rosdistro jazzy -r -y
```

Then rerun all application behavior tests, including the new launch test:

```bash
colcon test --packages-select tracking_controller mission_manager mock_tello \
  --event-handlers console_direct+ \
  --ctest-args -R 'controller_tests|supervisor_tests|candidate_safety_tests|model_tests|test_.*launch' \
  --output-on-failure
colcon test-result --all --verbose
```

That explicit regression filter excludes generated lint checks. The new mock
package registers its pure tests and launch test, without adding lint targets.
Inspect fresh test discovery/reports and retained failures rather than relying
only on a total. Record actual results in this document once run.

The new real-chain launch suite observes four isolated namespaces running the
actual three executables. Its five active tests cover delayed/noisy static
convergence and header/sign propagation, takeover, dropout with five-frame
reacquisition, latched mock landing/reset, stale delayed observations, and an
actual supervisor process suspension with independent mock timeout. One
post-shutdown method checks all twelve processes. These checks have not run in
the authoring environment. Discovery, services and behavior waits are bounded;
the probe publishes neither target nor candidate commands.

## First manual run

Terminal 1:

```bash
source /opt/ros/jazzy/setup.bash
cd /home/dylan/vision_guided_tello_ws
source install/local_setup.bash
ros2 launch mock_tello closed_loop.launch.py
```

Terminal 2, after sourcing ROS and the workspace:

```bash
ros2 service call /image_plane_mock/mission_manager/set_autonomy std_srvs/srv/SetBool '{data: true}'
ros2 topic echo /image_plane_mock/mock_tello/state
```

The console emits one JSON state per second. Initially errors remain at
`(0.6,-0.4)` with yielded authority. After explicit enable and five new frames,
positive lateral and vertical inputs reduce both magnitudes. Once inside the
dead zone, legitimate tracking can remain true while both inputs are zero.

Stop the topic echo with Ctrl+C. To reset a scenario deliberately:

```bash
ros2 service call /image_plane_mock/mission_manager/manual_takeover std_srvs/srv/Trigger '{}'
ros2 service call /image_plane_mock/mock_tello/reset std_srvs/srv/Trigger '{}'
ros2 service call /image_plane_mock/mission_manager/set_autonomy std_srvs/srv/SetBool '{data: true}'
```

If reset reports that a fresh disabled decision has not yet arrived, wait for
the next heartbeat and retry reset; do not re-enable before a successful reset.
After terminal landing, resetting the mock does not cancel a real landing
command because no aircraft commands exist in this implementation.

## Scenarios and logging

| Scenario | Behavior |
|---|---|
| static | One stationary valid target |
| moving | Target moves sinusoidally: x amplitude 0.12 at 0.2 Hz; y amplitude 0.10 at 0.15 Hz |
| dropout | No-hand observations during fault window; recovers afterward |
| ambiguous | Two-hand observations during fault window; recovers afterward |
| silence | No new frames during fault window; exercises upstream watchdogs |
| loss | 2.5 s no-hand window by default; supervisor should latch landing |

Use a fresh launch for each manual scenario. Fault time starts at startup, so
take over/reset and promptly enable after discovery to align the experiment.
Launch arguments expose common parameters; `sensor_hz`, `step_seconds`,
`state_hz` and `fault_duration` can be set when running the mock node directly.
Parameters are read-only for each run; restart to change them.

```bash
ros2 launch mock_tello closed_loop.launch.py scenario:=dropout fault_start:=3.0
ros2 launch mock_tello closed_loop.launch.py scenario:=loss
ros2 launch mock_tello closed_loop.launch.py scenario:=moving
ros2 launch mock_tello closed_loop.launch.py sensor_delay:=0.10 noise_stddev:=0.004 seed:=7
ros2 launch mock_tello closed_loop.launch.py sensor_delay:=0.30
ros2 launch mock_tello closed_loop.launch.py log_path:=/tmp/mock_trial_01.csv
```

A CSV stores diagnostic samples with run index and elapsed time, not every ROS
message. Use ROS bag recording separately if complete transport evidence is
needed. Exact random samples repeat with the seed; ROS scheduling does not
necessarily repeat timing or closed-loop trajectories.

## Deterministic experiment evidence

The authoring run's [metrics JSON](evidence/point3_policy_metrics.json) and
[eleven CSV logs](evidence/point3_policy_logs.zip) are retained with this change.
They are policy/model evidence, not ROS or aircraft recordings.

Reproduce without ROS, using Python 3 and g++ from the repository root:

```bash
PYTHONPATH=src/mock_tello python3 -m unittest discover -s src/mock_tello/test -p test_model.py -v
python3 src/mock_tello/test/run_scenarios.py --output /tmp/mock_policy_trial_01
```

The output directory must be new. It contains `metrics.json` and eleven full
100 Hz CSV logs. The driver is built in a temporary directory with C++17,
`-Wall -Wextra -Werror`, against the current repository headers. A deterministic
10 ms scheduler, 20 Hz observations and 50 Hz decisions replace ROS transport.
Fault scenarios start at 1.0 s while motion is still active, rather than waiting
until static centering has completed. Each experiment lasts 8 s.

| Experiment | Observed result in deterministic harness |
|---|---|
| Horizontal only | Settled permanently inside ±0.08 at 2.56 s; final x 0.06547 |
| Vertical only | Settled at 2.08 s; final y -0.06717 |
| Both axes | Settled at 2.56 s; final (0.06547,-0.06717); scalar saturation 10 exercised |
| 100 ms delay, noise σ=0.004, seed 7 | Settled at 2.46 s; final (0.05744,-0.06003) |
| Moving target | Final-second maximum absolute x 0.11035 and absolute y 0.09135; no landing |
| 600 ms dropout / ambiguity | Neutral at 1.0 s; tracking reacquired at 1.8 s; no landing |
| 600 ms sensor silence | Neutral at 1.2 s; tracking reacquired at 1.8 s; no landing |
| 2.5 s loss | Neutral at 1.0 s; landing requested at 2.5 s; terminal latched |
| 300 ms sensor delay | Never tracked; landing requested at 1.5 s |
| One-second supervisor stall | Consumer timeout observed; neutral; recovery tracking at 2.2 s |

Here “settled” means true errors stay inside the dead zone for the remainder of
the 8 s record, with at least a one-second tail. The verification also checks
scalar limits and no repeated out-of-dead-zone sign crossings after 3 s in the
static cases. Moving-target errors are not expected to remain in the dead zone.
Exact deadlines here are deterministic policy evidence, not measured ROS or
aircraft reaction latency.

## Remaining risks and next milestone

Unmeasured: real scalar-to-image-motion gain, variable camera/Wi-Fi latency,
axis mapping under mirroring, VPS behavior, manual override transport, hardware
landing completion, battery endurance and flight-safe tuning. No mass or power
budget changes occur because all added code runs on the laptop and adds no
payload. The simulation omits attitude, yaw, distance changes, perspective,
occlusion geometry, motor dynamics and external disturbances.

Point 3 can be accepted after the WSL build, new ROS launch suite and existing
behavior regressions pass with fresh recorded results. The next milestone is
webcam OpenCV/MediaPipe perception with motion still simulated. Replacing the
synthetic observations requires an explicit feedback design: a live stationary
webcam does not move in response to mock drone commands. Do not run two
`HandTarget` publishers on the same topic or claim webcam data closes this
plant feedback loop without modelling the image transformation.

Keep the real Tello bridge and propeller-on testing as separate later stages.
