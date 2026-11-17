# Spoke Protocol — EARS Specs

Design: `protocol-design.md`. Implementation: `reference/scripts/forge_spoke.py`, `reference/skills/forge-spoke/SKILL.md`, and `claude/agents/forge-*.md`.

Specs under *Skill instructions* describe what the `forge-spoke` skill tells every spoke to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do. Every other spec is tested with pytest.

## Invocation

- [x] **SPOKE-PROTO-CLI-001**: `forge_spoke.py` shall provide the subcommands `invocation`, `commit`, `finish`, and `scope`, print exactly one JSON object with a `status` to stdout, and exit `0` for every status.
- [x] **SPOKE-PROTO-CLI-002**: If `forge_spoke.py` is called with an unknown subcommand or missing or invalid arguments, then it shall exit `2` with a message on stderr.
- [x] **SPOKE-PROTO-CLI-003**: If the `--state` file is missing or does not parse as a JSON object, then `forge_spoke.py` shall return `status` `STATE_INVALID` with a `detail` saying which.
- [x] **SPOKE-PROTO-CLI-004**: If `invocation`, `commit`, or `finish` is given a `--dispatch` that differs from `lifecycle.dispatched.dispatch_id`, or `lifecycle.dispatched` is `null`, then `forge_spoke.py` shall return `status` `NOT_DISPATCHED` and change nothing.
- [x] **SPOKE-PROTO-CLI-005**: `forge_spoke.py` shall run every git command in `identity.worktree_path`.
- [x] **SPOKE-PROTO-CLI-006**: `forge_spoke.py` shall use only the Python standard library and external `git` commands.

## `invocation`

- [x] **SPOKE-PROTO-INV-001**: When `invocation` runs for the outstanding dispatch, `forge_spoke.py` shall return `status` `OK` with `invoke` set to `lifecycle.invoke`, `phase` set to `lifecycle.current_phase`, and `state` set to the whole `state.json` object.
- [x] **SPOKE-PROTO-INV-002**: `forge_spoke.py invocation` shall not create, change, or delete any file.

## `commit`

- [x] **SPOKE-PROTO-COMMIT-001**: When `commit` runs for a dispatch of `forge-planner`, `forge-qa`, or `forge-dev` with staged changes all inside that agent's write boundary, `forge_spoke.py` shall commit the index with the text of `--message-file` followed by the trailers `Forge-Agent: <invoke.agent>`, `Forge-Stage: <--stage or invoke.stage>`, `Forge-Mode: <invoke.mode>`, and `Forge-Input-SHA: <invoke.base_sha>`, and return `status` `COMMITTED` with the new `HEAD` in `sha`.
- [x] **SPOKE-PROTO-COMMIT-002**: If `commit` runs for a dispatch whose `invoke.agent` is not `forge-planner`, `forge-qa`, or `forge-dev`, then `forge_spoke.py` shall return `status` `NOT_A_BUILDER` and commit nothing.
- [x] **SPOKE-PROTO-COMMIT-003**: If `commit` runs while the index matches `HEAD`, then `forge_spoke.py` shall return `status` `NOTHING_STAGED` and commit nothing.
- [x] **SPOKE-PROTO-COMMIT-004**: If any staged path, counting both sides of a rename, is outside the agent's write boundary under the gates path classes and the workstream's `config.test_patterns`, then `forge_spoke.py commit` shall return `status` `BOUNDARY_VIOLATION` listing those paths in `paths` and commit nothing.
- [x] **SPOKE-PROTO-COMMIT-005**: If `commit` is given `--stage` for a dispatch other than `forge-planner` in mode `auto` or in mode `strict` with a non-null `invoke.notes`, or with a value other than `LID-S1`, `LID-S2`, `LID-S3`, or `LID-S4`, then `forge_spoke.py` shall return `status` `STAGE_INVALID` and commit nothing.
- [x] **SPOKE-PROTO-COMMIT-006**: If `commit`'s `--message-file` cannot be read or is empty, then `forge_spoke.py` shall exit `2` with a message on stderr and commit nothing.
- [ ] **SPOKE-PROTO-COMMIT-007**: `forge_spoke.py commit` shall commit only what is already staged, without staging or unstaging any path other than the spec files it changes per SPOKE-PROTO-MARK-001 and SPOKE-PROTO-MARK-002.

## Spec markers

