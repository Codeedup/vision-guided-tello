# Readiness audit — 13 October 2026

The handoff was read in full before edits. The checkout contained the five-file
launcher commit `35ee9c1`; it and local measurement files were preserved. The
handoff is a requirements/evidence snapshot; source and reproducible tests govern
implementation claims. No aircraft networking, camera access or flight occurred.

Software remediation is complete for the findings below. **Bench readiness is
still blocked by the enabled live FAKE target-loss sequence and real-mode design.**
A software pass does not authorize physical actuation. See the
[staged runbook](commissioning_runbook.md) for the next user test. Exact validation
commands and revision linkage are in [current checkpoint](current_checkpoint.md).

## Requirement → implementation → evidence

“Verified” means deterministic software evidence, with the physical limits below.
Paths are relative to the repository root. Hardware-unverified and missing rows
remain commissioning blockers; fake tests cannot close them.

| Requirement | Code | Test/evidence | State |
|---|---|---|---|
| Direct diagnostic CLI startup without test paths | explicit perception-path setup in `bench_hand_preview.py` | clean-environment `--help` subprocess tests; actual Windows headless synthetic replay | Implemented/verified |
| CI lint and both launcher suites discovered | `tools/*`, `src/tello_bridge/CMakeLists.txt` | registered flake8/pep257; 4 argument + 20 reliability cases | Implemented/verified |
| Receiver repeated stop signals | `ros_hand_receiver.py` | repeated SIGINT/SIGTERM inside callbacks; real launcher clean-stop check | Implemented/verified |
| Mock callback/context shutdown ordering | `src/mock_tello/mock_tello/node.py` | `test_shutdown.py` fails old code for both signals, passes fix; closed-loop clean exits | Implemented/verified; exact historical SIGKILL stack unavailable |
| Closed-loop reset response discovery | `test_closed_loop_launch.py` | actual reset with deliberately discarded first response; bounded repeat within original 3 s | Implemented/verified |
| Namespace/UDP ownership and required-child cleanup | `start_webcam_pipeline.py`, `pipeline_diagnostics.py`, `webcam.launch.py` | `test_launcher_reliability.py`; retained user launcher traces | Implemented/verified; user full launch repeat pending |
| FAKE default, explicit enable; no takeoff or restart authority | `tello_bridge/node.py`, `policy.py`, `operator.py` | boundary, restart/reconnect and loopback tests | Implemented/verified |
| One local SDK owner; diagnostic positive allowlist | `transport.py`, `diagnose.py`, `bench_hand_preview.py` | owner exclusion, actual loopback wire and flight-command traps | Implemented/verified on one host; cross-host ownership unverified |
| Production physical transport and Windows/WSL route | ROS bridge rejects every mode except FAKE; SDK class is a proposed adapter | real mode fails closed; camera-only diagnostics exist | Missing; resolve ownership/routing before designing flight gateway |
| Deliberate takeoff, trustworthy hover/landed confirmation | no production takeoff API; real authority rejected | no height/cache/SDK-reply transition to confirmed physical state | Missing; actual sensor evidence and operator procedure required |
| OS send, SDK reply and physical result separate | `transport.py`, `policy.py` | delayed/stuck reply, wire, bounded retry tests; `landing_delivery` status | Implemented/verified; physical outcome unknown |
| Finite bounded two-axis mapping; forward/yaw zero | controller config, `sdk_integer`, authority caps | C++ signs/dead-zone/cap tests; UDP `rc lateral 0 vertical 0` | Implemented/verified; physical signs unverified |
| Conservative independent axis configuration | controller read-only `gain`, `dead_zone`, `lateral_limit`, `vertical_limit`; bridge integer caps | independent axis isolation, invalid config and boundary cap tests | Implemented/verified; physical tuning unverified |
| Strict source/receipt freshness <250 ms | receiver, controller, supervisor, bridge | exact boundary, future/frozen clocks and silence cases | Implemented/verified |
| Five distinct frames for acquisition/reacquisition | supervisor and candidate replay watermark | duplicate/reorder and dropout recovery tests | Implemented/verified |
| Sustained loss → landing after 1.5 s; no hand-return restart | supervisor timer; sticky bridge landing | C++ policies and closed-loop loss/reset tests | Implemented/verified deterministically; live sequence pending |
| 300 ms operator lease, paced 80 ms heartbeats | `policy.py`, `cadence.py` | exact expiry, late renewal, operator disappearance, pacing tests | Implemented/verified; sustained laptop repeat pending |
| Independent approved-output/monotonic watchdog | `policy.py`, `arbiter.py`, mock consumer | suspended supervisor, controller exit, frozen/backward clock tests | Implemented/verified |
| Malformed/no/multiple/ineligible hand data → neutral | receiver validation and shared open-palm gate | malformed UDP then valid, NaN/Inf/range, open-palm geometry tests | Implemented/verified; perception envelope unverified |
| Low/missing/stale/invalid battery/height blocks authority | `Limits`, `telemetry_ok`, `parse_state` | battery/height and malformed/future receipt tests | Implemented/verified for FAKE; real telemetry lifecycle unavailable |
| Maximum autonomy duration; repeated arm cannot renew it | `maximum_session_ns`, monotonic arm timestamp | deadline with continuing fresh tracking/lease; sticky landing | Implemented/verified; default 60 s development session cap |
| Critical aircraft fault coverage | diagnostic state parsing covers battery/height only | no claim of complete aircraft fault reporting | Missing for physical authority; SDK-supported faults need review |
| Takeover vs controlled land vs motor cut | explicit operator actions; no emergency command interface | neutral-before-land and takeover preserving latch tests | Implemented/verified in FAKE; independent physical override unverified |
| Broken link/retries cannot imply guaranteed landing | `landing_delivery`; 3 bounded attempts | transport failure and exhausted-attempt status tests | Implemented/verified; no guaranteed delivery after link/host failure |
| Preserve receipt time; bounded newest-frame/UDP queues | `frame_sources.py`, receiver | identity, stale cached frame, worker timeout and finite burst tests | Implemented/verified; exposure/driver buffering unmeasured |
| Inference/age/gaps/eligibility/transport timing distinct | sender, passive monitor and bridge timing reports | timing unit tests; retained historical trace summaries | Implemented/verified; live 19 vs 86 ms cause unresolved |
| Controlled preview/headless comparison | `replay_hand_preview.py` with identical recorded-file hash | bounded replay/no-network cases; decoder fixture; actual Windows 12-frame synthetic replay | Implemented/verified tooling; representative recording comparison pending |
| Filtering/slew and safety neutral priority | proportional stateless controller, direct neutral safety paths | pure controller/expiry/arbiter tests | No filter/slew exists; no added latency without measured justification |
| Display mirroring/coordinate conventions | unmirrored frame contract, shared gate | frame colour/identity and preview/headless packet equivalence | Implemented/verified; actual aircraft signs unverified |
| Run/revision/model/clock pairing; no overwrite | sender provenance; `check_clocks.py`; operator trace label | timing/clock/collision tests; analyzer provenance rejection | Windows offline runtime verified; matched live trial pending |
| Complete TRACK → loss → latch → hand return analysis | `analyze_target_loss.py` | complete synthetic case; brief TRACK, truncation, restart, gap, stale data rejection | Implemented/verified tooling; historical run correctly fails |
| Reproducible live FAKE enabled target-loss evidence | prepared staged commands | historical maximum completed TRACK 0.119946 s | Hardware/user-unverified; immediate next gate |
| Manual baseline and staged one-/two-axis trials | commissioning runbook | prior user-reported ~10 s manual hover only | Hardware-unverified; current preflight/manual baseline required |
| ≥15 processed FPS, ≥10 Hz control, distance and 8/10 trials | measurement targets only | no commissioned repeated physical protocol/results | Hardware-unverified; no achieved guarantee |

