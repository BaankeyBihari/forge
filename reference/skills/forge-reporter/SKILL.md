---
name: forge-reporter
description: What forge-reporter does in modes draft and send — compose a pull request's title and body or a review submission, then send exactly the draft the user approved. Used by forge-reporter on every dispatch, after the forge-spoke skill has read the invocation.
---

# Forge reporter

You write the text Forge shows the outside world and send it once the user approves it. `draft` composes it; `send` submits exactly the approved draft. The `forge-spoke` skill covers the invocation and the result envelope; this skill covers the work.

Your only `context_refs` entry is one envelope file: the publisher's `PUBLISHED` or the reviewer's `PR_REVIEW_READY` for a first draft, your own `REPORT_DRAFTED` for a revision or for `send`. Run every command in `identity.worktree_path`. `<org>/<repo>` are `identity.org` and `identity.repo`; `<number>` is the pull request number (from `identity.pr`, `…/pull/<number>`, or the open pull request you found).

`REPORT_DRAFTED` carries `report`, one of:

| `kind` | Fields |
|---|---|
| `pr_create` | `title`, `body`, `base` (`identity.base_branch`), `head` (`identity.branch`) |
| `pr_update` | `number`, `title`, `body` |
| `pr_review` | `number`, `commit_id`, `event` (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`), `body`, `comments` (`[{"path", "line", "side": "RIGHT", "body"}]`) |

<!-- @spec SPOKE-REP-DRAFT-008 -->
## 1. Mode `draft`

**Never call GitHub to change anything while drafting**: reads only (`gh pr list`, `gh pr view`, `git ls-remote`). Return `REPORT_DRAFTED` with `report`. The user reads it before anything is sent.

<!-- @spec SPOKE-REP-DRAFT-001, SPOKE-REP-DRAFT-002 -->
## 2. Build flow: create or update

```bash
gh pr list --head <identity.branch> --state open --json number,title,body,url
```

- **An open pull request**: draft `pr_update` for it. `title` is its current title.
- **None**: check the base branch is on `origin` first:

  ```bash
  git ls-remote --heads origin <identity.base_branch>
  ```

  No output: return `ERROR` asking the user to push `<identity.base_branch>` first. Otherwise draft `pr_create` with a short title that says what the branch does.

<!-- @spec SPOKE-REP-DRAFT-003, SPOKE-REP-DRAFT-004, SPOKE-REP-DRAFT-009 -->
## 3. The pull request body

Ground every claim in the commits (`git log anchors.workstream_base_sha..HEAD`), the specs or `PLAN.md`, and the envelope in `context_refs`. **Describe no work a commit does not show**, and add nothing else: **no signature, footer, or AI attribution** (no `Co-Authored-By` line naming an AI, no "Generated with" line) in any title, body, or comment.

```markdown
<!-- forge:begin -->
## What and why
<what changed and the reason, in a few sentences>

## Implements
- <EARS ID or PLAN.md item>: <one line>

## Verification
<the tests added or changed, and that Forge's verifier and reviewer passed this HEAD>
<!-- forge:end -->
```

For `pr_create`, `body` is the marked block. For `pr_update`, start from the pull request's current body:

- It has the markers: replace only the text from `<!-- forge:begin -->` to `<!-- forge:end -->`; **keep everything outside them byte for byte**.
- It has none: keep it whole and append the marked block after it, separated by a blank line.

<!-- @spec SPOKE-REP-DRAFT-005, SPOKE-REP-DRAFT-006 -->
## 4. PR review flow

Draft `pr_review` from the `review` in the `PR_REVIEW_READY` envelope:

- `body` and each comment's `path`, `line`, `body`: **unchanged**; give each comment `"side": "RIGHT"`.
- `event`: `approve` → `APPROVE`, `request_changes` → `REQUEST_CHANGES`, `comment` → `COMMENT`.
- `commit_id`: `git rev-parse HEAD`.

When the author in `scout/pr.md` (beside `state_path`) is its `Authenticated user`, GitHub refuses `APPROVE` and `REQUEST_CHANGES` on your own pull request: set `event` to `COMMENT` and say so in the summary.

<!-- @spec SPOKE-REP-DRAFT-007 -->
## 5. Revising with `notes`

The `REPORT_DRAFTED` envelope in `context_refs` holds the previous draft. Change its `report` as `notes` say, and nothing else.

<!-- @spec SPOKE-REP-SEND-001, SPOKE-REP-SEND-002 -->
## 6. Mode `send`: already sent?

Take `report` **unchanged** from the `REPORT_DRAFTED` envelope in `context_refs`; never draft again. A session can end after sending and before the envelope is applied, so check first:

| `kind` | Already sent when |
|---|---|
| `pr_create` | `gh pr list --head <identity.branch> --state open --json url` lists a pull request |
| `pr_update` | `gh pr view <number> --json body,url`: `body` equals `report.body` |
| `pr_review` | `gh api --paginate repos/<org>/<repo>/pulls/<number>/reviews` has a review whose `user.login` is `gh api user --jq .login`, `commit_id` is `report.commit_id`, and `body` is `report.body` |

Already sent: return `REPORT_SENT` with `report.url` set to that pull request's `url` or that review's `html_url`. **Do not send again.**

<!-- @spec SPOKE-REP-SEND-003, SPOKE-REP-SEND-004 -->
## 7. Mode `send`: send it

With your file-write tool, write `report.body` (for `pr_review`, the JSON `{"commit_id", "event", "body", "comments"}` from `report`) to a file in the session scratchpad, then:

```bash
gh pr create --base <report.base> --head <report.head> --title '<report.title>' --body-file <file>
gh pr edit <report.number> --title '<report.title>' --body-file <file>
gh api repos/<org>/<repo>/pulls/<report.number>/reviews -X POST --input <file> --jq .html_url
```

(`pr_create`, `pr_update`, `pr_review` respectively.) Return `REPORT_SENT` with `report.url`: the URL `gh pr create` or `gh pr edit` prints, or the `html_url` the review call returns. When the command fails, return `ERROR` with `gh`'s message.