- [ ] **SPOKE-PROTO-MARK-001**: When `commit` runs for a `forge-qa` or `forge-dev` dispatch whose `invoke.mode` is `full` or `repair` in a workstream whose `config.repo_mode` is `LID`, and the checks of SPOKE-PROTO-COMMIT-003 and SPOKE-PROTO-COMMIT-004 pass, `forge_spoke.py` shall change to `[x]` the marker of each workstream spec whose line is `[ ]`, that has no `DUPLICATE_ID` finding, and that has both a test citation and a code tag, per SPOKE-PROTO-MARK-007.
- [ ] **SPOKE-PROTO-MARK-002**: In the same case as SPOKE-PROTO-MARK-001, `forge_spoke.py commit` shall change to `[ ]` the marker of each workstream spec whose line is `[x]`, that has no `DUPLICATE_ID` finding, and that lacks a test citation or a code tag, per SPOKE-PROTO-MARK-007.
- [ ] **SPOKE-PROTO-MARK-003**: `forge_spoke.py commit` shall decide workstream specs, `DUPLICATE_ID` findings, test citations, and code tags for SPOKE-PROTO-MARK-001 and SPOKE-PROTO-MARK-002 by the rules of SPOKE-VER-LID-001 through SPOKE-VER-LID-004, reading file contents from `HEAD` with the staged changes applied and never from unstaged edits.
- [ ] **SPOKE-PROTO-MARK-004**: When `commit` returns `COMMITTED` in the case of SPOKE-PROTO-MARK-001, `forge_spoke.py` shall include `marked` and `unmarked`, the IDs it changed to `[x]` and to `[ ]`, each an empty list when it changed none, and in every other case it shall include neither key.
- [ ] **SPOKE-PROTO-MARK-005**: If committing fails after `commit` changed markers, then `forge_spoke.py` shall restore each changed spec file in the index and the worktree to its state before `commit` ran.
- [ ] **SPOKE-PROTO-MARK-007**: When `forge_spoke.py commit` changes a spec's marker, it shall change that spec's line, found by its ID, in both the staged copy and the worktree copy of the spec file, leave every other line of both copies as it was, and include the staged copy in the commit.
- [ ] **SPOKE-PROTO-MARK-006**: `forge_spoke.py commit` shall change no spec marker for a `forge-planner` dispatch, a `skeleton` or `stub` dispatch, or a workstream whose `config.repo_mode` is not `LID`.

## `finish`

