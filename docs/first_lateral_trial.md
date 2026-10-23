# First supervised ROS lateral trial

This procedure sends **one signed lateral pulse** from a supervised ROS test
source through `mission_manager`, a data-only WSL forwarder, and the Windows
gateway. The Windows gateway alone owns the Tello SDK socket. The test source
is synthetic and is identified by a run-specific ROS frame ID; it is not hand
tracking. The gateway has no SDK takeoff command. The pilot takes off, hovers,
and lands with the app.

The first requested left test uses `--lateral -2 --duration-ms 150`. This is
an **intended** left direction under the SDK axis convention. Airborne sign and
distance are not established until the pilot observes and records them. The
hard flight gateway cap remains ±5 on lateral and vertical, but this procedure
uses only the specified single lateral value. Forward/backward, vertical, and
yaw commands remain zero.

## Gates before a real trial

- Use a committed revision whose full offline checks and hosted CI passed. Run
  the FAKE rehearsal below on the actual Windows/WSL route, using the same
  revision. Save and review both artifacts. Resolve any error before flight.
- Pilot is present with line of sight, a clear suitable area, inspected aircraft
  and battery, and the app ready for manual control. Reconfirm the app priority
  observation on this session. The pilot has already reported a manual
  takeoff/hover/normal landing and the aircraft's no-command landing after
  about 15 seconds; these are pilot observations, not software proof that a
  `land` packet succeeds.
- The pilot states and has practiced the intervention method to use if app
  takeover and onboard no-command landing fail. Do not use Space or disconnect
  as proof of landing. Space, gateway focus loss while armed, and window close
  request neutral then land; landing delivery is unconfirmed. If control or
  direction is unexpected, stop the trial, use the pilot's recovery method,
  and land. Never test failure recovery by deliberately killing the live link.
- Close all other SDK clients, including earlier gateway, ROS flight, and
  diagnostics. The app is the pilot's recovery tool. The flight gateway must
  be the only computer SDK socket owner. Use a new run ID and create-only
  evidence directory for each attempt.

## Network and shared run identity

Choose a Windows IPv4 address reachable from WSL as `gatewayIp`; it can differ
from the Tello Wi-Fi IPv4 (`telloIp`, commonly `192.168.10.2`). Record WSL's
IPv4 address as `wslIp`. Confirm the FAKE rehearsal reaches this exact Windows
listener and returns the pilot release to WSL. The real gateway pins packets
to `wslIp`; the forwarder accepts releases only from `gatewayIp:5011`.

In Windows PowerShell, replace addresses and keep the ID for the WSL commands:

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$env:PYTHONPATH = "$repo\src\tello_bridge"
$id = [guid]::NewGuid().ToString('N')
$frame = "supervised_lateral_trial_$id"
$gatewayIp = 'WINDOWS_IPV4_REACHABLE_FROM_WSL'
$wslIp = 'WSL_IPV4'
$telloIp = 'WINDOWS_TELLO_WIFI_IPV4'
$evidence = "$repo\artifacts\lateral-$id"
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
$trace = "$evidence\gateway.json"
Write-Host "Flight session ID: $id"
```

In WSL, use the printed ID. Verify the expected outbound source IPv4 with
`ip -4 route get WINDOWS_IPV4_REACHABLE_FROM_WSL` before putting that address
in `$wslIp` above.

```bash
cd /home/dylan/vision_guided_tello_ws
source /opt/ros/jazzy/setup.bash
colcon --log-base /tmp/tello-flight-log build \
  --build-base /tmp/tello-flight-build \
  --install-base /tmp/tello-flight-install
source /tmp/tello-flight-install/local_setup.bash
export FLIGHT_ID=PASTE_32_CHARACTER_LOWERCASE_ID
export FLIGHT_FRAME=supervised_lateral_trial_$FLIGHT_ID
export GATEWAY_IP=WINDOWS_IPV4_REACHABLE_FROM_WSL
```

The workspace's older default `build/` tree may contain a stale ROS generated
directory; the commands use an isolated prefix. Run the full local offline
gate on the exact revision after building:

```bash
colcon --log-base /tmp/tello-flight-log test \
  --build-base /tmp/tello-flight-build \
  --install-base /tmp/tello-flight-install
