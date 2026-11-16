# Dev — EARS Specs

Design: `dev-design.md`. Implementation: `reference/skills/forge-dev/SKILL.md`.

Every spec here describes what the `forge-dev` skill tells dev to do; they are verified by the sandbox scenario run, not by pytest.

## Modes

- [ ] **SPOKE-DEV-MODE-001**: When dispatched with mode `stub`, the `forge-dev` skill shall tell dev to write the modules, exported types, and function signatures the test skeletons name, with bodies that raise the language's not-implemented error, and return `STUBS_COMMITTED`.
- [ ] **SPOKE-DEV-MODE-002**: When dispatched with mode `full`, the `forge-dev` skill shall tell dev to write production code for everything in scope, run the tests in scope, and return `IMPLEMENTATION_COMPLETE`.
- [ ] **SPOKE-DEV-MODE-003**: When dispatched with mode `repair`, the `forge-dev` skill shall tell dev to change production code as the `delta` or finding in `notes` says and return `REPAIR_COMMITTED`, or `ERROR` describing a test or contract that stands in the way.

## Implementation

- [ ] **SPOKE-DEV-IMPL-001**: The `forge-dev` skill shall tell dev in a LID repository to annotate the entry point of each specified behavior with `@spec` and its EARS IDs.
- [ ] **SPOKE-DEV-IMPL-002**: The `forge-dev` skill shall define the tests in scope as the test files matching `assigned_scope`.
- [ ] **SPOKE-DEV-IMPL-003**: The `forge-dev` skill shall tell dev in mode `full`, when tests in scope still fail and dev finds no test at fault, to return `IMPLEMENTATION_COMPLETE` with the failing tests and what it tried in the summary.
- [ ] **SPOKE-DEV-IMPL-004**: The `forge-dev` skill shall tell dev to name failing tests outside scope in the summary.
- [ ] **SPOKE-DEV-IMPL-005**: The `forge-dev` skill shall tell dev to keep partial human implementations verbatim and complete around them.

## Escalations

- [ ] **SPOKE-DEV-ESC-001**: The `forge-dev` skill shall tell dev in mode `stub` or `full`, when a test asserts something the specs do not require or contradicts them, to commit valid progress and return `TEST_CHANGE_REQUESTED` with `escalation.details` naming the test, what it asserts, and the change needed.
- [ ] **SPOKE-DEV-ESC-002**: The `forge-dev` skill shall tell dev in mode `stub` or `full`, when tests and specs or two tests demand incompatible interfaces, to commit valid progress and return `CONTRACT_MISMATCH` with `escalation.details` naming both sides.
- [ ] **SPOKE-DEV-ESC-003**: The `forge-dev` skill shall tell dev to request approval with `HUMAN_CODE_CHANGE_REQUESTED` before changing a protected human-authored line, as the `forge-spoke` skill describes.

## Boundaries

- [ ] **SPOKE-DEV-BOUND-001**: The `forge-dev` skill shall tell dev to change only paths of the production path class and never to create, edit, skip, or delete a test.