- [x] **SPOKE-PROTO-FIN-001**: If `finish` runs while `envelopes/<dispatch_id>.json` beside `state.json` does not exist, then `forge_spoke.py` shall return `status` `ENVELOPE_MISSING` with the expected absolute path in `path`.
- [x] **SPOKE-PROTO-FIN-002**: When `finish` completes an envelope, `forge_spoke.py` shall set `dispatch_id` and `agent` from `lifecycle.dispatched`.
- [x] **SPOKE-PROTO-FIN-003**: When `finish` completes an envelope, `forge_spoke.py` shall set `head_sha` to the current `HEAD` for `forge-planner`, `forge-qa`, and `forge-dev`, and to `lifecycle.dispatched.base_sha` for every other agent.
- [x] **SPOKE-PROTO-FIN-004**: When `finish` completes an envelope, `forge_spoke.py` shall set `files_touched` to the paths changed by the first-parent commits in `base_sha..head_sha` whose `Forge-Agent` trailer equals the dispatched agent, or to an empty list for an agent that does not commit.
- [x] **SPOKE-PROTO-FIN-005**: If the envelope file is not a JSON object with a string `status` and a string `summary`, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-006**: If the envelope's `status` is not `ERROR` and is not accepted by the router for the dispatched agent in the current phase and mode (router specs ROUTE-ENV-012 and ROUTE-ENV-014), then `forge_spoke.py finish` shall report a problem, using the router's own status tables.
- [x] **SPOKE-PROTO-FIN-007**: If the envelope's `status` is not `ERROR`, the dispatched agent is `forge-planner`, `forge-qa`, or `forge-dev`, and the worktree has uncommitted changes or untracked files that are not ignored, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-008**: If the envelope's `status` is not `ERROR`, the dispatched agent does not commit, and a commit in `base_sha..HEAD` carries `Forge-Agent` equal to that agent, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-009**: If the envelope's `status` is `ERROR`, `CONCERNS`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `AUDIT_FAILED`, `TESTS_FAILED`, `CI_FAILED`, `CHANGES_REQUESTED`, `HUMAN_CODE_CHANGE_REQUESTED`, `PUBLISH_REFUSED`, or `MERGE_BLOCKED` and `escalation.details` is not a non-empty string, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-010**: If the envelope's `status` is `HUMAN_CODE_CHANGE_REQUESTED` and `escalation.paths` is not a list of strings or `escalation.ranges` is not an object mapping each listed path to a list of `[start, end]` integer pairs, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-011**: If the envelope's `status` is `CHANGES_REQUESTED` and `escalation.findings` is not a non-empty list of objects with `route_to` one of `forge-planner`, `forge-qa`, `forge-dev` and a non-empty string `detail`, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-012**: If the envelope's `status` is `READY_FOR_QA` and `scope.assigned` is not a list of strings, or the status is `REPAIR_COMMITTED` from `forge-planner` and `scope.assigned` is present but not a list of strings, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-013**: If the envelope's `status` is `PARTIAL_REMAINING`, `NOTHING_REMAINING`, or `PASS` in `RECONCILE`, and `scope.completed` and `scope.remaining` are not lists of strings that together hold each pattern of `scope_manifest.assigned` exactly once and nothing else, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-014**: If the envelope's `status` is `COUNCIL_RULING` and `ruling` lacks any of: `matrix_ref` naming an existing file under the workstream's `council/` directory by absolute path; boolean `requires_user`; `panel` as a non-empty list of strings; `options` as a non-empty list of `{label, summary, route_to, delta}` with distinct string labels, string `summary` and `delta`, and `route_to` a list of `forge-planner`, `forge-qa`, `forge-dev`; `recommended` equal to one option's `label`; and `route_to` and `delta` equal to the recommended option's, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-015**: If the envelope's `status` is `PUBLISHED`, `MERGE_READY`, `MERGE_BLOCKED`, or `PUBLISH_REFUSED` and `publish_target` differs from `invoke.mode`, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-016**: If the envelope's `status` is `PR_REVIEW_READY` and `review` is not an object with `verdict` one of `approve`, `request_changes`, `comment`, a string `body`, and `comments` a list of `{path, line, body}` with a string `path`, a positive integer `line`, and a string `body`, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-017**: If the envelope's `status` is `REPORT_DRAFTED` and `report` is not an object whose `kind` is `pr_create` with string `title`, `body`, `base`, `head`; `pr_update` with integer `number` and string `title`, `body`; or `pr_review` with integer `number`, string `commit_id`, `event` one of `APPROVE`, `REQUEST_CHANGES`, `COMMENT`, string `body`, and `comments` a list of `{path, line, side, body}`, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-018**: If the envelope's `status` is `REPORT_SENT` and `report.url` is not a non-empty string, then `forge_spoke.py finish` shall report a problem.
- [x] **SPOKE-PROTO-FIN-019**: When `finish` finds no problem, `forge_spoke.py` shall write the completed envelope back to the same file and return `status` `FINISHED`.
- [x] **SPOKE-PROTO-FIN-020**: When `finish` finds one or more problems, `forge_spoke.py` shall return `status` `INVALID` with every problem in `problems` and leave the envelope file unchanged.

## `scope`

- [x] **SPOKE-PROTO-SCOPE-001**: When `scope` runs, `forge_spoke.py` shall return `status` `OK` with one `patterns` entry per pattern of `scope_manifest.assigned`, in order, each listing in `files` the tracked paths at `HEAD` that match the pattern, with their gates path class.
- [x] **SPOKE-PROTO-SCOPE-002**: `forge_spoke.py scope` shall list in each `patterns` entry's `commits` the first-parent commits in `anchors.plan_base_sha..HEAD` that change a path matching the pattern, oldest first, each with `sha`, `kind` (`human` or `forge`), `agent` (the `Forge-Agent` value or `null`), and the matching `paths` it changes.
- [x] **SPOKE-PROTO-SCOPE-003**: While `anchors.plan_base_sha` is `null`, `forge_spoke.py scope` shall return every `commits` list empty.
- [x] **SPOKE-PROTO-SCOPE-004**: `forge_spoke.py scope` shall return `completed` and `remaining` equal to `scope_manifest.completed` and `scope_manifest.remaining`.
- [x] **SPOKE-PROTO-SCOPE-005**: `forge_spoke.py scope` shall not create, change, or delete any file.

## Agent profiles