## Shutdown investigation

The unchanged full suite at `35ee9c1` reported 467 aggregate entries, 36 failures,
10 cppcheck skips. These include wrappers, not 36 distinct behaviours. Its mock
post-shutdown assertion failed on exit 1: rclpy was inside `_take_subscription`
when `take_message` raised a pybind conversion RuntimeError. The earlier hosted
run at `5397129` hung in shutdown and reached exit -9. Neither log contains a
native stack proving the precise cause of that historical hang.

The mock used rclpy's default asynchronous signal/context shutdown while callbacks
and subscription takes could still run. A deterministic callback self-signal
regression demonstrates SIGINT interrupting callback completion and SIGTERM
shutting down the context inside a callback on the old code. The fix disables
that asynchronous shutdown, records a stop request, and tears down executor,
node/CSV and context on the spin thread. Both stop signals pass the new regression;
all original clean-exit assertions remain. No timeouts were increased or exit
codes whitelisted. Baseline and before/fix logs are kept under ignored `artifacts/`.

A later full-suite repeat exposed an independent first-response discovery race: RMW
reported `failed to send response (timeout)` and the probe timed out resetting the
stale-input scenario despite graph service discovery. The test now repeats only
its disabled, repeatable mock reset within the original overall 3-second deadline,
cancelling unobserved requests. A regression executes a real reset, deliberately
discards its first reply, then requires the next round trip and disabled state.
Other service operations still fail on missing responses; production retry/authority
policy is unchanged.

