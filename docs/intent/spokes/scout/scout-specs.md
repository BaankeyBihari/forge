# Scout — EARS Specs

Design: `scout-design.md`. Implementation: `reference/skills/forge-scout/SKILL.md`.

Every spec here describes what the `forge-scout` skill tells the scout to do; they are verified by the sandbox scenario run, not by pytest.

## Recon

- [ ] **SPOKE-SCOUT-PRE-001**: When dispatched with mode `PRE_PLAN`, the `forge-scout` skill shall tell the scout to read `identity.issue` with `gh issue view` and write `scout/issue.md` holding the problem as reported, stated or implied acceptance criteria, constraints from the comments, linked issues and pull requests, and the repository areas the issue's names point to, and return `OK`.
- [ ] **SPOKE-SCOUT-PR-001**: When dispatched with mode `PR_FETCH`, the `forge-scout` skill shall tell the scout to write `scout/pr.md` (title, body, author, base, head, head commit, files, and the authenticated user's login), `scout/pr.diff` (`gh pr diff`), and `scout/pr-threads.json` (the pull request's review comments and reviews, read with REST `gh api` calls only), and return `OK`.

## CI watch

- [ ] **SPOKE-SCOUT-CI-001**: When dispatched with mode `CI_WATCH`, the `forge-scout` skill shall tell the scout to list the CI runs for `HEAD`, listing again every 30 seconds for up to 5 minutes while none exists, and to return `CI_NONE` when none appears.
- [ ] **SPOKE-SCOUT-CI-002**: The `forge-scout` skill shall tell the scout to wait for every run to finish using single shell commands that loop internally within the shell call's time limit.
- [ ] **SPOKE-SCOUT-CI-003**: The `forge-scout` skill shall tell the scout to count a run concluded `success`, `skipped`, or `neutral` as passing and one concluded `failure`, `timed_out`, `startup_failure`, or `cancelled` as failed.
- [ ] **SPOKE-SCOUT-CI-004**: The `forge-scout` skill shall tell the scout to return `ERROR` naming the run when a run waits for approval or is `action_required`.
- [ ] **SPOKE-SCOUT-CI-005**: The `forge-scout` skill shall tell the scout to return `CI_GREEN` when every run passed.
- [ ] **SPOKE-SCOUT-CI-006**: The `forge-scout` skill shall tell the scout, when any run failed, to write `scout/ci-<run id>.md` per failed run with the run, the failed jobs and steps, and the shortest log excerpt showing each failure, and return `CI_FAILED` with `escalation.details` naming those files and one cause per run.
- [ ] **SPOKE-SCOUT-CI-007**: The `forge-scout` skill shall tell the scout to return `ERROR` saying the runs are still in progress when it cannot finish watching within its dispatch.
- [ ] **SPOKE-SCOUT-RERUN-001**: When dispatched with mode `CI_RERUN`, the `forge-scout` skill shall tell the scout to re-run the failed jobs of every run for `HEAD` whose latest attempt failed, then watch as in `CI_WATCH`.

## Boundaries

- [ ] **SPOKE-SCOUT-BOUND-001**: The `forge-scout` skill shall tell the scout never to change the repository, to write only under the workstream's `scout/` directory apart from its envelope, memory partition, and the scratchpad, and to make no GitHub call that changes anything other than `gh run rerun`.
