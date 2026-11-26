---
parent: high-level-design
prefix: ROUTE
---

# Router

## Context and Design Philosophy

`next_step.py` is the only place Forge decides what happens next. Given a workstream's `state.json`, the repository's current condition, and at most one new event, it returns exactly one decision — dispatch a spoke, pause for the user, stop, close, or finish — and persists the state that decision implies. The Hub carries decisions out; it never chooses a phase, an agent, or a mode itself.

Three properties follow from the HLD tenets:

- **One writer.** Apart from the identity, config, `anchors.stale_shas`, and `dropped` written by `resolve_state.py`, every field of `state.json` is written here, along with `config.mode` when the user switches it. Spokes return envelopes; they never touch state.
- **Pure decision, testable.** The decision is a function of state, repository condition, and event. The whole transition table runs under pytest against temporary git repositories.
- **Stop over guess.** Any input the router cannot account for — an envelope from an agent it did not dispatch, a HEAD it did not expect, a status not allowed in the current phase — produces a stop with a named reason and leaves state unchanged.

## Invocation

```text
next_step.py --holder-pid PID --holder-session ID [--workstream SLUG] [--event FILE|-] [--dry-run]
```

After the kill-switch check, every call runs the workstream-state wake (same identity, lock, and drift logic as `resolve_state.py wake`, imported, not shelled out). If the wake status is anything but `OK`, the router returns `stop` with that status and does nothing else.

The optional event is one JSON object:

| Event `type` | Sent when | Payload |
|---|---|---|
| `start` | The Hub begins routing a newly created workstream at `INIT` | None: the flow, issue, and pull request come from `identity` |
| `result` | A dispatched spoke returned | The spoke's result envelope |
| `user` | The user answered a pause | `awaiting` (the pause kind being answered), `decision`, optional `option`, optional `notes` |
| `mode` | The user switched the workstream's mode | `mode`, one of the six workstream modes |

With no event, the router re-derives the decision from state: a pending pause is returned again, an outstanding dispatch returns the stored invocation envelope (`lifecycle.invoke`) unchanged except for `agent_prefix` and `dispatch_as`, which describe the current install and are read again, and drift or a dirty tree is handled as described below. At `INIT` a no-event call stops with `NOT_STARTED`, because only a `start` event begins routing; at `DONE` it returns `done`, or, when `lifecycle.promote` is set, the same `close` with `promote` again, since the state directory still existing means the promotion did not finish. This makes calling the router after a crash or on resume always safe.

`--dry-run` computes the decision without writing state. The Hub uses it to build the resume brief's next step.

Each call runs these steps in order and returns at the first one that yields a stop or a pause:

1. Kill switch: when `.STOP_FORGE` exists at the main worktree's root or at the root of the worktree the router runs in (both found from git), stop with `KILL_SWITCH` without waking, taking the lock, reading or writing state, or applying the event; `detail` lists every such file and `phase` is `null`.
2. Wake.
3. Apply the event, if any. A `mode` event returns `mode_set` here.
4. Handle drift (see *Drift and Dirty Trees*).
5. Check for a dirty tree.
6. Dispatch, applying the entry guards and the attempt limit first.

Output is one JSON decision on stdout with exit `0`; a non-zero exit means a usage error.

## Decision Shape

| `action` | Fields | Hub does |
|---|---|---|
| `dispatch` | `invoke` (the invocation envelope) | Starts the named subagent with the envelope |
| `pause` | `awaiting`, `question`, `options`, `context_refs` | Asks the user, then calls the router with a `user` event |
| `stop` | `status`, `detail` | Reports the problem to the user; no further routing until it is resolved |
| `close` | `promote` (idea and triage only, when the user chose to promote): `{issue, proposal}` | Runs `resolve_state.py close` and reports the workstream finished; with `promote`, runs `resolve_state.py promote` instead, which starts a build workstream for `issue` with `proposal` as its task description and then closes this one |
| `done` | — | Nothing further; the workstream is already closed |
| `mode_set` | `mode` | Reports the switch; routes again with no event once no subagent is running |

Every decision also carries `phase` (the phase after the decision) and `summary` (one line for the resume brief).

### Invocation envelope

| Field | Meaning |
|---|---|
| `dispatch_id` | Unique per dispatch; the result envelope must echo it |
| `agent` | `forge-planner`, `forge-qa`, `forge-dev`, `forge-verifier`, `forge-reviewer`, `forge-scout`, `forge-publisher`, `forge-reporter`, or `forge-council` |
| `agent_prefix` | The prefix the harness needs before every Forge agent name: `forge:` for the Claude Code plugin, `""` otherwise |
| `dispatch_as` | `agent_prefix` followed by `agent`: the name the Hub starts the subagent by |
| `mode` | The agent's mode for this dispatch (see *Agent modes*) |
| `stage` | The value the spoke writes into its commits' `Forge-Stage` trailer; omitted for agents that do not commit |
| `state_path` | Absolute path of `state.json` (read-only for the spoke) |
| `base_sha` | `HEAD` at dispatch; the value of the `Forge-Input-SHA` trailer |
| `assigned_scope` | Repository path patterns, in the gate's glob syntax, the spoke works on: `scope_manifest.remaining` for qa and dev build phases, `scope_manifest.assigned` for the verifier |
| `context_refs` | Files the spoke must read: the task description, specs, scout briefs, council rulings, review findings |
| `notes` | User or council notes carried into this dispatch, if any |
| `panel` | Council only: the persona names the council convenes for this blocker |

The router reads `agent_prefix` from `forge_install.json` in its own directory, which only a plugin install writes; with no such file the prefix is `""`, and a file that does not parse as a JSON object with a string `agent_prefix` stops with `INSTALL_INVALID`. Every other field, `lifecycle.dispatched.agent` included, keeps the bare name.

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

A `mode` event is accepted in any phase but `DONE` when its `mode` is one of the six workstream modes; otherwise the router returns `stop` with `EVENT_INVALID` and changes nothing. Applying it sets `config.mode` (a no-op when it already holds that mode), answers any pacing pause the new mode lacks (*Modes*), and returns `mode_set` without handling drift, checking the tree, or dispatching: a switch can arrive while a subagent is still running, and the Hub routes again once nothing is outstanding. At `INIT` the Hub's next call carries the `start` event. In a `pr_review`, `idea`, or `triage` workstream the mode is stored but changes nothing, since those flows have no pacing pauses; a build workstream promoted from an idea or triage workstream starts in that stored mode.

A `result` event is accepted only when all of these hold; otherwise the router returns `stop` with `ENVELOPE_INVALID` and changes nothing:

- `dispatch_id` and `agent` match `lifecycle.dispatched`.
- `status` is allowed for that agent in the current phase and mode (*Statuses by mode*, below).
- `head_sha` is the current `HEAD` or an ancestor of it. Commits after `head_sha` were made by someone else while the spoke ran; they are drift, handled after the envelope is applied.
- For an agent that does not commit (`forge-verifier`, `forge-reviewer`, `forge-scout`, `forge-council`, `forge-reporter`, `forge-publisher`, and `forge-planner` in mode `propose`), `head_sha` equals the dispatch's `base_sha`.
- For a committing agent, every first-parent commit between `base_sha` and `head_sha` carries `Forge-Agent` equal to the dispatched agent. Any other commit in that range is drift (see *Drift and Dirty Trees*), not an envelope error: the envelope is applied, then the drift is handled.

Once `dispatch_id` and `agent` match, and before the other checks, the router reads the guard record `envelopes/<dispatch_id>.guard.json` beside `state.json` (guard design). When the file is missing, is not a JSON object whose `agents` is a list, or does not list the dispatched agent, the guard did not run for that spoke, and the router returns `stop` with `GUARD_INACTIVE` and changes nothing. The `detail` names the record's path and the known causes: a project-scope install in a `-p` session or in a folder whose workspace trust was not accepted, a managed policy that allows only managed hooks, and no `python3` on `PATH`. The dispatch stays outstanding. Once the guard runs again, the Commander discards the envelope and starts the spoke again with the stored invocation; any commits the unguarded spoke made stay, and the commit gate at `VERIFY_FINAL` checks them like every other Forge commit. An idea or triage workstream never reaches that gate, so there the `detail` also states whether `HEAD` differs from the dispatch's `base_sha` and lists any uncommitted paths; the restarted dispatch then meets the `WORKTREE_MOVED` stop or the `dirty_tree` pause, and a restarted reporter `send` finds an earlier send before sending again (reporter design).

In an `idea` or `triage` workstream, once the guard record is found, a `result` event arriving while `HEAD` differs from `last_known_head_sha` stops with `WORKTREE_MOVED` and changes nothing, whatever `head_sha` the envelope reports: a commit in the study's worktree is the same fault whoever made it.

## Lifecycle State

The router owns these `lifecycle` fields:

| Field | Meaning |
|---|---|
| `current_phase` | One of the phases below |
| `pass` | `1` or `2` in a two-pass build; `null` otherwise |
| `two_pass` | `true` or `false`, set at the first `POST_PLAN` `PASS` from the mode and kept for the rest of the workstream; `null` before that |
| `next_dispatch` | `{phase, opts}` a mode switch's answer to a pending pause leads to, with the answer's notes and agent mode; the next call without an event dispatches it and clears the field |
| `dispatched` | `{dispatch_id, agent, mode, base_sha}` while a spoke is out; `null` otherwise. A `PROPOSE` dispatch also carries `proposal_mtime_ns`, the `st_mtime_ns` of `proposal.md` in the workstream's state directory when the dispatch was made, or `null` when that file was absent; other dispatches omit it |
| `invoke` | The complete invocation envelope of the latest dispatch, so a re-derived dispatch carries the same notes and context files |
| `awaiting` | `{kind, payload}` while paused; `null` otherwise |
| `resume_phase` | Phase to return to after a council or repair detour |
| `after_reconcile` | Phase to return to after a drift-caused `RECONCILE`; `null` otherwise. Kept apart from `resume_phase` so drift during a council or repair detour does not lose where that detour returns |
| `repair_queue` | Ordered `[{agent, notes, context_refs, blocker_id}]` of repair dispatches still to run; `blocker_id` names the blocker the repair resolves |
| `attempts` | Dispatch count per phase since that phase was last entered |
| `blocker_counts` | Blockers raised per kind over the workstream's life |
| `last_agent`, `last_status` | From the most recently applied envelope |
| `report_input` | Absolute path of the envelope file the next report is built from: set when a `PUBLISHED`, `PR_REVIEW_READY`, `PROPOSAL_READY`, or `REPORT_DRAFTED` envelope is applied, to `envelopes/<dispatch_id>.json` beside `state.json` |
| `report_url` | Idea and triage only: `report.url` of the applied `REPORT_SENT` envelope — the filed issue, or the comment on the triaged issue; `null` before |
| `propose_context_refs` | Idea and triage only: the `context_refs` of the latest `PROPOSE` dispatch, kept so a `replan` from `report_approval` reads what the planner read; `null` before the first |
| `promote` | Idea and triage only: the `{issue, proposal}` of a `close` decision that promotes, set when that decision is returned; `null` otherwise |

It also writes `anchors` (except `stale_shas`), `scope_manifest`, `blockers`, `council_decisions`, and `approvals`.

## Modes

`config.mode` decides where a workstream stops for the user. The router reads it each time it decides a transition or builds a dispatch, so a switched mode governs every step decided after the switch and nothing already decided.

| Mode | Planner mode | Plan approval | Build flow | Edit windows | Council rulings with `requires_user` `false` |
|---|---|---|---|---|---|
| `delivery` | `auto` | None | Single pass | None | Applied without asking |
| `collab-plan` | `strict` | After every stage | Single pass | None | Signed off by the user |
| `collab-qa` | `auto` | Once, on the finished plan | Single pass | After `POST_QA` | Signed off by the user |
| `collab-dev` | `auto` | Once, on the finished plan | Single pass | After `DEV_FULL` | Signed off by the user |
| `collab-code` | `auto` | Once, on the finished plan | Two-pass | After `QA_SKEL`, `DEV_STUB`, and `POST_QA` | Signed off by the user |
| `collab` | `strict` | After every stage | Two-pass | After `QA_SKEL`, `DEV_STUB`, and `POST_QA` | Signed off by the user |

A workstream whose `config` has no `mode` is routed as `collab`. In a naive repository the planner mode is always `naive`; plan approval follows the table. The planner mode of a planning dispatch comes from the current mode, so after a switch planning continues from the next stage in the new mode's planner mode (an `auto` planner may start at any stage and chains to the end); a `revise` re-dispatch keeps the planner mode of the dispatch it replaces. **Single pass** is `QA_FULL` → `POST_QA` → `DEV_FULL`. **Two-pass** adds a first pass of QA skeletons and dev stubs, with the verifier recomputing remaining scope before each QA and dev phase. The build flow is chosen once, at the first `POST_PLAN` `PASS`, and recorded in `lifecycle.two_pass`; re-entering `POST_PLAN` keeps it. Switching mode afterwards changes only which edit windows and approvals still come. Statuses are checked against the agent mode recorded on the dispatch that returns them (`lifecycle.dispatched.mode`), so a dispatch made before a switch is never rejected for it; the transition is then decided with the current workstream mode.

