# Current readiness checkpoint — 13 October 2026

The full handoff was reviewed before edits, local commit `35ee9c1` preserved,
and concrete CI/lifecycle findings repaired. Source validation revision:
`3e098392038177b3372fdcebd0f192473d807ea1`. The release commit adds documentation/evidence only;
`git rev-parse HEAD` identifies the final checkout. Consult the
[GitHub workflow](https://github.com/Codeedup/vision-guided-tello/actions/workflows/offline.yml)
for that exact release revision; local success alone is not hosted CI success.

**Current state: software checks pass; bench/flight readiness remains blocked.**
The immediate next user test is the enabled headless FAKE target-loss repeat in
[the staged runbook](commissioning_runbook.md), with matched clock/run/revision
traces, ≥5 s actual TRACK, ≥3 s hand absence and ≥5 s eligible hand return without
restart. The historical ~0.12 s TRACK interval does not pass. Real ROS actuation
still rejects hardware mode; no aircraft connection or flight occurred here.

Validation:

- Full offline build/test: **507 reported entries, 0 errors, 0 failures, 10 skips**.
  The ten skips are inherited ament/cppcheck 2.13.0 performance exclusions, five
  each in controller and mission manager. Existing copyright/cpplint exclusions
  remain explicit. Counts include wrappers, not 507 distinct flight behaviours.
- Standalone pure-policy selection: **152 passed**, overlapping registered suites.
- Optional PyAV 19.0.1 lossless synthetic decode/colour/identity check passed.
- Actual launcher: duplicate ownership rejected; normal stop exit 0; receiver-loss
  exit 1; completed traces, no child errors/leftovers, observation port released.
- Actual Windows environment: direct diagnostic CLI startup, 12-frame headless
  synthetic replay with the actual model/OpenCV, and bounded Windows/WSL clock
  probe passed. No live camera, SDK or palm performance claim follows.
- All 28 local raw measurement JSON files retained and ignored. Generated
  build/install/log/venv/model/recording artifacts stay out of Git. Editor paths
  now use the workspace folder instead of a personal absolute directory.

The [requirement/code/test matrix](readiness_2026-10-13.md) lists implemented
checks and unresolved blockers, including the physical command-owner route,
trustworthy hover/landed telemetry, critical fault coverage, independent operator
abort, representative timing comparison and all commissioned physical trials.
A configurable 60-second session cap and independent lower axis limits now support
staged tests; defaults remain uncalibrated development settings.
[Exact commands, logs/hashes and limitations](evidence/readiness_validation_2026-10-13.json)
and [historical timing reanalysis](evidence/readiness_trace_review_2026-10-13.json)
preserve the evidence. Raw logs remain under ignored `artifacts/`.

Everything below is the historical 10 October record, not current status.

## Historical offline preparation checkpoint — 10 October 2026

Current live debugging checkpoint: the latest user handoff records eligible webcam
input and supervisor TRACK, but independent bridge operator-lease expiry. The
[FAKE lease timing diagnostic](lease_timing_debug.md) now provides paired server/client
traces and an explicitly paced operator. Its GitHub regression evidence does not
replace repeated WSL webcam trials. The offline preparation record below is historical.

Branch: `codex/offline-preparation-2026-10-10`, based on clean `1a16868`.
Validated implementation: `75de0905974349535266a3037e8982ecb721148a`.
The follow-up commit adds evidence/documentation only; `git rev-parse HEAD`
identifies the final record commit. No push, merge, webcam access, aircraft
endpoint probe, aircraft command or flight test was performed. The model remains
locally available with its verified hash, but is removed from new Git tracking.

**Ready for your involvement:** the 60-second live laptop baseline with fake
transport and autonomy disabled. Follow [acceptance steps](user_acceptance_tests.md)
and [Windows/WSL terminal recipes](windows_wsl_setup.md). Preserve the baseline JSON,
summary, graph diagnostic and Windows/WSL clock bounds before fault trials.

Final aggregate: **344 reported tests, 0 errors, 0 failures, 10 skipped**.
This includes linter entries and wrappers; it is not a count of flight behaviours.
Counts changed as lint failures were fixed and reports regenerated, so they are
not a permanent expected suite size. The ten existing cppcheck skips remain
explicit (2.13.0 performance issue). Existing copyright/cpplint exclusions remain
as inherited, not new passes. The 110-test pure-Python selection also passed
without sourcing ROS; it overlaps registered suites and must not be added to the
aggregate. Embedded monitor tests, actual synthetic PyAV decoding, clean Linux
model load, Windows wheel resolution, fake diagnostics, startup graph introspection
and Ctrl+C cleanup were checked separately.

Validation ran the full workspace, then reran the entire bridge package after
fixing the monitor interruption test to wait for actual traffic on all topics.
All behavioural suites passed; a final formatting-only flake8 recheck passed.
Exact commands, skips, metadata and synthetic summaries/transitions are in
[evidence/offline_preparation_results.json](evidence/offline_preparation_results.json).
The baseline/loss raw reports are in the local `/tmp/tello-evidence-*-d191fcd`
directories and can be regenerated with the offline demo commands.

Both synthetic runs observed tracking; the loss run observed latched landing
intent. One baseline run during concurrent regression/demo load had observer-age
warnings on 1 hand, 2 candidate and 5 approved messages. Retained tracking headers
were fresh at the supervisor decision (maximum 55.2 ms). These are different
measurement points; the warnings are preserved and sustained live/load acceptance
remains open. No exact physical stopping guarantee is inferred.

Implemented components and reproducible commands are in
[offline validation](offline_validation.md), [architecture/authority](architecture.md),
and [provenance](provenance.md). The CI workflow is prepared but unrun on GitHub.
Real ROS actuation remains gated until network/flight-state/operator evidence
supports commissioning. The proposed SDK/video adapters have fake/loopback tests,
not standard-Tello hardware compatibility evidence.

After the baseline, perform separate laptop fault trials, then later propeller-off
network/video and operator/lifecycle checks, used-battery/manual-hover trials,
and separately authorized bounded autonomy. Host placement, physical manual
control, calibrated command/stopping response, camera buffering, used-battery
limits and total host/link failure behaviour remain unresolved. The 2D open-hand
gate cannot prove the palm faces the camera; lighting/distance/orientation accuracy
still needs real evaluation. No propeller-on test is authorized by this checkpoint.
