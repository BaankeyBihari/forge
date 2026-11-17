---
parent: spokes
prefix: SPOKE-PROTO
---

# Spoke Protocol

## Context and Design Philosophy

Every spoke reads the same invocation, commits under the same trailer rules, and reports through the same envelope. The values in those exchanges that a model would otherwise copy by hand — dispatch ID, agent name, `HEAD`, the files a dispatch changed, four commit trailers — are exactly the values a mistyped character breaks silently. `forge_spoke.py` (in `reference/scripts/`, Python standard library only) produces them from `state.json` and git, and the `forge-spoke` skill (in `reference/skills/forge-spoke/`) tells every spoke when to call it.

`forge_spoke.py` checks early what the router will check later: a spoke that would return an envelope the router rejects learns why while it can still fix it. The router stays the authority; `forge_spoke.py` imports the router's status tables instead of keeping its own.

## Commands

```text
forge_spoke.py invocation --state PATH --dispatch ID
forge_spoke.py commit     --state PATH --dispatch ID --message-file FILE [--stage STAGE]
forge_spoke.py finish     --state PATH --dispatch ID
forge_spoke.py scope      --state PATH
```

Every command prints one JSON object with a `status` and exits `0`; a usage error exits `2` with the message on stderr. Every command except `scope` first checks that `lifecycle.dispatched.dispatch_id` equals `--dispatch`, and otherwise returns `NOT_DISPATCHED` and does nothing else: a spoke whose dispatch was replaced (a re-dispatch after a crash, a discarded envelope) stops before it changes anything.

A missing or unparsable `state.json` returns `STATE_INVALID` with a `detail`. Git commands run in `identity.worktree_path`.

### `invocation`

Returns `{"status": "OK", "invoke": <lifecycle.invoke>, "phase": <lifecycle.current_phase>, "state": <the whole state.json>}`. The spoke reads its mode, stage, scope, context files, and notes from `invoke`, and identity, anchors, scope manifest, approvals, and council decisions from `state`.

### `commit`

Commits what the spoke has staged, with the Forge trailers appended to the message from `FILE`:

| Trailer | Value |
|---|---|
| `Forge-Agent` | `invoke.agent` |
| `Forge-Stage` | `invoke.stage`, or `--stage` |
| `Forge-Mode` | `invoke.mode` |
| `Forge-Input-SHA` | `invoke.base_sha` |

| Status | When |
|---|---|
| `COMMITTED` | The commit was made; `sha` is the new `HEAD` |
| `NOT_A_BUILDER` | `invoke.agent` is not `forge-planner`, `forge-qa`, or `forge-dev`; nothing is committed |
| `NOTHING_STAGED` | The index matches `HEAD` |
| `BOUNDARY_VIOLATION` | A staged path is outside the agent's write boundary (gates design, *Write boundaries*); `paths` lists them and nothing is committed |
| `STAGE_INVALID` | `--stage` was given by an agent other than the planner in `auto` mode or in a `strict` revision, or names a stage other than `LID-S1`–`LID-S4` |

**Spec markers.** In a repository whose `config.repo_mode` is `LID`, a `commit` for a `forge-qa` or `forge-dev` dispatch in mode `full` or `repair` also brings the workstream's spec markers in line with the evidence. After the `NOTHING_STAGED` and boundary checks pass, it computes, against the commit being made (`HEAD` plus the staged changes, never unstaged edits), every workstream spec as `lid_check.py` defines it (verifier design) that is not marked `D` and has no `DUPLICATE_ID` finding. Each such spec whose line is `[ ]` and that has both a test citation and a code tag becomes `[x]`; each whose line is `[x]` and that lacks either becomes `[ ]`. Each change is made to the spec's line, found by its ID, in both the staged copy and the worktree copy of the file, so unstaged edits elsewhere in the file survive; the staged copy goes into the same commit. When the worktree copy no longer has a line with that ID, only the staged copy changes, so the unstaged deletion survives too. `COMMITTED` then also carries `marked` and `unmarked`, the IDs changed each way (empty lists when none). If the commit itself fails after markers were changed, the spec files are restored to their staged state before `commit` returns, so a failed `commit` leaves the worktree as it found it. `skeleton` and `stub` commits change no markers: placeholders and stubs carry `@spec` tags before the behavior exists. A spec file staged by the agent itself is still a boundary violation; only `commit`'s own marker changes are allowed (gates design, *Write boundaries*).