An actual launcher stop also exposed a second SIGINT interrupting the receiver
inside service destruction (exit -2). The owned process group and ROS launch can
both deliver signals during teardown. The receiver now uses the same stop-request
and ordered executor/socket/node/context cleanup pattern. Repeated-signal callback
regressions retain the live context until callback completion. This does not
change observation timestamps, freshness, acquisition or command authority.

The full process check then isolated duplicate delivery in noninteractive Jazzy
launch: `ExecuteLocal.__on_shutdown` forwards SIGINT when the launch context is
noninteractive, while the wrapper had already signalled the entire group. A C++
controller exited -2 during this duplicate interrupt. The wrapper now sends the
first SIGINT to the noninteractive launch leader, which forwards to its children;
interactive launch and generic helpers retain group delivery. All escalation still
reaches the owned group. A forwarding-parent/child regression requires exactly
one initial child interrupt. Actual normal-stop, duplicate-owner and receiver-loss
checks now pass with finalized traces, no child errors, no leftovers and a free
observation port.

## Authority, defaults and unresolved design

The production chain consumes only `ApprovedCommand`; the bridge accepts FAKE
transport only. `UdpSdkTransport` can be explicitly opened by camera diagnostics
or isolated loopback tests but has no commissioned ROS flight entry point. Do not
construct a flight path by weakening disabled/unarmed launcher readiness checks.

Controller defaults remain gain 20, inclusive ±0.08 dead zone, ±10 command scalars.
Read-only gain can be reduced in (0,20], dead zone in [0,1), and each axis cap in
[0,10]; zero isolates an axis. The bridge independently caps integer output with
`maximum_lateral_command` and `maximum_vertical_command` (0..10). Invalid input
still neutralizes both axes; safety neutral bypasses any normal command state.
No smoothing/slew limiter is present or added. These values are not flight tuned
or calibrated speeds. Numerical correctness cannot establish physical direction.

Bridge `Limits` fields are read-only ROS parameters. Defaults: battery ≥25%,
height 30..150 cm, telemetry <500 ms, lease <300 ms, input-loss land timer 1.5 s,
three land attempts at ≥1 s intervals during normal ticking. New monotonic session
cap: 60 s from explicit arm, including acquisition. This bounds development
sessions to the existing short diagnostic window; it is not a battery endurance
estimate. A repeated arm or heartbeat cannot extend it. Explicit takeover/rearm
requires the existing interlocks; a landing latch requires deliberate FAKE reset.
Shutdown may make a final bounded land attempt without waiting for the running
retry interval. Failed delivery remains visible and physical outcome UNKNOWN.

For current Tello camera-only work Windows owns SDK command UDP 8889, state 8890
and video 11111; the same process sends observation UDP to WSL 5005. Stop the
phone/app and all other SDK diagnostics before starting it. WSL receiver owns
5005; the ROS bridge owns no aircraft socket. A same-OS endpoint lock cannot
arbitrate another OS/laptop. Before any flight gateway implementation, select one
command owner and prove routing, lifecycle telemetry and available operator abort
with the user. Neither an uncorrelated `ok` nor height alone proves takeoff/landing.

## Timing evidence and limitations

The [historical trace review](evidence/readiness_trace_review_2026-10-13.json)
contains hashes and compact aggregates; raw measurements remain local. Inference
medians were 47.36 ms (preview), 19.01 ms (headless disabled baseline), and 86.14 ms
(headless failed loss trial). Eligible-only medians were 47.77, 19.13 and 86.34 ms.
These separate sessions do not isolate preview, scheduling, detector re-detection,
queueing, duplicate processes or load as causes. Do not lower safety freshness to
make the slow run pass. A representative bounded recording and matched replay on
the actual Windows host are still required; synthetic decoder tests do not answer
that performance question.

The analyzer deliberately refuses incomplete provenance, omitted records, long
observer gaps, insufficient tracking/hand absence/return or a cleared landing
latch. Its 1.45..2.0 s observed loss-to-land window accounts for passive subscriber
scheduling; production rules remain strictly 250 ms and 1.5 s. A subscriber pass
cannot prove it saw every event. Record intentional actions separately. Source
stamps represent decoded-frame receipt (webcam read completion for that source),
not exposure. Record source-age rejection, last nonzero command and eventual
physical deceleration separately during later commissioning.

The existing Windows Python 3.14 environment also passed a headless 12-frame
synthetic FFV1 replay with the actual MediaPipe model and OpenCV, plus direct CLI
startup without the test PYTHONPATH. This exposed and fixed a diagnostic import
ordering dependency that isolated Python tests had masked. No camera or SDK socket
was opened. The replay report records its precommit revision/dirty state and model
hash; it is a runtime smoke check, not live readiness or representative palm timing.
A fresh Windows-to-WSL clock probe with nodes stopped measured bounds of
-0.180018 to +0.049382 ms. These bounds apply only to that offline check; repeat
them with a matched run ID before the next live test.
