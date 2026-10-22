# Staged commissioning runbook

Updated 22 October 2026. Run **one stage at a time**. These are prepared user
commands, not automatic aircraft authorization. The enabled FAKE target-loss
repeat has been reviewed; the next aircraft stage is the grounded SDK bench.
[Readiness blockers and source defaults](readiness_2026-10-13.md) remain binding.

## Before the next test — nodes stopped

**WSL terminal C:** confirm the tested checkout, build, create a unique evidence
folder and rediscover the reachable IP. Do not reuse an old WSL address.

```bash
cd ~/vision_guided_tello_ws
git status --short
git rev-parse HEAD

tools/run_offline_checks.sh && {
    source /opt/ros/jazzy/setup.bash
    source install/local_setup.bash
    hostname -I
    run="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
    evidence="$PWD/artifacts/$run"
    mkdir -m 700 "$evidence"
    export TELLO_REVISION="$(git rev-parse HEAD)"
    printf 'Run ID: %s\nEvidence: %s\nRevision: %s\n' \
        "$run" "$evidence" "$TELLO_REVISION"
}
```

> Continue only if the build and tests succeed. The guarded setup block does not run after failure. In every other WSL terminal, source ROS and this workspace’s rebuilt installation before launching nodes. Do not change the checkout during a recorded trial.

Require a clean tracked tree and passing CI for that revision. Copy the printed
run ID and revision into the other terminals. In every WSL terminal source ROS
and `install/local_setup.bash`; export the same `TELLO_REVISION`. `artifacts/` and
root measurement JSON are ignored, retained locally, and never bulk-added to Git.

**Windows PowerShell terminal B:** use the existing working environment and
shared WSL checkout. Replace `RUN_ID`, `FULL_COMMIT_SHA`, and `CURRENT_WSL_IPV4`
with the values just printed, before starting nodes or any sender.

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$run = 'RUN_ID'
$env:TELLO_REVISION = 'FULL_COMMIT_SHA'
$wslIp = 'CURRENT_WSL_IPV4'
$evidence = "$repo\artifacts\$run"
& $python "$repo\tools\webcam_perception\check_model.py"
& $python "$repo\tools\webcam_perception\check_clocks.py" --label $run --output "$evidence\clock.json"
```

The clock probe targets WSL distribution `Ubuntu`; adapt the distribution name in
its source only if the installed distribution differs, then rebuild/retest the
revision. Do not correct clocks while nodes are running. Require the new bounds
within ±10 ms for this diagnostic protocol; they are evidence for this run only.
A missing/empty report or stale/uncertain bounds stops the test.

## 1. Clean enabled FAKE target loss — immediate next user test

**WIFI: connect the laptop to the Tello network.** For this repeat, the Tello is a
camera/telemetry source only: remove propellers, keep it grounded, and stop the
phone app and every other SDK/video client. The real camera sender below cannot
send takeoff, RC, land or emergency commands. Only run that sender in Windows;
WSL hosts the FAKE ROS pipeline. Connecting Wi-Fi can affect WSL routing, so
rediscover the IP and redo the clock check after changing networks if necessary.

**WSL terminal A:** set the same ID, then start exactly one pipeline.

```bash
cd ~/vision_guided_tello_ws
run='RUN_ID'
TELLO_BIND=0.0.0.0 tools/start_webcam_pipeline.sh --namespace /webcam_test \
  --bridge-trace "$PWD/artifacts/$run/bridge.json" \
  --provenance-output "$PWD/artifacts/$run/runtime_provenance.json" \
  --trace-label "$run"