colcon --log-base /tmp/tello-flight-log test-result \
  --test-result-base /tmp/tello-flight-build --verbose
git diff --check
```

## FAKE route rehearsal, with no aircraft connection

Use a fresh ID/evidence directory for this rehearsal. Windows terminal 1:

```powershell
& $python -m tello_bridge.windows_gateway --mode flight-dry-run `
  --bind $gatewayIp --port 5011 --upstream-ip $wslIp `
  --flight-session-id $id --flight-source-frame-id $frame --trace $trace
```

WSL terminal 1, after sourcing the environment above:

```bash
ros2 launch tello_bridge lateral_trial.launch.py \
  namespace:=lateral_trial windows_ip:=$GATEWAY_IP port:=5011 \
  flight_session_id:=$FLIGHT_ID source_frame_id:=$FLIGHT_FRAME
```

WSL terminal 2, after sourcing the same environment:

```bash
ros2 run tello_bridge tello_lateral_trial \
  --namespace /lateral_trial --flight-session-id "$FLIGHT_ID" \
  --lateral -2 --duration-ms 150 --dry-run \
  --report "artifacts/lateral-$FLIGHT_ID/ros.json"
```

Follow the prompts: click **Confirm FAKE hover**, return to the WSL trial
terminal and press Enter **before arming**, then return to the Windows gateway
and click **Arm flight session** and **Release ONE ROS lateral pulse**. Keep the
gateway focused until the trial reports completion; losing focus while armed
aborts. Close the gateway to write its trace. Review the FAKE trace and ROS
report together:

```powershell
& $python "$repo\tools\tello_diagnostics\review_lateral_trial.py" `
  $trace "$evidence\ros.json"
```

Require `pass: true` and `scope: FAKE_SINGLE_AXIS_PATH_ONLY`. This proves the
Windows/WSL message and local gate sequence using a socket-free FAKE transport.
It proves no aircraft behavior.

## One real lateral trial

Create a **new** ID and evidence directory, using the setup above. Repeat the
WSL launch and trial commands, omitting `--dry-run`. On Windows, with propellers
installed and the pilot present, start the real gateway:

```powershell
& $python -m tello_bridge.windows_gateway --mode flight --hardware `
  --tello-bind $telloIp --pilot-present --propellers-installed `
  --bind $gatewayIp --port 5011 --upstream-ip $wslIp `
  --flight-session-id $id --flight-source-frame-id $frame --trace $trace
```

The gateway starts disabled. Start ROS launch and trial while grounded. The
pilot takes off and establishes a steady hover **with the app**, then clicks
**Pilot confirms hover** in the gateway. The pilot returns to the WSL trial
terminal and presses Enter **before arming**. After that, focus the Windows
gateway, click **Arm flight session**, verify it shows `ARMED`, and click
**Release ONE ROS lateral pulse** exactly once. Keep that window focused until
the ROS trial ends and the gateway disarms. The pilot watches for actual motion
and can use Space while the gateway is focused, or the practiced app recovery
method. Do not release if hover, telemetry, focus, or pilot readiness is in
doubt. The pilot lands normally with the app after the trial; close the gateway
to save the trace.

Review using the command above. Require `pass: true` and
`scope: SINGLE_AXIS_WIRE_AND_LOCAL_GATES_ONLY`. The reviewer checks the signed
single-axis wire command, OS byte counts, one pilot release, external lease,
bounded nonzero interval, final neutral, and no abort or land request. It
**cannot** prove motion direction, displacement, normal landing, or app
takeover. Record the pilot's actual observed direction, neutral response,
landing, and any intervention separately. If the direction was not left, stop
and correct the sign mapping before another flight command. Do not advance to
hand tracking based on a software pass alone.
