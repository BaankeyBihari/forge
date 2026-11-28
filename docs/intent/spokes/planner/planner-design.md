---
parent: spokes
prefix: SPOKE-PLAN
---

# Planner

## Context and Design Philosophy

`forge-planner` turns the task into the intent every later spoke builds against: in a LID repository, the HLD, LLD, EARS, and edge-audit stages of linked-intent development; in any other repository, a `PLAN.md`. In a build workstream it writes plan paths only (`PLAN.md` at the root and anything under `docs/`), and its last act in every plan is naming the scope: the repository path patterns the change will touch, which the verifier and builders work from.

In an idea or triage workstream the planner studies a change instead of planning it: in mode `propose` it investigates the code without changing the repository and writes a proposal, `proposal.md` in the workstream's state directory. The reporter turns the proposal into a new issue (idea) or a comment on the issue (triage), and promotion makes it the task description of the build workstream that follows.

The planner cannot talk to the user. Every point where linked-intent development would stop for the user's judgment is a Forge pause instead: a stage boundary is `plan_approval` in `strict` cadence, and a reading only the user can choose is `CONCERNS`, which goes through the council to the user. The planner never picks one reading of an ambiguous requirement itself.

Bootstrapping linked-intent development in a repository that has none is not part of Forge. A repository is planned in LID mode only when it already has LID structure (`config.repo_mode`).

## Inputs

The invocation's `context_refs` carry `task.md` first when the workstream has one, the scout's directory when the workstream came from an issue, and the council's decision matrix after `CONCERNS`. `notes` holds the user's text after `revise`, the blocked dispatch's `notes` followed by the chosen council option's `delta` after `CONCERNS`, or the review findings in `repair`. The inputs of mode `propose` are listed under *Proposal*.

## Modes

| Mode | Phase | Does | Returns |
|---|---|---|---|
| `strict` | `PLAN_S1`–`PLAN_S4` | One LID stage, matching the phase: S1 HLD, S2 LLD, S3 EARS specs, S4 edge audit | S1–S3: `STAGE_COMPLETE`; S4: `READY_FOR_QA` |
| `auto` | `PLAN_S1` onward | Every remaining stage in order, in one dispatch | `READY_FOR_QA` |
| `naive` | `PLAN` | Writes `PLAN.md` | `READY_FOR_QA` |
| `repair` | `REPAIR` | Applies review findings routed to the planner (`notes`) | `REPAIR_COMMITTED` |
| `propose` | `PROPOSE` | Investigates the code read-only and writes `proposal.md` in the workstream's state directory | `PROPOSAL_READY` |

`strict`, `auto`, `naive`, and `propose` may also return `CONCERNS`. `repair` returns only `REPAIR_COMMITTED` or `ERROR`; a repair that reveals a fork returns `ERROR` describing it. The planner never returns `HUMAN_CODE_CHANGE_REQUESTED`: its commits are exempt from the human-code rule.

### LID stages

Each stage follows the `linked-intent-dev` skill's phase of the same content, applied to the workstream's task, and commits its output with that stage's `Forge-Stage` (`LID-S1`–`LID-S4`). The edge audit's resolutions land as narrowing edits to the specs in the same stage. A stage whose content needs no change (the HLD already covers the task) commits nothing and says so in the summary.

The skill's instructions to stop for the user are met by Forge's pauses: in `strict` cadence the planner returns after each stage; in `auto` cadence the user chose to see the plan once, when it is complete. In both, a spec fork — a requirement with more than one plausible reading that the task, the issue, and the repository do not settle — is returned as `CONCERNS`, with each reading and its consequences in `escalation.details`. Work finished before the fork stays committed; the dispatch that resumes after the ruling continues from it. In `auto` cadence it goes through every stage again in order, checking each against the ruling and keeping what still holds, so a stage left half-done before the fork is finished and a stage that needed no commit is not skipped.

### After a council ruling

A dispatch after an approved ruling, in every mode that returns `CONCERNS`, applies the option the user approved. The matrix is the last entry of `context_refs`, named `council/<blocker id>-matrix-<timestamp>.md`; the `council_decisions` entry in the state the invocation returns (spoke protocol design) whose `blocker_id` is that blocker names the option's label. The planner applies that option as the matrix describes it, reading the matrix for its reasons, with the option's `delta` where `notes` carry one after the blocked dispatch's own `notes`. A council may leave an option's `delta` empty; the matrix still describes the option, so the planner applies it from the matrix alone, and the blocked dispatch's `notes`, when there are any, are still the request the option answers.