A switch made while a pacing pause is pending answers that pause when the new mode would not have made it: a `plan_approval` raised after a stage (`STAGE_COMPLETE`) is answered `approve` when the new mode does not stop after every stage, a `plan_approval` on the finished plan is answered `approve` when the new mode is `delivery`, an `edit_window` is answered `continue` when the new mode has no edit window after the phase that raised it, and a switch to `delivery` applies a pending `council_signoff` ruling that `delivery` would have applied without asking. A stored `config.mode` that is not one of the six names stops the router with `STATE_INVALID`. The answer has the same effect as the user's, without dispatching; the Hub's next call dispatches. Every other pause stays until the user answers it.

Every mode keeps the pauses that guard against harm rather than pace the work: `publish_approval`, `manual_merge`, `human_code_approval`, `findings_signoff`, `dirty_tree`, `attempt_limit`, `ci_none`, `report_approval`, `promotion`, `council_signoff` for `CONCERNS` and for rulings that need the user, and every stop.

## Phases

| Phase | Flow | Agent and mode |
|---|---|---|
| `INIT` | all | — (awaits `start`) |
| `RECON` | build, triage, idea | `forge-scout` `PRE_PLAN` (build), `TRIAGE` (triage), or `IDEA` (idea) |
| `PROPOSE` | idea, triage | `forge-planner` `propose` |
| `PLAN_S1` … `PLAN_S4` | build, LID repo | `forge-planner` `strict` or `auto`, by mode |
| `PLAN` | build, naive repo | `forge-planner` `naive` |
| `POST_PLAN` | build | `forge-verifier` `POST_PLAN` |
| `PRE_QA` | build, two-pass | `forge-verifier` `PRE_QA` |
| `QA_SKEL` | build, two-pass, pass 1 | `forge-qa` `skeleton` |
| `PRE_DEV` | build, two-pass | `forge-verifier` `PRE_DEV` |
| `DEV_STUB` | build, two-pass, pass 1 | `forge-dev` `stub` |
| `QA_FULL` | build | `forge-qa` `full` |
| `POST_QA` | build | `forge-verifier` `POST_QA` |
| `DEV_FULL` | build | `forge-dev` `full` |
| `VERIFY_FINAL` | build | `forge-verifier` `VERIFY_FINAL` |
| `REVIEW_INTERNAL` | build | `forge-reviewer` `internal` |
| `PUBLISH` | build | `forge-publisher` `push` or `merge` |
| `CI_WATCH` | build, after `push` | `forge-scout` `CI_WATCH` or `CI_RERUN` |
| `PR_FETCH` | pr_review | `forge-scout` `PR_FETCH` |
| `PR_REVIEW` | pr_review | `forge-reviewer` `external_pr` |
| `REPORT` | all | `forge-reporter` `draft`, then `send` |
| `RECONCILE` | build | `forge-verifier` `RECONCILE` |
| `COUNCIL` | build; idea and triage for `CONCERNS` | `forge-council` (mode = blocker kind) |
| `REPAIR` | build | head of `repair_queue`, mode `repair` |
| `DONE` | all | — |

`Forge-Stage` values carried in `stage`: `LID-S1` … `LID-S4`, `NAIVE-PLAN`, `COLLAB-SKEL`, `COLLAB-STUB`, `LID-S5` (QA full), `LID-S6` (dev full), `REPAIR`. In a naive repository QA full and dev full use `NAIVE-TESTS` and `NAIVE-IMPL`.

## Transition Table — Build Flow

A row applies when the current phase and the accepted envelope status match. Pauses are listed with the user decisions they accept.

| Phase | Status | Next |
|---|---|---|
| `INIT` | `start` event | `RECON` if `identity.issue` is set; else the first planning phase (`PLAN_S1` for LID, `PLAN` for naive) |
| `RECON` | `OK` | First planning phase; the workstream's `scout/` directory, which holds the scout's brief, joins the planner's `context_refs` |
| `PLAN_Sn` | `STAGE_COMPLETE` | Mode with approval after every stage: pause `plan_approval` → `approve`: `PLAN_S(n+1)`; `revise`: re-dispatch `PLAN_Sn` with `notes`. Any other mode: `PLAN_S(n+1)` |
| `PLAN_Sn` / `PLAN` | `READY_FOR_QA` | Record `scope_manifest.assigned` (empty when the plan changes only plan paths). Mode with a plan approval: pause `plan_approval` → `approve`: set `plan_base_sha = HEAD`, `scope_manifest.remaining = assigned`, `completed = []`, go to `POST_PLAN`; `revise`: re-dispatch the current planning phase with `notes`. `delivery`: the same as `approve`, without pausing |
| `PLAN_Sn` / `PLAN` | `CONCERNS` | Blocker `CONCERNS` |
| `POST_PLAN` | `PASS` | `scope_manifest.assigned` empty: `VERIFY_FINAL`. Otherwise two-pass: `pass = 1`, `PRE_QA`; single pass: `QA_FULL` |
| `POST_PLAN` | `AUDIT_FAILED` | Blocker `AUDIT_FAILED` |
| `PRE_QA` | `PARTIAL_REMAINING` | Record scope; pass 1: `QA_SKEL`; pass 2: `QA_FULL` |
| `PRE_QA` | `NOTHING_REMAINING` | Record scope; pass 1: `PRE_DEV`; pass 2: `PRE_DEV` |
| `QA_SKEL` | `SKELETON_COMMITTED` | Mode with an edit window after `QA_SKEL`: pause `edit_window` → `continue`: `PRE_DEV`. Otherwise `PRE_DEV` |
| `PRE_DEV` | `PARTIAL_REMAINING` | Record scope; pass 1: `DEV_STUB`; pass 2: `DEV_FULL` |
| `PRE_DEV` | `NOTHING_REMAINING` | Record scope; pass 1: `pass = 2`, `PRE_QA`; pass 2: `VERIFY_FINAL` |
| `DEV_STUB` | `STUBS_COMMITTED` | Mode with an edit window after `DEV_STUB`: pause `edit_window` → `continue`: `pass = 2`, `PRE_QA`. Otherwise `pass = 2`, `PRE_QA` |
| `QA_FULL` | `TESTS_COMMITTED` | `POST_QA` |
| `POST_QA` | `PASS` | Next is `PRE_DEV` in a two-pass build, `DEV_FULL` in a single pass. Mode with an edit window after `POST_QA`: pause `edit_window` → `continue`: next. Otherwise next |
| `POST_QA` | `AUDIT_FAILED` | Blocker `AUDIT_FAILED` |
| `DEV_STUB` / `DEV_FULL` | `TEST_CHANGE_REQUESTED` or `CONTRACT_MISMATCH` | Blocker of that kind |
| `DEV_FULL` | `IMPLEMENTATION_COMPLETE` | Mode with an edit window after `DEV_FULL`: pause `edit_window` → `continue`: `VERIFY_FINAL`. Otherwise `VERIFY_FINAL` |
| `VERIFY_FINAL` | `PASS` | Run the commit gate (below); on success set `verifier_pass_sha = HEAD`, go to `REVIEW_INTERNAL`; on failure see *Commit gate* |
| `VERIFY_FINAL` | `AUDIT_FAILED` or `TESTS_FAILED` | Blocker of that kind |
| `REVIEW_INTERNAL` | `REVIEW_APPROVED` | Set `reviewer_pass_sha = HEAD`; pause `publish_approval`, naming any merged specs (*Merged specs*, below) and, when the repository has no `origin` remote, that push is unavailable (*Publish without an origin*, below) → `approve` with `option` `push` or `merge` (`merge` only, without an `origin`): `PUBLISH`; `hold`: stay paused |
| `REVIEW_INTERNAL` | `CHANGES_REQUESTED` | Blocker `CHANGES_REQUESTED` |
| `PUBLISH` | `PUBLISHED` | `REPORT` (push target only) |
| `PUBLISH` | `MERGE_READY` | Pause `manual_merge`: the user merges the branch into `identity.base_branch` themselves → `done`: `close` when `HEAD` is an ancestor of the base branch, otherwise pause again with that finding |
| `PUBLISH` | `MERGE_BLOCKED` | Stop `MERGE_BLOCKED` with the gate's findings |
| `PUBLISH` | `PUBLISH_REFUSED` | Pause `publish_approval` again, with the publisher's reason added to the question (*Publish without an origin*, below) |
| `CI_WATCH` | `CI_GREEN` | `close` |
| `CI_WATCH` | `CI_FAILED` | Blocker `CI_FAILED` |
| `CI_WATCH` | `CI_NONE` | Pause `ci_none` → `approve`: `close`; `wait`: re-dispatch `CI_WATCH` |
| `REPORT` | `REPORT_DRAFTED` | Pause `report_approval`, with `report_input` (the draft) in `context_refs` → `approve`: re-dispatch reporter `send`; `revise`: re-dispatch reporter `draft` with `notes`; `skip`: as for `REPORT_SENT` |
| `REPORT` | `REPORT_SENT` | `CI_WATCH` |
| `RECONCILE` | `PASS` | Replace `scope_manifest.completed` and `remaining` with the envelope's scope; go to `after_reconcile` and clear it |
| `COUNCIL` | `COUNCIL_RULING` | See *Blockers and Council* |
| `REPAIR` | `REPAIR_COMMITTED` | When the agent is `forge-planner` and the envelope carries `scope.assigned`, replace `scope_manifest.assigned` with it. Pop the queue head; next queue item, or `resume_phase` when empty |
| `QA_SKEL` / `QA_FULL` / `REPAIR` (qa) | `CONTRACT_MISMATCH` | Blocker `CONTRACT_MISMATCH` |
| any phase dispatching `forge-qa` or `forge-dev` | `HUMAN_CODE_CHANGE_REQUESTED` | See *Human-Code Approvals*. The planner never returns it: its commits are exempt from the human-code rule (gates design) |