- [x] **SPOKE-PROTO-PROF-001**: `claude/agents/forge-planner.md` shall set `model: opus`, tools Read, Grep, Glob, Bash, Write, Edit, and preload the skills `forge-spoke`, `forge-memory`, `forge-planner`, and `linked-intent-dev`.
- [x] **SPOKE-PROTO-PROF-002**: `claude/agents/forge-qa.md` and `claude/agents/forge-dev.md` shall each set `model: sonnet`, tools Read, Grep, Glob, Bash, Write, Edit, and preload `forge-spoke`, `forge-memory`, and the skill of the same name as the agent.
- [x] **SPOKE-PROTO-PROF-003**: `claude/agents/forge-verifier.md` shall set `model: sonnet`, tools Read, Grep, Glob, Bash, Write, and preload `forge-spoke`, `forge-memory`, and `forge-verifier`.
- [x] **SPOKE-PROTO-PROF-004**: `claude/agents/forge-reviewer.md` shall set `model: opus`, tools Read, Grep, Glob, Bash, Write, and preload `forge-spoke`, `forge-memory`, and `forge-reviewer`.
- [x] **SPOKE-PROTO-PROF-005**: `claude/agents/forge-scout.md` and `claude/agents/forge-reporter.md` shall each set `model: haiku`, tools Read, Grep, Glob, Bash, Write, and preload `forge-spoke`, `forge-memory`, and the skill of the same name as the agent.
- [x] **SPOKE-PROTO-PROF-006**: `claude/agents/forge-publisher.md` shall set `model: haiku`, tools Read, Bash, Write, and preload `forge-spoke`, `forge-memory`, and `forge-publisher`.
- [x] **SPOKE-PROTO-PROF-007**: `claude/agents/forge-council.md` shall set `model: sonnet`, tools Read, Grep, Glob, Bash, Write, Agent, and preload `forge-spoke`, `forge-memory`, and `forge-council`.
- [x] **SPOKE-PROTO-PROF-008**: For each of `pm`, `architect`, `sec-ops`, `debugger`, `senior-dev`, and `critic`, `claude/agents/forge-council-<persona>.md` shall set `model: opus` and tools Read, Grep, Glob only, and preload no skill.
- [x] **SPOKE-PROTO-PROF-009**: The body of each spoke profile other than the persona profiles shall name the agent and tell it to follow the `forge-spoke` skill for the dispatch it receives.

## Skill instructions

- [D] **SPOKE-PROTO-SKILL-001**: The `forge-spoke` skill shall tell every spoke to run `forge_spoke.py invocation` before anything else, and to end its turn saying the dispatch was replaced when it returns `NOT_DISPATCHED`.
- [D] **SPOKE-PROTO-SKILL-002**: The `forge-spoke` skill shall tell every spoke to load its memory per the `forge-memory` skill after reading the invocation and to read every file in `context_refs` before starting work.
- [D] **SPOKE-PROTO-SKILL-003**: The `forge-spoke` skill shall tell committing spokes to stage with `git add` and commit only with `forge_spoke.py commit`, never writing Forge trailers themselves.
- [D] **SPOKE-PROTO-SKILL-004**: The `forge-spoke` skill shall tell every spoke to write the envelope's judgment fields to `envelopes/<dispatch_id>.json` with the file-write tool and run `forge_spoke.py finish` until it returns `FINISHED`, fixing each reported problem it can — editing the envelope, committing work it meant to commit, removing files its own test runs left — and reporting a problem it cannot fix that way in an `ERROR` envelope instead.
- [D] **SPOKE-PROTO-SKILL-005**: The `forge-spoke` skill shall tell every spoke to end with a one-paragraph reply to the Hub carrying nothing the router needs, and never to write `state.json`.
- [D] **SPOKE-PROTO-SKILL-006**: The `forge-spoke` skill shall tell spokes that run tests to use `config.test_command` when set, otherwise to determine the command from the repository and save it to their memory partition as a tooling quirk, to return `ERROR` when no working command can be found, and to remove untracked files their test runs leave that the repository does not ignore.
- [D] **SPOKE-PROTO-SKILL-007**: The `forge-spoke` skill shall tell `forge-qa` and `forge-dev` to run `verify_gate.py human-lines --agent <self>` before changing an existing file, and instead of changing a protected line to commit valid progress, run `human-lines` again at that `HEAD`, and return `HUMAN_CODE_CHANGE_REQUESTED` with the paths and line ranges as they stand at that `HEAD`.
- [D] **SPOKE-PROTO-SKILL-008**: The `forge-spoke` skill shall tell `forge-qa` and `forge-dev`, when re-dispatched after a rejected human-code change, to return `ERROR` saying so when the work cannot be done without those lines.
