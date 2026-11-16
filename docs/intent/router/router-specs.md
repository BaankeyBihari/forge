# Router — EARS Specs

Design: `router-design.md`. Implementation: `reference/scripts/next_step.py`.

## Invocation

- [x] **ROUTE-CLI-001**: On every call, `next_step.py` shall run the workstream-state wake for the resolved workstream before applying any event.
- [x] **ROUTE-CLI-002**: If the wake status is not `OK`, then `next_step.py` shall return action `stop` with that status and change no state.
- [x] **ROUTE-CLI-003**: `next_step.py` shall accept at most one event, of type `start`, `result`, or `user`, from the `--event` file or from stdin when the argument is `-`.
- [x] **ROUTE-CLI-004**: When called with `--dry-run`, `next_step.py` shall return the decision it would make without writing `state.json`.
- [x] **ROUTE-CLI-005**: `next_step.py` shall print exactly one JSON decision to stdout, exit `0` for every decision, and exit non-zero only for a usage error.
- [x] **ROUTE-CLI-006**: `next_step.py` shall include `action`, `phase` (the phase after the decision), and a one-line `summary` in every decision.
- [x] **ROUTE-CLI-007**: `next_step.py` shall be the only writer of every `state.json` field except `identity`, `config`, `anchors.workstream_base_sha`, and `anchors.stale_shas`.
- [x] **ROUTE-CLI-008**: On every call, `next_step.py` shall run the wake, then apply the event if any, then handle drift, then check for a dirty tree, then dispatch, returning at the first step that yields a stop or a pause.

## Re-derivation

- [x] **ROUTE-REDO-001**: When called with no event while `lifecycle.awaiting` is set, `next_step.py` shall return the same pause again.
- [x] **ROUTE-REDO-002**: When called with no event while `lifecycle.dispatched` is set, no drift is reported, and ROUTE-DRIFT-008 does not apply, `next_step.py` shall return the invocation envelope stored in `lifecycle.invoke` again, unchanged.
- [x] **ROUTE-REDO-003**: When called with no event while `lifecycle.current_phase` is `INIT`, `next_step.py` shall return `stop` with `NOT_STARTED` and change no state.
- [x] **ROUTE-REDO-004**: When called with no event while `lifecycle.current_phase` is `DONE`, `next_step.py` shall return `done`.

## Dispatch

- [x] **ROUTE-DISP-001**: When `next_step.py` dispatches an agent, it shall record `{dispatch_id, agent, mode, base_sha}` in `lifecycle.dispatched` with a new unique `dispatch_id` and `base_sha` equal to `HEAD`.
- [x] **ROUTE-DISP-002**: When `next_step.py` dispatches an agent, the invocation envelope shall carry `dispatch_id`, `agent`, `mode`, `state_path`, `base_sha`, `assigned_scope`, `context_refs`, and `notes`, plus `stage` for `forge-planner`, `forge-qa`, and `forge-dev`.
- [x] **ROUTE-DISP-003**: `next_step.py` shall set the envelope's `stage` to `LID-S<n>` for LID planning stage `n`, `NAIVE-PLAN` for naive planning, `COLLAB-SKEL` for QA skeletons, `COLLAB-STUB` for dev stubs, `LID-S5` or `NAIVE-TESTS` for QA full tests in a LID or naive repository, `LID-S6` or `NAIVE-IMPL` for dev full implementation in a LID or naive repository, and `REPAIR` for repair dispatches.
- [x] **ROUTE-DISP-004**: When dispatching `forge-qa` or `forge-dev` in `QA_SKEL`, `DEV_STUB`, `QA_FULL`, or `DEV_FULL`, `next_step.py` shall set `assigned_scope` to `scope_manifest.remaining`.
- [x] **ROUTE-DISP-005**: When `next_step.py` dispatches an agent, it shall store the complete invocation envelope in `lifecycle.invoke`.
- [x] **ROUTE-DISP-006**: When `next_step.py` dispatches an agent while `anchors.last_known_head_sha` is `null`, it shall set `anchors.last_known_head_sha` to `HEAD`.
- [x] **ROUTE-DISP-007**: When dispatching `forge-verifier`, `next_step.py` shall set `assigned_scope` to `scope_manifest.assigned`.
- [ ] **ROUTE-DISP-008**: When re-dispatching the same phase while applying a `revise` answer, a `reject` answer, or an `approve` answer to a spoke's `human_code_approval`, `next_step.py` shall set `context_refs` to the replaced dispatch's `lifecycle.invoke.context_refs` followed by any new entries not already in it, except for `forge-reporter`, whose `context_refs` follow ROUTE-BUILD-033.