In the build and PR review flows, every `REPORT` dispatch carries exactly one `context_refs` entry, `report_input`: the publisher's `PUBLISHED` envelope, the reviewer's `PR_REVIEW_READY` envelope, or the reporter's own latest `REPORT_DRAFTED` envelope when it sends or revises that draft. The reporter sends exactly the draft the user saw.

A `push` publish creates or updates the pull request before CI is watched, because many repositories run CI only on pull requests. Forge never merges into the base branch itself. The `merge` target only confirms the merge is possible and hands it to the user; once the branch is merged there is no remote branch for CI or a pull request, so the workstream closes. After a `MERGE_BLOCKED` stop, the user typically merges `main` into the branch; the next call sees that as drift and the branch goes back through verification and review before publishing again.

Any agent may return `ERROR` with `escalation.details` when it cannot do its job at all (a tool is missing, authentication failed, the test runner cannot start). In every phase, `ERROR` stops with `AGENT_ERROR` and the details; state is unchanged apart from `last_agent` and `last_status`.

### Entry guards

Before dispatching, the router enforces:

- `REVIEW_INTERNAL` requires `verifier_pass_sha == HEAD`; otherwise it goes to `VERIFY_FINAL`.
- `PUBLISH` requires `verifier_pass_sha == reviewer_pass_sha == HEAD` and a clean tree; otherwise it goes to `VERIFY_FINAL` (SHA mismatch) or pauses `dirty_tree`.

### Commit gate

On a `VERIFY_FINAL` `PASS`, the router itself runs the gate checks (`verify_gate.py` trailer, boundary, and human-code checks over `workstream_base_sha..HEAD`, or over every first-parent commit reachable from `HEAD` when `workstream_base_sha` is `null`). The range starts at the workstream's base so the planner's commits are checked too; the gate exempts planner commits from the human-code check. The verifier spoke's own claim is not enough to set `verifier_pass_sha`: the checks that can be computed are computed here.

A gate error names a commit already in the branch, and agents cannot rewrite history, so no repair dispatch can clear it:

- When every error finding is `HUMAN_CODE_MODIFIED`, the router pauses `human_code_approval` presenting each finding's commit, path, and lines. `approve` appends one approval per finding — `{id, agent: the commit's Forge-Agent, paths: [path], ranges: {path: lines}, base_sha: the commit's parent, approved_by: "user"}` — and re-dispatches `VERIFY_FINAL`; `reject` stops with `GATE_REJECTED`.
- Otherwise the router stops with `GATE_UNREPAIRABLE` and the error findings. The user rewrites the branch themselves; the next call sees that as drift and verifies again.

### Merged specs

A merge from another branch can bring in specs nobody has implemented yet; they are not the workstream's (verifier design, *Merged specs*), but the user decides whether to take them up. In a LID repository, before pausing `publish_approval`, the router calls `lid_check.py`'s importable check on the workstream's state and, when its `merged_specs` is not empty, records the IDs in the pause's `merged_specs` and adds a line naming them to the question. In the same way, a non-empty `unmarked_specs` (specs tested and tagged but still `[ ]`, verifier design) is recorded in the pause's `unmarked_specs` with a line naming them, so the user can mark them before merging. Publishing is the last step the user approves before the branch leaves the worktree, so every workstream that publishes shows the list at each `publish_approval`, computed by a script on every entry to the pause, including one raised again after `hold` or a refusal.

### Publish without an origin

A repository can have no `origin` remote: a local-only project, or a sandbox. On every entry to `publish_approval`, including one raised again after `hold` or a refusal, the router runs `git remote get-url origin` in the worktree. When it fails, the pause's payload carries `no_origin: true` and the question gains a line saying push is unavailable; the options stay `approve` and `hold`, but `approve` with `option` `push` is `EVENT_INVALID` ("push needs an origin remote") and only `merge` is accepted. `no_origin` is meaningful only at `publish_approval`; it is recomputed at each entry, so a remote added or removed while paused is seen at the next entry. A user who cannot push then reaches `manual_merge`, and `done` closes the workstream without a `dropped` record.

