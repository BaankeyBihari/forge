---
parent: high-level-design
prefix: ROUTE
---

# Router

## Context and Design Philosophy

`next_step.py` is the only place Forge decides what happens next. Given a workstream's `state.json`, the repository's current condition, and at most one new event, it returns exactly one decision — dispatch a spoke, pause for the user, stop, close, or finish — and persists the state that decision implies. The Hub carries decisions out; it never chooses a phase, an agent, or a mode itself.

Three properties follow from the HLD tenets:

- **One writer.** Apart from the identity, config, and `anchors.stale_shas` written by `resolve_state.py`, every field of `state.json` is written here. Spokes return envelopes; they never touch state.
- **Pure decision, testable.** The decision is a function of state, repository condition, and event. The whole transition table runs under pytest against temporary git repositories.
- **Stop over guess.** Any input the router cannot account for — an envelope from an agent it did not dispatch, a HEAD it did not expect, a status not allowed in the current phase — produces a stop with a named reason and leaves state unchanged.

## Invocation

```text
next_step.py --holder-pid PID --holder-session ID [--workstream SLUG] [--event FILE|-] [--dry-run]
```

Every call first runs the workstream-state wake (same identity, lock, and drift logic as `resolve_state.py wake`, imported, not shelled out). If the wake status is anything but `OK`, the router returns `stop` with that status and does nothing else.

The optional event is one JSON object:

| Event `type` | Sent when | Payload |
|---|---|---|
| `start` | The Hub begins routing a newly created workstream at `INIT` | None: the flow, issue, and pull request come from `identity` |
| `result` | A dispatched spoke returned | The spoke's result envelope |
| `user` | The user answered a pause | `awaiting` (the pause kind being answered), `decision`, optional `option`, optional `notes` |

With no event, the router re-derives the decision from state: a pending pause is returned again, an outstanding dispatch returns the stored invocation envelope (`lifecycle.invoke`) unchanged, and drift or a dirty tree is handled as described below. At `INIT` a no-event call stops with `NOT_STARTED`, because only a `start` event begins routing; at `DONE` it returns `done`. This makes calling the router after a crash or on resume always safe.

`--dry-run` computes the decision without writing state. The Hub uses it to build the resume brief's next step.

Each call runs these steps in order and returns at the first one that yields a stop or a pause:

1. Wake.
2. Apply the event, if any.
3. Handle drift (see *Drift and Dirty Trees*).
4. Check for a dirty tree.
5. Dispatch, applying the entry guards and the attempt limit first.

Output is one JSON decision on stdout with exit `0`; a non-zero exit means a usage error.

## Decision Shape

| `action` | Fields | Hub does |
|---|---|---|
| `dispatch` | `invoke` (the invocation envelope) | Starts the named subagent with the envelope |
| `pause` | `awaiting`, `question`, `options`, `context_refs` | Asks the user, then calls the router with a `user` event |
| `stop` | `status`, `detail` | Reports the problem to the user; no further routing until it is resolved |
| `close` | — | Runs `resolve_state.py close` and reports the workstream finished |
| `done` | — | Nothing further; the workstream is already closed |

Every decision also carries `phase` (the phase after the decision) and `summary` (one line for the resume brief).

### Invocation envelope

| Field | Meaning |
|---|---|
| `dispatch_id` | Unique per dispatch; the result envelope must echo it |
| `agent` | `forge-planner`, `forge-qa`, `forge-dev`, `forge-verifier`, `forge-reviewer`, `forge-scout`, `forge-publisher`, `forge-reporter`, or `forge-council` |
| `mode` | The agent's mode for this dispatch (see *Agent modes*) |
| `stage` | The value the spoke writes into its commits' `Forge-Stage` trailer; omitted for agents that do not commit |
| `state_path` | Absolute path of `state.json` (read-only for the spoke) |
| `base_sha` | `HEAD` at dispatch; the value of the `Forge-Input-SHA` trailer |
| `assigned_scope` | Repository path patterns, in the gate's glob syntax, the spoke works on: `scope_manifest.remaining` for qa and dev build phases, `scope_manifest.assigned` for the verifier |
| `context_refs` | Files the spoke must read: specs, scout briefs, council rulings, review findings |
| `notes` | User or council notes carried into this dispatch, if any |
| `panel` | Council only: the persona names the council convenes for this blocker |

