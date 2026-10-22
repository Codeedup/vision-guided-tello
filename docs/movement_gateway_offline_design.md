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
named, independent trained pilot and recovery procedure are still required for
host or Wi-Fi failure; neither gateway mode makes Space independent.

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

The reviewer rejects a non-real/non-bench trace, event omissions, forbidden wire
commands, any mixed-axis/non-capped output, repeated signed pulses, a pulse not
immediately followed by neutral, or a non-neutral final wire command. Passing
this review is evidence of commands and OS sends only—not movement, flight state,
or a completed landing.

## Offline acceptance

`tools/run_offline_checks.sh` must pass on the committed revision. Focused tests
cover assertion failures, fresh-state/hover/arm gating, discarded bench ingress,
the four allowed pulses, neutral sequencing, and reviewer rejection paths.