```

Wait for `READY`. Stop on duplicate ownership, busy UDP port, missing/duplicate
publishers, non-real clocks, enabled/nonneutral startup output or startup failure.
Do not bypass the readiness check. Keep terminal A running throughout the trial.
Require `runtime_provenance.json` to report `passed: true`; it records the live
ROS child PIDs, installed paths, hashes and package prefixes from this checkout.

**Windows terminal B:** start the bounded headless camera sender (the variables
from setup must still be present).

**WIFI: connect the laptop to the Tello network.**

```powershell
& $python "$repo\tools\tello_diagnostics\bench_hand_preview.py" --hardware --headless --duration 60 --receiver $wslIp --port 5005 --timing-trace "$evidence\sender.json" --trace-label $run
```

Keep a single eligible open palm steadily in view. This sender is the sole SDK
socket owner; do not start `diagnose.py` alongside it. It retains decoded receipt
timestamps and closes the video stream when done. Start the monitor and operator
promptly, leaving enough of the 60-second sender window for the full sequence.

**WSL terminal C:** use the same ID; start the 25-second passive monitor **before**
enabling so it records acquisition and loss.

```bash
run='RUN_ID'
python3 tools/webcam_perception/measure_pipeline.py --namespace /webcam_test \
  --duration 25 --source tello_video --run-kind fault --label "$run" \
  --configuration '{"transport":"fake","sender":"headless_tello_camera","procedure":"track5_absent3_return5"}' \
  --output "$PWD/artifacts/$run/monitor.json"
```

**WSL terminal D:** after the monitor says `All pipeline topics observed`, prepare
FAKE hover and explicitly enable the leased software session.

```bash
run='RUN_ID'
export TELLO_REVISION="$(git rev-parse HEAD)"
ros2 run tello_bridge tello_operator prepare-fake --namespace /webcam_test
ros2 run tello_bridge tello_operator enable --namespace /webcam_test --duration 30 \
  --trace "$PWD/artifacts/$run/operator.json" --trace-label "$run"
```

Perform just this sequence, recording action times separately:

1. Hold the eligible palm steady for at least 5 seconds of actual TRACK. The
   `Software session enabled` marker alone does not establish tracking.
2. Remove it completely for at least 3 seconds. Expect neutral immediately after
   validity/freshness loss, then a supervisor landing request near 1.5 seconds
   after observed loss. The operator should exit with `Landing intent latched`;
   a lease fault or session deadline is a failed target-loss trial.
3. Return a clearly eligible palm for at least 5 seconds. Expect the landing
   latch and neutral commands to persist. Do not reset or enable again.

Allow the monitor to finish and the sender to reach its bounded finish. Then
Ctrl+C in terminal A finalizes the bridge shutdown trace. Do not invoke
`prepare-fake` again until this evidence has been analyzed. A short TRACK or
eligibility/freshness flicker means repeat after investigation, never relax gates.

**WSL terminal C:** analyze only after all files have finalized.

```bash
python3 tools/analyze_target_loss.py \
  --monitor "$PWD/artifacts/$run/monitor.json" \
  --bridge "$PWD/artifacts/$run/bridge.json" \
  --operator "$PWD/artifacts/$run/operator.json" \
  --sender "$PWD/artifacts/$run/sender.json" \
  --clock "$PWD/artifacts/$run/clock.json" \
  --output "$PWD/artifacts/$run/analysis.json"