When `forge-publisher` returns `PUBLISH_REFUSED`, for any reason (no `origin` found late, a failed gate, a rejected push), the router pauses `publish_approval` again with `refused` set to the publisher's reason in the payload and the reason added to the question. The user chooses `merge` or `hold`, or `push` when an origin exists; `hold` is always offered, so the loop always ends. `hold` returns the pause without `refused`. A refusal is not counted by `attempt_limit`: each one passes through the user, whose answer starts a fresh count (*Attempt Limits*). `MERGE_BLOCKED` remains a stop: it names a conflict or missing base the user fixes outside Forge. The publisher keeps its own `origin` check as a backstop for a remote removed after the pause.

## Transition Table — PR Review Flow

| Phase | Status | Next |
|---|---|---|
| `INIT` | `start` event | `PR_FETCH` |
| `PR_FETCH` | `OK` | `PR_REVIEW`, with the fetched diff and threads in `context_refs` |
| `PR_REVIEW` | `PR_REVIEW_READY` | `REPORT` (reporter drafts the review submission) |
| `REPORT` | `REPORT_DRAFTED` | As in the build flow |
| `REPORT` | `REPORT_SENT` | `close` |

## Transition Table — Idea and Triage Flows

Both flows study a change without making it: nothing commits, and the only outward action is the issue (idea) or issue comment (triage) the user approves.

| Phase | Status | Next |
|---|---|---|
| `INIT` | `start` event | `RECON` |
| `RECON` | `OK` | `PROPOSE`; the workstream's `scout/` directory joins the planner's `context_refs` |
| `PROPOSE` | `PROPOSAL_READY` | `REPORT` (reporter `draft`) |
| `PROPOSE` | `CONCERNS` | Blocker `CONCERNS`, as in the build flow: the council drafts options, the user signs off, and `PROPOSE` is dispatched again with the matrix in `context_refs` and the chosen option's `delta` in `notes`. `council_signoff` here also offers `leave_open` (below) |
| `REPORT` | `REPORT_DRAFTED` | Pause `report_approval`, with `report_input` (the draft) in `context_refs` → `approve`: re-dispatch reporter `send`; `revise`: re-dispatch reporter `draft` with `notes`; `replan`: dispatch `PROPOSE` with `notes` and with `propose_context_refs` followed by `proposal.md` and the rejected draft (`report_input`) as `context_refs` |
| `REPORT` | `REPORT_SENT` | Set `report_url`; pause `promotion`, naming `report_url` → `promote`: set `lifecycle.promote` and `close` with `promote` `{issue, proposal}`; `close`: `close` |
| `COUNCIL` | `COUNCIL_RULING` | As for `CONCERNS` in *Blockers and Council*; `council_signoff` always pauses |

Every `PROPOSE` dispatch records its `context_refs` in `lifecycle.propose_context_refs`. The reporter's `context_refs` in these flows are exact, never carried forward from the dispatch they replace: a `draft` after `PROPOSAL_READY` carries `report_input` (the planner's `PROPOSAL_READY` envelope) followed by `proposal.md` in the workstream's state directory, which the planner wrote; a revised `draft` carries `report_input` (the reporter's latest `REPORT_DRAFTED` envelope) followed by `proposal.md`; `send` carries only that `report_input`, so it sends exactly the draft the user saw. `replan` and the `promotion` pause exist only in these flows: `replan` at a `report_approval` of another flow is `EVENT_INVALID`. `skip` is not offered at their `report_approval`, since sending is the point of the flow; a user who wants nothing sent drops the workstream.

Three envelope checks apply only in these flows; each failure is `ENVELOPE_INVALID` and changes nothing:

- `PROPOSAL_READY` requires `proposal.md` in the workstream's state directory, not empty and rewritten during the dispatch (its `st_mtime_ns` is not `null` and differs from `lifecycle.dispatched.proposal_mtime_ns`), and an empty `files_touched`. A `PROPOSE` planner therefore always rewrites `proposal.md`, even when the proposal stands.
- `REPORT_SENT` requires `report.url`. In an idea workstream it must be an issue URL of the workstream's repository, in the form `resolve_state.py new --issue` accepts, since promotion passes it there.
- A `COUNCIL_RULING` on a `PROPOSE` blocker may route only to `forge-planner` or to nobody: every `route_to`, top-level and per option, is `[]` or `["forge-planner"]`. Either way the blocker is resolved and `PROPOSE` runs again; `reject` re-dispatches the council once, as in the build flow.

**Leaving a fork open.** A proposal need not settle every choice: the user may accept none of the council's options and leave the decision to whoever reads the issue. At a `council_signoff` in these flows, `leave_open` (with optional `notes`) resolves the blocker, records a `council_decisions` entry with `option` `null`, `left_open` `true`, and `approved_by` `"user"`, and dispatches `PROPOSE` again with the matrix in `context_refs` and `notes` telling the planner to present the council's options and its recommendation as an open decision, unresolved, with the user's notes. The draft that follows goes through `report_approval` as usual. `leave_open` at a `council_signoff` in the build flow is `EVENT_INVALID`, because building needs one answer.

In `promote`, `issue` is `identity.issue` for a triage workstream and `report_url` for an idea workstream; `proposal` is the absolute path of `proposal.md`. The Hub carries it out with one `resolve_state.py promote` call (workstream-state design), which creates the build workstream before closing this one. A failure before the close leaves this one open and unchanged. When only the close fails, the build workstream stays open with its lock held by the caller and this one stays open; a repeated `promote` takes that build workstream through its `identity.promoted_from` instead of creating another, and finishes the close. Until a `promote` call succeeds the state directory remains at `DONE` with `lifecycle.promote` set, so the next call returns the same `close` and the study is never lost.

The worktree is detached at the default branch and nothing should commit there. `new` sets `last_known_head_sha` to `workstream_base_sha`, so even a commit made before the first dispatch is drift. `RECONCILE` and `REPAIR` are never reached in these flows: any drift stops with `WORKTREE_MOVED` (*Drift and Dirty Trees*). A dirty tree before a `PROPOSE` or `COUNCIL` dispatch pauses `dirty_tree` with `stash` as the only option, since a checkpoint would commit on the detached `HEAD`.

## Blockers and Council

A blocker status appends `{id, kind, agent, phase, details, context_refs}` to `blockers`, sets `resume_phase`, and routes by kind. The blocker's `context_refs` are those of the dispatch that raised it, so the council and the repairs its ruling queues read what the blocked spoke read. A blocker raised while in `REPAIR` keeps the existing `resume_phase`, and the repairs its ruling queues go to the front of `repair_queue`, so the interrupted repair runs after them and the detour still returns where it began.