## Result envelopes

- [x] **ROUTE-ENV-001**: If a `result` event's `dispatch_id` or `agent` differs from `lifecycle.dispatched`, then `next_step.py` shall return `stop` with `ENVELOPE_INVALID` and change no state.
- [ ] **ROUTE-ENV-002**: If a `result` event's `status` is neither `ERROR` nor accepted in the current phase by ROUTE-ENV-014, or is excluded for the dispatch's agent or mode by ROUTE-ENV-012, then `next_step.py` shall return `stop` with `ENVELOPE_INVALID` and change no state.
- [ ] **ROUTE-ENV-014**: `next_step.py` shall accept, besides `ERROR`, only these statuses per phase: `RECON` and `PR_FETCH` `OK`; `PLAN_S1`–`PLAN_S4` `STAGE_COMPLETE`, `READY_FOR_QA`, `CONCERNS`; `PLAN` `READY_FOR_QA`, `CONCERNS`; `POST_PLAN` and `POST_QA` `PASS`, `AUDIT_FAILED`; `PRE_QA` and `PRE_DEV` `PARTIAL_REMAINING`, `NOTHING_REMAINING`; `QA_SKEL` `SKELETON_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED`; `QA_FULL` `TESTS_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED`; `DEV_STUB` `STUBS_COMMITTED`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED`; `DEV_FULL` `IMPLEMENTATION_COMPLETE`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED`; `VERIFY_FINAL` `PASS`, `AUDIT_FAILED`, `TESTS_FAILED`; `REVIEW_INTERNAL` `REVIEW_APPROVED`, `CHANGES_REQUESTED`; `PUBLISH` `PUBLISHED`, `MERGE_READY`, `MERGE_BLOCKED`, `PUBLISH_REFUSED`; `CI_WATCH` `CI_GREEN`, `CI_FAILED`, `CI_NONE`; `REPORT` `REPORT_DRAFTED`, `REPORT_SENT`; `RECONCILE` `PASS`; `COUNCIL` `COUNCIL_RULING`; `REPAIR` `REPAIR_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED`; `PR_REVIEW` `PR_REVIEW_READY`.
- [ ] **ROUTE-ENV-012**: `next_step.py` shall not accept `READY_FOR_QA` from a `strict` dispatch in `PLAN_S1` through `PLAN_S3`; `STAGE_COMPLETE` from a dispatch in `PLAN_S4` or any `auto` dispatch; `REPORT_SENT` from a `draft` dispatch; `REPORT_DRAFTED` from a `send` dispatch; `HUMAN_CODE_CHANGE_REQUESTED` from `forge-planner`; or `CONTRACT_MISMATCH` in `REPAIR` from an agent other than `forge-qa`.
- [ ] **ROUTE-ENV-013**: When `next_step.py` accepts a `result` event with status `PUBLISHED`, `PR_REVIEW_READY`, or `REPORT_DRAFTED`, it shall set `lifecycle.report_input` to the absolute path of `envelopes/<dispatch_id>.json` beside `state.json`.
- [x] **ROUTE-ENV-003**: If a `result` event's `head_sha` is neither the current `HEAD` nor an ancestor of it, then `next_step.py` shall return `stop` with `ENVELOPE_INVALID` and change no state.
- [x] **ROUTE-ENV-004**: If a `result` event from `forge-verifier`, `forge-reviewer`, `forge-scout`, `forge-council`, `forge-reporter`, or `forge-publisher` has a `head_sha` different from the dispatch's `base_sha`, then `next_step.py` shall return `stop` with `ENVELOPE_INVALID` and change no state.
- [ ] **ROUTE-ENV-005**: If a council `result` event's `ruling.route_to`, or the `route_to` of any entry of `ruling.options`, names an agent other than `forge-planner`, `forge-qa`, or `forge-dev`, then `next_step.py` shall return `stop` with `ENVELOPE_INVALID` and change no state.
- [x] **ROUTE-ENV-006**: When `next_step.py` accepts a `result` event, it shall clear `lifecycle.dispatched` and set `lifecycle.last_agent` and `lifecycle.last_status` from the envelope.
- [x] **ROUTE-ENV-007**: When `next_step.py` accepts a `result` event from `forge-planner`, `forge-qa`, or `forge-dev`, it shall set `anchors.last_known_head_sha` to the envelope's `head_sha`.
- [x] **ROUTE-ENV-008**: When an accepted `result` event from a committing agent spans commits that do not all carry `Forge-Agent` equal to that agent, `next_step.py` shall apply the envelope and then handle the other commits as drift.
- [x] **ROUTE-ENV-010**: When an accepted `result` event's `head_sha` is a proper ancestor of `HEAD`, `next_step.py` shall apply the envelope and then handle the commits after `head_sha` as drift.
- [x] **ROUTE-ENV-011**: When an accepted `result` event leads to a pause and its range holds commits handled as drift per ROUTE-ENV-008 or ROUTE-ENV-010, `next_step.py` shall leave `anchors.last_known_head_sha` at its value before the call.
- [x] **ROUTE-ENV-009**: When any agent returns status `ERROR`, `next_step.py` shall return `stop` with `AGENT_ERROR` and the envelope's `escalation.details`, changing only `lifecycle.dispatched`, `lifecycle.last_agent`, and `lifecycle.last_status`.

