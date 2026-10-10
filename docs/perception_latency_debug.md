# Webcam source freshness diagnostics — 10 October 2026

## Current evidence

User-supplied live webcam trials at `41e0dcd0eae643460f1d3704948305bb1b2657ad`
completed three ten-second paced FAKE operator sessions. Each correlated all 125
client heartbeats to accepted bridge callbacks. Maximum actual server renewal
gaps were 81.443 ms (07), 82.289 ms (08), and 81.652 ms (09), below the strict
300 ms lease limit. No landing fault or operator expiry was recorded. The supplied
final statuses showed disarmed, upstream disabled, zero RC output and no landing
attempts. All bridge reports finished with a shutdown event and valid JSON.

Runs 07 and 08 retained bridge TRACKING after acquisition until takeover. Run 09
had five isolated `NEUTRAL_INPUT_EXPIRED_OR_INVALID` policy ticks after acquisition;
each resumed TRACKING on the next approximately 20 ms tick. The recent approved
callbacks were accepted tracking commands. Their source timestamps indicate the
source-age cutoff was crossed before immediate send-time revalidation. The maximum
source age at the supervisor decision during run 09 was 242.664 ms, below 250 ms
but with limited remaining margin. Supervisor flags alone do not establish that
the independent bridge is still allowed to send movement.

The [machine-readable summary](evidence/live_paced_runs_2026-10-10.json) records
source-report SHA-256 hashes, omissions and correlated counts. Server rings omitted
10,184 / 123 / 1,823 earlier events respectively, but each retains the complete
enabled interval; client and monitor records were not omitted. These are three
lease/cleanup passes, not three uninterrupted end-to-end tracking passes. They do
not establish the root cause of the original run 05 delayed response.

## Sender timing

`hand_preview.py --timing-trace PATH` enables bounded in-memory per-frame timing.
It reserves a new report exclusively, refuses overwrite before opening the camera,
and writes JSON after Q, ordinary failure or Ctrl+C. Forceful termination can leave
an empty file. The default retains the latest 10,000 completed frames and reports
omissions; percentile summaries cover retained records only. No images or landmarks
are stored. The normal sender is untraced unless the option is supplied.

Each record preserves the packet's `capture_time_ns`, frame identity, dimensions,
eligibility flags and Windows monotonic marks. Elapsed stages include camera read,
frame packing/copy, image conversion, inference, annotation, open-hand mapping,
JSON encoding, UDP send, console output and preview display. Capture-to-send,
receipt/send gaps, loop time and gaps between completed frames are also recorded.
Stages include descheduling as well as work; a long inference elapsed time does
not prove MediaPipe CPU work alone is responsible.

Capture remains stamped immediately after `camera.read()`, before frame packaging
and perception. It is software read completion, not exposure. This diagnostic
cannot measure image age inside the camera/driver before read completion. Windows
and WSL monotonic clocks have different origins. Correlate the unchanged source
stamp to ROS monitor messages, after verifying Windows/WSL wall-clock agreement.
Subtract sender capture-to-send duration from monitor source age to estimate the
remaining send-to-observation delay; that residual includes network/receiver/DDS/
monitor scheduling and clock uncertainty, not UDP transport alone.

Tracing records a frame after preview completes, preserving the original send and
display order. The `between_frames` interval includes recorder work and scheduling.
An unfinished frame on error is not in the records; the report's completion marks
that the run failed. Trace overhead can itself affect timing.

## Run 10: same paced session with sender timing

No aircraft is connected. Stop the previous sender and pipeline before updating.
In WSL, source ROS, `git pull --ff-only`, build `tello_bridge`, and source the
workspace. Preserve local changes; do not proceed after a pull/build failure.
Keep camera, hand pose/distance, background, lighting and host load consistent.
Do not resize frames, change model settings, or widen freshness/lease limits.

WSL terminal 1:

```bash
TELLO_BIND=0.0.0.0 TELLO_NAMESPACE=webcam_test tools/start_webcam_pipeline.sh \
  --bridge-trace "$HOME/bridge-trace-10.json" --trace-label perception-timing-10
```

WSL terminal 2, after sourcing the same workspace:

```bash
ros2 run tello_bridge tello_operator prepare-fake --namespace /webcam_test
ros2 run tello_bridge tello_operator status --namespace /webcam_test
```

Require FAKE, unarmed, upstream disabled, no landing latch and HOVER_CONFIRMED.
Windows PowerShell:

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$wslIp = ((wsl -d Ubuntu hostname -I).Trim() -split '\s+')[0]
$revision = (wsl -d Ubuntu git -C /home/dylan/vision_guided_tello_ws rev-parse HEAD).Trim()
$timingPath = "$env:USERPROFILE\webcam-timing-10.json"
& $python "$repo\tools\webcam_perception\hand_preview.py" --receiver $wslIp --port 5005 `
  --timing-trace $timingPath --trace-label perception-timing-10 --revision $revision
```

WSL terminal 3, source ROS and the workspace first:

```bash
python3 tools/webcam_perception/measure_pipeline.py --namespace /webcam_test \
  --duration 60 --source live_webcam --run-kind fault --label perception-timing-10 \
  --output "$HOME/webcam-lease-diagnostic-10.json"
```

