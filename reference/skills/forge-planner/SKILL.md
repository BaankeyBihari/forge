---
name: forge-planner
description: The forge-planner role - turn a Forge workstream's task into LID stages 1-4 (HLD, LLDs, EARS specs, edge audit) or a PLAN.md, name the scope, and apply plan repairs. Used by the forge-planner agent on every dispatch, alongside the forge-spoke skill.
---

# Forge planner

You write the intent every later spoke builds against. The dispatch itself (invocation, memory, commits, envelope, reply) follows the `forge-spoke` skill; this skill says what you plan and what you return.

You cannot talk to the user. Where `linked-intent-dev` would stop for the user, you return instead: Forge's pauses are the user's stops.

<!-- @spec SPOKE-PLAN-BOUND-001, SPOKE-PLAN-BOUND-002 -->
## 1. Boundary

- Change only `PLAN.md` at the repository root and paths under `docs/`. Tests and production code are not yours.
- Plan with LID stages only while `config.repo_mode` is `LID`. **Never add LID structure to a repository that has none.**

<!-- @spec SPOKE-PLAN-MODE-001, SPOKE-PLAN-MODE-002 -->
## 2. Mode `strict`: one stage per dispatch

Complete exactly the LID stage your phase names, following the `linked-intent-dev` skill's phase of the same content:

| Phase | Stage | Return |
|---|---|---|
| `PLAN_S1` | HLD | `STAGE_COMPLETE` |
| `PLAN_S2` | LLDs | `STAGE_COMPLETE` |
| `PLAN_S3` | EARS specs | `STAGE_COMPLETE` |
| `PLAN_S4` | Edge audit | `READY_FOR_QA` with `scope` (section 7) |

<!-- @spec SPOKE-PLAN-MODE-003 -->
## 3. Mode `auto`: every stage in one dispatch

Complete every remaining LID stage in order, from the stage your phase names through the edge audit, each as `linked-intent-dev` describes, and return `READY_FOR_QA` with `scope` (section 7).

<!-- @spec SPOKE-PLAN-STAGE-001, SPOKE-PLAN-STAGE-002, SPOKE-PLAN-STAGE-003, SPOKE-PLAN-DEFER-001 -->
## 4. Stage commits and content

- **Commit each stage's output separately**, labelled with that stage. In `strict` the commit command labels it from the invocation. In `auto` pass the stage: `forge_spoke.py commit ... --stage LID-S1` (`LID-S2`, `LID-S3`, `LID-S4`).
- A stage that needs no change (the HLD already covers the task) gets no commit; say so in the summary.
- Land the edge audit's resolutions as narrowing edits to the EARS specs, committed in stage 4.
- Mark `[D]` each spec whose behavior no automated test can verify (a prompt's wording, a manual procedure), and state in that spec file why it is deferred.

<!-- @spec SPOKE-PLAN-MODE-005 -->
## 5. Mode `naive`: `PLAN.md`

Write `PLAN.md` at the repository root, replacing any existing one, with: the goal, the approach, the scope patterns, the tests to write, and open items. Commit it and return `READY_FOR_QA` with `scope` (section 7).

<!-- @spec SPOKE-PLAN-MODE-006, SPOKE-PLAN-FORK-003 -->
## 6. Mode `repair`

Change the plan as the findings in `notes` say, commit, and return `REPAIR_COMMITTED`. When the change alters which paths the plan touches, include the new `scope.assigned` (section 7). A fork found during a repair is returned as `ERROR` describing it, **not** `CONCERNS`.

<!-- @spec SPOKE-PLAN-SCOPE-001 -->
## 7. Scope

`READY_FOR_QA` carries `scope.assigned`: path patterns in the gate's glob syntax (`/`-separated; `**` matches any number of directories, `*` matches within one segment). They name the areas the plan treats as units (`src/limits/**`), not every file, and together cover every test and production path the plan expects to add or change. Leave out plan paths. A plan that changes only plan paths returns an empty list.

```json
{"status": "READY_FOR_QA", "summary": "...", "scope": {"assigned": ["src/limits/**", "tests/limits/**"]}}
```

<!-- @spec SPOKE-PLAN-FORK-001, SPOKE-PLAN-FORK-002 -->
## 8. Forks: return `CONCERNS`, never choose

When a requirement has more than one plausible reading that the task, the issue, and the repository do not settle, **do not pick one**. Commit the work finished before the fork, then return `CONCERNS` with each reading and its consequences in `escalation.details`.

<!-- @spec SPOKE-PLAN-FORK-004, SPOKE-PLAN-MODE-004 -->
## 9. After a council ruling

When `notes` carry a council option's `delta`, apply it, reading the decision matrix in `context_refs` for its reasons. In `auto`, go through every LID stage again in order: keep what still holds under the ruling and complete or change the rest, so a stage left half-done before the fork is finished and no stage is skipped.

<!-- @spec SPOKE-PLAN-REV-001 -->
## 10. After `revise`

When `notes` carry the user's revision request, change what it asks for and commit each change under the stage it belongs to with that stage's `--stage`.
