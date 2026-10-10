# Your next tests

Start with the laptop baseline below. All command consumers remain fake. The
aircraft, flight controls and webcam have not been opened during this offline
implementation. Setup/terminal commands are in [Windows/WSL setup](windows_wsl_setup.md).

1. **Laptop baseline.** Start WSL terminal A and Windows terminal B as documented,
   check Windows/WSL clock bounds, then run the read-only graph diagnostic and
   60-second baseline monitor in terminal C. Keep autonomy disabled initially.
   Confirm one publisher per topic, `use_sim_time=false`, no missing topics,
   neutral disabled commands, finite signed source ages and no unexplained clock
   warning. Save `webcam-baseline-01.json` and the summary. Report lighting,
   background, hand distance and CPU/load. Rates describe observed deliveries;
   they are not measured camera FPS. This is the immediate next participation point.
2. **Laptop fault trials.** Use `prepare-fake` and the leased `enable` terminal.
   Start a separate `--run-kind fault --label TRIAL_NAME` monitor report for each
   trial. Repeat each at least three times. Check hand right/left/up/down and
   centre; no hand/fist/two hands; brief loss/recovery requiring five new stamps;
   prolonged loss; stop/restart Windows sender; stop controller; stop/suspend
   supervisor; stop operator heartbeat; ordinary disable versus takeover; and
   explicit fake lifecycle reset/re-enable. Mark the deliberate action with an
   independently recorded time/video if exact fault onset matters. A last-input
   anchor in the monitor is not fault onset. Preserve flags, stamps and values.
   Expect zero output without tracking, latched supervisor landing after
   prolonged loss, consumer landing intent surviving supervisor takeover, and
   disabled/unknown state after restart. A failed safety invariant ends the trial.
3. **Propeller-off network/SDK/video.** Only after reviewing laptop reports, choose
   the command owner and host routing. Physically remove propellers, prepare the
   aircraft and battery, connect its Wi-Fi deliberately, and run the explicit
   non-actuating diagnostic on the chosen host. First run the fake diagnostic:

   ```bash
   python3 tools/tello_diagnostics/diagnose.py --duration 2 --video
   ```

   The later physical command is provided for deliberate user execution, and was
   **not run** during this assignment:

   ```bash
   python3 tools/tello_diagnostics/diagnose.py --hardware --duration 10 --video
   ```

   SDK command, state and video use separate UDP paths. Record local/remote
   addresses, interfaces, ports, replies, battery/height validity, telemetry age,
   new decoded frames and stream gaps. Missing decoder dependency must be fixed
   in a separate selected runtime. The diagnostic cannot take off, land, send RC
   or cut motors; even cleanup can only disable the video stream and close sockets.
   SDK configuration is still a real aircraft interaction. Do not run another
   command client concurrently. Record supported replies rather than assuming
   standard Tello supports every newer EDU feature.
4. **Propeller-off operator/lifecycle review.** Independently establish actual
   flight-state evidence, arming/restart rules, normal land and physical manual
   control. Review command signs with physical actuation still disabled. A ROS
   takeover response revokes software authority only; it does not prove a phone,
   controller or aircraft has accepted manual control. Real bridge actuation is
   deliberately uncommissioned until this interface and deployment are reviewed.
5. **Used-battery trials and manual hover.** Check each of the three batteries
   separately, recording voltage/percent, condition, dropouts, load behaviour and
   duration under an agreed controlled procedure. Earlier user-reported hover
   does not establish battery capacity, SDK control or this stack's reliability.
6. **Later bounded autonomous trials.** Requires a separate explicit go/no-go
   review after the evidence above. No takeoff or propeller-on execution was
   authorized or performed by this preparation. Review the checklist below first.

For every trial use a new report filename; the monitor refuses overwrite. Stop
on nonneutral output while tracking is false, autonomy without explicit enable,
stale/future/replayed data authorizing motion, source stamps renewed by heartbeat,
unexplained clock jumps, multiple publishers or an unexpected landing/reset.
For a fake laptop trial, take over both layers, stop the sender and preserve logs.
For later physical testing, use the pretested physical manual/normal land path;
do not rely on sending ROS services after a host/link failure.

Before any later propeller-on test, explicitly review:

- Aircraft, propellers, guards and each battery's condition.
- Clear area, separation from people/animals, lighting and suitable floor.
- Proven control link, mode, takeoff/arming behaviour and failure response.
- Operating area/altitude limits and how each will be observed/enforced.
- Tested physical manual override and normal landing method.
- Emergency motor-cut as a distinct last-resort action; never automatic on
  ordinary target loss, timeout or shutdown.
- Weather where relevant, current official aviation rules for the actual location,
  emergency response, and the exact software/configuration commit.

Copy this record for each run:

```text
Trial ID / date and timezone:
Commit / branch / git status:
Synthetic, webcam, recorded or aircraft source:
Windows / WSL Python, ROS and dependency versions:
Namespace / addresses / ports / graph diagnostic:
Clock bounds / how measured / nodes stopped for any correction:
Configuration / lighting / distance / load / battery ID if applicable:
Requested duration / observed duration / baseline or fault:
Deliberate action and independently measured onset, or unavailable:
Observed neutral/tracking/land/takeover transitions:
Source/decision ages, message/source rates, receipt gaps, terminal silence:
Flags and command values / warnings / missing or truncated evidence:
Expected result / observed result / pass, fail or not established:
Logs/report paths / stop reason / next review:
```