### Result envelope

| Field | Meaning |
|---|---|
| `dispatch_id` | Echoed from the invocation |
| `agent` | Must equal the dispatched agent |
| `status` | One of the statuses the transition table allows for this agent in this phase |
| `head_sha` | `HEAD` when the spoke finished |
| `files_touched` | Paths changed by the spoke's commits |
| `scope` | Optional: `assigned` (planner: the patterns the plan changes), or `completed` and `remaining` (verifier). Every scope list holds repository path patterns in the gate's glob syntax |
| `summary` | One paragraph for the user |
| `escalation` | Required with a blocker status: `details`; for `HUMAN_CODE_CHANGE_REQUESTED`, also the `paths` and line ranges the spoke needs to change; for `CHANGES_REQUESTED`, also `findings`, a list of `{route_to, detail}` with `route_to` one of `forge-planner`, `forge-qa`, `forge-dev` |
| `ruling` | Council only: `matrix_ref`, `requires_user`, `panel` (the personas convened), `options` (list of `{label, summary, route_to, delta}`), `recommended` (a label), and `route_to` and `delta` copied from the recommended option; every `route_to` is a list of `forge-planner`, `forge-qa`, `forge-dev` |
| `publish_target` | Publisher only: `push` or `merge`, echoing the mode |
| `review` | Reviewer `external_pr` only: the review to submit (reviewer design) |
| `report` | Reporter only: the drafted or sent report (reporter design) |

### Envelope validation

A `user` event is accepted only when its `awaiting` matches the pending pause and its `decision` is one of that pause's options; otherwise the router returns `stop` with `EVENT_INVALID` and changes nothing.

A `result` event is accepted only when all of these hold; otherwise the router returns `stop` with `ENVELOPE_INVALID` and changes nothing:

- `dispatch_id` and `agent` match `lifecycle.dispatched`.
- `status` is allowed for that agent in the current phase and mode (*Statuses by mode*, below).
- `head_sha` is the current `HEAD` or an ancestor of it. Commits after `head_sha` were made by someone else while the spoke ran; they are drift, handled after the envelope is applied.
- For an agent that does not commit (`forge-verifier`, `forge-reviewer`, `forge-scout`, `forge-council`, `forge-reporter`, `forge-publisher`), `head_sha` equals the dispatch's `base_sha`.
- For a committing agent, every first-parent commit between `base_sha` and `head_sha` carries `Forge-Agent` equal to the dispatched agent. Any other commit in that range is drift (see *Drift and Dirty Trees*), not an envelope error: the envelope is applied, then the drift is handled.

## Lifecycle State

The router owns these `lifecycle` fields:

| Field | Meaning |
|---|---|
| `current_phase` | One of the phases below |
| `pass` | `1` or `2` in collaborative build; `null` otherwise |
| `dispatched` | `{dispatch_id, agent, mode, base_sha}` while a spoke is out; `null` otherwise |
| `invoke` | The complete invocation envelope of the latest dispatch, so a re-derived dispatch carries the same notes and context files |
| `awaiting` | `{kind, payload}` while paused; `null` otherwise |
| `resume_phase` | Phase to return to after a reconcile, council, or repair detour |
| `repair_queue` | Ordered `[{agent, notes, context_refs, blocker_id}]` of repair dispatches still to run; `blocker_id` names the blocker the repair resolves |
| `attempts` | Dispatch count per phase since that phase was last entered |
| `blocker_counts` | Blockers raised per kind over the workstream's life |
| `last_agent`, `last_status` | From the most recently applied envelope |
| `last_envelope` | Absolute path of the most recently applied result envelope's file, `envelopes/<dispatch_id>.json` beside `state.json` |