| Kind | Raised by | Council panel | User sign-off | After approval |
|---|---|---|---|---|
| `CONCERNS` | planner, in a planning phase or `PROPOSE` | `pm`, `architect`, `critic` | Always | Re-dispatch the blocked planning phase (or `PROPOSE`) with the ruling's matrix in `context_refs` and the chosen option's `delta` in `notes` |
| `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH` | dev, or qa for `CONTRACT_MISMATCH` | `senior-dev`, `architect` | Unless auto-resolve applies | Queue the ruling's `route_to` agents with its delta; resume the blocked phase. An empty `route_to` resolves the blocker and runs the blocked phase again (in `REPAIR`, the interrupted item, with the delta added to its notes) with the delta in `notes` |
| `AUDIT_FAILED` | verifier | `architect`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents with its delta; resume the failed verifier phase |
| `TESTS_FAILED` | verifier, in `VERIFY_FINAL` only | `debugger`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents with its delta; resume `VERIFY_FINAL` |
| `CI_FAILED` | scout | `debugger`, `senior-dev` | Unless auto-resolve applies | Queue the ruling's `route_to` agents with its delta; resume `VERIFY_FINAL`. An empty `route_to` (a flaky failure) resolves the blocker and goes to `CI_WATCH` with mode `CI_RERUN`, which re-runs the failed jobs and watches again |
| `CHANGES_REQUESTED` | reviewer | none | Only when a finding routes to the planner (pause `findings_signoff`) | Queue repairs for each `route_to` agent in arrow order (planner, qa, dev); resume `VERIFY_FINAL` |

**Auto-resolve applies** when the mode applies council rulings without asking (`delivery`) and the ruling's `requires_user` is `false`. Otherwise the router pauses `council_signoff`, presenting the ruling's options; `approve` (with the chosen `option`, which must be one of the ruling's option labels, otherwise `EVENT_INVALID`) records a `council_decisions` entry `{id, blocker_id, panel, option, approved_by: "user"}`, where `panel` is the ruling's `panel` (the personas actually convened) and applies the "after approval" column using that option's `route_to` and `delta`; auto-resolve applies the recommended option's and records the entry with `approved_by: "auto"`; `reject` with `notes` re-dispatches the council once with those notes.

Before dispatching the council, the router writes the blocker to `council/<blocker id>.json` in the workstream's state directory and puts that file first in `context_refs`, followed by the `context_refs` of the dispatch that raised the blocker. The returned ruling is stored on the blocker as `ruling`, so a `council_signoff` answered in a later session applies exactly the ruling the user was shown. A ruling for `AUDIT_FAILED` or `TESTS_FAILED` with an empty `route_to` needs no repair: the blocker is resolved and the failed phase runs again. The applied option's `route_to` limits which agents the queue may contain; a ruling whose top-level or option `route_to` names an agent outside `forge-planner`, `forge-qa`, `forge-dev` is an envelope error.

Resolving a blocker removes it from `blockers`. Each repair-queue item names its blocker in `blocker_id` and carries the applied option's `delta` as `notes` and the ruling's matrix followed by the blocked dispatch's `context_refs` as `context_refs`. When an item completes and no remaining item names the same blocker, that blocker is removed, so a blocker raised during a repair is removed when its own repairs finish, not with the outer one.

`lifecycle.blocker_counts` counts blockers raised per kind over the workstream's life. Raising a fourth blocker of the same kind pauses `attempt_limit` instead of routing it, with the same `retry` (resets that kind's count) and `stop` options as the per-phase limit. This bounds loops that re-enter phases — review, repair, verify, review — which the per-phase count alone resets.

## Human-Code Approvals

`HUMAN_CODE_CHANGE_REQUESTED` pauses `human_code_approval`, presenting the paths and line ranges from the escalation. `approve` appends `{id, agent, paths, ranges, base_sha, approved_by: "user"}` to `approvals`, with `base_sha` set to the escalating envelope's `head_sha` (the commit whose lines the user was shown), and re-dispatches the same phase with the approval ID appended to the replaced dispatch's `notes`; `reject` re-dispatches the same phase with the user's notes appended to the replaced dispatch's `notes`. In `REPAIR`, the replaced dispatch's notes are the queue item's, so its delta is kept either way. The gate component treats an approval as covering only the listed ranges as they stood at `base_sha`.

## Drift and Dirty Trees

On every call, after any event is applied, a workstream whose worktree is detached (`pr_review`, `idea`, or `triage`) stops with `WORKTREE_MOVED` on any drift, and steps 1, 2, 3, and 5 do not apply; nothing should commit there, so there is nothing to reconcile. The stop leaves state unchanged and repeats on every call until `HEAD` is back at `last_known_head_sha` (the user resets the worktree, for example `git checkout --detach <sha>`) or the user drops the workstream. In a build workstream:

1. **Drift `ahead` with human commits, `behind`, or `diverged`:** set `verifier_pass_sha` and `reviewer_pass_sha` to `null`. If the next phase is already a verifier phase (`PRE_QA`, `PRE_DEV`, `POST_QA`, `VERIFY_FINAL`, `RECONCILE`), continue to it. Otherwise set `after_reconcile` to the next phase — or to `VERIFY_FINAL` when the next phase is `REPORT` or `CI_WATCH`, whose commits were never verified, reviewed, or pushed — and go to `RECONCILE`. In both cases set `last_known_head_sha = HEAD`.
2. **Drift `ahead` with only Forge commits** from a dispatch whose envelope never arrived (the session ended while the spoke ran): treated as in step 1, so the verifier accounts for the work before the phase is retried.
3. **Applied envelope from a committing agent:** set `last_known_head_sha = head_sha`. When the envelope's range also holds commits from someone else and the envelope leads to a pause, `last_known_head_sha` keeps its value from before the call, so those commits are still reported as drift when the pause is answered rather than absorbed into the anchor.
4. **Outstanding dispatch on a dirty tree:** returning an outstanding dispatch again counts as dispatching, so a dirty tree pauses `dirty_tree` as in step 7. The changes may be the unfinished work of a spoke whose session ended, or the user's own edits; the question says so, because only the user can tell which, and a `checkpoint` records them as human-authored.
5. **Workstream about to close:** drift found on a call that would return `close` goes to `RECONCILE` with `after_reconcile = VERIFY_FINAL`, so the new commits are verified and reviewed before the workstream finishes.
6. **First dispatch:** when `last_known_head_sha` is still `null`, dispatching sets it to `HEAD`, so commits made before the first spoke commit are still seen as drift. (Idea and triage workstreams never reach this: `new` sets the anchor.)
7. **Dirty tree:** before dispatching any agent except `forge-scout` and `forge-reporter`, pause `dirty_tree` with options `checkpoint` (the Hub commits the changes as `wip(user): partial implementation`, without Forge trailers) and `stash`. In an `idea` or `triage` workstream `stash` is the only option. For `stash` the Hub runs `git stash push -u -m "forge:<workstream>"`, reports the stash it made, and never restores it; the stash is the user's to apply. The answer clears the pause; the next call re-checks the tree.

