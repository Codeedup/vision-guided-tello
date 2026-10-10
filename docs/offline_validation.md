# Offline validation boundaries

Use `tools/run_offline_checks.sh` from a Jazzy WSL checkout. It builds all packages,
runs registered tests and uses `colcon test-result --verbose` to fail on reported
failures (a `colcon test` process exit alone is insufficient). Webcam receiver,
open-hand, monitor, frame-source and diagnostic suites are now registered under
`tello_bridge`; they are no longer undocumented extra checks. The exact final
counts/commit are recorded in [current checkpoint](current_checkpoint.md).

Registered coverage includes:

- Existing C++ controller/supervisor pure policies and ROS launch tests, including
  controlled ROS-clock faults, replay protection and independent watchdogs.
- Existing mock plant tests and actual production C++ closed-loop launch tests.
- Independent bridge flags, scalar types/bounds, strict source/decision/receipt
  freshness, clock faults, replay/reorder, leased restart/reconnect authority,
  pending-arm neutrality, low/missing/stale telemetry, normal shutdown,
  delayed/missing replies, bounded land attempts and sticky consumer landing intent.
- Proposed SDK adapter constructed without sockets, then exercised only against
  an actual isolated loopback UDP peer. Its wire RC channels keep forward/yaw zero.
- Actual loopback JSON → original receiver callback → production C++ controller
  → production C++ supervisor → fake bridge in an isolated ROS domain/namespace.
  Tests assert signs, dead zones/saturation, zero invalid output, preserved stamps,
  five advancing acquisition frames, duplicate rejection, malformed-followed-by-valid
  packets, newest batch selection, stale/future packets and finite bursts.
- Actual sender silence, controller process exit and supervisor process suspension;
  independent bridge expiry and operator lease loss; actual bounded operator CLI.
- Passive monitor callbacks with production QoS, JSON output/metadata, missing-topic
  exit status, repeated approved heartbeats, command audit values and Ctrl+C output.
  Unit cases also cover signed ages, header selection, invalid CLI, older stamps,
  first-sample truncation, clock disagreement, terminal silence and missing anchors.
- Fake decoder/camera injection, latest-only identity, timeout, colour order,
  dimensions, unmirrored images and reuse of the original open-hand geometry.
- Complete diagnostic fake/error/cleanup paths with a positive allowlist and
  traps for RC/land, plus fake telemetry parsing with documented units.
- Existing checks remain enabled. New Python tools receive registered ament flake8
  and pep257 checks; package XML and CMake have registered lint checks.

Additional executable checks:

```bash
# No sourced ROS needed for the pure suites:
PYTHONPATH=src/tello_bridge:tools/webcam_perception:tools/tello_diagnostics \
  python3 -m pytest -q src/tello_bridge/test/test_boundary.py \
  tools/webcam_perception/test_measure_pipeline.py tools/webcam_perception/test_frame_sources.py \
  tools/webcam_perception/test_open_palm.py tools/webcam_perception/test_model_asset.py \
  tools/tello_diagnostics/test_diagnose.py
python3 tools/webcam_perception/measure_pipeline.py --self-test
python3 tools/webcam_perception/model_asset.py
python3 tools/tello_diagnostics/diagnose.py --duration 0.1 --video
bash -n tools/start_webcam_pipeline.sh tools/run_offline_checks.sh
```

Optional actual decoder test, separate from system/user interpreters:

```bash
python3 -m venv --system-site-packages /tmp/tello-video-check
/tmp/tello-video-check/bin/pip install -r tools/webcam_perception/requirements-video.txt
/tmp/tello-video-check/bin/python tools/webcam_perception/check_decoder.py
```

The fixture is generated FFV1 lossless video, not a recording of a person or
Tello H.264 footage. A separate isolated Linux dependency installation loaded
the verified MediaPipe bundle without opening a camera. Windows cp314 dependencies
were resolved/downloaded separately; that is not a Windows runtime test.

The loopback suite gives 0.5 s scheduler margin on observed neutral/landing
transitions (neutral budget <0.75 s and landing budget <2.5 s after the last sent
observation). It prints actual observer intervals. The exact 250 ms boundaries
are asserted in pure/fake-clock tests. Integration discovery is warmed up with
end-to-end disabled observations before acquisition, rather than assuming a
publisher count proves first-message delivery. These are local scheduling tests,
not aircraft stopping measurements or promises under arbitrary system load.

Receiver work remains bounded to 64 datagrams per 5 ms callback with a finite
kernel receive buffer. A tested finite burst is not sustained-load performance.
Replay state is process-local; UDP sender identity is not authenticated. Production
QoS was not changed to improve metrics. Reliable depth-1 monitoring can miss
messages/transitions and perturb load. First-sample percentiles lose full-run
representativeness after truncation, which is explicitly reported.

A complete demonstration, using separate output directories:

```bash
source /opt/ros/jazzy/setup.bash
source install/local_setup.bash
python3 tools/offline_demo.py --scenario static --output /tmp/tello-baseline
python3 tools/offline_demo.py --scenario loss --output /tmp/tello-loss
```

The runner reserves an isolated local ROS domain and uses the existing image-plane
plant. Its explicit `--reset-mock` operator session finishes endpoint discovery,
resets the original mock through its existing service, and then enables. The
monitor starts after the enabled marker; initial enable/acquisition can precede
its observation window and are tested separately. Missing expected tracking or
landing evidence makes the demo fail. It creates reports with commit, dirty state, environment, namespace,
configuration, baseline/fault kind and synthetic classification. Hardware publisher
rate/camera throughput remain unknown. The mock reacts to the supervisor's approved
command through its established consumer; the separate fake SDK boundary observes
and independently validates that same command. A fake SDK send is not a physical
plant measurement. The stationary webcam is never presented as closed-loop convergence.

The checked-in GitHub Actions workflow uses Ubuntu 24.04 with ROS Jazzy, actual
registered tests, an optional real decoder fixture check, pure tests without a ROS
environment and uploaded result artifacts. It was prepared locally and not pushed
or run on GitHub in this assignment. Container/package availability remains a
remote CI check; local results are not substituted for a hosted run.

Known existing skip inventory: ten cppcheck entries (five each in controller and
mission manager), skipped by the installed ament wrapper because cppcheck 2.13.0
has recorded performance issues. Existing CMake also disables copyright/cpplint;
this preparation does not newly suppress them or claim they passed. No ROS/system
package was replaced to bypass the wrapper. Separate newer cppcheck validation
remains outstanding; the reported skipped static analysis is not behaviour coverage.

Unrun by design: webcam/Windows runtime, sustained live/load trials, aircraft
networking, standard-Tello SDK/video compatibility, physical manual control,
used-battery capacity, calibrated command signs/speeds/stopping response, physical
landing confirmation and all flight trials. Those need the staged user acceptance
record and explicit later hardware authorization.