It also writes `anchors` (except `stale_shas`), `scope_manifest`, `blockers`, `council_decisions`, and `approvals`.

## Phases

| Phase | Flow | Agent and mode |
|---|---|---|
| `INIT` | both | — (awaits `start`) |
| `RECON` | build | `forge-scout` `PRE_PLAN` |
| `PLAN_S1` … `PLAN_S4` | build, LID repo | `forge-planner` `strict` or `auto` |
| `PLAN` | build, naive repo | `forge-planner` `naive` |
| `POST_PLAN` | build | `forge-verifier` `POST_PLAN` |
| `PRE_QA` | build, collaborative | `forge-verifier` `PRE_QA` |
| `QA_SKEL` | build, collaborative pass 1 | `forge-qa` `skeleton` |
| `PRE_DEV` | build, collaborative | `forge-verifier` `PRE_DEV` |
| `DEV_STUB` | build, collaborative pass 1 | `forge-dev` `stub` |
| `QA_FULL` | build | `forge-qa` `full` |
| `POST_QA` | build | `forge-verifier` `POST_QA` |
| `DEV_FULL` | build | `forge-dev` `full` |
| `VERIFY_FINAL` | build | `forge-verifier` `VERIFY_FINAL` |
| `REVIEW_INTERNAL` | build | `forge-reviewer` `internal` |
| `PUBLISH` | build | `forge-publisher` `push` or `merge` |
| `CI_WATCH` | build, after `push` | `forge-scout` `CI_WATCH` or `CI_RERUN` |
| `PR_FETCH` | pr_review | `forge-scout` `PR_FETCH` |
| `PR_REVIEW` | pr_review | `forge-reviewer` `external_pr` |
| `REPORT` | both | `forge-reporter` `draft`, then `send` |
| `RECONCILE` | build | `forge-verifier` `RECONCILE` |
| `COUNCIL` | build | `forge-council` (mode = blocker kind) |
| `REPAIR` | build | head of `repair_queue`, mode `repair` |
| `DONE` | both | — |

`Forge-Stage` values carried in `stage`: `LID-S1` … `LID-S4`, `NAIVE-PLAN`, `COLLAB-SKEL`, `COLLAB-STUB`, `LID-S5` (QA full), `LID-S6` (dev full), `REPAIR`. In a naive repository QA full and dev full use `NAIVE-TESTS` and `NAIVE-IMPL`.

## Transition Table — Build Flow

A row applies when the current phase and the accepted envelope status match. Pauses are listed with the user decisions they accept.