A pause that is waiting for the user is not disturbed by drift: drift is handled when the pause is answered.

## Attempt Limits

Each dispatch into a phase increments `attempts[phase]`; entering a phase from another phase resets its count, and so does dispatching the next item of the repair queue, so in `REPAIR` the limit counts retries of one repair rather than the length of the queue. A phase about to be dispatched for the fourth time pauses `attempt_limit` instead, with options `retry` (resets the count) and `stop`. This bounds automatic loops: repeated verifier failures, repeated council rulings that do not resolve a blocker, and crash–retry loops.

A dispatch made while applying a user event — `revise`, `reject`, `wait`, an approval that re-dispatches the same phase, or `retry` itself — sets that phase's count to `1` and is never paused by the limit. The user's answer is new input, so the automatic retries after it start a fresh count; loops that pass through the user each time remain bounded by the per-kind blocker count.

## Statuses by Phase

Besides `ERROR`, which every dispatch may return, the router accepts exactly these statuses:

| Phase | Accepted |
|---|---|
| `RECON`, `PR_FETCH` | `OK` |
| `PROPOSE` | `PROPOSAL_READY`, `CONCERNS` |
| `PLAN_S1`–`PLAN_S4` | `STAGE_COMPLETE`, `READY_FOR_QA`, `CONCERNS` |
| `PLAN` | `READY_FOR_QA`, `CONCERNS` |
| `POST_PLAN`, `POST_QA` | `PASS`, `AUDIT_FAILED` |
| `PRE_QA`, `PRE_DEV` | `PARTIAL_REMAINING`, `NOTHING_REMAINING` |
| `QA_SKEL` | `SKELETON_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED` |
| `QA_FULL` | `TESTS_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED` |
| `DEV_STUB` | `STUBS_COMMITTED`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED` |
| `DEV_FULL` | `IMPLEMENTATION_COMPLETE`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED` |
| `VERIFY_FINAL` | `PASS`, `AUDIT_FAILED`, `TESTS_FAILED` |
| `REVIEW_INTERNAL` | `REVIEW_APPROVED`, `CHANGES_REQUESTED` |
| `PUBLISH` | `PUBLISHED`, `MERGE_READY`, `MERGE_BLOCKED`, `PUBLISH_REFUSED` |
| `CI_WATCH` | `CI_GREEN`, `CI_FAILED`, `CI_NONE` |
| `REPORT` | `REPORT_DRAFTED`, `REPORT_SENT` |
| `RECONCILE` | `PASS` |
| `COUNCIL` | `COUNCIL_RULING` |
| `REPAIR` | `REPAIR_COMMITTED`, `CONTRACT_MISMATCH`, `HUMAN_CODE_CHANGE_REQUESTED` |
| `PR_REVIEW` | `PR_REVIEW_READY` |

`TESTS_FAILED` is accepted only in `VERIFY_FINAL`: earlier verifier phases expect red tests, so failing tests there are not a blocker.

## Statuses by Mode

The transition table lists which statuses each phase accepts. Within a phase, the mode narrows them further:

| Phase and mode | Not accepted |
|---|---|
| `PLAN_S1`–`PLAN_S3`, planner mode `strict` | `READY_FOR_QA` (strict planning finishes only at stage 4) |
| `PLAN_S4`, planner mode `strict`; any planning phase, planner mode `auto` | `STAGE_COMPLETE` |
| `REPORT`, `draft` | `REPORT_SENT` |
| `REPORT`, `send` | `REPORT_DRAFTED` |
| Any `forge-planner` dispatch | `HUMAN_CODE_CHANGE_REQUESTED` |
| `REPAIR`, any agent | Every status but `REPAIR_COMMITTED`, `ERROR`, `HUMAN_CODE_CHANGE_REQUESTED` (except from `forge-planner`), and, for `forge-qa`, `CONTRACT_MISMATCH` |

## Context Carried into Re-dispatches

A dispatch of the same phase caused by a user answer (`revise`, `reject` of a human-code change, approval of a human-code change) keeps every `context_refs` entry of the dispatch it replaces and appends the new ones, so a planner revising stage 2 still has the scout's brief. `REPORT` dispatches are the exception: their `context_refs` are exactly those listed for each flow.

