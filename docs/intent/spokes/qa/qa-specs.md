# QA — EARS Specs

Design: `qa-design.md`. Implementation: `reference/skills/forge-qa/SKILL.md`.

Every spec here describes what the `forge-qa` skill tells QA to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do.

## Modes

- [D] **SPOKE-QA-MODE-001**: When dispatched with mode `skeleton`, the `forge-qa` skill shall tell QA to write one placeholder test per behavior in scope — named, annotated with its `@spec` ID in a LID repository or naming its `PLAN.md` test item otherwise, marked skipped or to-do in the test framework's own way, and without assertions — and return `SKELETON_COMMITTED`.
- [D] **SPOKE-QA-MODE-002**: When dispatched with mode `full`, the `forge-qa` skill shall tell QA to write executable tests for every behavior in scope, filling existing placeholders in place, and return `TESTS_COMMITTED`.
- [D] **SPOKE-QA-MODE-003**: When dispatched with mode `repair`, the `forge-qa` skill shall tell QA to change exactly what the `delta` or finding in `notes` names and return `REPAIR_COMMITTED`.

## Full tests

- [D] **SPOKE-QA-FULL-001**: The `forge-qa` skill shall tell QA to give every workstream spec (added or changed in the workstream and not marked `D`) whose behavior the plan places in `assigned_scope` at least one test annotated `@spec <ID>` on the test that exercises it in a LID repository, and every `PLAN.md` test item in scope at least one test otherwise.
- [D] **SPOKE-QA-FULL-002**: The `forge-qa` skill shall tell QA, after committing tests, to run them and fix any test that cannot be collected or crashes the runner before returning, while accepting failures of behavior not yet implemented.
- [D] **SPOKE-QA-FULL-003**: The `forge-qa` skill shall tell QA to keep existing tests that already cover a behavior in scope and add only what is missing.
- [D] **SPOKE-QA-FULL-004**: The `forge-qa` skill shall tell QA, when existing tests already cover everything in scope, to return `SKELETON_COMMITTED` in mode `skeleton` or `TESTS_COMMITTED` in mode `full` without committing, and say so in the summary.

## Escalations

- [D] **SPOKE-QA-ESC-001**: The `forge-qa` skill shall tell QA to return `CONTRACT_MISMATCH`, naming the specs and why in `escalation.details`, when specs in scope contradict each other or a spec cannot be tested as written.
- [D] **SPOKE-QA-ESC-002**: The `forge-qa` skill shall tell QA to request approval with `HUMAN_CODE_CHANGE_REQUESTED` before changing a protected human-authored test line, as the `forge-spoke` skill describes.

## Boundaries

- [D] **SPOKE-QA-BOUND-001**: The `forge-qa` skill shall tell QA to change only paths of the test path class.