## Build flow

- [x] **ROUTE-BUILD-001**: When a `build`-flow workstream at `INIT` receives a `start` event, `next_step.py` shall go to `RECON` if `identity.issue` is set, otherwise to `PLAN_S1` in a LID repository or `PLAN` in a naive repository.
- [x] **ROUTE-BUILD-002**: When `forge-scout` returns `OK` in `RECON`, `next_step.py` shall go to the first planning phase and add the workstream's `scout/` directory to the planner's `context_refs`.
- [x] **ROUTE-BUILD-003**: While `config.plan_mode` is `strict`, `next_step.py` shall dispatch `forge-planner` with mode `strict` for one LID stage at a time; while it is `auto`, `next_step.py` shall dispatch mode `auto` from `PLAN_S1`, and in a naive repository mode `naive` in `PLAN`.
- [x] **ROUTE-BUILD-004**: When `forge-planner` returns `STAGE_COMPLETE` in `PLAN_S<n>`, `next_step.py` shall pause `plan_approval`; on `approve` it shall go to `PLAN_S<n+1>`.
- [ ] **ROUTE-BUILD-005**: When `forge-planner` returns `READY_FOR_QA`, `next_step.py` shall store the envelope's `scope.assigned`, which may be empty, in `scope_manifest.assigned` and pause `plan_approval`; on `approve` it shall set `anchors.plan_base_sha` to `HEAD`, `scope_manifest.remaining` to `scope_manifest.assigned`, and `scope_manifest.completed` to an empty list, and go to `POST_PLAN`.
- [x] **ROUTE-BUILD-006**: When the user answers `plan_approval` with `revise`, `next_step.py` shall re-dispatch the current planning phase with the user's `notes`.
- [ ] **ROUTE-BUILD-007**: When `forge-verifier` returns `PASS` in `POST_PLAN` while `scope_manifest.assigned` is not empty, `next_step.py` shall set `lifecycle.pass` to `1` and go to `PRE_QA` if `config.collaborative` is `true`, otherwise go to `QA_FULL`.
- [ ] **ROUTE-BUILD-035**: When `forge-verifier` returns `PASS` in `POST_PLAN` while `scope_manifest.assigned` is empty, `next_step.py` shall go to `VERIFY_FINAL`.
- [x] **ROUTE-BUILD-008**: When `forge-verifier` returns `PARTIAL_REMAINING` or `NOTHING_REMAINING` in `PRE_QA` or `PRE_DEV`, `next_step.py` shall replace `scope_manifest.completed` and `scope_manifest.remaining` with the envelope's `scope`.
- [x] **ROUTE-BUILD-009**: When `forge-verifier` returns `PARTIAL_REMAINING` in `PRE_QA`, `next_step.py` shall go to `QA_SKEL` in pass 1 and `QA_FULL` in pass 2.
- [x] **ROUTE-BUILD-010**: When `forge-verifier` returns `NOTHING_REMAINING` in `PRE_QA`, `next_step.py` shall go to `PRE_DEV`.
- [x] **ROUTE-BUILD-011**: When `forge-qa` returns `SKELETON_COMMITTED` in `QA_SKEL`, `next_step.py` shall pause `edit_window`; on `continue` it shall go to `PRE_DEV`.
- [x] **ROUTE-BUILD-012**: When `forge-verifier` returns `PARTIAL_REMAINING` in `PRE_DEV`, `next_step.py` shall go to `DEV_STUB` in pass 1 and `DEV_FULL` in pass 2.
- [x] **ROUTE-BUILD-013**: When `forge-verifier` returns `NOTHING_REMAINING` in `PRE_DEV`, `next_step.py` shall set `lifecycle.pass` to `2` and go to `PRE_QA` in pass 1, and go to `VERIFY_FINAL` in pass 2.
- [x] **ROUTE-BUILD-014**: When `forge-dev` returns `STUBS_COMMITTED` in `DEV_STUB`, `next_step.py` shall pause `edit_window`; on `continue` it shall set `lifecycle.pass` to `2` and go to `PRE_QA`.
- [x] **ROUTE-BUILD-015**: When `forge-qa` returns `TESTS_COMMITTED` in `QA_FULL`, `next_step.py` shall go to `POST_QA`.
- [x] **ROUTE-BUILD-016**: When `forge-verifier` returns `PASS` in `POST_QA` of a collaborative workstream (`config.collaborative` is `true`), `next_step.py` shall pause `edit_window`; on `continue` it shall go to `PRE_DEV`.
- [x] **ROUTE-BUILD-032**: When `forge-verifier` returns `PASS` in `POST_QA` of a sequential workstream (`config.collaborative` is `false`), `next_step.py` shall go to `DEV_FULL` without pausing.
- [x] **ROUTE-BUILD-017**: When `forge-dev` returns `IMPLEMENTATION_COMPLETE` in `DEV_FULL`, `next_step.py` shall go to `VERIFY_FINAL`.
- [x] **ROUTE-BUILD-018**: When `forge-verifier` returns `PASS` in `VERIFY_FINAL`, `next_step.py` shall run the gate's commit checks with the workstream's state over `anchors.workstream_base_sha..HEAD`, or over every first-parent commit reachable from `HEAD` when `anchors.workstream_base_sha` is `null`.
- [x] **ROUTE-BUILD-019**: When the `VERIFY_FINAL` commit checks pass, `next_step.py` shall set `anchors.verifier_pass_sha` to `HEAD` and go to `REVIEW_INTERNAL`.
- [ ] **ROUTE-BUILD-020**: If the `VERIFY_FINAL` commit checks report error findings that are all `HUMAN_CODE_MODIFIED`, then `next_step.py` shall pause `human_code_approval` presenting each finding's commit, path, and lines, and leave `anchors.verifier_pass_sha` unchanged.
- [ ] **ROUTE-BUILD-036**: When the user answers a `human_code_approval` raised by the commit checks with `approve`, `next_step.py` shall append to `approvals` one `{id, agent, paths, ranges, base_sha, approved_by: "user"}` per finding, with `agent` the commit's `Forge-Agent`, `paths` the finding's path, `ranges` the finding's lines, and `base_sha` the commit's parent, and dispatch `VERIFY_FINAL` again.
- [ ] **ROUTE-BUILD-037**: When the user answers a `human_code_approval` raised by the commit checks with `reject`, `next_step.py` shall return `stop` with `GATE_REJECTED`.
- [ ] **ROUTE-BUILD-038**: If the `VERIFY_FINAL` commit checks report any error finding other than `HUMAN_CODE_MODIFIED`, then `next_step.py` shall return `stop` with `GATE_UNREPAIRABLE` and the error findings, and leave `anchors.verifier_pass_sha` unchanged.
- [x] **ROUTE-BUILD-021**: When `forge-reviewer` returns `REVIEW_APPROVED` in `REVIEW_INTERNAL`, `next_step.py` shall set `anchors.reviewer_pass_sha` to `HEAD` and pause `publish_approval` with options `approve` (which requires an `option` of `push` or `merge`) and `hold`.
- [x] **ROUTE-BUILD-022**: When the user answers `publish_approval` with `approve` and option `push` or `merge`, `next_step.py` shall go to `PUBLISH` and dispatch `forge-publisher` with that mode; on `hold` it shall return the same pause.
- [x] **ROUTE-BUILD-023**: When `forge-publisher` returns `PUBLISHED` in `PUBLISH`, `next_step.py` shall go to `REPORT`.
- [x] **ROUTE-BUILD-024**: When `forge-publisher` returns `MERGE_READY` in `PUBLISH`, `next_step.py` shall pause `manual_merge`.
- [x] **ROUTE-BUILD-025**: When the user answers `manual_merge` with `done`, `next_step.py` shall return `close` if `HEAD` is an ancestor of the local `identity.base_branch`, and otherwise return the `manual_merge` pause again with that finding.
- [x] **ROUTE-BUILD-026**: When `forge-publisher` returns `MERGE_BLOCKED` or `PUBLISH_REFUSED` in `PUBLISH`, `next_step.py` shall return `stop` with that status and the publisher's findings.
- [ ] **ROUTE-BUILD-027**: When `forge-reporter` returns `REPORT_DRAFTED` in `REPORT`, `next_step.py` shall pause `report_approval` with `lifecycle.report_input` as the pause's only `context_refs` entry; on `approve` it shall dispatch `forge-reporter` with mode `send`, and on `revise` dispatch mode `draft` with the user's `notes`.
- [ ] **ROUTE-BUILD-033**: When dispatching `forge-reporter`, `next_step.py` shall set `context_refs` to exactly `[lifecycle.report_input]`.
- [ ] **ROUTE-BUILD-034**: When `forge-qa` returns `CONTRACT_MISMATCH` in `QA_SKEL`, `QA_FULL`, or `REPAIR`, `next_step.py` shall raise blocker `CONTRACT_MISMATCH`.
- [x] **ROUTE-BUILD-028**: When a `build`-flow workstream's reporter returns `REPORT_SENT`, or the user answers its `report_approval` with `skip`, `next_step.py` shall go to `CI_WATCH`.
- [x] **ROUTE-BUILD-029**: When `forge-scout` returns `CI_GREEN` in `CI_WATCH`, `next_step.py` shall return `close`.
- [x] **ROUTE-BUILD-030**: When `forge-scout` returns `CI_NONE` in `CI_WATCH`, `next_step.py` shall pause `ci_none`; on `approve` it shall return `close`, and on `wait` re-dispatch `CI_WATCH`.
- [ ] **ROUTE-BUILD-031**: When `forge-verifier` returns `PASS` in `RECONCILE`, `next_step.py` shall replace `scope_manifest.completed` and `scope_manifest.remaining` with the envelope's `scope`, go to `lifecycle.after_reconcile`, and set `lifecycle.after_reconcile` to `null`.

