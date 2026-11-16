---
parent: spokes
prefix: SPOKE-REV
---

# Reviewer

## Context and Design Philosophy

`forge-reviewer` is the qualitative check the verifier cannot make: is the change correct, simple, safe, and what the user asked for. It reviews and writes nothing in the repository. Every finding it raises names the agent who can fix it, so the router can send it straight to a repair.

## Modes

### `internal` (`REVIEW_INTERNAL`)

Reviews the change against the plan (specs or `PLAN.md`) and the task. The change is `git diff <base>...HEAD`, where `<base>` is `origin/<identity.base_branch>` when it exists and `identity.base_branch` otherwise, so a base branch merged into the workstream does not show up as the workstream's own work. The router dispatches it only when `verifier_pass_sha` equals `HEAD`, so the reviewer reads code that already passes every mechanical check.

It looks for: behavior that misses or exceeds the specs, bugs the tests do not catch, security problems, needless complexity, and code that fights the repository's conventions. It does not repeat the verifier's checks.

| Returns | When |
|---|---|
| `REVIEW_APPROVED` | No finding that should block publishing |
| `CHANGES_REQUESTED` | One or more findings; `escalation.findings` lists each as `{route_to, detail}`, and `escalation.details` summarises them in one paragraph |

`route_to` is `forge-planner` when the specs or plan are wrong, `forge-qa` when a test is missing or wrong, and `forge-dev` for production code. `detail` names the path and lines and says what to change. Suggestions that should not block publishing go in the summary, not in findings.

### `external_pr` (`PR_REVIEW`)

Reviews a pull request someone else opened. The worktree is checked out at the pull request's head; the scout's `scout/` directory holds its description, diff, and existing review threads. The reviewer reads the code in the worktree, not only the diff.

Returns `PR_REVIEW_READY` with `review`:

| Field | Meaning |
|---|---|
| `verdict` | `approve`, `request_changes`, or `comment` |
| `body` | The overall review text |
| `comments` | List of `{path, line, body}`, each anchored to a line on the new side of the diff |

A comment anchored to a line the diff does not show cannot be submitted as an inline comment; the reviewer puts such a remark in `body` instead. Points already raised in existing threads are not repeated.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Finding routing | Each finding names its fixing agent | The council triages findings | Router design: findings are specific already; a council round adds cost without new information. |
| Non-blocking remarks | In the summary only | As findings with a severity | Every finding causes a repair dispatch; a nit should not. |
| External review payload | Structured `review` in the envelope; the reporter drafts the submission from it | The reviewer submits via `gh` | Submitting is outward-facing and needs the user's approval through the report pause; the reviewer stays read-only. |
| Inline anchors | New-side lines in the diff only | Any line in the file | GitHub rejects review comments on lines outside the diff. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `REVIEW_INTERNAL` entry guard, `CHANGES_REQUESTED` repair routing, PR review flow.
- Scout design (`spokes/scout/`) — what `PR_FETCH` leaves in `scout/`.
- Reporter design (`spokes/reporter/`) — how `review` becomes a submission.