### Naive plan

`PLAN.md` holds the goal, the approach, the scope patterns, the tests to write, and anything left open. An existing `PLAN.md` from an earlier workstream is replaced.

### Proposal

Mode `propose` runs in `PROPOSE` of an idea or triage workstream, whose worktree is detached at the default branch. The planner reads the code there to ground the proposal and leaves the worktree as it found it: it commits nothing and changes no file in it. Besides its envelope and memory entry, its one write is `proposal.md` in the workstream's state directory, and `PROPOSAL_READY` carries an empty `files_touched`.

**Inputs.** `context_refs` hold `task.md` when the workstream has one (the user's idea, or what the user wants from the issue) and the workstream's `scout/` directory (what the scout gathered from the tracker: the issue itself in triage, related issues and pull requests in both flows). After `replan` they also hold the earlier `proposal.md` and the reporter's rejected draft envelope; after a council ruling or `leave_open`, the council's decision matrix. `notes` hold the user's text after `replan`; after a ruling, the blocked dispatch's `notes` followed by the chosen option's `delta`; and after `leave_open`, the blocked dispatch's `notes`, then the instruction to present the council's options and recommendation as an open decision, then the user's text when they gave any.

**Writing.** Every dispatch that returns `PROPOSAL_READY` writes `proposal.md` whole, even when its content stands: the router accepts `PROPOSAL_READY` only when the file was rewritten during the dispatch (router design, *Proposal freshness*). When `context_refs` name an earlier `proposal.md`, the planner starts from it and changes what `notes` ask for, so the parts the user did not question carry over, open decisions included. Otherwise it writes the proposal from its inputs. A dispatch that returns `CONCERNS` or `ERROR` leaves `proposal.md` as it was.

**Contents.** `proposal.md` is Markdown written for three readers: the user approving the reporter's draft, the people reading the issue or comment, and the planner of a promoted build workstream, for whom it is `task.md`. It holds, in this order:

1. A `#` title naming the proposed change.
2. **Problem**: what the idea or issue asks for and why, as the planner reads it.
3. **Findings**: what the code does today where the change would land, citing repository paths, and the related issues and pull requests the scout found, by number.
4. **Proposal**: the approach; the areas of the repository it would change, and in a LID repository the design nodes; the alternatives weighed, each with why it was not chosen.
5. **Done when**: the observable outcomes that show the change is complete.
6. **Open decisions**, only when the user left a decision open: for each, the question, the council's options with their consequences, and its recommendation, stated as unresolved.

The proposal is self-contained. It names no path in the workstream's state directory (scout files, the matrix, envelopes) and carries what it needs from them as text. It asks no question the planner could have settled from its inputs, and holds no fork the user has not left open: those are returned as `CONCERNS`.

**Forks.** A requirement with more than one plausible reading that the task, the issue, the scout's context, and the code do not settle is returned as `CONCERNS`, with each reading and its consequences in `escalation.details`; there is no earlier work to commit first. After a ruling the planner applies the approved option as *After a council ruling* says. After `leave_open` it applies the user's text when `notes` carry it, and the user's text decides what stays open. When the text picks one of the council's options or otherwise settles the decision, the planner applies that choice to the proposal and records no open decision for it. Otherwise it adds the decision to **Open decisions** from the matrix and settles nothing in it that the user's text leaves open. The matrix a ruling or `leave_open` refers to is the last entry of `context_refs`, where the router appends it; earlier matrices there belong to earlier forks.

**Replan.** After `replan` the planner reads the rejected draft for what the user saw, then changes what the user's `notes` ask for, starting from the earlier `proposal.md`.

### Scope

`READY_FOR_QA` carries `scope.assigned`: path patterns in the gate's glob syntax covering every test and production path the plan expects to add or change. Plan paths are not included, so a plan that changes only docs returns an empty list and the workstream goes from plan verification straight to final verification. The patterns are the verifier's unit of progress, so they name areas the plan treats as units (`src/limits/**`), not every file.

The `READY_FOR_QA` summary names, with its path, every edit to a test or production path the plan makes that no test will check: an `@spec` tag or a comment to fix, prose in a profile or skill reference. Dev works from the specs and the failing tests, and the router passes this envelope to `DEV_FULL` (router design, *Plan edits no test checks*), so the summary is where dev learns of such an edit.

### Revisions and repairs

After `revise`, the same phase runs again with the user's notes; the planner changes what the notes ask for and commits it under the stage each change belongs to.

A `repair` that changes which paths the plan touches returns the new `scope.assigned` with `REPAIR_COMMITTED`; the router then sends the workstream back through plan verification, so new areas reach QA and dev.

### Specs no test can verify

A spec whose behavior no automated test can check (a prompt's wording, a manual procedure) is written with the `D` marker, and the spec file states why it is deferred, following linked-intent development. The verifier's coverage check is strict for every other spec.

### Human-authored docs

Every planner commit is exempt from the human-code rule (gates design, *Human-authored lines*): the user approves its edits at the plan approval or findings sign-off that leads to them, so the planner may edit HLD and LLD text the user wrote. Production and test paths are outside its boundary.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| User stops | Forge pauses stand in for LID's stops: stage boundaries in `strict`, one plan approval in `auto`, `CONCERNS` for forks | The planner stops LID-style mid-dispatch | A subagent cannot ask the user; the router's pauses are the only path to them. |
| Spec forks | Always `CONCERNS`, never resolved by the planner | Pick the likelier reading and note it | Only the user holds the intent; a guessed reading reaches tests and code before anyone sees it. |
| Edge audit | Stage 4, resolutions applied to the specs in the same stage | A separate verifier pass | The planner already holds the context; the verifier's `POST_PLAN` check comes next anyway. |
| Scope granularity | Area patterns, chosen by the planner | Every file the plan names | Files a plan cannot foresee still fall inside an area; per-file scope would leave them outside every boundary. |
| Bootstrap | Not part of Forge | `map-codebase` in stage 1; `CONCERNS` asking the user to bootstrap | Bootstrapping is interactive and repository-wide; it belongs to the user before a workstream starts. |
| Proposal contents | Fixed parts in a fixed order: title, problem, findings, proposal, done when, open decisions | Free-form text; the finished issue body or comment, sent as written | The reporter shapes the proposal for the tracker and a promoted build planner reads it as its task; both need to find the same parts in every proposal. Sending it as written drops the step that shapes it for the tracker (router design, *Approval before an idea or triage sends*). |
| Self-contained proposal | No state-directory paths; what the proposal needs from scout files or the matrix is carried as text | Link the scout files and the matrix | The issue's readers cannot open the state directory, and promotion closes the study's workstream, so a promoted planner's task would point at files outside its own workstream. |
| Re-dispatch with an earlier proposal | Start from it and change what `notes` ask for | Write the proposal again from the inputs | The user's notes address the proposal they saw; a fresh write can drop or reword parts nobody questioned, including an open decision recorded earlier. |
| `proposal.md` on `CONCERNS` | Left as it was | Write the proposal so far | A later `PROPOSE` dispatch may start from `proposal.md` as the proposal the user saw; a partial write would replace it. |
| Option applied after a ruling | The option the `council_decisions` entry for the matrix's blocker names, as the matrix describes it, with its `delta` when `notes` carry one | Apply the `delta` alone, and the matrix's recommended option when the `delta` is empty; have the router name the option in `notes` | The `delta` is the council's shorthand for the option and may be empty, while the matrix always describes every option. The recommended option is wrong whenever the user approved another. The state the invocation returns already names the approved option, so no router text is needed to find it. |
| Edits no test checks | Named, with their paths, in the `READY_FOR_QA` summary | A new envelope field; listed in the specs | The summary is already the planner's account of the plan, which the user reads at plan approval and dev reads at `DEV_FULL`; a field would add a format to validate for one list, and specs state behavior, not edits. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — planning phases, `plan_approval`, `CONCERNS` routing; idea and triage flows, `replan`, `leave_open`, proposal freshness.
- Workstream-state design (`docs/intent/workstream-state/`) — `proposal.md` in the state directory; promotion makes it the build workstream's `task.md`.
- Gates design (`docs/intent/gates/`) — plan path class; planning commits exempt from the human-code check.
- `linked-intent-dev` skill — the content of each LID stage.
