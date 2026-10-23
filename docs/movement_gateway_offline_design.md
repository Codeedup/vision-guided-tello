# Windows movement gateway: offline design gate

The Windows gateway is the only proposed real SDK owner. WSL forwards the
existing `ApprovedCommand` packets only; it never opens the Tello command socket.
FAKE remains the default mode.

## Deliberately separate modes

`--mode grounded-bench` is for a propellers-removed, grounded aircraft only. It
requires `--hardware --tello-bind WINDOWS_WIFI_IPV4 --pilot-present
--propellers-removed --aircraft-grounded`. It ignores every WSL decision packet.
The focused local window exposes exactly four one-packet checks: lateral +5/-5
and vertical +5/-5. Each is immediately followed by `rc 0 0 0 0`; mixed axes,
forward/backward and yaw have no interface.

`--mode flight` is a separate process and rejects the grounded/propellers-removed
assertions. It requires `--hardware --tello-bind WINDOWS_WIFI_IPV4
--pilot-present --propellers-installed`. Before active RC it requires a fresh,
strict state-stream battery/height sample, a local pilot hover confirmation, a
fresh disabled decision, a local arm, and then a new enabled decision. Its final
boundary caps lateral and vertical independently at +/-5. It has no takeoff or
emergency command.

The local Space key, focus loss while armed, and normal close use the existing
neutral-first bounded normal-land request. Delivery remains unconfirmed. A
pilot monitors the aircraft during host or Wi-Fi failure and relies first on
the user-reported, tested onboard no-command landing behavior. Space is not an
independent recovery path. On 23 October 2026 the user marked the pilot
contingency gate complete: app takeover has been practiced, and the pilot accepts
the built-in onboard landing fallback without a separate intervention method
for hypothetical fallback failure. This records the pilot's risk decision; it
does not establish a software or hardware guarantee that the fallback cannot
fail.

In flight mode, stale or failed state telemetry and SDK transport errors latch
the session off, attempt neutral RC, then attempt normal land while retaining
the local socket for the bounded retries. A failed neutral send does not suppress
the land attempt. This is only the first layer: a dead Windows host cannot run
this handler, and a lost Wi-Fi link cannot carry its land packet. The pilot
accepts the onboard no-command landing behavior as the fallback for that
separate failure path; this is not a software or hardware guarantee.

The provisional offline flight envelope is +/-5 SDK units independently on
lateral and vertical axes, with a 60-second maximum armed session. These values
are for offline validation only. They are uncalibrated and do not authorize
propellers-on use.

## Grounded-bench evidence

Create a new evidence directory and trace filename for every run. The gateway
writes traces create-only and prints a SHA-256 after it closes. Review the saved
trace before any subsequent stage:

```powershell
& $python -m tello_bridge.windows_gateway --mode grounded-bench --hardware `
  --tello-bind WINDOWS_WIFI_IPV4 --pilot-present --propellers-removed `
  --aircraft-grounded --bind WINDOWS_WIFI_IPV4 --port 5010 --trace $trace
python tools/tello_diagnostics/review_grounded_movement_bench.py $trace
```

The reviewer rejects a non-real/non-bench trace, event omissions, missing or
mismatched decision/command/OS-send/SDK-reply/physical-observation records,
failed or incomplete OS sends, forbidden wire commands, any mixed-axis/non-capped
output, repeated signed pulses, a pulse not immediately followed by neutral, or
a non-neutral final wire command. All four single-axis signs must be present.
Physical outcome must remain `UNKNOWN`.
Passing this review is evidence of commands and OS sends only—not movement,
flight state, or a completed landing.

## Offline acceptance

`tools/run_offline_checks.sh` must pass on the committed revision. Focused tests
cover assertion failures, fresh-state/hover/arm gating, discarded bench ingress,
the four allowed pulses, neutral sequencing, telemetry/transport-loss landing
attempts, and reviewer rejection paths.
