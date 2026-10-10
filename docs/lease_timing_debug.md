# FAKE operator lease diagnostics — 10 October 2026

The current laptop blocker is an unlocalized heartbeat delay. The supplied run
05 handoff records eligible input and supervisor TRACK, then bridge lease expiry:
66.7 ms maximum client send gap and a 335.5 ms observed rejected-response delay.
These are reported handoff measurements, not new measurements from this change.
The raw run 05 JSON and standalone client wrapper are outside this repository.

Default operator sends are now explicitly paced at 80 ms, with one request in
flight and no catch-up bursts. The previous spin timeout was not an enforced
interval. Pacing corrects the burst behavior; it is not proof that the original
335 ms stall is resolved. The operator lease remains strictly below 300 ms and
decision/source/receipt freshness remains strictly below 250 ms. Real ROS
actuation remains gated. No automatic rearm or landing-latch reset was added.

## Evidence provided

`--bridge-trace PATH` on the webcam launcher enables FAKE-only bounded server
events. The normal launch remains untraced. The bridge records heartbeat callback
entry/exit, actual authority renewal timestamps and lease ages, policy tick gaps,
approved callback flags/stamps/results, status callback durations, and transport
poll/send durations. It preserves the first landing fault and first lease-expiry
event separately from later mutable status reasons and ring eviction.

The operator's `--trace PATH` records call send/completion-observation times,
success and returned state without registering future completion callbacks.
`--legacy-heartbeat-cadence` reproduces the original cadence for an explicit FAKE
comparison only. Both diagnostic options require a prepared, unarmed, upstream
disabled, unlatched FAKE bridge. Ordinary commands retain their existing behavior.

Reports reserve a new output file exclusively at startup and refuse overwrite.
Events stay in memory and are written at orderly completion/shutdown. A running
report is empty until then. SIGKILL, host failure or a write failure can leave an
empty/incomplete file. Do not treat it as a complete report. No per-event console
or file writes or added executor-ready callbacks are used. Tracing still adds
work and can affect timing; compare equivalent traced trials first.

Default server capacity is 20,000 events. Record omitted counts; do not infer
complete history from a truncated ring. The regression run uses a larger bound.
Client and server monotonic timestamps can be compared within the same WSL boot.
Client send-to-callback entry includes client/transport/server scheduling delay;
callback exit-to-client observation includes response transport/client delay.
The public Trigger service has no request ID: correlate sequential heartbeats
using exactly one operator client, acceptance order and absolute monotonic times.
Timed-out calls and extra clients make matching ambiguous; report that explicitly.
Windows monotonic time has a different origin; pipeline source-age analysis still
uses the existing source/ROS timestamps and clock-check procedure.

## First user action: update while authority is disabled

In WSL terminal 2, use the existing installed code to request takeover and observe
status. Expect `armed=false`, `upstream_disabled=true`, neutral last RC. A remaining
landing latch is expected. Stop the old pipeline with Ctrl+C in terminal 1 and
stop the webcam sender with Q before updating. No aircraft is connected.

```bash
cd ~/vision_guided_tello_ws
source /opt/ros/jazzy/setup.bash
source install/local_setup.bash
ros2 run tello_bridge tello_operator takeover --namespace /webcam_test
ros2 run tello_bridge tello_operator status --namespace /webcam_test
```

After both old processes have stopped:

```bash
git pull --ff-only
git rev-parse HEAD
git status --short
colcon build --packages-select tello_bridge
source install/local_setup.bash
```

Do not reset/clean away local changes. Do not continue after a pull/build failure.
Source `/opt/ros/jazzy/setup.bash` and `install/local_setup.bash` in every new WSL
terminal. Keep lighting, hand pose/distance, background and host load consistent.

## Correlated run 06: original cadence with new timing evidence

Use new names if 06 already exists. Start this baseline before the paced trial;
it preserves evidence before attributing a pass to the cadence change.

WSL terminal 1 — starts the disabled FAKE pipeline and server recording:

