# Verifier — EARS Specs

Design: `verifier-design.md`. Implementation: `reference/scripts/lid_check.py` and `reference/skills/forge-verifier/SKILL.md`.

Specs under *Modes* describe what the `forge-verifier` skill tells the verifier to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do. The `lid_check.py` specs are tested with pytest.

## `lid_check.py` invocation

- [x] **SPOKE-VER-LID-CLI-001**: `lid_check.py --state PATH` shall print exactly one JSON object with a `status` to stdout and exit `0`, and exit `2` with a message on stderr on a usage error.
- [x] **SPOKE-VER-LID-CLI-002**: If `config.repo_mode` is not `LID`, then `lid_check.py` shall return `status` `NOT_LID`.
- [x] **SPOKE-VER-LID-CLI-003**: If the `--state` file is missing or does not parse as a JSON object, then `lid_check.py` shall return `status` `STATE_INVALID` with a `detail` saying which.
- [x] **SPOKE-VER-LID-CLI-004**: `lid_check.py` shall run in `identity.worktree_path`, use only the Python standard library and external `git` commands, and not create, change, or delete any file.
- [x] **SPOKE-VER-LID-CLI-005**: `lid_check.py` shall expose a function `check(state)` that takes the parsed `state.json` object and returns the same result object the command prints for it, so the router computes `merged_specs` identically.

## Specs and annotations

- [x] **SPOKE-VER-LID-001**: `lid_check.py` shall treat as a spec every line of a `docs/intent/**/*-specs.md` file matching `- [<marker>] **<ID>**:`, where the marker is a space, `x`, or `D`, and the ID is upper-case letters and digits in hyphen-separated segments ending in a three-digit number.
- [x] **SPOKE-VER-LID-002**: `lid_check.py` shall treat a spec as a workstream spec when its line was added or changed by a non-merge first-parent commit in `anchors.workstream_base_sha..HEAD`, or by any non-merge first-parent commit reachable from `HEAD` while `anchors.workstream_base_sha` is `null`, and return their IDs in `workstream_specs`.
- [x] **SPOKE-VER-LID-003**: `lid_check.py` shall read an `@spec` annotation as the text `@spec` followed by one or more comma-separated tokens on the same line that each match the spec ID syntax of SPOKE-VER-LID-001, ignoring `@spec` followed by anything else, and count one in a file of the test path class as a test citation and one in a file of the production path class as a code tag.
- [x] **SPOKE-VER-LID-009**: `lid_check.py` shall list in `merged_specs` the ID of every spec not marked `D` that is not a workstream spec, whose line was added or changed by a merge commit on the first-parent chain of the SPOKE-VER-LID-002 range, and that has no test citation, and shall report no finding for it.

## Findings

- [x] **SPOKE-VER-LID-004**: When another spec line anywhere in the repository defines a workstream spec's ID, `lid_check.py` shall report `DUPLICATE_ID` for it, not counting a mention of the ID in a spec's text.
- [x] **SPOKE-VER-LID-005**: When an `@spec` annotation in a file changed in the workstream's range names an ID that no spec line has, `lid_check.py` shall report `UNKNOWN_ID` with its `path` and `line`.
- [x] **SPOKE-VER-LID-006**: When a workstream spec not marked `D` has no test citation, `lid_check.py` shall report `UNTESTED` for it.
- [x] **SPOKE-VER-LID-007**: When a workstream spec not marked `D` has no code tag, `lid_check.py` shall report `UNTAGGED` for it.
- [x] **SPOKE-VER-LID-008**: `lid_check.py` shall return `status` `OK` with `workstream_specs`, `merged_specs`, `unmarked_specs`, and `findings`, each finding carrying `kind`, `id`, `detail`, and, where it applies, `path` and `line`.
- [x] **SPOKE-VER-LID-010**: `lid_check.py` shall list in `unmarked_specs` the ID of every workstream spec whose line is `[ ]`, that has no `DUPLICATE_ID` finding, and that has both a test citation and a code tag, and shall report no finding for it.

## Modes

- [D] **SPOKE-VER-MODE-001**: When dispatched with mode `POST_PLAN`, the `forge-verifier` skill shall tell the verifier to return `PASS` only when `scope_manifest.assigned` is empty solely for a plan that changes nothing outside plan paths and, in a LID repository, `lid_check.py` reports no `DUPLICATE_ID` and every LLD behavior the plan adds has a spec and every new spec traces to the LLD, or, in a naive repository, `PLAN.md` states the goal, approach, scope, and tests; otherwise `AUDIT_FAILED`.
- [D] **SPOKE-VER-MODE-002**: When dispatched with mode `PRE_QA`, the `forge-verifier` skill shall tell the verifier to judge, from `forge_spoke.py scope` and the tests in scope — the test files matching a pattern and the tests citing a workstream spec whose behavior the plan places in it — which assigned patterns have an executable test for every behavior the plan places in them, counting a skipped or to-do placeholder as no test.
- [D] **SPOKE-VER-MODE-003**: When dispatched with mode `PRE_DEV`, the `forge-verifier` skill shall tell the verifier to judge which assigned patterns have tests in scope that run and pass, counting a skipped test as not passing and code that raises not-implemented as not implemented.
- [D] **SPOKE-VER-MODE-004**: The `forge-verifier` skill shall tell the verifier in `PRE_QA`, `PRE_DEV`, and `RECONCILE` to return `scope.completed` and `scope.remaining` that together hold each assigned pattern exactly once, and in `PRE_QA` and `PRE_DEV` to return `NOTHING_REMAINING` exactly when `remaining` is empty and `PARTIAL_REMAINING` otherwise.
- [D] **SPOKE-VER-MODE-005**: When dispatched with mode `POST_QA`, the `forge-verifier` skill shall tell the verifier to return `PASS` only when the test suite loads without collection errors and, in a LID repository, `lid_check.py` reports no `UNTESTED` and no `UNKNOWN_ID`, accepting failing tests; otherwise `AUDIT_FAILED`.
- [D] **SPOKE-VER-MODE-006**: When dispatched with mode `VERIFY_FINAL`, the `forge-verifier` skill shall tell the verifier to return `PASS` only when the whole test suite passes, no check fails per SPOKE-VER-MODE-010, and, in a LID repository, `lid_check.py` reports no finding and the changed code does what its specs say.
- [D] **SPOKE-VER-MODE-007**: When dispatched with mode `RECONCILE`, the `forge-verifier` skill shall tell the verifier to judge scope by the `PRE_QA` criterion when `lifecycle.after_reconcile` is `PRE_QA`, `QA_SKEL`, `QA_FULL`, or `POST_QA`, by the `PRE_DEV` criterion otherwise, and to return both lists empty while `scope_manifest.assigned` is empty, always with `PASS`.
- [D] **SPOKE-VER-MODE-008**: The `forge-verifier` skill shall tell the verifier in mode `VERIFY_FINAL` to return `TESTS_FAILED` when tests fail, listing any failing check findings too, and `AUDIT_FAILED` when tests pass but a check fails, with the failing tests or findings in `escalation.details` specific enough to route a repair, and in every other mode never to return `TESTS_FAILED`.
- [D] **SPOKE-VER-MODE-009**: The `forge-verifier` skill shall tell the verifier never to change the repository.
- [D] **SPOKE-VER-MODE-010**: When dispatched with mode `VERIFY_FINAL`, the `forge-verifier` skill shall tell the verifier to report as a failing check each pattern of `scope_manifest.assigned` whose `forge_spoke.py scope` entry lists no commit in `commits`, naming the pattern in `escalation.details`.