## Pull request review flow

- [x] **ROUTE-PR-001**: When a `pr_review`-flow workstream at `INIT` receives a `start` event, `next_step.py` shall go to `PR_FETCH` and dispatch `forge-scout` with mode `PR_FETCH`.
- [x] **ROUTE-PR-002**: When `forge-scout` returns `OK` in `PR_FETCH`, `next_step.py` shall go to `PR_REVIEW` and dispatch `forge-reviewer` with mode `external_pr`, adding the fetched diff and threads to `context_refs`.
- [x] **ROUTE-PR-003**: When `forge-reviewer` returns `PR_REVIEW_READY` in `PR_REVIEW`, `next_step.py` shall go to `REPORT` and dispatch `forge-reporter` with mode `draft`.
- [x] **ROUTE-PR-004**: When a `pr_review`-flow workstream's reporter returns `REPORT_SENT`, or the user answers its `report_approval` with `skip`, `next_step.py` shall return `close`.

## Entry guards

- [x] **ROUTE-GUARD-001**: If `next_step.py` would dispatch `REVIEW_INTERNAL` while `anchors.verifier_pass_sha` differs from `HEAD`, then it shall go to `VERIFY_FINAL` instead.
- [x] **ROUTE-GUARD-002**: If `next_step.py` would dispatch `PUBLISH` while `anchors.verifier_pass_sha` or `anchors.reviewer_pass_sha` differs from `HEAD`, then it shall go to `VERIFY_FINAL` instead.

