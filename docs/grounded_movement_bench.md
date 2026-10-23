# Stage D: grounded movement gateway bench

This procedure exercises the Windows real-SDK gateway with the aircraft
grounded and all propellers removed. It checks four single-axis wire mappings,
neutral packets, the focused Space abort dispatch, bounded session expiry, and
trace integrity. It does not establish airborne direction, braking, landing,
or manual recovery.

## What the previous run showed

Run `20261022T2154Z-grounded-movement-stageD` recorded the four capped pulses
and Space abort, but it was not a complete pass. The grounded mode never arms,
so the 60-second limit in the flight policy did not apply. After Space, the
gateway sent 20 Hz neutral RC for about 50 seconds and retried `land` three
times. Its old reviewer still passed because it checked the pulse pattern but
did not require the abort, a bounded session, or a single land dispatch.

Grounded-bench mode now has a 60-second wall-clock session bound. Space sends
neutral followed by one normal `land` attempt. The loop then only polls replies;
it does not stream neutral or retry land. Closing the window sends a final
neutral and closes the SDK socket.

The three `error` replies in the previous trace cannot be assigned conclusively
to the three `land` packets. Tello SDK 1.3 uses the same unsequenced UDP reply
channel for control and RC commands; both can return `ok` or `error`. The
reviewer reports such replies as uncorrelated warnings and always leaves
landing delivery unconfirmed. See the [Tello SDK 1.3 command and response
format](https://dl-cdn.ryzerobotics.com/downloads/tello/20180910/Tello%20SDK%20Documentation%20EN_1.3.pdf).

## Preconditions

- Pilot is present; aircraft is inspected, powered on, and physically grounded.
- Remove all four propellers with the supplied tool. Keep hands clear of motors.
- Connect Windows to the Tello Wi-Fi and verify its IPv4 address is on
  `192.168.10.0/24`.
- Close the Tello app and all other SDK clients on Windows, WSL, phones, and
  other computers. Confirm UDP 8889 has no owner on Windows.
- Checkout is clean, local offline checks pass, and hosted CI is green on the
  exact tested revision. Do not run ROS, the FAKE gateway, or a second SDK tool.
- Keep a new trace filename. Stop for any unexpected behavior or OS send error.

## Run on Windows

In one Windows PowerShell window, use the existing Python environment. Replace
`192.168.10.2` with the IPv4 address reported for the Tello Wi-Fi adapter.

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
$env:PYTHONPATH = "$repo\src\tello_bridge"
$ip = '192.168.10.2'
$trace = Join-Path $env:TEMP ("tello-grounded-movement-" + [guid]::NewGuid().ToString('N') + '.json')
& $python -m tello_bridge.windows_gateway --mode grounded-bench --hardware --tello-bind $ip --pilot-present --propellers-removed --aircraft-grounded --bind $ip --port 5011 --trace $trace
```

The window shows a 60-second limit. Complete the sequence promptly:

1. Click each button once: lateral-positive, lateral-negative,
   vertical-positive, vertical-negative. Each button sends one `+/-5` pulse
   on one axis, immediately followed by neutral.
2. Press Space while the gateway window is focused. Confirm the abort status is
   visible. This dispatches neutral followed by one `land` attempt.
3. Close the window normally. Shutdown sends one final neutral and closes the
   socket. If the deadline expires first, the window aborts and closes itself;
   that trace fails the complete procedure because all four mappings were not
   necessarily exercised.

## Review

After the gateway exits, run:

```powershell
& $python "$repo\tools\tello_diagnostics\review_grounded_movement_bench.py" $trace
```

Require `pass: true`, all four pulse strings, no omitted events, one Space
abort, exactly one `land` packet, a neutral packet before it and a final neutral
after it, successful OS byte counts, and a session shorter than 60 seconds.
The report's scope is `WIRE_MAPPING_AND_LOCAL_ABORT_ONLY`; its
`landing_delivery` is always `UNCONFIRMED`. Uncorrelated SDK `error` replies
are warnings, not proof that the aircraft rejected `land`; OS acceptance is not
proof of aircraft execution. Record the pilot's physical observation separately.

Keep propellers removed after this procedure. No takeoff, flight, motion-sign
claim, or propellers-on test is authorized by a Stage D pass. The pilot
contingency gate is recorded as complete per the user's 23 October decision:
app takeover is practiced, and the pilot accepts the built-in onboard no-command
landing fallback without a separate intervention method for hypothetical
fallback failure. This is pilot risk acceptance, not proof that the fallback
cannot fail.