| Phase | Status | Next |
|---|---|---|
| `INIT` | `start` event | `RECON` if `identity.issue` is set; else the first planning phase (`PLAN_S1` for LID, `PLAN` for naive) |
| `RECON` | `OK` | First planning phase; the workstream's `scout/` directory, which holds the scout's brief, joins the planner's `context_refs` |
| `PLAN_Sn` (strict) | `STAGE_COMPLETE` | Pause `plan_approval` → `approve`: `PLAN_S(n+1)`; `revise`: re-dispatch `PLAN_Sn` with `notes` |
| `PLAN_Sn` / `PLAN` | `READY_FOR_QA` | Record `scope_manifest.assigned`; pause `plan_approval` → `approve`: set `plan_base_sha = HEAD`, go to `POST_PLAN`; `revise`: re-dispatch the current planning phase with `notes` |
| `PLAN_Sn` / `PLAN` | `CONCERNS` | Blocker `CONCERNS` |
| `POST_PLAN` | `PASS` | Collaborative: `pass = 1`, `PRE_QA`. Sequential: `QA_FULL` |
| `POST_PLAN` | `AUDIT_FAILED` | Blocker `AUDIT_FAILED` |
| `PRE_QA` | `PARTIAL_REMAINING` | Record scope; pass 1: `QA_SKEL`; pass 2: `QA_FULL` |
| `PRE_QA` | `NOTHING_REMAINING` | Record scope; pass 1: `PRE_DEV`; pass 2: `PRE_DEV` |
| `QA_SKEL` | `SKELETON_COMMITTED` | Pause `edit_window` → `continue`: `PRE_DEV` |
| `PRE_DEV` | `PARTIAL_REMAINING` | Record scope; pass 1: `DEV_STUB`; pass 2: `DEV_FULL` |
| `PRE_DEV` | `NOTHING_REMAINING` | Record scope; pass 1: `pass = 2`, `PRE_QA`; pass 2: `VERIFY_FINAL` |
| `DEV_STUB` | `STUBS_COMMITTED` | Pause `edit_window` → `continue`: `pass = 2`, `PRE_QA` |
| `QA_FULL` | `TESTS_COMMITTED` | `POST_QA` |
| `POST_QA` | `PASS` | Collaborative: pause `edit_window` → `continue`: `PRE_DEV`. Sequential: `DEV_FULL` |
| `POST_QA` | `AUDIT_FAILED` | Blocker `AUDIT_FAILED` |
| `DEV_STUB` / `DEV_FULL` | `TEST_CHANGE_REQUESTED` or `CONTRACT_MISMATCH` | Blocker of that kind |
| `DEV_FULL` | `IMPLEMENTATION_COMPLETE` | `VERIFY_FINAL` |
| `VERIFY_FINAL` | `PASS` | Run the commit gate (below); on success set `verifier_pass_sha = HEAD`, go to `REVIEW_INTERNAL`; on failure blocker `AUDIT_FAILED`, agent `commit-gate`, with the gate's error findings |
| `VERIFY_FINAL` | `AUDIT_FAILED` or `TESTS_FAILED` | Blocker of that kind |
| `REVIEW_INTERNAL` | `REVIEW_APPROVED` | Set `reviewer_pass_sha = HEAD`; pause `publish_approval` → `approve` with `option` `push` or `merge`: `PUBLISH`; `hold`: stay paused |
| `REVIEW_INTERNAL` | `CHANGES_REQUESTED` | Blocker `CHANGES_REQUESTED` |
| `PUBLISH` | `PUBLISHED` | `REPORT` (push target only) |
| `PUBLISH` | `MERGE_READY` | Pause `manual_merge`: the user merges the branch into `identity.base_branch` themselves → `done`: `close` when `HEAD` is an ancestor of the base branch, otherwise pause again with that finding |
| `PUBLISH` | `MERGE_BLOCKED` | Stop `MERGE_BLOCKED` with the gate's findings |
| `PUBLISH` | `PUBLISH_REFUSED` | Stop `PUBLISH_REFUSED` with the publisher's reason |
| `CI_WATCH` | `CI_GREEN` | `close` |
| `CI_WATCH` | `CI_FAILED` | Blocker `CI_FAILED` |
| `CI_WATCH` | `CI_NONE` | Pause `ci_none` → `approve`: `close`; `wait`: re-dispatch `CI_WATCH` |
| `REPORT` | `REPORT_DRAFTED` | Pause `report_approval`, with `last_envelope` (the draft) in `context_refs` → `approve`: re-dispatch reporter `send`; `revise`: re-dispatch reporter `draft` with `notes`; `skip`: as for `REPORT_SENT` |
| `REPORT` | `REPORT_SENT` | `CI_WATCH` |
| `RECONCILE` | `PASS` | Replace `scope_manifest.completed` and `remaining` with the envelope's scope; go to `resume_phase` |
| `COUNCIL` | `COUNCIL_RULING` | See *Blockers and Council* |
| `REPAIR` | `REPAIR_COMMITTED` | When the agent is `forge-planner` and the envelope carries `scope.assigned`, replace `scope_manifest.assigned` with it. Pop the queue head; next queue item, or `resume_phase` when empty |
| `QA_SKEL` / `QA_FULL` / `REPAIR` (qa) | `CONTRACT_MISMATCH` | Blocker `CONTRACT_MISMATCH` |
| any phase dispatching `forge-qa` or `forge-dev` | `HUMAN_CODE_CHANGE_REQUESTED` | See *Human-Code Approvals*. The planner never returns it: its commits are exempt from the human-code rule (gates design) |

