---
parent: spokes
prefix: SPOKE-PLAN
---

# Planner

## Context and Design Philosophy

`forge-planner` turns the task into the intent every later spoke builds against: in a LID repository, the HLD, LLD, EARS, and edge-audit stages of linked-intent development; in any other repository, a `PLAN.md`. It writes plan paths only (`PLAN.md` at the root and anything under `docs/`), and its last act in every plan is naming the scope: the repository path patterns the change will touch, which the verifier and builders work from.

The planner cannot talk to the user. Every point where linked-intent development would stop for the user's judgment is a Forge pause instead: a stage boundary is `plan_approval` in `strict` cadence, and a reading only the user can choose is `CONCERNS`, which goes through the council to the user. The planner never picks one reading of an ambiguous requirement itself.

Bootstrapping linked-intent development in a repository that has none is not part of Forge. A repository is planned in LID mode only when it already has LID structure (`config.repo_mode`).

## Inputs

The invocation's `context_refs` carry the scout's directory when the workstream came from an issue, and the council's decision matrix after `CONCERNS`. `notes` holds the user's text after `revise`, the chosen council option's `delta` after `CONCERNS`, or the review findings in `repair`.

## Modes

| Mode | Phase | Does | Returns |
|---|---|---|---|
| `strict` | `PLAN_S1`–`PLAN_S4` | One LID stage, matching the phase: S1 HLD, S2 LLD, S3 EARS specs, S4 edge audit | S1–S3: `STAGE_COMPLETE`; S4: `READY_FOR_QA` |
| `auto` | `PLAN_S1` onward | Every remaining stage in order, in one dispatch | `READY_FOR_QA` |
| `naive` | `PLAN` | Writes `PLAN.md` | `READY_FOR_QA` |
| `repair` | `REPAIR` | Applies review findings routed to the planner (`notes`) | `REPAIR_COMMITTED` |

`strict`, `auto`, and `naive` may also return `CONCERNS`. `repair` returns only `REPAIR_COMMITTED` or `ERROR`; a repair that reveals a fork returns `ERROR` describing it. The planner never returns `HUMAN_CODE_CHANGE_REQUESTED`: its commits are exempt from the human-code rule.

### LID stages

Each stage follows the `linked-intent-dev` skill's phase of the same content, applied to the workstream's task, and commits its output with that stage's `Forge-Stage` (`LID-S1`–`LID-S4`). The edge audit's resolutions land as narrowing edits to the specs in the same stage. A stage whose content needs no change (the HLD already covers the task) commits nothing and says so in the summary.

The skill's instructions to stop for the user are met by Forge's pauses: in `strict` cadence the planner returns after each stage; in `auto` cadence the user chose to see the plan once, when it is complete. In both, a spec fork — a requirement with more than one plausible reading that the task, the issue, and the repository do not settle — is returned as `CONCERNS`, with each reading and its consequences in `escalation.details`. Work finished before the fork stays committed; the dispatch that resumes after the ruling continues from it. In `auto` cadence it goes through every stage again in order, checking each against the ruling and keeping what still holds, so a stage left half-done before the fork is finished and a stage that needed no commit is not skipped.

### Naive plan

`PLAN.md` holds the goal, the approach, the scope patterns, the tests to write, and anything left open. An existing `PLAN.md` from an earlier workstream is replaced.

### Scope

`READY_FOR_QA` carries `scope.assigned`: path patterns in the gate's glob syntax covering every test and production path the plan expects to add or change. Plan paths are not included, so a plan that changes only docs returns an empty list and the workstream goes from plan verification straight to final verification. The patterns are the verifier's unit of progress, so they name areas the plan treats as units (`src/limits/**`), not every file.

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

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — planning phases, `plan_approval`, `CONCERNS` routing.
- Gates design (`docs/intent/gates/`) — plan path class; planning commits exempt from the human-code check.
- `linked-intent-dev` skill — the content of each LID stage.
