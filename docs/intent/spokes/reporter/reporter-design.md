---
parent: spokes
prefix: SPOKE-REP
---

# Reporter

## Context and Design Philosophy

`forge-reporter` writes the text Forge shows the outside world — a pull request's title and body, or a review submission — and sends it after the user approves it. It works in two dispatches: `draft` composes and returns the text without calling GitHub to change anything; `send` submits exactly the approved draft. What the user approved and what GitHub receives are the same bytes, because `send` reads the draft from the envelope file the user was shown rather than writing it again.

The reporter grounds every claim in committed artifacts and receipts: the commits, the specs or `PLAN.md`, and the envelope that led to the report. It does not describe work no commit shows.

## Inputs

The router gives the reporter exactly one `context_refs` entry, `report_input` (router design): the publisher's `PUBLISHED` envelope, the reviewer's `PR_REVIEW_READY` envelope, or — for `send` and for a revised draft — the reporter's own latest `REPORT_DRAFTED` envelope.

## The `report` field

`REPORT_DRAFTED` carries `report`:

| `kind` | Fields | Sent with |
|---|---|---|
| `pr_create` | `title`, `body`, `base` (`identity.base_branch`), `head` (`identity.branch`) | `gh pr create --base --head --title --body-file` |
| `pr_update` | `number`, `title`, `body` | `gh pr edit <number> --title --body-file` |
| `pr_review` | `number`, `commit_id`, `event` (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`), `body`, `comments` (`[{path, line, side: "RIGHT", body}]`) | `gh api repos/<owner>/<repo>/pulls/<number>/reviews -X POST --input <file>` |

## Modes

### `draft`

- **Build flow:** checks for an open pull request from `identity.branch` (`gh pr list --head <branch> --state open`). None: drafts `pr_create`, after checking that `identity.base_branch` exists on `origin` (`git ls-remote --heads origin <base>`); when it does not — a stacked workstream whose parent branch is unpushed — it returns `ERROR` asking the user to push the parent first. One exists: drafts `pr_update`. The body says what changed and why, lists the specs or plan items it implements, and states how it was verified.
- **Forge's part of a body** sits between `<!-- forge:begin -->` and `<!-- forge:end -->`. `pr_update` replaces only that block and keeps everything outside it, which is what the user added; an existing body with no markers is kept whole, and the block is appended after it.
- **PR review flow:** turns the reviewer's `review` into `pr_review`, keeping its body and comment texts unchanged, mapping the verdict to GitHub's event (`approve` to `APPROVE`, `request_changes` to `REQUEST_CHANGES`, `comment` to `COMMENT`) and giving every comment `side: "RIGHT"`; `commit_id` is the pull request head the review was made at (`HEAD` of the detached worktree). When the pull request's author is the authenticated user (from the scout's `pr.md`), GitHub refuses `APPROVE` and `REQUEST_CHANGES`, so `event` is `COMMENT` and the summary says why.
- With `notes` (after `revise`), it changes the previous draft as the notes say.

Returns `REPORT_DRAFTED`.

### `send`

Reads `report` from the `REPORT_DRAFTED` envelope in `context_refs` and first checks whether it was already sent, since a session can end after sending and before the envelope is applied: for `pr_create`, an open pull request from `identity.branch`; for `pr_update`, the pull request's body already equal to the draft; for `pr_review`, a review by the authenticated user on `commit_id` with the same body. Already sent: `REPORT_SENT` with that pull request's or review's URL. Otherwise it writes the body or review payload to a file in the session scratchpad and runs the command for its `kind`. Returns `REPORT_SENT` with `report.url`: the URL `gh pr create` or `gh pr edit` prints, or `html_url` from the review `gh api` returns. A failing `gh` command returns `ERROR` with its message.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Draft handoff | `send` reads the approved draft from its envelope file, passed by the router | A `reports/` directory the reporter writes; `send` drafts again | The user approves a specific text; re-drafting could send something else, and a new directory needs new guard and state rules for no gain. |
| Existing pull request | Update it, keeping user-added text | Always create; overwrite the body | Each publish of a branch after review fixes targets the same pull request; the user may have edited it. |
| External review content | The reviewer's verdict and comments, unchanged | The reporter rewrites them | The review is the reviewer's judgment; the reporter only shapes it for GitHub. |
| Review submission | One `POST` to the reviews endpoint with all comments | One comment per call | One review groups the comments and notifies the author once. |

## References

- Spokes design (`docs/intent/spokes/`) and protocol design (`spokes/protocol/`).
- Router design (`docs/intent/router/`) — `REPORT` transitions, `report_approval`, envelope handoff.
- Reviewer design (`spokes/reviewer/`) — the `review` field.
- Guard design (`docs/intent/guard/`) — `gh` write commands allowed only to the reporter.
