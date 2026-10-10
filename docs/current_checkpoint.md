# Offline preparation checkpoint — 10 October 2026

Branch: `codex/offline-preparation-2026-10-10`, based on clean `1a16868`.
Implementation, final verification commit and exact counts are recorded below
when the final local regression completes. No push, merge, webcam access,
aircraft endpoint probe, aircraft command or flight test was performed.

The next user involvement is the 60-second **live laptop baseline with fake
transport**, followed by separate fault trials. Follow [the acceptance guide](user_acceptance_tests.md)
and [terminal/setup recipes](windows_wsl_setup.md).

Implemented work is described in [offline validation](offline_validation.md),
[architecture/authority](architecture.md), and [provenance](provenance.md).
Real ROS actuation remains deliberately gated until network/flight-state/operator
evidence supports a commissioning interface. The proposed SDK/video adapters are
code plus fake/loopback tests, not hardware compatibility evidence.
