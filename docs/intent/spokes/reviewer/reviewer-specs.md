# Reviewer — EARS Specs

Design: `reviewer-design.md`. Implementation: `reference/skills/forge-reviewer/SKILL.md`.

Every spec here describes what the `forge-reviewer` skill tells the reviewer to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do.

## Internal review

- [D] **SPOKE-REV-INT-001**: When dispatched with mode `internal`, the `forge-reviewer` skill shall tell the reviewer to review `git diff <base>...HEAD`, with `<base>` being `origin/<identity.base_branch>` when that ref exists and `identity.base_branch` otherwise, against the plan's specs or `PLAN.md` and the task.
- [D] **SPOKE-REV-INT-002**: The `forge-reviewer` skill shall tell the reviewer in mode `internal` to look for behavior that misses or exceeds the specs, bugs the tests do not catch, security problems, needless complexity, and code that departs from the repository's conventions, without repeating the verifier's checks.
- [D] **SPOKE-REV-INT-003**: The `forge-reviewer` skill shall tell the reviewer in mode `internal` to return `REVIEW_APPROVED` when no finding should block publishing.
- [D] **SPOKE-REV-INT-004**: The `forge-reviewer` skill shall tell the reviewer in mode `internal` to return `CHANGES_REQUESTED` with `escalation.findings` as `{route_to, detail}` entries — `route_to` `forge-planner` for wrong specs or plan, `forge-qa` for a missing or wrong test, `forge-dev` for production code, and `detail` naming the path, lines, and change — and `escalation.details` summarizing them.
- [D] **SPOKE-REV-INT-005**: The `forge-reviewer` skill shall tell the reviewer to put suggestions that should not block publishing in the summary, not in findings.

## External pull request review

- [D] **SPOKE-REV-EXT-001**: When dispatched with mode `external_pr`, the `forge-reviewer` skill shall tell the reviewer to read the code in the worktree as well as the scout's `pr.md`, `pr.diff`, and `pr-threads.json`.
- [D] **SPOKE-REV-EXT-002**: The `forge-reviewer` skill shall tell the reviewer in mode `external_pr` to return `PR_REVIEW_READY` with `review` holding `verdict` (`approve`, `request_changes`, or `comment`), `body`, and `comments` as `{path, line, body}` entries.
- [D] **SPOKE-REV-EXT-003**: The `forge-reviewer` skill shall tell the reviewer to anchor each comment to a line on the new side of the pull request's diff, and to put a remark about any other line in `body`.
- [D] **SPOKE-REV-EXT-004**: The `forge-reviewer` skill shall tell the reviewer not to repeat points already raised in the pull request's existing threads.

## Boundaries

- [D] **SPOKE-REV-BOUND-001**: The `forge-reviewer` skill shall tell the reviewer never to change the repository or call GitHub to change anything.
