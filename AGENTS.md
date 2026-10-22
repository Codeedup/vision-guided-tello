# Project guidance

## Context and token efficiency

- Start by identifying the packages and files relevant to the task.
- Read only those files and their direct dependencies initially.
- Prefer targeted searches over repository-wide file dumps.
- Do not read entire logs when a short error excerpt is sufficient.
- Expand investigation only when required by evidence or dependencies.
- Do not repeatedly inspect unchanged files.

## Testing efficiency

- During development, run tests for the affected package or component first.
- Do not automatically run the complete ROS 2 test suite after every edit.
- Run broader regression tests when changes cross package boundaries, affect shared interfaces, or introduce integration risks.
- Run all mandatory offline and safety validation gates before hardware testing or relevant integration milestones.
- Never skip safety-critical tests solely to save tokens.
- Summarize test results instead of returning large successful test logs.

## Scope

- Keep changes within the requested task and affected dependencies.
- Do not refactor unrelated packages without justification.
- Preserve mission_manager safety rules, watchdogs, abort behavior, and approved-output gating.
- Never send live flight commands without explicit operator authorization.