Every `REPORT` dispatch carries `last_envelope` first in `context_refs`: the publisher's `PUBLISHED` envelope, the reviewer's `PR_REVIEW_READY` envelope, or the reporter's own `REPORT_DRAFTED` envelope when it sends or revises that draft. The reporter sends exactly the draft the user saw.

A `push` publish creates or updates the pull request before CI is watched, because many repositories run CI only on pull requests. Forge never merges into the base branch itself. The `merge` target only confirms the merge is possible and hands it to the user; once the branch is merged there is no remote branch for CI or a pull request, so the workstream closes. After a `MERGE_BLOCKED` stop, the user typically merges `main` into the branch; the next call sees that as drift and the branch goes back through verification and review before publishing again.

Any agent may return `ERROR` with `escalation.details` when it cannot do its job at all (a tool is missing, authentication failed, the test runner cannot start). In every phase, `ERROR` stops with `AGENT_ERROR` and the details; state is unchanged apart from `last_agent` and `last_status`.

### Entry guards

Before dispatching, the router enforces:

- `REVIEW_INTERNAL` requires `verifier_pass_sha == HEAD`; otherwise it goes to `VERIFY_FINAL`.
- `PUBLISH` requires `verifier_pass_sha == reviewer_pass_sha == HEAD` and a clean tree; otherwise it goes to `VERIFY_FINAL` (SHA mismatch) or pauses `dirty_tree`.

### Commit gate

On a `VERIFY_FINAL` `PASS`, the router itself runs the gate checks (`verify_gate.py` trailer, boundary, and human-code checks over `workstream_base_sha..HEAD`, or over every first-parent commit reachable from `HEAD` when `workstream_base_sha` is `null`). The range starts at the workstream's base so the planner's commits are checked too; the gate exempts commits up to `plan_base_sha` from the human-code check, because approving the plan approved them. The verifier spoke's own claim is not enough to set `verifier_pass_sha`: the checks that can be computed are computed here.

## Transition Table — PR Review Flow

| Phase | Status | Next |
|---|---|---|
| `INIT` | `start` event | `PR_FETCH` |
| `PR_FETCH` | `OK` | `PR_REVIEW`, with the fetched diff and threads in `context_refs` |
| `PR_REVIEW` | `PR_REVIEW_READY` | `REPORT` (reporter drafts the review submission) |
| `REPORT` | `REPORT_DRAFTED` | As in the build flow |
| `REPORT` | `REPORT_SENT` | `close` |

## Blockers and Council

A blocker status appends `{id, kind, agent, phase, details}` to `blockers`, sets `resume_phase`, and routes by kind. A blocker raised by the commit gate rather than a spoke has agent `commit-gate`.

| Kind | Raised by | Council panel | User sign-off | After approval |
|---|---|---|---|---|
| `CONCERNS` | planner | `pm`, `architect`, `critic` | Always | Re-dispatch the blocked planning phase with the ruling's matrix in `context_refs` and the chosen option's `delta` in `notes` |
| `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH` | dev, or qa for `CONTRACT_MISMATCH` | `senior-dev`, `architect` | Unless auto-resolve applies | Queue the ruling's `route_to` agents with its delta; resume the blocked phase. An empty `route_to` resolves the blocker and runs the blocked phase again with the delta in `notes` |
| `AUDIT_FAILED` | verifier or commit gate | `architect`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents; resume the failed verifier phase |
| `TESTS_FAILED` | verifier | `debugger`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents; resume `VERIFY_FINAL` |
| `CI_FAILED` | scout | `debugger`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents; resume `VERIFY_FINAL`. An empty `route_to` (a flaky failure) goes to `CI_WATCH` with mode `CI_RERUN`, which re-runs the failed jobs and watches again |
| `CHANGES_REQUESTED` | reviewer | none | Only when a finding routes to the planner (pause `findings_signoff`) | Queue repairs for each `route_to` agent in arrow order (planner, qa, dev); resume `VERIFY_FINAL` |