```

Expected: exit 0, `passed: true`, a completed TRACK interval ≥5 s, loss-to-land
near 1.5 s, eligible hand return, no restart, complete matching run IDs/revisions,
no truncation and no numerical/safety violation. Stop on any analyzer failure;
retain all logs and action times. The analyzer is a conservative subscriber audit,
not physical flight evidence. Confirm no leftover pipeline processes and UDP 5005
is free before another trial; a failed cleanup is itself a stop condition.

A laptop webcam FAKE repeat can additionally isolate SDK/video timing using
`hand_preview.py --receiver ... --timing-trace ... --trace-label ... --revision ...`.
It has a GUI and is a different source; do not label it a headless Tello repeat.

## 2. Camera-only timing and perception envelope

**WIFI: connect the laptop to the Tello network.** Keep propellers removed and
only one SDK owner. Repeat the same Windows sender with ROS autonomy disabled;
use new IDs/reports. Compare preview (omit `--headless`) and headless under matched
lighting, distances around 0.7/1.0/1.5 m, CPU/load and hand motion. Preserve inference
percentiles, eligible/ineligible counts, source age, inter-observation gaps,
controller invalidations and neutral approved output. Deliberately remove the
hand and stop decoded frames on the ground; watchdogs must still act during silence.

For an available, consented local recording ≤256 MiB, use sequential replay with
the same input and frame count. This never opens SDK, video UDP or observation UDP.
Do not commit recordings or substitute synthetic frames for representative palms.

**Windows terminal B**, after stopping the live diagnostic:

```powershell
& $python "$repo\tools\tello_diagnostics\replay_hand_preview.py" --input C:\path\bounded-palm-recording.mkv --output "$evidence\replay-headless.json" --label $run --max-frames 300 --max-seconds 60
& $python "$repo\tools\tello_diagnostics\replay_hand_preview.py" --input C:\path\bounded-palm-recording.mkv --output "$evidence\replay-preview.json" --label $run --max-frames 300 --max-seconds 60 --preview
```

Compare identical input/model hashes and frame sequences. Repeat in reversed order
with new output filenames, after warmup and with host-load notes. A time-limited
run with different processed frames does not isolate GUI overhead. Existing JSON
contains timings, not replayable pixels; it cannot be converted back into video.
An appropriate recording still has to be supplied/captured by the user.

## 3. Grounded real-SDK command bench

Use the separate [grounded SDK bench procedure](grounded_sdk_bench.md). Its
Windows process is the single real SDK owner and has a literal four-command
allowlist: SDK entry, battery and height queries, and zero RC. The ROS bridge
and the Space-abort gateway remain FAKE-only. Review the saved trace with the
included auditor before interpreting any SDK result.

**WIFI: connect the laptop to the Tello network.** User-supervised, grounded,
propellers removed. Current code has **no commissioned flight-capable ROS mode**.
Do not invent an enable/takeoff command or switch the launcher to hardware.
The first grounded bench does not enable a movement gateway. Resolve
telemetry provenance/lifecycle evidence and an independent operator abort path
before any later movement-capable stage.

This first bench proves mode isolation, local socket ownership, the harmless
wire allowlist, neutral output on lost input, reply-loss detection, and normal
shutdown on the ground. It checks one battery and height query before neutral
output, then checks periodic battery replies. Bounded movement mapping and the
full telemetry lifecycle still require a separate guarded command-path test.
Separate attempted command, OS send, SDK reply, and observed physical outcome.
Bench wire behaviour cannot establish airborne signs or confirmed landing.
No current height or SDK `ok` alone can satisfy real hover/landed confirmation.

## 4–6. Manual baseline, isolated axes, then closed loop — user-supervised only

**WIFI: connect the laptop to the Tello network.** Each physical session requires
fresh battery/aircraft inspection, guards fitted, clear space, textured floor,
appropriate lighting, line of sight and separation from propellers. Establish a
current controlled manual hover and normal land with telemetry/video evidence.

After bench gates and deliberate flight authorization, test conservative lateral
movement alone, then vertical alone. Prove actual sign, neutral response and the
available operator abort before tracking a hand. Then progress lateral tracking,
vertical tracking and combined small motion. Manually maintain range; there is no
autonomous distance hold, forward/backward command or yaw command.

Define each counted trial before measuring: e.g. a proposed 10-second eligible
tracking window, fixed bounded hand movement, configured dead-zone centering,
no sustained oscillation or safety fault, and a successful operator normal land.
Agree the envelope and stop criteria with the user before counting 8/10 trials.
Measure ≥15 processed FPS and ≥10 Hz useful control as targets, not guarantees.
Record code/configuration, lighting/distance, latency, successes and failures.

Takeover revokes software authority; it does not prove physical manual control.
Controlled land is intent until confirmed by trustworthy evidence. Emergency
motor cut is a distinct last-resort physical procedure, never an ordinary stop.
A same-Wi-Fi override is not an independent link. Host/process/link failure can
remove all software commands, so identify the tested remaining recovery path.
Never deliberately kill the only command link in flight to recreate a simulated
fault; plan and prove ground fault injection and recovery first. No automatic
flight is authorized or performed by this runbook.