When the workstream directory holds `task.md` (the user's task description, written by `new --task-file`), every `forge-planner` dispatch, every `forge-reviewer` dispatch in `REVIEW_INTERNAL`, and every `RECON` dispatch in an idea or triage workstream lists it first in `context_refs`, once, so the planner plans, the reviewer reviews, and the scout searches against the user's own words.

## Agent Modes

| Agent | Modes |
|---|---|
| `forge-planner` | `strict`, `auto`, `naive`, `repair`, `propose` |
| `forge-qa` | `skeleton`, `full`, `repair` |
| `forge-dev` | `stub`, `full`, `repair` |
| `forge-verifier` | `POST_PLAN`, `PRE_QA`, `PRE_DEV`, `POST_QA`, `VERIFY_FINAL`, `RECONCILE` |
| `forge-reviewer` | `internal`, `external_pr` |
| `forge-scout` | `PRE_PLAN`, `TRIAGE`, `IDEA`, `PR_FETCH`, `CI_WATCH`, `CI_RERUN` |
| `forge-publisher` | `push`, `merge` |
| `forge-reporter` | `draft`, `send` |
| `forge-council` | the blocker kind |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Router reads repository condition itself | Imports the wake logic and runs it on every call | Hub passes a wake report in | One call per step for the Hub, and the router can never act on a stale report. |
| Re-derivable decisions | No-event calls return the pending pause or the outstanding dispatch | Hub remembers what it was doing | The Hub's memory does not survive a session; state does. |
| Plan approval | Every mode but `delivery` pauses at least once on the finished plan, before any test or code | No pause until review in every mode | Planning output decides everything downstream; one approval is cheap compared with rebuilding against a wrong plan. `delivery` is for a user who hands the change over and approves only its publishing. |
| Pacing | One `config.mode`, read at each decision | Separate `plan_mode`, `collaborative`, and `council_auto_resolve` settings | The user picks and switches one name; every pause is derived from it, so no stored combination falls outside the six modes. |
| Build flow after a mode switch | Fixed at `POST_PLAN`; only edit windows and approvals follow the new mode | Re-derived at each phase | Switching mid-pass from two-pass to single pass would strand skeletons and stubs without their full counterparts; keeping the flow and dropping its pauses finishes the work cleanly. |
| Kill switch | Checked by the router before anything else | Checked by the Hub before each router call | A script check cannot be skipped or mistyped, and it needs no shell command the harness may refuse in a worktree session. The guard still blocks every spoke tool call. |
| Reviewer findings | Routed straight to repairs; council not involved | Council (`senior-dev`, `critic`) triages findings first | Review findings are already specific and tagged with an owner; a council round adds cost without new information. A finding for the planner still needs the user's sign-off. |
| Publish approval | Always paused for the user, who chooses `push` or `merge` | Publish automatically after review | Pushing and merging are outward-facing and hard to undo. |
| Refused or unavailable push | `publish_approval` offers only `merge` and `hold` without an `origin`, and returns after `PUBLISH_REFUSED` | Offer push regardless and stop on refusal; only return after refusal | A refusal that stops leaves a user who has no remote, or a rejected push, with no way to choose `merge`; they would merge by hand and close the workstream as dropped, so the archive misstates it. Not offering an impossible push avoids the refusal in the common case. |
| Local merge | Forge checks the merge is possible; the user performs it | Forge merges into `main` | Changing `main` is the user's call; Forge stops at proving it would succeed. |
| Commit gate at `VERIFY_FINAL` | The router runs the computable checks before recording `verifier_pass_sha` | Trust the verifier's `PASS` | *Scripts over prompts*: a model's claim does not stand in for a check a script can make. |
| Where merged specs reach the user | Named in the `publish_approval` question, computed by the router | The Commander relays each spoke's summary; a separate pause | The router already runs scripted checks and the user already answers this pause; a summary relay would have a model copy IDs, and a pause of its own would interrupt every workstream a merge touched. |
| Empty-route ruling on a blocker raised in `REPAIR` | Re-run the interrupted repair item with the delta added to its notes | Jump to `resume_phase` | Jumping skips the interrupted item, which then stays in the queue unrun; re-running it keeps every queued repair. |
| Commit gate range | `workstream_base_sha..HEAD`, with planning commits exempt from the human-code check | `plan_base_sha..HEAD`; a separate gate run at plan approval | Starting at `plan_base_sha` leaves the planner's commits unchecked for trailers and boundaries. One range at one call site covers every Forge commit; the exemption reflects that the user approved the planning commits with the plan. |
| Guard-ran check | Refuse a result whose dispatch's guard record lacks the dispatched agent, with `GUARD_INACTIVE`, before the other envelope checks | Trust the installed guard; check at the verifier's phases only; report `ENVELOPE_INVALID` | Claude Code skips hooks silently in several cases, and a script check on every result catches all of them. Checking it first stops the Commander from offering an envelope retry that cannot fix an inactive guard; a code of its own names the real cause. |
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
| Unrepairable gate errors | Human-code findings get a retroactive approval pause; anything else stops for the user | Raise `AUDIT_FAILED` and let the council route repairs | A repair adds commits after the bad one, which stays in the range; agents cannot rewrite history, so the loop would only end at the attempt limit. |
| Drift return | `after_reconcile`, separate from `resume_phase` | One shared field | Drift during a council or repair detour would overwrite where the detour returns. |
| Drift after publishing | Back to `VERIFY_FINAL` | Back to the interrupted phase | `REPORT` and `CI_WATCH` would describe and watch commits nobody verified, reviewed, or pushed. |
| Docs-only plans | Empty scope allowed; `POST_PLAN` goes straight to `VERIFY_FINAL` | Require a scope | A plan that changes only docs has no tests or code for QA and dev to write. |
| Read-only agents and dirty trees | Scout and reporter may run on a dirty tree; every other agent waits | Block all dispatch | Scout and reporter read remote state or committed receipts only; the verifier and reviewer would otherwise certify a HEAD that differs from the files on disk. |
| Approval before an idea or triage sends | One `report_approval` on the reporter's draft, offering `approve`, `revise` (reword), and `replan` (rework the proposal) | Approve the proposal, then the draft; send `proposal.md` as written | The user approves the exact text the tracker receives, in one question, and can still send the planner back. A second pause would mostly repeat the first; sending the proposal as written drops the step that shapes it for the tracker. |
| Forks in a proposal | `CONCERNS` to the council and the user, then `PROPOSE` again; the user may `leave_open`, filing the council's options and recommendation as an open decision | The planner lists open questions in the proposal without a council; the user must pick an option | A fork the planner cannot settle gets a considered set of options before anything is filed, but the user may disagree with every option, or the decision may belong to the people who read the issue; an issue can carry an open decision where a build cannot. |
| Promotion | A `promotion` pause after sending; `close` carries `promote`, kept in `lifecycle.promote` until one `resolve_state.py promote` call creates the build workstream and then closes this one | The router creates the build workstream; the Commander closes, then runs `new`; a separate command | Creating a workstream is `resolve_state.py`'s job and needs a worktree the router never makes. Closing first archives the proposal and loses the study when `new` then fails; one script call that creates before it closes never loses the study, and when only the close fails, a repeated call finishes the close on the build workstream it already created. Asking at the end of the flow is when the user knows whether the change is worth building. |
| Proposal freshness | Record `proposal.md`'s `st_mtime_ns` on the `PROPOSE` dispatch (`null` when absent); `PROPOSAL_READY` needs a current value that is not `null` and differs from it | Record a dispatch start time and require a later file mtime; compare with the guard record's mtime; check only that `proposal.md` is not empty | Comparing two timestamps of the same file needs no wall clock, so a filesystem with coarse timestamps cannot refuse a quick valid rewrite; only a rewrite within the same timestamp tick goes unseen. The guard record is rewritten whenever a subagent joins, so it postdates valid proposals. Without a freshness check, a replan or council re-run that leaves `proposal.md` untouched passes the stale proposal to the reporter. |
| Drift in a detached worktree | One stop, `WORKTREE_MOVED`, for `pr_review`, `idea`, and `triage`, cleared only by restoring `HEAD`; in idea and triage also checked before a result is applied | Reconcile as in the build flow; a stop code per flow; check only after applying | Nothing should commit in these worktrees and no later gate would check a commit, so reconciling has nothing to verify against. Checking before applying gives a stray commit one stop code, whatever `head_sha` the spoke reports, and leaves state as it was. |
| Task description in dispatches | `task.md` first in every planner dispatch and every internal review dispatch | Planner only; every dispatch | The planner turns the task into a plan and the reviewer checks the change against it; builders and auditors work from the plan and specs the planner produced. |

## Open Questions & Future Decisions

### Deferred
1. Stacked workstreams whose base branch changes after creation — the parent receives review fixes, or merges into the default branch (often squashed). Agents cannot rebase, so the user must; detecting the change (scout, or drift against `identity.base_branch`) and pausing with instructions is designed with the scout and Commander.

## References

- `docs/high-level-design.md` — scripts own routing; tenets *Scripts over prompts*, *Stop over guess*.
- Workstream state design (`docs/intent/workstream-state/`) — wake, drift kinds, `stale_shas`, `close`.
- Gates design (`docs/intent/gates/`) — the commit gate the router runs at `VERIFY_FINAL` and the approval semantics it enforces.
- Guard design (`docs/intent/guard/`) — the per-dispatch guard record the guard-ran check reads.