## Blockers and council

- [x] **ROUTE-BLK-001**: When an accepted envelope has status `CONCERNS`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `AUDIT_FAILED`, `TESTS_FAILED`, `CI_FAILED`, or `CHANGES_REQUESTED`, `next_step.py` shall append a blocker `{id, kind, agent, phase, details}` to `blockers` and set `lifecycle.resume_phase` per the blocker kind.
- [ ] **ROUTE-BLK-002**: When a blocker is raised outside `REPAIR`, `next_step.py` shall set `lifecycle.resume_phase` to the blocked planning phase for `CONCERNS`, to the blocked dev or QA phase for `TEST_CHANGE_REQUESTED` and `CONTRACT_MISMATCH`, to the failed verifier phase for `AUDIT_FAILED`, and to `VERIFY_FINAL` for `TESTS_FAILED`, `CI_FAILED`, and `CHANGES_REQUESTED`.
- [ ] **ROUTE-BLK-025**: When a blocker is raised in `REPAIR`, `next_step.py` shall leave `lifecycle.resume_phase` unchanged, keep the interrupted item in `lifecycle.repair_queue`, and put the repairs its ruling queues before every existing item.
- [x] **ROUTE-BLK-003**: When a blocker of kind `CONCERNS`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `AUDIT_FAILED`, `TESTS_FAILED`, or `CI_FAILED` is raised, `next_step.py` shall go to `COUNCIL` and write the blocker to `council/<blocker id>.json` in the workstream's state directory and dispatch `forge-council` with the blocker kind as mode and that file first in `context_refs`.
- [ ] **ROUTE-BLK-021**: When dispatching `forge-council`, `next_step.py` shall follow the blocker file in `context_refs` with the `context_refs` of the dispatch that raised the blocker.
- [x] **ROUTE-BLK-004**: When dispatching `forge-council`, `next_step.py` shall set the invocation envelope's `panel` to `pm`, `architect`, `critic` for `CONCERNS`; `senior-dev`, `architect` for `TEST_CHANGE_REQUESTED` and `CONTRACT_MISMATCH`; `architect`, `senior-dev` for `AUDIT_FAILED`; and `debugger`, `senior-dev` for `TESTS_FAILED` and `CI_FAILED`.
- [x] **ROUTE-BLK-018**: When `forge-council` returns `COUNCIL_RULING`, `next_step.py` shall store the envelope's `ruling` in the blocker's `ruling` field, and apply that stored ruling when the user approves `council_signoff`.
- [x] **ROUTE-BLK-005**: When `forge-council` returns `COUNCIL_RULING` for a `CONCERNS` blocker, `next_step.py` shall pause `council_signoff`.
- [ ] **ROUTE-BLK-006**: When `forge-council` returns `COUNCIL_RULING` for a blocker other than `CONCERNS`, `next_step.py` shall, if `config.council_auto_resolve` is `true` and the ruling's `requires_user` is `false`, append `{id, blocker_id, panel, option, approved_by: "auto"}` to `council_decisions` with `option` the ruling's `recommended` and apply the recommended option (its top-level `route_to` and `delta`) without pausing, and otherwise pause `council_signoff`.
- [ ] **ROUTE-BLK-007**: When the user answers `council_signoff` with `approve` and an `option` that is the `label` of an entry of the stored ruling's `options`, `next_step.py` shall append `{id, blocker_id, panel, option, approved_by: "user"}` to `council_decisions`, with `panel` from the ruling's `panel`, and apply that option's `route_to` and `delta`.
- [ ] **ROUTE-BLK-022**: If the user answers `council_signoff` with `approve` and an `option` that is not the `label` of an entry of the stored ruling's `options`, then `next_step.py` shall return `stop` with `EVENT_INVALID` and change no state.
- [x] **ROUTE-BLK-008**: When the user answers `council_signoff` with `reject`, `next_step.py` shall re-dispatch `forge-council` for the same blocker with the user's `notes`.
- [ ] **ROUTE-BLK-009**: When applying a ruling for `CONCERNS`, `next_step.py` shall re-dispatch the blocked planning phase with `context_refs` set to the blocked dispatch's `context_refs` followed by the ruling's `matrix_ref`, and the applied option's `delta` in `notes`.
- [ ] **ROUTE-BLK-010**: When applying a ruling for `TEST_CHANGE_REQUESTED` or `CONTRACT_MISMATCH` whose applied option has a non-empty `route_to`, `next_step.py` shall queue a repair for each agent in that `route_to`, in the order given, per ROUTE-BLK-026, and go to `REPAIR`.
- [ ] **ROUTE-BLK-023**: When applying a ruling for `TEST_CHANGE_REQUESTED` or `CONTRACT_MISMATCH` whose applied option has an empty `route_to`, `next_step.py` shall remove the blocker from `blockers` and re-dispatch `lifecycle.resume_phase` with the option's `delta` in `notes`.
- [ ] **ROUTE-BLK-011**: When applying a ruling for `AUDIT_FAILED`, `TESTS_FAILED`, or `CI_FAILED` whose applied option has a non-empty `route_to`, `next_step.py` shall queue a repair for each agent in that `route_to`, in the order given, per ROUTE-BLK-026, and go to `REPAIR`.
- [ ] **ROUTE-BLK-026**: When queuing a repair from a council ruling, `next_step.py` shall give the queue item the applied option's `delta` as `notes` and `context_refs` set to the ruling's `matrix_ref` followed by the blocked dispatch's `context_refs`.
- [ ] **ROUTE-BLK-019**: When applying a ruling for `AUDIT_FAILED` or `TESTS_FAILED` whose applied option has an empty `route_to`, `next_step.py` shall remove the blocker from `blockers` and go to `lifecycle.resume_phase`.
- [ ] **ROUTE-BLK-012**: When applying a ruling for `CI_FAILED` whose applied option has an empty `route_to`, `next_step.py` shall remove the blocker from `blockers`, go to `CI_WATCH`, and dispatch `forge-scout` with mode `CI_RERUN`.
- [x] **ROUTE-BLK-013**: When a `CHANGES_REQUESTED` blocker is raised, `next_step.py` shall queue one repair per `route_to` agent among the envelope's `escalation.findings`, ordered `forge-planner`, `forge-qa`, `forge-dev`, each carrying that agent's findings, without dispatching `forge-council`.
- [x] **ROUTE-BLK-014**: When a `CHANGES_REQUESTED` blocker has a finding routed to `forge-planner`, `next_step.py` shall pause `findings_signoff` presenting the planner findings, and on `approve` go to `REPAIR`.
- [x] **ROUTE-BLK-015**: While in `REPAIR`, `next_step.py` shall dispatch the head of `lifecycle.repair_queue` with mode `repair`, its `notes`, and its `context_refs`.
- [ ] **ROUTE-BLK-024**: When `forge-planner` returns `REPAIR_COMMITTED` with `scope.assigned`, `next_step.py` shall replace `scope_manifest.assigned` with it, keep in `scope_manifest.completed` only patterns still assigned, set `scope_manifest.remaining` to the other assigned patterns, and set `lifecycle.resume_phase` to `POST_PLAN`.
- [x] **ROUTE-BLK-016**: When the queue head's agent returns `REPAIR_COMMITTED`, `next_step.py` shall remove it from `lifecycle.repair_queue` and dispatch the next item, or go to `lifecycle.resume_phase` when the queue is empty.
- [ ] **ROUTE-BLK-017**: When `next_step.py` applies a `CONCERNS` ruling, it shall remove the resolved blocker from `blockers`.
- [ ] **ROUTE-BLK-020**: When `next_step.py` queues a repair, it shall record in the queue item the `blocker_id` of the blocker being resolved, and when a queue item completes and no remaining item has the same `blocker_id`, remove that blocker from `blockers`.