Because the markers follow tests and tags, a spec can show `[x]` before its tests pass; the suite run at `VERIFY_FINAL` is what holds the behavior, and the markers are correct once it passes.

`--stage` exists for the planner in `auto` mode, which completes several LID stages in one dispatch and labels each stage's commit with that stage, and for a `strict` revision (a planner dispatch whose `invoke.notes` is not `null`), whose changes may belong to stages before the current one. The boundary check uses the same path classes as the gate, so a commit `commit` accepts is one the gate's boundary check accepts.

### `finish`

Completes and checks the result envelope the spoke wrote to `envelopes/<dispatch_id>.json` beside `state.json`. The spoke writes only `status`, `summary`, and the fields its status needs (`scope`, `escalation`, `ruling`, `publish_target`, `review`, `report`); `finish` sets:

| Field | Value |
|---|---|
| `dispatch_id`, `agent` | From `lifecycle.dispatched` |
| `head_sha` | Current `HEAD` for a committing agent; `base_sha` for every other agent, so commits someone else made while it ran are drift for the router, not an error |
| `files_touched` | The paths changed by the first-parent commits in `base_sha..head_sha` whose `Forge-Agent` is the dispatched agent; empty for an agent that does not commit |

It then checks the envelope, collecting every problem:

- The file parses as a JSON object with a string `status` and a string `summary`.
- `status` is allowed for the agent in the current phase and mode, by the router's tables (`ERROR` is always allowed).
- Unless `status` is `ERROR`: for a committing agent, the worktree has no uncommitted changes, counting untracked files that are not ignored, because the router would pause on them; for any other agent, no commit since `base_sha` carries its own `Forge-Agent`.
- The status's required fields are present and well formed:

| Status | Required |
|---|---|
| `ERROR`, `CONCERNS`, `TEST_CHANGE_REQUESTED`, `CONTRACT_MISMATCH`, `AUDIT_FAILED`, `TESTS_FAILED`, `CI_FAILED`, `CHANGES_REQUESTED`, `HUMAN_CODE_CHANGE_REQUESTED`, `PUBLISH_REFUSED`, `MERGE_BLOCKED` | `escalation.details`, a non-empty string |
| `HUMAN_CODE_CHANGE_REQUESTED` | Also `escalation.paths` (list of strings) and `escalation.ranges` (object mapping each path to a list of `[start, end]` line pairs) |
| `CHANGES_REQUESTED` | Also `escalation.findings`, a non-empty list of `{route_to, detail}` with `route_to` one of `forge-planner`, `forge-qa`, `forge-dev` |
| `READY_FOR_QA` | `scope.assigned`, a list of path patterns, empty only for a plan that changes plan paths alone |
| `REPAIR_COMMITTED` from `forge-planner` | `scope.assigned`, when present, a list of path patterns, empty only for a plan that changes plan paths alone |
| `PARTIAL_REMAINING`, `NOTHING_REMAINING`, and `PASS` in `RECONCILE` | `scope.completed` and `scope.remaining`, lists of path patterns that together equal `scope_manifest.assigned` |
| `COUNCIL_RULING` | `ruling` with `matrix_ref` (an absolute path to an existing file under the workstream's `council/`), `requires_user` (boolean), `panel` (non-empty list of persona names), `options` (non-empty list of `{label, summary, route_to, delta}` with distinct labels), `recommended` (one of the labels), and `route_to` and `delta` equal to the recommended option's; every `route_to` a list of `forge-planner`, `forge-qa`, `forge-dev` and every `delta` a string |
| `PUBLISHED`, `MERGE_READY`, `MERGE_BLOCKED`, `PUBLISH_REFUSED` | `publish_target` equal to `invoke.mode` |
| `PR_REVIEW_READY` | `review` (reviewer design) |
| `REPORT_DRAFTED` | `report` (reporter design) |
| `REPORT_SENT` | `report.url`, a non-empty string |

| Status | When |
|---|---|
| `FINISHED` | No problems; the completed envelope has been written back to the same file |
| `INVALID` | `problems` lists each problem; the file is left as the spoke wrote it |
| `ENVELOPE_MISSING` | There is no envelope file |

A spoke runs `finish` until it returns `FINISHED`, fixing each problem and running it again: editing the envelope, committing work it meant to commit, or removing files its own test runs left behind. Problems it cannot fix that way (a commit by an agent that does not commit, changes it did not make) are reported in an `ERROR` envelope instead.

### `scope`

Facts for the verifier's scope judgment, from `scope_manifest.assigned` and git:

```json
{"status": "OK",
 "patterns": [{"pattern": "src/limits/**",
               "files": [{"path": "src/limits/window.py", "class": "production"}],
               "commits": [{"sha": "...", "kind": "human", "agent": null, "paths": ["src/limits/window.py"]}]}],
 "completed": [...], "remaining": [...]}
```

`files` are the paths at `HEAD` matching the pattern, with their gates path class. `commits` are the first-parent commits in `plan_base_sha..HEAD` that touched a matching path, oldest first, with `kind` `human` or `forge` and the `Forge-Agent` value. `completed` and `remaining` repeat the current `scope_manifest`. While `plan_base_sha` is `null`, `commits` is empty.

## The `forge-spoke` Skill

Preloaded into every spoke, it carries the procedure of the spokes design's *A Dispatch, End to End*, in that order, and these rules:

- Read the invocation with `invocation` before anything else; on `NOT_DISPATCHED`, end the turn saying the dispatch was replaced.
- Commit only through `commit`, staging with `git add` first. Never pass trailers by hand.
- Write the envelope's judgment fields with the file-write tool, then run `finish` until `FINISHED`.
- Reply to the Hub with one paragraph; put nothing the router needs in the reply.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Spec markers | `commit` sets `[x]` or `[ ]` in qa and dev `full` and `repair` commits to match whether each workstream spec is tested and tagged | Only dev marks, never unmarking; one router commit at `VERIFY_FINAL`; dev edits spec files | The decision is mechanical, so a script makes it in the commits that change the evidence. Marking only from dev leaves specs finished by QA or by the user unmarked and a removed test still marked; a router commit would make the router a writer of the repository. |
| Envelope completion | The spoke writes judgment fields; `finish` fills computed fields in place | The model writes the whole envelope | `head_sha` and `files_touched` are git facts; a model computing them is the error the router would reject. |
| Commit helper | `forge_spoke.py commit` appends trailers from the invocation | The spoke types `--trailer` values; the gate catches mistakes at `VERIFY_FINAL` | Agents cannot amend, so a bad trailer found later cannot be fixed. Writing them from state makes them correct by construction. |
| Who may use `commit` | Only the dispatched builder, checked from state | Rely on the guard | The guard sees `python3 forge_spoke.py`, not `git commit`, so the script enforces the builder rule itself. |
| Early checks | `finish` reuses the router's status tables and adds field-shape checks | No early check; a separate copy of the rules | A spoke that learns of a problem before returning fixes it in the same dispatch; importing the router's tables keeps one source of truth. |
| Stale dispatches | Every dispatch-scoped command refuses a dispatch ID that is not outstanding | Let the router reject the envelope later | A replaced spoke would otherwise commit work the new dispatch duplicates. |
| Scope facts | `scope` reports files and commits per pattern; the verifier judges | The script decides completeness | Whether a human commit finished a pattern or only started it is judgment; which commits touched it is a fact. |
| Stage labels in a `strict` revision | `--stage` allowed when the planner dispatch carries `notes` | Every commit takes the phase's stage | A revision can change work from an earlier stage; labelling it with the current stage mislabels that commit's stage. |

## References

- Spokes design (`docs/intent/spokes/`) — the dispatch sequence this skill carries.
- Router design (`docs/intent/router/`) — invocation and result envelopes, allowed statuses, modes and stages.
- Gates design (`docs/intent/gates/`) — trailers, path classes, write boundaries.
- Guard design (`docs/intent/guard/`) — envelope file write rule; shell commands outside the guard's reach.
