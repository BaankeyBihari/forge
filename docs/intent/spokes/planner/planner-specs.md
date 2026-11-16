# Planner — EARS Specs

Design: `planner-design.md`. Implementation: `reference/skills/forge-planner/SKILL.md`.

Every spec here describes what the `forge-planner` skill tells the planner to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do.

## Modes

- [D] **SPOKE-PLAN-MODE-001**: When dispatched with mode `strict`, the `forge-planner` skill shall tell the planner to complete exactly the LID stage its phase names — `PLAN_S1` the HLD, `PLAN_S2` the LLDs, `PLAN_S3` the EARS specs, `PLAN_S4` the edge audit — following the `linked-intent-dev` skill's phase of the same content.
- [D] **SPOKE-PLAN-MODE-002**: The `forge-planner` skill shall tell the planner in mode `strict` to return `STAGE_COMPLETE` from `PLAN_S1` through `PLAN_S3` and `READY_FOR_QA` from `PLAN_S4`.
- [D] **SPOKE-PLAN-MODE-003**: When dispatched with mode `auto`, the `forge-planner` skill shall tell the planner to complete every remaining LID stage in order in one dispatch and return `READY_FOR_QA`.
- [D] **SPOKE-PLAN-MODE-004**: The `forge-planner` skill shall tell the planner in mode `auto`, when re-dispatched after a council ruling, to go through every LID stage again in order, keeping what still holds under the ruling and completing or changing the rest.
- [D] **SPOKE-PLAN-MODE-005**: When dispatched with mode `naive`, the `forge-planner` skill shall tell the planner to write `PLAN.md` at the repository root with the goal, the approach, the scope patterns, the tests to write, and open items, replacing any existing `PLAN.md`, and to return `READY_FOR_QA`.
- [D] **SPOKE-PLAN-MODE-006**: When dispatched with mode `repair`, the `forge-planner` skill shall tell the planner to change the plan as the findings in `notes` say and return `REPAIR_COMMITTED`, with `scope.assigned` when the change alters which paths the plan touches.

## Stages

- [D] **SPOKE-PLAN-STAGE-001**: The `forge-planner` skill shall tell the planner to commit each LID stage's output separately with that stage's `Forge-Stage`, using `forge_spoke.py commit --stage` in mode `auto`.
- [D] **SPOKE-PLAN-STAGE-002**: The `forge-planner` skill shall tell the planner, when a stage needs no change, to commit nothing for it and say so in the summary.
- [D] **SPOKE-PLAN-STAGE-003**: The `forge-planner` skill shall tell the planner to land the edge audit's resolutions as narrowing edits to the EARS specs within stage 4.

## Forks

- [D] **SPOKE-PLAN-FORK-001**: The `forge-planner` skill shall tell the planner, when a requirement has more than one plausible reading that the task, the issue, and the repository do not settle, to return `CONCERNS` with each reading and its consequences in `escalation.details`, and never to choose a reading itself.
- [D] **SPOKE-PLAN-FORK-002**: The `forge-planner` skill shall tell the planner to commit the work finished before a fork, before returning `CONCERNS`.
- [D] **SPOKE-PLAN-FORK-003**: The `forge-planner` skill shall tell the planner in mode `repair` to return `ERROR` describing a fork it finds, instead of `CONCERNS`.
- [D] **SPOKE-PLAN-FORK-004**: When re-dispatched after a council ruling, the `forge-planner` skill shall tell the planner to apply the chosen option's `delta` from `notes`, reading the decision matrix in `context_refs` for its reasons.

## Scope and revisions

- [D] **SPOKE-PLAN-SCOPE-001**: The `forge-planner` skill shall tell the planner to return with `READY_FOR_QA` a `scope.assigned` of path patterns, in the gate's glob syntax, naming the areas the plan expects to change and covering every test and production path it expects to add or change, without plan paths, and to return an empty list when the plan changes only plan paths.
- [D] **SPOKE-PLAN-REV-001**: When re-dispatched after `revise`, the `forge-planner` skill shall tell the planner to change what the user's `notes` ask for and commit each change under the stage it belongs to.
- [D] **SPOKE-PLAN-DEFER-001**: The `forge-planner` skill shall tell the planner to mark with `D` each spec whose behavior no automated test can verify, and to state in that spec file why it is deferred.

## Boundaries

- [D] **SPOKE-PLAN-BOUND-001**: The `forge-planner` skill shall tell the planner to change only `PLAN.md` at the repository root and paths under `docs/`.
- [D] **SPOKE-PLAN-BOUND-002**: The `forge-planner` skill shall tell the planner to plan with LID stages only while `config.repo_mode` is `LID`, and never to add LID structure to a repository that has none.
