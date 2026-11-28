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

- [D] **SPOKE-PLAN-FORK-001**: The `forge-planner` skill shall tell the planner in modes `strict`, `auto`, and `naive`, when a requirement has more than one plausible reading that the task, the issue, and the repository do not settle, to return `CONCERNS` with each reading and its consequences in `escalation.details`, and never to choose a reading itself.
- [D] **SPOKE-PLAN-FORK-002**: The `forge-planner` skill shall tell the planner in modes `strict`, `auto`, and `naive` to commit the work finished before a fork, before returning `CONCERNS`.
- [D] **SPOKE-PLAN-FORK-003**: The `forge-planner` skill shall tell the planner in mode `repair` to return `ERROR` describing a fork it finds, instead of `CONCERNS`.
- [D] **SPOKE-PLAN-FORK-004**: When re-dispatched in mode `strict`, `auto`, `naive`, or `propose` after an approved council ruling, the `forge-planner` skill shall tell the planner to apply the option whose label the `council_decisions` entry for the ruling's blocker holds in `option`, as the decision matrix describes that option, reading the matrix for its reasons.
- [D] **SPOKE-PLAN-FORK-005**: The `forge-planner` skill shall tell the planner in modes `strict`, `auto`, `naive`, and `propose` that the decision matrix of the ruling a dispatch follows is the last entry of `context_refs`, and that its file name, `council/<blocker id>-matrix-<timestamp>.md`, names the blocker whose `council_decisions` entry holds the approved option.
- [D] **SPOKE-PLAN-FORK-006**: The `forge-planner` skill shall tell the planner, when re-dispatched after an approved council ruling, that `notes` hold the blocked dispatch's `notes`, when it had any, followed by the approved option's `delta`, when the council gave one, and to apply that `delta` as a change to the request those earlier `notes` make.
- [D] **SPOKE-PLAN-FORK-007**: The `forge-planner` skill shall tell the planner, when re-dispatched after an approved council ruling whose approved option has an empty `delta`, to apply that option from the decision matrix alone, together with the blocked dispatch's `notes` when `notes` carry any.

## Scope and revisions

- [D] **SPOKE-PLAN-SCOPE-001**: The `forge-planner` skill shall tell the planner to return with `READY_FOR_QA` a `scope.assigned` of path patterns, in the gate's glob syntax, naming the areas the plan expects to change and covering every test and production path it expects to add or change, without plan paths, and to return an empty list when the plan changes only plan paths.
- [D] **SPOKE-PLAN-SCOPE-002**: The `forge-planner` skill shall tell the planner to name in the summary of a `READY_FOR_QA` envelope, with its path, every edit to a test or production path the plan makes that no test will check, such as an `@spec` tag, a comment, or prose in a profile or skill reference.
- [D] **SPOKE-PLAN-REV-001**: When re-dispatched after `revise`, the `forge-planner` skill shall tell the planner to change what the user's `notes` ask for and commit each change under the stage it belongs to.
- [D] **SPOKE-PLAN-DEFER-001**: The `forge-planner` skill shall tell the planner to mark with `D` each spec whose behavior no automated test can verify, and to state in that spec file why it is deferred.

## Proposal

