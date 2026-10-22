# Grounded real-SDK bench

This is the next, **propellers removed and aircraft grounded** stage. The
20 October bridge trace records `metadata.fake: true` and `completion:
shutdown`. The Windows abort gateway still constructs `FakeTransport` only.
Neither trace commissions the ROS bridge for real SDK movement.

The separate `grounded_sdk_bench.py` is the sole Windows SDK command owner for
this run. It requires three explicit CLI flags before opening the SDK socket.
Its SDK peer is fixed to `192.168.10.1:8889`, and the Windows bind address
must be on the Tello Wi-Fi subnet. Its final wire allowlist is `command`,
`battery?`, `height?`, and literal
`rc 0 0 0 0`. It has no takeoff, land, motor, or nonzero RC path. It rejects
all enabled, tracking, land, nonzero, malformed, replayed, future, or stale
decision packets. Loss of input still emits only neutral RC at approximately
20 Hz. SDK entry and read-only queries must receive plausible replies before
the neutral loop starts; a periodic battery query checks for reply loss without
pausing neutral output. A missing or invalid reply ends the run. On normal
shutdown or a fault, it attempts one final neutral send and closes its socket.

The grounded-height check accepts the SDK guide's numeric centimetre response,
an explicit `cm` suffix, and the `dm` suffix observed from the test aircraft.
It converts decimetres to centimetres before applying the 0–20 cm grounded
bound; all other forms are rejected.

## Operator run on Windows

1. Physically remove all propellers and keep the aircraft on the ground.
   Inspect battery and the area. Close the Tello app, DJITelloPy, the camera
   diagnostic, and every other SDK client on Windows and WSL. Use one Windows
   PowerShell process for this bench. A local owner lock excludes cooperative
   processes on this host; it cannot exclude another computer or phone.
2. Connect Windows to the Tello Wi-Fi. Discover the Windows Wi-Fi IPv4 address
   with `ipconfig`; use it for `--bind`. Confirm the intended checkout/revision
   and run offline checks first. Do not start the ROS pipeline for this test.
3. In the existing Windows Python environment, run the following with a **new**
   trace filename. The `--hardware`, `--propellers-removed`, and
   `--aircraft-grounded` flags are deliberate operator assertions.

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$env:PYTHONPATH = "$repo\src\tello_bridge"
$trace = "$env:TEMP\tello-grounded-sdk-$(Get-Date -Format yyyyMMddHHmmss).json"
& $python "$repo\tools\tello_diagnostics\grounded_sdk_bench.py" --hardware --propellers-removed --aircraft-grounded --bind WINDOWS_WIFI_IPV4 --duration 10 --trace $trace
& $python "$repo\tools\tello_diagnostics\review_grounded_sdk_bench.py" $trace
```

The bench starts with no decision sender. That intentionally exercises lost
input: the trace must contain `STALE_OR_LOST_INPUT` and only zero RC. An
optional separate input packet trial can be performed later; it is not needed
to prove that the grounded real wire is neutral with no upstream producer.

Stop immediately on an unexpected sound, motion, command, reply, or status.
The neutral-output phase is bounded to 30 seconds; SDK entry and queries can
add up to six seconds before it. The process never retries a failed physical
session automatically. If interrupted, inspect the trace and aircraft before
another run. A nonzero exit or audit failure is a failed bench; retain the
trace. Avoid disconnecting Wi-Fi deliberately for this physical run: the
offline reply-loss test covers that failure path without risking loss of the
only link.

## Evidence and limits

The trace distinguishes each `attempt` and `os_send_result` from each
`UNCORRELATED_SDK_REPLY`. The Tello SDK provides no sequence ID for these
replies, so a reply observed after a query is only a candidate response; the
report labels its correlation `UNVERIFIED`. UDP send success means the OS
accepted a datagram. Neither send nor reply proves physical motion, stopping,
or landing. The trace always records `physical_outcome: UNKNOWN`.

Pass this stage only when the reviewer reports `pass: true`, all observed wire
commands are on the allowlist, the final command is neutral, stale/lost input
was seen, and shutdown completed. Record the aircraft's grounded condition
separately. This stage does not enable a flight-capable gateway, verify
airborne axis signs, or establish a manual takeover path.