## Human-code approvals

- [ ] **ROUTE-HC-001**: When `forge-qa` or `forge-dev` returns `HUMAN_CODE_CHANGE_REQUESTED`, `next_step.py` shall pause `human_code_approval` presenting the escalation's `paths` and line ranges.
- [ ] **ROUTE-HC-002**: When the user answers a `human_code_approval` raised by a spoke with `approve`, `next_step.py` shall append `{id, agent, paths, ranges, base_sha, approved_by: "user"}` to `approvals`, with `base_sha` equal to the escalating envelope's `head_sha`, and re-dispatch the same phase with `notes` set to the replaced dispatch's `notes` followed by the approval ID.
- [ ] **ROUTE-HC-003**: When the user answers a `human_code_approval` raised by a spoke with `reject`, `next_step.py` shall re-dispatch the same phase with `notes` set to the replaced dispatch's `notes` followed by the user's `notes`.

## Drift and dirty trees

- [x] **ROUTE-DRIFT-001**: When the wake reports drift `behind` or `diverged`, or drift `ahead` with any commit not accounted for by an envelope accepted in the same call, `next_step.py` shall set `anchors.verifier_pass_sha` and `anchors.reviewer_pass_sha` to `null` and `anchors.last_known_head_sha` to `HEAD`.
- [ ] **ROUTE-DRIFT-002**: When handling drift per ROUTE-DRIFT-001 and the next phase is `PRE_QA`, `PRE_DEV`, `POST_QA`, `VERIFY_FINAL`, or `RECONCILE`, `next_step.py` shall continue to that phase; otherwise it shall set `lifecycle.after_reconcile` to `VERIFY_FINAL` when the next phase is `REPORT` or `CI_WATCH` and to the next phase otherwise, and go to `RECONCILE`.
- [ ] **ROUTE-DRIFT-009**: When handling drift per ROUTE-DRIFT-001 in a `pr_review`-flow workstream, `next_step.py` shall return `stop` with `REVIEW_WORKTREE_MOVED` instead.
- [ ] **ROUTE-DRIFT-010**: `next_step.py` shall leave `lifecycle.resume_phase` unchanged when handling drift.
- [ ] **ROUTE-DRIFT-007**: When drift per ROUTE-DRIFT-001 is reported on a call that would return `close` in a `build`-flow workstream, `next_step.py` shall set `lifecycle.after_reconcile` to `VERIFY_FINAL` and go to `RECONCILE` instead.
- [x] **ROUTE-DRIFT-003**: When handling drift per ROUTE-DRIFT-001 while `lifecycle.dispatched` is set, `next_step.py` shall clear `lifecycle.dispatched` before routing to `RECONCILE`.
- [x] **ROUTE-DRIFT-004**: While `lifecycle.awaiting` is set, `next_step.py` shall defer drift handling until the pause is answered.
- [x] **ROUTE-DRIFT-005**: If the wake reports a dirty tree when `next_step.py` would dispatch any agent other than `forge-scout` or `forge-reporter`, then it shall pause `dirty_tree` with options `checkpoint` and `stash` instead of dispatching.
- [x] **ROUTE-DRIFT-008**: If the wake reports a dirty tree when `next_step.py` would return an outstanding dispatch again for an agent other than `forge-scout` or `forge-reporter`, then it shall pause `dirty_tree` instead, stating in the question that the changes may be the dispatched agent's unfinished work.
- [x] **ROUTE-DRIFT-006**: When the user answers `dirty_tree`, `next_step.py` shall clear the pause and re-check the tree on the same call before dispatching.

