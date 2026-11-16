---
parent: spokes
prefix: SPOKE-SCOUT
---

# Scout

## Context and Design Philosophy

`forge-scout` gathers what other spokes need from GitHub and leaves it as files in the workstream's `scout/` directory, which the router passes on in `context_refs`. It reads; it never changes the repository, and its only outward action is re-running failed CI jobs when the council rules a failure flaky. All GitHub access goes through `gh`, and only through read calls (`gh api` without a write method; the guard allows other methods only to the reporter).

The scout may run on a dirty tree (router design, *Read-only agents and dirty trees*), because it reads only committed and remote state.

## Modes

| Mode | Phase | Writes to `scout/` | Returns |
|---|---|---|---|
| `PRE_PLAN` | `RECON` | `issue.md` | `OK` |
| `PR_FETCH` | `PR_FETCH` | `pr.md`, `pr.diff`, `pr-threads.json` | `OK` |
| `CI_WATCH` | `CI_WATCH` | `ci-<run id>.md` for each failed run | `CI_GREEN`, `CI_FAILED`, or `CI_NONE` |
| `CI_RERUN` | `CI_WATCH` | As `CI_WATCH` | As `CI_WATCH` |

### `PRE_PLAN`

Reads `identity.issue` with `gh issue view` (title, body, labels, comments, linked issues and pull requests) and writes `issue.md`: the problem in the reporter's words, acceptance criteria stated or implied, constraints from the comments, linked work, and the repository areas the issue points at, found by searching the code for the names it mentions. It records what the issue says, not a plan.

### `PR_FETCH`

Reads `identity.pr`: `gh pr view` (title, body, author, base, head, head commit, files) and the authenticated user's login (`gh api user`) into `pr.md`, `gh pr diff` into `pr.diff`, and the existing review comments and reviews (`gh api` REST reads of the pull request's `comments` and `reviews`) into `pr-threads.json`.

### `CI_WATCH`

Watches the CI runs for `HEAD`:

1. List runs for the commit (`gh run list --commit <HEAD>`). When none exists, list again every 30 seconds for up to 5 minutes, since runs register shortly after a push. Still none: `CI_NONE`.
2. Wait for every run to finish (`gh run watch <id> --exit-status`).
3. All passed: `CI_GREEN`. Any failed: for each failed run, `gh run view <id> --log-failed`, and write `ci-<run id>.md` with the run, the failed jobs and steps, and the shortest log excerpt that shows each failure. Return `CI_FAILED` with `escalation.details` naming those files and a one-line cause per run.

Each wait is one shell command that loops internally, stays within the shell call's time limit, exits early when `.STOP_FORGE` exists at the main worktree's root or the workstream worktree's root, and carries its round number, so no two wait calls are identical and the guard's duplicate-call breaker never trips. The scout keeps issuing rounds for up to 60 minutes in total; runs still unfinished then are returned as `ERROR` saying they are in progress, and resuming the workstream watches again. A run's conclusion `success`, `skipped`, or `neutral` passes; `failure`, `timed_out`, `startup_failure`, or `cancelled` fails; a run waiting for approval or `action_required` is returned as `ERROR` naming the run, because only the user can release it.

### `CI_RERUN`

Re-runs the failed jobs of every run for `HEAD` whose latest attempt failed (`gh run rerun <id> --failed`), then watches as `CI_WATCH` does.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Output location | Files in `scout/`, passed on in `context_refs` | Contents in the envelope | Issue bodies, diffs, and logs are large; files keep them out of the Hub's context and the router's state. |
| Thread reads | REST reads only | GraphQL review threads | `gh api graphql` sends a POST, which the guard reserves for the reporter. |
| Waiting for runs to appear | 5 minutes, then `CI_NONE` | Wait indefinitely; check once | Runs take seconds to register after a push; repositories without CI should reach the user's `ci_none` choice quickly. |
| Failure extract | Shortest decisive excerpt per failed step | The full log | The council's debugger panel needs the failure, not the noise around it. |

## Open Questions & Future Decisions

### Unverified
1. Whether one subagent dispatch can keep watching for 60 minutes. The sandbox run measures it; the limit is lowered if not.

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `RECON`, `PR_FETCH`, `CI_WATCH`, `CI_FAILED` council routing, `CI_RERUN`.
- Guard design (`docs/intent/guard/`) — `gh` rules per agent; scout writes to `scout/`.
