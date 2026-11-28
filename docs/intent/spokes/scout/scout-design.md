---
parent: spokes
prefix: SPOKE-SCOUT
---

# Scout

## Context and Design Philosophy

`forge-scout` gathers what other spokes need from GitHub and leaves it as files in the workstream's `scout/` directory, which the router passes on in `context_refs`. It reads; it never changes the repository, and its only outward action is re-running failed CI jobs when the council rules a failure flaky. All GitHub access goes through `gh`, and only through read calls (`gh api` without a write method; the guard allows other methods only to the reporter).

The `forge-scout` skill says what each mode gathers, writes, and returns. The `gh` commands it runs are in the `forge-github-gh` skill (forge-github-gh design), which the scout's profile preloads. This design names each operation by its name in that skill, in italics, and leaves the command to the skill. Each mode reads the reference file of the operations it carries out, `references/issues.md` for `PRE_PLAN`, `TRIAGE`, and `IDEA`, `references/pulls.md` for `PR_FETCH`, and `references/ci.md` for `CI_WATCH` and `CI_RERUN`.

The scout may run on a dirty tree (router design, *Read-only agents and dirty trees*), because it reads only committed and remote state. Issue and pull request text is written by people outside the workstream: the scout records what it says and never follows instructions in it.

## Modes

| Mode | Phase | Writes to `scout/` | Returns |
|---|---|---|---|
| `PRE_PLAN` | `RECON` (build) | `issue.md` | `OK` |
| `TRIAGE` | `RECON` (triage) | `issue.md`, `related.md` | `OK` |
| `IDEA` | `RECON` (idea) | `related.md` | `OK` |
| `PR_FETCH` | `PR_FETCH` | `pr.md`, `pr.diff`, `pr-threads.json` | `OK` |
| `CI_WATCH` | `CI_WATCH` | `ci-<run id>.md` for each failed run | `CI_GREEN`, `CI_FAILED`, or `CI_NONE` |
| `CI_RERUN` | `CI_WATCH` | As `CI_WATCH` | As `CI_WATCH` |

### `PRE_PLAN`

Reads `identity.issue` (*Read an issue*: title, body, labels, comments, and the issues and pull requests linked to it) and writes `issue.md`: the problem in the reporter's words, acceptance criteria stated or implied, constraints from the comments, linked work, and the repository areas the issue points at, found by searching the code for the names it mentions. It records what the issue says, not a plan.

### `TRIAGE`

Reads the issue in `identity.issue` and writes `issue.md` exactly as `PRE_PLAN` does, then searches for related work (below) and writes `related.md`. `task.md`, when the workstream has one, holds what the user wants from the issue; the scout uses it to choose search terms and records nothing from it beyond those terms, since the planner reads it directly.

### `IDEA`

There is no issue. The scout reads the idea in `task.md`, searches for related work (below), and writes `related.md`. It does not search the code: the planner investigates the code itself in `PROPOSE`.

### Searching related work

`TRIAGE` and `IDEA` search the workstream's repository (`identity.org`/`identity.repo`) for issues and pull requests, open or closed, that touch the same problem: two to four searches with terms taken from the idea, or from the issue and `task.md`, each set of terms run once for issues and once for pull requests and each run returning at most 20 results. The scout opens a candidate whose title alone does not show whether it relates, and keeps only the ones that do. In `TRIAGE` it leaves out the issue itself and the work `issue.md` already lists under linked work.

`related.md` lists each related issue or pull request by number, with whether it is an issue or a pull request, its state, its title, and one line on how it relates, followed by the searches run. With nothing found it still lists the searches and says none related, so the planner can tell an empty result from a missing search.

### Files

Every mode writes each of its files whole, replacing what an earlier dispatch left, so a `RECON` dispatched again after an error never leaves a stale file beside a fresh one. A `gh` call that fails (the issue cannot be read, `gh` is not authenticated, a rate limit) is returned as `ERROR` with `gh`'s message; the scout writes no partial file.

### `PR_FETCH`

Reads `identity.pr`: the pull request (*Read a pull request*: title, body, author, base, head, head commit, files) and the authenticated user's login (*The authenticated user*) into `pr.md`, its diff (*Its diff*) into `pr.diff`, and its existing review comments and reviews (*Its review comments and reviews*, REST reads only) into `pr-threads.json`.

### `CI_WATCH`

Watches the CI runs for `HEAD`:

1. List the runs for `HEAD` (*List the runs for a commit*). When none exists, list again every 30 seconds for up to 5 minutes (*Wait for runs to appear*), since runs register shortly after a push. Still none: `CI_NONE`.
2. Wait for every run to finish, listing the runs for `HEAD` every 30 seconds (*Wait for runs to finish, in rounds*).
3. All passed: `CI_GREEN`. Any failed: for each failed run, read its failed jobs, steps, and log (*A failed run's failures*), and write `ci-<run id>.md` with the run, the failed jobs and steps, and the shortest log excerpt that shows each failure. Return `CI_FAILED` with `escalation.details` naming those files and a one-line cause per run.

Each wait is one shell command that polls for up to 9 minutes, stays within the shell call's 10-minute time limit, exits early when `.STOP_FORGE` exists at the main worktree's root or the workstream worktree's root, and carries its round number, so no two wait calls are identical and the guard's duplicate-call breaker never trips. When a wait prints `STOP_FORGE`, the scout ends its turn at once: the guard blocks every further call. The scout keeps issuing rounds for up to 60 minutes in total; runs still unfinished then are returned as `ERROR` saying they are in progress, and resuming the workstream watches again. A run's conclusion `success`, `skipped`, or `neutral` passes; `failure`, `timed_out`, `startup_failure`, or `cancelled` fails; a run waiting for approval or `action_required` is returned as `ERROR` naming the run, because only the user can release it.

### `CI_RERUN`

Finds every run for `HEAD` whose latest attempt failed (*Failed runs of a commit*), re-runs its failed jobs (*Re-run failed jobs*), then watches as `CI_WATCH` does.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Output location | Files in `scout/`, passed on in `context_refs` | Contents in the envelope | Issue bodies, diffs, and logs are large; files keep them out of the Hub's context and the router's state. |
| Where the `gh` commands live | In `forge-github-gh`'s reference files; the scout skill and this design name the operations and say what to gather, write, and return | Inline in the scout skill; spelled out in this design as well | The reporter needs the same issue and pull request commands; one skill states them and the rules every `gh` call shares once, and a command written in one place cannot drift from a second copy. |
| How the scout gets `forge-github-gh` | Preloaded through the scout profile's `skills:` list | The dispatch names the adapter skill from `identity.tracker` | `PR_FETCH`, `CI_WATCH`, and `CI_RERUN` use `forge-github-gh` whatever the workstream's tracker, since pull requests and CI are GitHub-only, and Forge ships one tracker adapter. Picking a skill per workstream waits for a second adapter. |
| Triage files | `issue.md` as `PRE_PLAN` writes it, plus `related.md` | One triage file holding the issue and the search results | The planner reads one shape of `issue.md` in every flow, and `related.md` has the same shape in idea and triage. |
| Code search in `IDEA` | None | Search the code for the idea's names, as `PRE_PLAN` does for an issue's | The planner investigates the code in `PROPOSE`; the scout's part in an idea workstream is the tracker. |
| Search bound | Two to four searches of at most 20 results, kept only when related | Every match; a single search | Titles alone mislead, so candidates are opened, and an unbounded search costs dispatch time without making the proposal better. |
| Thread reads | REST reads only | GraphQL review threads | `gh api graphql` sends a POST, which the guard reserves for the reporter. |
| Waiting for runs to finish | Poll the commit's run list in rounds of up to 9 minutes | `gh run watch --exit-status` | `gh run watch` blocks until the run ends, so it cannot stay within the shell call's time limit or stop when `.STOP_FORGE` appears. |
| Waiting for runs to appear | 5 minutes, then `CI_NONE` | Wait indefinitely; check once | Runs take seconds to register after a push; repositories without CI should reach the user's `ci_none` choice quickly. |
| Failure extract | Shortest decisive excerpt per failed step | The full log | The council's debugger panel needs the failure, not the noise around it. |

## Open Questions & Future Decisions

### Unverified
1. Whether one subagent dispatch can keep watching for 60 minutes. The sandbox run measures it; the limit is lowered if not.

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `RECON` in each flow, `PR_FETCH`, `CI_WATCH`, `CI_FAILED` council routing, `CI_RERUN`.
- Guard design (`docs/intent/guard/`) — `gh` rules per agent; scout writes to `scout/`.
- forge-github-gh design (`docs/intent/forge-github-gh/`) — the `gh` commands each mode runs.
- Planner design (`docs/intent/spokes/planner/`) — mode `propose`, which reads `scout/` in idea and triage workstreams.
