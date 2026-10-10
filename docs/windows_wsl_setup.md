# Windows/WSL setup and operation

Keep source in `~/vision_guided_tello_ws`. The Windows venv is an interpreter
environment, not a second source tree. ROS runs on WSL Ubuntu 24.04 / Jazzy with
system Python 3.12. Existing Windows webcam Python 3.14 is user-reported.

WSL build/check:

```bash
cd ~/vision_guided_tello_ws
tools/run_offline_checks.sh
source /opt/ros/jazzy/setup.bash
source install/local_setup.bash
python3 tools/webcam_perception/model_asset.py
```

The local model was retained on disk but removed from new Git tracking. A new
clone explicitly fetches it using `python3 tools/webcam_perception/model_asset.py --fetch`.
The download is accepted only for the expected SHA-256. An existing differing
file is refused. The upstream `latest` URL is mutable; a changed hash requires
review, never automatic acceptance. No download occurs on import or in tests.

Start **WSL terminal A**, receiver/controller/supervisor/fake bridge:

```bash
TELLO_BIND=0.0.0.0 TELLO_OBSERVATION_PORT=5005 TELLO_NAMESPACE=webcam_test \
  tools/start_webcam_pipeline.sh
```

The default bind without an override is loopback. Explicit `0.0.0.0` permits the
existing Windows sender. Confirm the current WSL IPv4 with `hostname -I`; choose
the address Windows can reach, not a remembered 172.x address. Check firewall and
routing if needed; no script changes firewall, clocks, NTP or network membership.

Start **Windows PowerShell terminal B**, substituting the current reachable address:

```powershell
$repo = '\\wsl.localhost\Ubuntu\home\dylan\vision_guided_tello_ws'
$python = "$env:LOCALAPPDATA\vision-guided-tello\webcam-venv\Scripts\python.exe"
& $python "$repo\tools\webcam_perception\check_model.py"
& $python "$repo\tools\webcam_perception\hand_preview.py" --receiver CURRENT_WSL_IPV4 --port 5005
```

Use your actual WSL distribution name if it is not `Ubuntu` (`wsl -l -q`). The
preview is unmirrored; Q stops the camera/sender. Do not run a mock publisher in
`webcam_test`. Reusing the existing working venv needs no package reinstall.

For a **new** Windows environment, the direct candidate dependencies are in
`requirements-windows.in`. `requirements-windows.txt` is preserved as the historical
full snapshot and contains both OpenCV distributions; do not install both into a
new environment. Candidate clean setup, separate from the working venv:

```powershell
py -3.14 -m venv "$env:LOCALAPPDATA\vision-guided-tello\webcam-check-venv"
$checkpython = "$env:LOCALAPPDATA\vision-guided-tello\webcam-check-venv\Scripts\python.exe"
& $checkpython -m pip install -r "$repo\tools\webcam_perception\requirements-windows.in"
& $checkpython "$repo\tools\webcam_perception\check_model.py"
```

Windows wheel resolution passed locally using cross-platform pip download. It
is not a Windows installation/inference/camera result. A separate clean Linux
Python 3.12 install and explicit model-load test are recorded in offline validation.
Optional decoder requirements are in `requirements-video.txt`; install only into
the selected video runtime. Real Tello decoding/network routing remains untested.

**WSL terminal C**, read-only checks and baseline monitor:

```bash
source /opt/ros/jazzy/setup.bash
source install/local_setup.bash
python3 tools/pipeline_diagnostics.py --namespace /webcam_test
python3 tools/webcam_perception/measure_pipeline.py --namespace /webcam_test \
  --duration 60 --source live_webcam --run-kind baseline \
  --output /tmp/webcam-baseline-01.json
python3 tools/webcam_perception/summarize_report.py /tmp/webcam-baseline-01.json
```

The diagnostic reports executables, message types, publisher counts, QoS and
use_sim_time through read-only parameter GETs. Exactly one publisher per pipeline
topic is expected. All three flags should initially be false with zero movement.
The monitor has no publishers/services; publisher rate and camera FPS are unknown.
Check clocks separately before comparing source ages: run `& $python "$repo\tools\webcam_perception\check_clocks.py"` in Windows
PowerShell (its existing probe targets the Ubuntu WSL distribution). Stop nodes before any deliberate clock correction.
No script changes clock settings. ROS/monotonic agreement does not establish Windows agreement.

For software-only enabled trials using the fake boundary, prepare the synthetic
hover and retain the operator terminal:

```bash
ros2 run tello_bridge tello_operator prepare-fake --namespace /webcam_test
ros2 run tello_bridge tello_operator enable --namespace /webcam_test
```

Ctrl+C in that terminal takes over both bridge and supervisor. `status`, `disable`,
`takeover`, and `land` are explicit operator actions. Killing the operator prevents
lease renewal and requests fake landing. After landing intent, take over the
supervisor, then explicitly `prepare-fake` before another fake trial; a supervisor
latch reset alone does not cancel the consumer latch. This is not a physical flight-reset recipe.

Ctrl+C in terminal A attempts takeover while ROS still exists, then stops launch.
`kill -9`, host crash, Wi-Fi loss and a stopped executor cannot promise cleanup.
Restart starts disabled/unknown; it is not an in-flight latch reset.