## Attempt limits

- [x] **ROUTE-LIMIT-001**: When `next_step.py` dispatches into a phase, it shall increment `lifecycle.attempts` for that phase, resetting the count to `1` when entering the phase from a different phase or, in `REPAIR`, when dispatching a new `repair_queue` head.
- [x] **ROUTE-LIMIT-002**: If `next_step.py` would dispatch into a phase whose `lifecycle.attempts` count is already `3`, other than while applying a `user` event, then it shall pause `attempt_limit` with options `retry` and `stop` instead.
- [x] **ROUTE-LIMIT-003**: When `next_step.py` raises a blocker, it shall increment `lifecycle.blocker_counts` for that kind.
- [x] **ROUTE-LIMIT-004**: If `next_step.py` would raise a fourth blocker of the same kind in a workstream, then it shall pause `attempt_limit` with options `retry` and `stop` instead of routing the blocker.
- [x] **ROUTE-LIMIT-005**: When the user answers `attempt_limit` with `retry`, `next_step.py` shall reset the exceeded count and continue; on `stop` it shall return `stop` with `ATTEMPT_LIMIT`.
- [x] **ROUTE-LIMIT-006**: When `next_step.py` dispatches into a phase while applying a `user` event, it shall set `lifecycle.attempts` for that phase to `1`.

## Pauses

- [x] **ROUTE-PAUSE-001**: When `next_step.py` pauses, it shall record `{kind, payload}` in `lifecycle.awaiting` and return `awaiting`, `question`, `options`, and `context_refs`.
- [x] **ROUTE-PAUSE-002**: If a `user` event's `awaiting` differs from `lifecycle.awaiting.kind`, or its `decision` is not one of that pause's options, then `next_step.py` shall return `stop` with `EVENT_INVALID` and change no state.
