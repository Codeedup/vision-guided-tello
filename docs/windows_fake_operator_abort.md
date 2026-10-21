# Windows Space abort: first FAKE stage

This stage opens **no aircraft socket** and sends **no aircraft command**. The
Windows window owns the local Space key, FAKE transport, and abort latch. The
existing ROS bridge remains FAKE-only. The Windows gateway has no hardware
selector. Do not run the Tello diagnostic and a future real gateway together.

## Local FAKE exercise

Build and test in WSL first with `tools/run_offline_checks.sh`. In Windows
PowerShell, use the existing Windows Python environment and this shared checkout:

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$env:PYTHONPATH = "$repo\src\tello_bridge"
& $python -m tello_bridge.windows_gateway --bind 127.0.0.1 --port 5010 --trace "$env:TEMP\tello-fake-abort.json"
```

In a second Windows PowerShell terminal, send synthetic decisions:

```powershell
& $python "$repo\tools\tello_diagnostics\exercise_fake_gateway.py" --receiver 127.0.0.1 --port 5010
```

The sender keeps publishing disabled decisions while you click **Prepare FAKE
hover** and **Arm FAKE session**. It detects that local FAKE arm and begins
tracking decisions immediately, before the 1.5-second input-loss watchdog.
Keep the gateway window focused. Wait for `FAKE ARMED` and the sender's
`sending tracking decisions` message, then press **Space** while tracking is
still active. The window must immediately show `ABORT LATCHED` and
`landing requested/unconfirmed`. Close it to save the trace. Inspect the
transport events: a non-neutral `rc` before Space, then `rc 0 0 0 0`, then
`land`, with no later non-neutral `rc`. Repeat with the sender stopped, and
repeat while its tracking packets continue. The gateway must remain aborted.
Closing the window or pressing Ctrl+C saves the trace without a traceback.

Space is listened for **only in the focused gateway window**; it is not a
system-wide hotkey. Losing window focus while armed also aborts. Closing the
window while armed requests neutral and land. The local handler does not call
ROS, wait for a UDP reply, or invoke Tello `emergency`. Repeated Space presses
do not renew the three-attempt land budget. The status and log never claim a
physical landing. **Explicit FAKE reset** clears the simulation latch and
requires a new disabled decision before another FAKE arm. It is not a real
grounded-lifecycle reset.

## Optional WSL ROS forwarding

After rebuilding and sourcing `/opt/ros/jazzy/setup.bash` and
`install/local_setup.bash`, run:

```bash
ros2 run tello_bridge tello_approved_forwarder --namespace /webcam_test --windows-ip WINDOWS_IPV4 --port 5010
```

Start the Windows gateway with `--bind WINDOWS_IPV4 --port 5010`. Discover and
enter the reachable Windows address explicitly. Check Windows and WSL clock
bounds with the existing `check_clocks.py` procedure before treating forwarded
freshness as meaningful. The forwarder subscribes to
`/webcam_test/approved_command` at depth one. It sends version-1 UTF-8 JSON
datagrams with exact keys `version`, `decision_ns`, `source_ns`, `enabled`,
`tracking`, `land`, `lateral`, and `vertical`. Both stamps are original Unix
nanoseconds from the ROS message. It cannot arm, reset, or create SDK authority.
The gateway independently checks schema, types, freshness strictly below
250 ms, ordering, source association, and the local arm/lifecycle state.
UDP/source IP is **not authentication**; use only a controlled local network for
this FAKE exercise. There is no remote enable endpoint.

## Limits before any aircraft test

The gateway and sender above are FAKE-only. Windows GUI focus behavior and
actual key-to-dispatch timing require measurement on the user's Windows setup.
The phone app takeover, physical landing, independent control after laptop or
Wi-Fi failure, and real grounded-lifecycle evidence remain unverified. A local
owner lock cannot exclude a phone or another computer. The next aircraft
stage is a supervised, grounded, propeller-off, narrowly allowlisted command
path bench, after a separate real transport design and review.