**Auto-resolve applies** when `config.council_auto_resolve` is `true` and the ruling's `requires_user` is `false`. Otherwise the router pauses `council_signoff`, presenting the ruling's options; `approve` (with the chosen `option`, which must be one of the ruling's option labels, otherwise `EVENT_INVALID`) records a `council_decisions` entry `{id, blocker_id, panel, option, approved_by: "user"}`, where `panel` is the ruling's `panel` (the personas actually convened) and applies the "after approval" column using that option's `route_to` and `delta`; auto-resolve applies the recommended option's; `reject` with `notes` re-dispatches the council once with those notes.

Before dispatching the council, the router writes the blocker to `council/<blocker id>.json` in the workstream's state directory and puts that file first in `context_refs`, followed by the `context_refs` of the dispatch that raised the blocker. The returned ruling is stored on the blocker as `ruling`, so a `council_signoff` answered in a later session applies exactly the ruling the user was shown. A ruling for `AUDIT_FAILED` or `TESTS_FAILED` with an empty `route_to` needs no repair: the blocker is resolved and the failed phase runs again. The applied option's `route_to` limits which agents the queue may contain; a ruling whose top-level or option `route_to` names an agent outside `forge-planner`, `forge-qa`, `forge-dev` is an envelope error.

Resolving a blocker removes it from `blockers`. Each repair-queue item names its blocker in `blocker_id`, so draining the queue removes the right one.

`lifecycle.blocker_counts` counts blockers raised per kind over the workstream's life. Raising a fourth blocker of the same kind pauses `attempt_limit` instead of routing it, with the same `retry` (resets that kind's count) and `stop` options as the per-phase limit. This bounds loops that re-enter phases — review, repair, verify, review — which the per-phase count alone resets.

## Human-Code Approvals

`HUMAN_CODE_CHANGE_REQUESTED` pauses `human_code_approval`, presenting the paths and line ranges from the escalation. `approve` appends `{id, agent, paths, ranges, base_sha, approved_by: "user"}` to `approvals`, with `base_sha` set to the escalating envelope's `head_sha` (the commit whose lines the user was shown), and re-dispatches the same phase with the approval ID in `notes` (in `REPAIR`, appended to the queue item's notes, which are kept); `reject` with `notes` re-dispatches the same phase with the user's notes. The gate component treats an approval as covering only the listed ranges as they stood at `base_sha`.

## Drift and Dirty Trees

On every call, after any event is applied:

1. **Drift `ahead` with human commits, `behind`, or `diverged`:** set `verifier_pass_sha` and `reviewer_pass_sha` to `null`. If the next phase is already a verifier phase (`PRE_QA`, `PRE_DEV`, `POST_QA`, `VERIFY_FINAL`, `RECONCILE`), continue to it. Otherwise set `resume_phase` to the current phase and go to `RECONCILE`. In both cases set `last_known_head_sha = HEAD`.
2. **Drift `ahead` with only Forge commits** from a dispatch whose envelope never arrived (the session ended while the spoke ran): treated as in step 1, so the verifier accounts for the work before the phase is retried.
3. **Applied envelope from a committing agent:** set `last_known_head_sha = head_sha`. When the envelope's range also holds commits from someone else and the envelope leads to a pause, `last_known_head_sha` keeps its value from before the call, so those commits are still reported as drift when the pause is answered rather than absorbed into the anchor.
4. **Outstanding dispatch on a dirty tree:** returning an outstanding dispatch again counts as dispatching, so a dirty tree pauses `dirty_tree` as in step 7. The changes may be the unfinished work of a spoke whose session ended, or the user's own edits; the question says so, because only the user can tell which, and a `checkpoint` records them as human-authored.
5. **Workstream about to close:** drift found on a call that would return `close` goes to `RECONCILE` with `resume_phase = VERIFY_FINAL`, so the new commits are verified and reviewed before the workstream finishes.
6. **First dispatch:** when `last_known_head_sha` is still `null`, dispatching sets it to `HEAD`, so commits made before the first spoke commit are still seen as drift.
7. **Dirty tree:** before dispatching any agent except `forge-scout` and `forge-reporter`, pause `dirty_tree` with options `checkpoint` (the Hub commits the changes as `wip(user): partial implementation`, without Forge trailers) and `stash`. The answer clears the pause; the next call re-checks the tree.