- [D] **SPOKE-PLAN-PROP-001**: When dispatched with mode `propose`, the `forge-planner` skill shall tell the planner to investigate the code in the worktree without committing and without changing any file in the worktree.
- [D] **SPOKE-PLAN-PROP-002**: The `forge-planner` skill shall tell the planner in mode `propose` that its one write, besides its result envelope and memory entry, is `proposal.md` in the workstream's state directory, the directory holding `state.json`.
- [D] **SPOKE-PLAN-PROP-003**: The `forge-planner` skill shall tell the planner in mode `propose` to return `PROPOSAL_READY` once `proposal.md` holds the proposal.
- [D] **SPOKE-PLAN-PROP-004**: The `forge-planner` skill shall tell the planner in mode `propose` to write `proposal.md` whole on every dispatch that returns `PROPOSAL_READY`, even when its content stands unchanged.
- [D] **SPOKE-PLAN-PROP-005**: The `forge-planner` skill shall tell the planner in mode `propose`, when `context_refs` name an earlier `proposal.md`, to start from it and change only what `notes` ask for, keeping every part `notes` do not question, open decisions included.
- [D] **SPOKE-PLAN-PROP-006**: The `forge-planner` skill shall tell the planner in mode `propose`, when `context_refs` name no earlier `proposal.md`, to write the proposal from `task.md` when `context_refs` name it, the files of the `scout/` directory `context_refs` name, and the code.
- [D] **SPOKE-PLAN-PROP-007**: The `forge-planner` skill shall tell the planner in mode `propose` to leave `proposal.md` as it was when returning `CONCERNS` or `ERROR`.
- [D] **SPOKE-PLAN-PROP-008**: The `forge-planner` skill shall tell the planner in mode `propose` to write `proposal.md` as Markdown holding, in this order, a `#` title naming the proposed change and `##` sections headed Problem, Findings, Proposal, and Done when, followed by a `##` section headed Open decisions only when the user left a decision open.
- [D] **SPOKE-PLAN-PROP-009**: The `forge-planner` skill shall tell the planner in mode `propose` to state under Problem what the idea or issue asks for and why, as the planner reads it.
- [D] **SPOKE-PLAN-PROP-010**: The `forge-planner` skill shall tell the planner in mode `propose` to state under Findings what the code does today where the change would land, citing paths relative to the repository root, and the related issues and pull requests the scout found, by number.
- [D] **SPOKE-PLAN-PROP-011**: The `forge-planner` skill shall tell the planner in mode `propose` to state under Proposal the approach, the areas of the repository it would change, the design nodes it would change while `config.repo_mode` is `LID`, and each alternative weighed with why it was not chosen.
- [D] **SPOKE-PLAN-PROP-012**: The `forge-planner` skill shall tell the planner in mode `propose` to state under Done when the observable outcomes that show the change is complete.
- [D] **SPOKE-PLAN-PROP-013**: The `forge-planner` skill shall tell the planner in mode `propose` to state under Open decisions, for each decision the user left open, the question, the council's options with their consequences, and the council's recommendation, marked as unresolved.
- [D] **SPOKE-PLAN-PROP-014**: The `forge-planner` skill shall tell the planner in mode `propose` to name no path in the workstream's state directory in `proposal.md`, and to carry as text what the proposal needs from the scout's files or a decision matrix.
- [D] **SPOKE-PLAN-PROP-015**: The `forge-planner` skill shall tell the planner in mode `propose` to put in `proposal.md` no question its inputs settle and no fork the user has not left open.
- [D] **SPOKE-PLAN-PROP-016**: The `forge-planner` skill shall tell the planner in mode `propose`, when a requirement has more than one plausible reading that the task, the issue, the scout's context, and the code do not settle, to return `CONCERNS` with each reading and its consequences in `escalation.details`, and never to choose a reading itself.
- [D] **SPOKE-PLAN-PROP-018**: The `forge-planner` skill shall tell the planner in mode `propose` that the decision matrix a council ruling or `leave_open` refers to is the last entry of `context_refs`.
- [D] **SPOKE-PLAN-PROP-019**: When re-dispatched in mode `propose` with `notes` telling it to present the council's options and recommendation as an open decision, the `forge-planner` skill shall tell the planner, unless the user's text that follows that instruction in `notes` settles the decision, to add that decision to Open decisions from the decision matrix and to settle nothing in it that the user's text leaves open.
- [D] **SPOKE-PLAN-PROP-020**: When re-dispatched in mode `propose` with `notes` telling it to present the council's options and recommendation as an open decision, the `forge-planner` skill shall tell the planner to apply the user's text that follows that instruction in `notes`, when there is any, and, when that text picks one of the council's options or otherwise settles the decision, to apply that choice to the proposal and to record no open decision for it.
- [D] **SPOKE-PLAN-PROP-021**: The `forge-planner` skill shall tell the planner in mode `propose`, when `context_refs` hold reporter draft envelopes, to read the one listed last among them, the draft the user rejected most recently, for what the user saw before changing the proposal as `notes` ask.
- [D] **SPOKE-PLAN-PROP-022**: When re-dispatched in mode `propose` with `notes` telling it to present the council's options and recommendation as an open decision, the `forge-planner` skill shall tell the planner that the text before that instruction in `notes` is the blocked dispatch's `notes`, and to apply it as the request that dispatch was given.

## Boundaries

- [D] **SPOKE-PLAN-BOUND-001**: The `forge-planner` skill shall tell the planner in every mode other than `propose` to change only `PLAN.md` at the repository root and paths under `docs/`.
- [D] **SPOKE-PLAN-BOUND-002**: The `forge-planner` skill shall tell the planner to plan with LID stages only while `config.repo_mode` is `LID`, and never to add LID structure to a repository that has none.