```bash
TELLO_BIND=0.0.0.0 TELLO_NAMESPACE=webcam_test tools/start_webcam_pipeline.sh \
  --bridge-trace "$HOME/bridge-trace-06.json" --trace-label lease-diagnostic-06-legacy
```

Windows PowerShell — start the existing verified webcam sender. Recheck WSL IP
with `hostname -I` after restart; replace the example address when needed:

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
& $python "$repo\tools\webcam_perception\hand_preview.py" --receiver 172.23.53.22 --port 5005
```

WSL terminal 3 — passive pipeline monitor:

```bash
python3 tools/webcam_perception/measure_pipeline.py --namespace /webcam_test \
  --duration 60 --source live_webcam --run-kind fault \
  --label lease-diagnostic-06-legacy --output "$HOME/webcam-lease-diagnostic-06.json"
```

Wait for all topics and sustained `detected=true`, `tracked_hands=1` before enable.
Green landmarks alone do not establish eligibility.

WSL terminal 2 — explicitly prepare/reset the synthetic lifecycle, observe status,
then enable exactly one bounded operator session:

```bash
ros2 run tello_bridge tello_operator prepare-fake --namespace /webcam_test
ros2 run tello_bridge tello_operator status --namespace /webcam_test
TELLO_REVISION="$(git rev-parse HEAD)" ros2 run tello_bridge tello_operator enable \
  --namespace /webcam_test --duration 10 --trace "$HOME/operator-trace-06.json" \
  --legacy-heartbeat-cadence
```

Prepared status must show FAKE, unarmed, upstream disabled, no latch and
HOVER_CONFIRMED. This is a synthetic reset, never a real-aircraft procedure.
The final command enables autonomy. Ctrl+C requests takeover. Do not also run the
old standalone tracer or a second enable client.

After the session, explicitly take over and observe status in terminal 2 using
the commands above, even if the session failed. Let the monitor complete. Then
Q the sender and Ctrl+C terminal 1 to flush the server report. Preserve all three
JSON files, operator output and post-takeover status. Takeover may be silent on
success; use status to verify both authority layers.

## Run 07: paced comparison

Restart the same recipe with fresh `bridge-trace-07.json`,
`operator-trace-07.json`, `webcam-lease-diagnostic-07.json` and a `paced` label.
Omit `--legacy-heartbeat-cadence`; all other conditions stay the same. Do not pull
or change clock/configuration between the paired trials.

Acceptance requires the intended ten-second session to complete, eligible input
and supervisor acquisition, bridge authority retained during tracking, actual
accepted server renewal gaps below 300 ms, neutral output without permission,
and both layers disabled after cleanup. A single pass is insufficient: require
three independent paced webcam passes, then complete the separate fault matrix.
Deliberate expiry must still latch, reject late renewal and prevent auto-rearm.

Review the first-expiry event and callbacks around the gap if either trial fails.
Short client gaps with delayed server entry suggest a delivery/scheduling issue;
long server callbacks localize work inside the bridge; prompt server handling
with late client observation suggests delay on the response/client side. A long
inter-tick gap without a matching long callback leaves descheduling or executor
dispatch as hypotheses, not proven causes. Lighting can affect perception and
inference load, but it does not itself establish a heartbeat root cause.

## Regression scope

The added pure tests cover paced ten-second renewals despite immediately ready
executor work, no catch-up, interruption/rejection propagation, trace bounds,
nonoverwrite, FAKE-only guards and first-expiry preservation. The production ROS
CLI test now runs ten seconds, records the client, checks successful paced calls
and cleanup, and checks correlated server receipts after shutdown. Existing
operator-loss, supervisor-suspension and input-loss tests remain registered.
GitHub CI uploads the paired regression reports with its normal test artifacts.
The CI checkout's exact directory is marked safe for Git inside its container;
the previous run's tests passed but its final Git whitespace check failed due to
container checkout ownership. No global wildcard trust is added.

CI and synthetic passes are software evidence only. The original WSL webcam
failure must be tested on the user's laptop. Physical flight remains a later gate.