A pause that is waiting for the user is not disturbed by drift: drift is handled when the pause is answered.

## Attempt Limits

Each dispatch into a phase increments `attempts[phase]`; entering a phase from another phase resets its count, and so does dispatching the next item of the repair queue, so in `REPAIR` the limit counts retries of one repair rather than the length of the queue. A phase about to be dispatched for the fourth time pauses `attempt_limit` instead, with options `retry` (resets the count) and `stop`. This bounds automatic loops: repeated verifier failures, repeated council rulings that do not resolve a blocker, and crash–retry loops.

A dispatch made while applying a user event — `revise`, `reject`, `wait`, an approval that re-dispatches the same phase, or `retry` itself — sets that phase's count to `1` and is never paused by the limit. The user's answer is new input, so the automatic retries after it start a fresh count; loops that pass through the user each time remain bounded by the per-kind blocker count.

## Statuses by Mode

The transition table lists which statuses each phase accepts. Within a phase, the mode narrows them further:

| Phase and mode | Not accepted |
|---|---|
| `PLAN_S1`–`PLAN_S3`, `strict` | `READY_FOR_QA` (strict planning finishes only at stage 4) |
| `PLAN_S4`, `strict`; any planning phase, `auto` | `STAGE_COMPLETE` |
| `REPORT`, `draft` | `REPORT_SENT` |
| `REPORT`, `send` | `REPORT_DRAFTED` |
| Any `forge-planner` dispatch | `HUMAN_CODE_CHANGE_REQUESTED` |
| `REPAIR`, any agent | Every status but `REPAIR_COMMITTED`, `ERROR`, `HUMAN_CODE_CHANGE_REQUESTED` (except from `forge-planner`), and, for `forge-qa`, `CONTRACT_MISMATCH` |

## Context Carried into Re-dispatches

A dispatch of the same phase caused by a user answer (`revise`, `reject` of a human-code change, approval of a human-code change) keeps every `context_refs` entry of the dispatch it replaces and appends the new ones, so a planner revising stage 2 still has the scout's brief.

## Agent Modes

