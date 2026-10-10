# Dependency, SDK and fixture provenance

The source checkpoint was `1a16868`, with a clean working tree on `main`.
All new fixture pixels and landmarks are synthetic, authored within the tests.
No user recordings, secrets, downloaded SDK source, venv or generated ROS tree
are included. The existing model bytes remain locally available but are removed
from new Git tracking; earlier Git history still contains them.

| Component | Record / boundary |
|---|---|
| Project packages | Existing metadata declares Apache-2.0; no top-level licence file existed. No third-party relicensing is asserted. |
| Hand Landmarker | Google MediaPipe pretrained bundle; URL and required SHA-256 in `model_asset.py` and existing SHA256SUMS. Redistribution/licence review of the actual bundle remains open; no licence is invented from the documentation footer. |
| Model hash | `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`, 7,819,105 bytes, locally verified. |
| Existing Windows snapshot | `requirements-windows.txt` preserved; both OpenCV packages appear. It is historical environment evidence, not a supported clean lock. |
| Candidate direct dependencies | MediaPipe 1.1.0, opencv-contrib-python 5.0.0.93, NumPy 2.5.3 in `.in`; one OpenCV distribution. Clean Linux Python 3.12 install/model load passed; Windows cp314 wheel resolution passed, Windows runtime unrun. |
| Proposed decoder | PyAV 19.0.1, isolated Linux Python 3.12 install; generated lossless FFV1 colour-bar encode/decode passed. Real H.264 UDP behaviour and Windows runtime remain unvalidated. Review PyAV/FFmpeg and bundled codec licences for deployment/distribution. |
| Measurement draft | Input hash `bf4a94f1311bcd80948405752813bfa1efbae62befeff8a0b4edc3459c762875`; integrated and extended, not treated as prevalidated ROS code. |
| DJITelloPy inspection | 2.5.0 wheel, SHA-256 `525e9072dfb53a1f43051b6a8cbe33737def8430e8bf7b838c44a4c485c961e8`; wheel metadata reports MIT. Exact PyPI artifact/version recorded in `evidence/sdk_inspection.json`; inspected source stayed in `/tmp`. Not installed as runtime dependency. |
| Actual proposed transport | Small standard-library SDK 1.3 subset. No EDU commands, takeoff or emergency primitive. Construction has no network effects. Only loopback/fake transport was exercised. |

Inspected DJITelloPy 2.5.0 creates sockets/receiver threads in its constructor;
command wait defaults to seven seconds with three control retries, and `end()`
can call `land()` when its cached `is_flying` is true. Its background `.frame`
property returns a cached image without a new-frame identity. Those behaviours
are the reasons for using an explicit nonblocking UDP subset and decoder events
instead of placing that helper directly in the safety loop or diagnostic cleanup.
This is a software design choice, not a finding that the library cannot operate
a standard Tello. Compatibility with this aircraft or Windows Python 3.14 remains untested.

Primary references inspected during implementation:

- [Ryze SDK 1.3 PDF](https://dl-cdn.ryzerobotics.com/downloads/tello/20180910/Tello%20SDK%20Documentation%20EN_1.3.pdf): text command ordering, RC channel fields and UDP ports.
- [DJITelloPy maintainer API/source](https://djitellopy.readthedocs.io/en/latest/tello/) and the exact [2.5.0 package record](https://pypi.org/project/djitellopy/2.5.0/): inspected pinned wheel rather than assuming mutable master matched.
- [MediaPipe Hand Landmarker documentation](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker): model/API context; actual model redistribution terms are still to be established.
- [ROS 2 QoS design](https://design.ros2.org/articles/qos): unchanged production publishers and reliable/volatile monitor depth 1.

The SDK PDF has differing barometer unit descriptions in its query and state
sections. The safety parser therefore consumes only documented `bat` percent and
`h` centimetres; it does not silently derive flight state or altitude from baro.
No vendor speed/endurance specification or regulatory conclusion is asserted.
