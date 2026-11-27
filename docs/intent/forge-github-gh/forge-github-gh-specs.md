# forge-github-gh — EARS Specs

Design: `forge-github-gh-design.md`. Implementation: `reference/skills/forge-github-gh/` and the link `claude/skills/forge-github-gh`.

Specs under *Layout* are tested with pytest. Every other spec describes what the skill tells a role to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do.

## Layout

- [ ] **GH-LAYOUT-001**: `reference/skills/forge-github-gh/` shall hold `SKILL.md`, `references/issues.md`, `references/pulls.md`, and `references/ci.md`, and no other file.
- [ ] **GH-LAYOUT-002**: `claude/skills/forge-github-gh` shall be a symbolic link to `../../reference/skills/forge-github-gh`.

## Index

- [D] **GH-INDEX-001**: The `forge-github-gh` `SKILL.md` shall list each reference file with the role and the modes that use it: `references/issues.md` for `forge-scout` in `PRE_PLAN`, `TRIAGE`, and `IDEA`; `references/pulls.md` for `forge-scout` in `PR_FETCH`; `references/ci.md` for `forge-scout` in `CI_WATCH` and `CI_RERUN`.
- [D] **GH-INDEX-002**: The `forge-github-gh` `SKILL.md` shall tell the role to read a reference file, from the `references/` directory beside `SKILL.md`, before running any of its commands, and to read only the reference files its mode uses.

## Shared rules

- [D] **GH-RULE-001**: The `forge-github-gh` `SKILL.md` shall tell the role to run every `gh` command in `identity.worktree_path`, and to name `identity.org`/`identity.repo` in each `gh api` path.
- [D] **GH-RULE-002**: The `forge-github-gh` `SKILL.md` shall tell the role to request `gh` output as JSON with named fields (`--json <fields>`) wherever the command offers it, never as `gh`'s human-readable tables.
- [D] **GH-RULE-003**: The `forge-github-gh` `SKILL.md` shall tell the role, when a `gh` command exits non-zero, to end the operation and return `ERROR` with `gh`'s message.
- [D] **GH-RULE-004**: The `forge-github-gh` `SKILL.md` shall tell the role, when `gh` reports it is not authenticated, to say in its `ERROR` that the user must run `gh auth login`, and never to run `gh auth` itself.
- [D] **GH-RULE-005**: The `forge-github-gh` `SKILL.md` shall tell the role to pass `gh api` no method other than `GET` and none of `-f`, `-F`, `--raw-field`, `--field`, or `--input` on a read, and never to call `gh api graphql`.
- [D] **GH-RULE-006**: The `forge-github-gh` `SKILL.md` shall tell the role to pass text a command sends (a body, a comment, a review) from a file in the session scratchpad (`--body-file`, `--input`), never inline.

## Issues

- [D] **GH-ISS-001**: The `forge-github-gh` `references/issues.md` shall read an issue with `gh issue view <ref> --json number,title,body,state,labels,comments,url`.
- [D] **GH-ISS-002**: The `forge-github-gh` `references/issues.md` shall find an issue's linked issues and pull requests in the `cross-referenced` and `connected` events of `gh api --paginate repos/<org>/<repo>/issues/<number>/timeline`, and read each with `gh issue view` or `gh pr view`.
- [D] **GH-ISS-003**: The `forge-github-gh` `references/issues.md` shall search related issues with `gh issue list --search '<terms>' --state all --limit 20 --json number,title,state,url` and related pull requests with `gh pr list --search '<terms>' --state all --limit 20 --json number,title,state,url`.

## Pull requests

- [D] **GH-PR-001**: The `forge-github-gh` `references/pulls.md` shall read a pull request with `gh pr view <ref> --json number,title,body,author,baseRefName,headRefName,headRefOid,files,url` and its diff with `gh pr diff <ref>`.
- [D] **GH-PR-002**: The `forge-github-gh` `references/pulls.md` shall read a pull request's review comments and reviews with `gh api --paginate --slurp` REST reads of `repos/<org>/<repo>/pulls/<number>/comments` and `repos/<org>/<repo>/pulls/<number>/reviews`, merged into one JSON object `{"comments": [...], "reviews": [...]}`.
- [D] **GH-PR-003**: The `forge-github-gh` `references/pulls.md` shall read the authenticated user's login with `gh api user --jq .login`.

## CI runs

- [D] **GH-CI-001**: The `forge-github-gh` `references/ci.md` shall list the runs for a commit with `gh run list --commit <sha> --limit 100 --json databaseId,name,status,conclusion,attempt,url`.
- [D] **GH-CI-002**: The `forge-github-gh` `references/ci.md` shall wait for runs to appear with one shell command that lists the runs for the commit every 30 seconds for up to 5 minutes and stops at the first non-empty list.
- [D] **GH-CI-003**: The `forge-github-gh` `references/ci.md` shall wait for runs to finish with one shell command per round that polls every 30 seconds for up to 9 minutes, stops early when no run is unfinished or a run is `waiting`, and carries the round number in its first line.
- [D] **GH-CI-004**: The `forge-github-gh` `references/ci.md` shall have both waiting commands check, before each poll, whether `.STOP_FORGE` exists at the main worktree's root or the workstream worktree's root, and exit printing `STOP_FORGE` when it does.
- [D] **GH-CI-005**: The `forge-github-gh` `references/ci.md` shall read a failed run's failures with `gh run view <id> --json jobs`, keeping the failed jobs and their failed steps, and `gh run view <id> --log-failed` trimmed to its last 300 lines.
- [D] **GH-CI-006**: The `forge-github-gh` `references/ci.md` shall re-run a run's failed jobs with `gh run rerun <id> --failed`, and hold no other command that changes anything on GitHub.
