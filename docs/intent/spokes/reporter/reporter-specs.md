# Reporter — EARS Specs

Design: `reporter-design.md`. Implementation: `reference/skills/forge-reporter/SKILL.md`.

Every spec here describes what the `forge-reporter` skill tells the reporter to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do.

## Draft

- [D] **SPOKE-REP-DRAFT-001**: When dispatched with mode `draft` in a `build`-flow workstream, the `forge-reporter` skill shall tell the reporter to look for an open pull request from `identity.branch` (`gh pr list --head <branch> --state open`) and draft `pr_create` when there is none and `pr_update` for the one found.
- [D] **SPOKE-REP-DRAFT-002**: The `forge-reporter` skill shall tell the reporter, before drafting `pr_create`, to check that `identity.base_branch` exists on `origin` (`git ls-remote --heads origin <base>`) and to return `ERROR` asking the user to push that branch first when it does not.
- [D] **SPOKE-REP-DRAFT-003**: The `forge-reporter` skill shall tell the reporter to write a pull request body that says what changed and why, lists the specs or plan items implemented, and states how it was verified, grounded only in the commits, the specs or `PLAN.md`, and the envelopes in `context_refs`, and to place it between `<!-- forge:begin -->` and `<!-- forge:end -->`.
- [D] **SPOKE-REP-DRAFT-004**: The `forge-reporter` skill shall tell the reporter drafting `pr_update` to replace only the text between the Forge markers of the existing body and keep everything outside them, and, when the existing body has no Forge markers, to keep it whole and append the marked block after it.
- [D] **SPOKE-REP-DRAFT-005**: When dispatched with mode `draft` in a `pr_review`-flow workstream, the `forge-reporter` skill shall tell the reporter to draft `pr_review` from the reviewer's `review`, keeping its body and comment texts unchanged, mapping `verdict` `approve`, `request_changes`, `comment` to `event` `APPROVE`, `REQUEST_CHANGES`, `COMMENT`, giving each comment `side` `RIGHT`, and setting `commit_id` to `HEAD`.
- [D] **SPOKE-REP-DRAFT-006**: The `forge-reporter` skill shall tell the reporter, when the pull request's author is the authenticated user recorded in the scout's `pr.md`, to set the `pr_review` `event` to `COMMENT` and say why in the summary.
- [D] **SPOKE-REP-DRAFT-007**: When dispatched with mode `draft` with `notes`, the `forge-reporter` skill shall tell the reporter to change the draft in the `REPORT_DRAFTED` envelope given as its only `context_refs` entry as the notes say.
- [D] **SPOKE-REP-DRAFT-008**: The `forge-reporter` skill shall tell the reporter in mode `draft` to return `REPORT_DRAFTED` with `report`, and never to call GitHub to change anything.

## Send

- [D] **SPOKE-REP-SEND-001**: When dispatched with mode `send`, the `forge-reporter` skill shall tell the reporter to take `report` unchanged from the `REPORT_DRAFTED` envelope given as its only `context_refs` entry.
- [D] **SPOKE-REP-SEND-002**: The `forge-reporter` skill shall tell the reporter in mode `send` first to check whether the report was already sent — an open pull request from `identity.branch` for `pr_create`, the pull request body already equal to the draft for `pr_update`, a review by the authenticated user on `commit_id` with the same body for `pr_review` — and when it was, to return `REPORT_SENT` with that pull request's or review's URL without sending again.
- [D] **SPOKE-REP-SEND-003**: The `forge-reporter` skill shall tell the reporter in mode `send` to write the body or review payload to a file in the session scratchpad and send it with `gh pr create --body-file` for `pr_create`, `gh pr edit --body-file` for `pr_update`, and `gh api repos/<owner>/<repo>/pulls/<number>/reviews -X POST --input <file>` for `pr_review`.
- [D] **SPOKE-REP-SEND-004**: The `forge-reporter` skill shall tell the reporter in mode `send` to return `REPORT_SENT` with `report.url` set to the URL `gh pr create` or `gh pr edit` prints, or to the `html_url` of the review `gh api` returns, and to return `ERROR` with `gh`'s message when the command fails.
