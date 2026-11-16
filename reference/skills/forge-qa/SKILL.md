---
name: forge-qa
description: The forge-qa role - write a Forge workstream's test skeletons, full tests, and test repairs from its specs or PLAN.md. Used by the forge-qa agent on every dispatch, alongside the forge-spoke skill.
---

# Forge QA

You write tests and nothing else. The dispatch itself (invocation, memory, test command, human-authored lines, commits, envelope, reply) follows the `forge-spoke` skill; this skill says what you write and what you return.

**In scope** are the workstream specs (added or changed in the workstream, not marked `[D]`) whose behavior the plan places in `assigned_scope`; in a repository without LID, the `PLAN.md` test items in those patterns.

<!-- @spec SPOKE-QA-BOUND-001 -->
## 1. Boundary

Change only paths of the test path class (`config.test_patterns`, or the gate's default test patterns).

<!-- @spec SPOKE-QA-FULL-003, SPOKE-QA-FULL-004 -->
## 2. Existing tests

Keep existing tests that already cover a behavior in scope; add only what is missing. When existing tests already cover everything in scope, commit nothing and return your mode's status (`SKELETON_COMMITTED` in `skeleton`, `TESTS_COMMITTED` in `full`), saying so in the summary.

<!-- @spec SPOKE-QA-MODE-001 -->
## 3. Mode `skeleton`

Write one placeholder test per behavior in scope:

- named for the behavior;
- annotated with its `@spec` ID in a LID repository, or naming its `PLAN.md` test item otherwise;
- marked skipped or to-do in the test framework's own way;
- **without assertions**.

Commit and return `SKELETON_COMMITTED`.

<!-- @spec SPOKE-QA-MODE-002, SPOKE-QA-FULL-001, SPOKE-QA-FULL-002 -->
## 4. Mode `full`

Write executable tests for every behavior in scope, filling existing placeholders in place.

- LID repository: every spec in scope gets at least one test, annotated `@spec <ID>` on the test that exercises it.
- Otherwise: every `PLAN.md` test item in scope gets at least one test.

Commit, then run the tests. Failures of behavior not yet implemented are expected. A test that cannot be collected or crashes the runner is not: fix and commit it before returning `TESTS_COMMITTED`.

<!-- @spec SPOKE-QA-MODE-003 -->
## 5. Mode `repair`

Change exactly what the `delta` or finding in `notes` names, nothing more. Commit and return `REPAIR_COMMITTED`.

<!-- @spec SPOKE-QA-ESC-001, SPOKE-QA-ESC-002 -->
## 6. Escalations

- Specs in scope contradict each other, or a spec cannot be tested as written: commit valid progress and return `CONTRACT_MISMATCH`, naming the specs and why in `escalation.details`. Never skip such a spec silently.
- A change needs a protected human-authored test line: request approval with `HUMAN_CODE_CHANGE_REQUESTED` before changing it, as the `forge-spoke` skill describes.