Once all topics are observed, paste the following into terminal 2, then use the
five-second delay to position one eligible open hand. Keep it visible until the
bounded enable completes; green landmarks alone do not establish eligibility.

```bash
sleep 5
TELLO_REVISION="$(git rev-parse HEAD)" ros2 run tello_bridge tello_operator enable \
  --namespace /webcam_test --duration 10 --trace "$HOME/operator-trace-10.json"
ros2 run tello_bridge tello_operator takeover --namespace /webcam_test
ros2 run tello_bridge tello_operator status --namespace /webcam_test
```

Check disarmed, upstream disabled and zero RC. Let the monitor finish, Q the
Windows preview to flush sender timing, then Ctrl+C terminal 1 once and wait for
the prompt to flush bridge timing (the local SIGTERM fallback is verified).
Preserve the three WSL JSON reports, Windows `webcam-timing-10.json`, operator
output and final status. The Windows file is in the directory shown by
`$env:USERPROFILE`; use new filenames if any report already exists.

## Decisions after review

Use source stamps to align stages around each bridge neutral tick. Large sender
inference/copy/display elapsed times localize the measured interval; large residual
send-to-ROS delays require receiver/DDS scheduling evidence. Long read times can
explain cadence but cannot reveal the exposure age of buffered camera images.
Choose a targeted change only after measurement; do not assume lighting or memory
pressure caused the delay. Controlled FAKE hand-loss and operator-loss trials
remain pending. Real-aircraft network/video/lifecycle commissioning is a later gate.

The added tests use injected camera, OpenCV, model and MediaPipe interfaces to
exercise the real sender loop without hardware. They check identical traced and
untraced UDP observations, source stamps, stage attribution, bounded retention,
nonoverwrite and completion on quit/error/interruption. Actual Windows webcam
stage performance must be measured in the user run.

## Run 10 result and run 11: heartbeat submission timing

Run 10 at `0d5011f42cd18241edb967e185f5be8c750ed768` failed with
`OPERATOR_LEASE_EXPIRED`. The supplied clock check bounded WSL minus Windows
between -0.150 and +0.051 ms. All four reports are readable; sender completion
was Q/quit with all 1,261 completed frames retained and no omissions. The bridge
retained the complete enabled interval despite 37 omitted earlier events. See
[evidence with source-report hashes](evidence/live_run10_2026-10-10.json).

The first heartbeat renewed authority. The second client attempt began 80.515 ms
later, but its bridge callback began 294.452 ms after that attempt. The callback
itself took 0.105 ms; the client observed its rejection another 0.588 ms later.
A policy tick had already latched landing at lease age 319.471 ms. Between the two
heartbeat callbacks, bridge ticks continued with a maximum gap of 20.255 ms and
maximum duration of 0.335 ms. This localizes the observed delay before heartbeat
callback entry; it does not establish whether the client was blocked inside
`call_async()`, the request was delayed in DDS, or service dispatch was delayed.
The old client `send_ns` marker precedes `call_async()` and is not proof of delivery.

Matched eligible camera frames around the failure spent approximately 95–100 ms
in inference and 97–102 ms from software capture receipt to UDP send. Across the
whole sender run, capture-to-send p95 was 93.513 ms and max 128.492 ms; send gap
max was 160.156 ms. These reveal limited source freshness margin but do not
explain the independent operator lease failure. This short enable failed before
supervisor tracking acquisition; later supervisor TRACK flags cannot establish
an armed bridge tracking pass. Final takeover status showed disarmed, upstream
disabled, zero RC and a retained landing latch.

The operator trace now preserves `send_ns` as the attempt marker and adds:

- `submission_returned_ns`: immediately after `call_async()` returns its future.
- `submission_duration_ns`: elapsed time inside that call, including scheduling.
- `response_wait_ns`: submission return to client response observation, including
  spinning, scheduling, response processing and result extraction.

The two duration fields sum to the existing `observed_delay_ns`. Compare the
bridge callback entry against both client marks using the same WSL monotonic
clock. The server can process a request before `call_async()` returns, so do not
interpret a negative callback-entry-minus-submission-return interval as clock
failure. The service's existing timeout begins after submission and spin calls
can return late; this diagnostic does not make it a strict wall-clock bound.
Requests, pacing, authority rules and cleanup are unchanged. No automatic retry
or rearm is added. A failed/interrupted submission still propagates through the
existing cleanup; it cannot supply a completed service-call record.

Run 11 repeats the four-terminal run 10 recipe above with new filenames and
`heartbeat-submission-11` as the trace/monitor label. Stop the old processes,
pull and rebuild the changed ROS package first:

```bash
cd ~/vision_guided_tello_ws
git pull --ff-only
source /opt/ros/jazzy/setup.bash
colcon build --packages-select tello_bridge
source install/local_setup.bash
```

Use `bridge-trace-11.json`, `operator-trace-11.json`,
`webcam-lease-diagnostic-11.json` and Windows `webcam-timing-11.json`. Run the clock
check again before starting the Windows preview. Keep the one eligible hand
visible through the bounded enable and use the default paced client. Finish with
explicit takeover/status, Q the preview and Ctrl+C the pipeline once, waiting
for its prompt. Retain all four reports and console status even if the session
fails. This is a FAKE measurement run, not a cleared real-aircraft test.