| Agent | Modes |
|---|---|
| `forge-planner` | `strict`, `auto`, `naive`, `repair` |
| `forge-qa` | `skeleton`, `full`, `repair` |
| `forge-dev` | `stub`, `full`, `repair` |
| `forge-verifier` | `POST_PLAN`, `PRE_QA`, `PRE_DEV`, `POST_QA`, `VERIFY_FINAL`, `RECONCILE` |
| `forge-reviewer` | `internal`, `external_pr` |
| `forge-scout` | `PRE_PLAN`, `PR_FETCH`, `CI_WATCH`, `CI_RERUN` |
| `forge-publisher` | `push`, `merge` |
| `forge-reporter` | `draft`, `send` |
| `forge-council` | the blocker kind |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Router reads repository condition itself | Imports the wake logic and runs it on every call | Hub passes a wake report in | One call per step for the Hub, and the router can never act on a stale report. |
| Re-derivable decisions | No-event calls return the pending pause or the outstanding dispatch | Hub remembers what it was doing | The Hub's memory does not survive a session; state does. |
| Plan approval in `auto` cadence | Pause once when planning finishes, before any test or code | No pause until review | Planning output decides everything downstream; one approval is cheap compared with rebuilding against a wrong plan. |
| Reviewer findings | Routed straight to repairs; council not involved | Council (`senior-dev`, `critic`) triages findings first | Review findings are already specific and tagged with an owner; a council round adds cost without new information. A finding for the planner still needs the user's sign-off. |
| Publish approval | Always paused for the user, who chooses `push` or `merge` | Publish automatically after review | Pushing and merging are outward-facing and hard to undo. |
| Local merge | Forge checks the merge is possible; the user performs it | Forge merges into `main` | Changing `main` is the user's call; Forge stops at proving it would succeed. |
| Commit gate at `VERIFY_FINAL` | The router runs the computable checks before recording `verifier_pass_sha` | Trust the verifier's `PASS` | *Scripts over prompts*: a model's claim does not stand in for a check a script can make. |
| Commit gate range | `workstream_base_sha..HEAD`, with planning commits exempt from the human-code check | `plan_base_sha..HEAD`; a separate gate run at plan approval | Starting at `plan_base_sha` leaves the planner's commits unchecked for trailers and boundaries. One range at one call site covers every Forge commit; the exemption reflects that the user approved the planning commits with the plan. |
| Envelope `head_sha` | `HEAD` or an ancestor of it; later commits are drift | Must equal `HEAD` | A user who commits while a spoke runs would otherwise make every envelope invalid; treating the extra commits as drift sends them through reconciliation instead. |
| Approval `base_sha` | The escalating envelope's `head_sha` | The dispatch's `base_sha` | The spoke may commit before escalating; the line ranges it reports refer to its own `head_sha`. |
| Repair mechanism | One `REPAIR` phase draining `repair_queue`, then `resume_phase` | A dedicated repair phase per blocker kind | Every blocker resolves the same way — run some fixes, then resume — so one mechanism covers them all. |
| Attempt limit | 3 automatic dispatches per phase, then ask; a dispatch caused by the user's answer restarts the count at `1` | Unbounded retries; a configurable limit; counting user-caused dispatches; not counting them without a reset | Bounds automatic loops with one rule. Counting the user's own re-dispatches would ask them to confirm an answer they just gave; restarting the count gives the new input a full set of retries. |
| Drift during a pause | Handled when the pause is answered | Interrupt the pause | During an edit window, human commits are the expected outcome, not a surprise. |
| Re-derived dispatch | Return the stored envelope (`lifecycle.invoke`) | Rebuild the envelope from state | Notes and context files come from the event that caused the dispatch (the user's notes, a council ruling); state alone cannot rebuild them. |
| Other commits inside an envelope that ends in a pause | The anchor keeps its value; the commits are handled as drift when the pause is answered | Advance the anchor to the envelope's `head_sha` | Advancing would absorb the other commits without reconciliation. |
| Report handoff | The router passes the most recently applied envelope file to `REPORT` dispatches and the `report_approval` pause | A directory the reporter writes drafts to; the reporter drafts again before sending | The envelope files already persist; the user approves the exact text `send` submits, with no new write rule. |
| Council option applied | The option the user chose, each option carrying its own `route_to` and `delta` | Always the recommended option's repair | A user who picks another option must get that option's repair. |
| Status checks | By phase and mode | By phase only | A strict stage-2 planner returning `READY_FOR_QA` would skip stages 3 and 4; a `PLAN_S4` `STAGE_COMPLETE` would route to a stage that does not exist. |
| Council routing for test disputes | The ruling's `route_to`, like every other kind | Always `forge-qa` | The council may find the code, not the test, at fault; forcing QA would repair the wrong side. |
| Read-only agents and dirty trees | Scout and reporter may run on a dirty tree; every other agent waits | Block all dispatch | Scout and reporter read remote state or committed receipts only; the verifier and reviewer would otherwise certify a HEAD that differs from the files on disk. |

## Open Questions & Future Decisions

### Deferred
1. Stacked workstreams whose base branch changes after creation — the parent receives review fixes, or merges into the default branch (often squashed). Agents cannot rebase, so the user must; detecting the change (scout, or drift against `identity.base_branch`) and pausing with instructions is designed with the scout and Commander.

## References

- `docs/high-level-design.md` — scripts own routing; tenets *Scripts over prompts*, *Stop over guess*.
- Workstream state design (`docs/intent/workstream-state/`) — wake, drift kinds, `stale_shas`, `close`.
- Gates design (`docs/intent/gates/`) — the commit gate the router runs at `VERIFY_FINAL` and the approval semantics it enforces.
