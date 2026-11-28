---
parent: high-level-design
prefix: GH
---

# forge-github-gh

## Context and Design Philosophy

`forge-github-gh` (in `reference/skills/forge-github-gh/`) holds the `gh` commands Forge's roles run against GitHub: issues, pull requests, and workflow runs. It is Forge's tracker adapter for GitHub (HLD, *Trackers*) and, because pull requests and CI are GitHub-only, also the home of every pull request and CI command.

The skill says *how* to carry out an operation: the command, the fields it asks for, and what its output means. The role skill that uses it says *what* to do with the result: which files to write, what to return. A role's skill names the operations it carries out; it does not repeat their commands.

Every GitHub write stays a raw `gh` command written out in the skill, because the guard decides on a command's words (guard design). Reads may go through helper scripts; the skill ships none while every read is one `gh` command or a short pipeline.

## Layout

```text
reference/skills/forge-github-gh/
  SKILL.md            # index and shared rules
  references/
    issues.md         # issue reads
    pulls.md          # pull request reads
    ci.md             # workflow runs: list, wait, view, rerun
```

`claude/skills/forge-github-gh` links to it, as every other Forge skill is linked, so the installer copies it with the rest.

### `SKILL.md`

A short index, so a role that preloads the skill carries little context until it needs an operation:

| Reference file | Used by | Modes |
|---|---|---|
| `references/issues.md` | `forge-scout` | `PRE_PLAN`, `TRIAGE`, `IDEA` |
| `references/pulls.md` | `forge-scout` | `PR_FETCH` |
| `references/ci.md` | `forge-scout` | `CI_WATCH`, `CI_RERUN` |

It tells the role to read a reference file, from the `references/` directory beside `SKILL.md`, before running any of its commands, and to read only the files its mode uses.

It also states the rules every `gh` call shares:

- **Where to run, and which repository.** In `identity.worktree_path`, so `gh` takes the host from the worktree's remotes. A command about an issue or pull request known by its URL takes the repository from that URL: it passes the URL, or, in a `gh api` path, the owner, repository, and number in it, so the read reaches the item even when it lives in another repository. Every other command acting on a repository passes `-R identity.org/identity.repo`: a search, a bare issue or pull request number, and every workflow-run command. `gh` otherwise picks a repository by its own remote order (an `upstream` remote first), which can differ from the `origin` the workstream is named from. `-R` goes after the command's subcommand words (`gh run rerun <id> -R <org>/<repo>`), because the guard reads the words right after `gh` to decide what a command is. `gh api user` names no repository.
- **Output.** Ask for JSON with named fields (`--json <fields>`), never `gh`'s human-readable tables, so the result is parsed rather than read.
- **Failure.** A `gh` command that exits non-zero ends the operation: the role returns `ERROR` with `gh`'s message. When the message says `gh` is not authenticated, the role says the user must run `gh auth login`; it never runs `gh auth` itself.
- **Reads stay reads.** `gh api` reads pass no method other than `GET` (`-X`) and no field (`-f`, `-F`, `--raw-field`, `--field`, `--input`, which switch it to `POST`), and never call `gh api graphql`, which always sends a `POST`.
- **Text goes in files.** A command that sends text (a body, a comment, a review) takes it from a file in the session scratchpad (`--body-file`, `--input`), never inline, so quoting cannot change it.

### `references/issues.md`

- **Read an issue**: `gh issue view <ref> --json number,title,body,state,labels,comments,url`, with `-R <org>/<repo>` when `<ref>` is a bare number, then `gh api --paginate repos/<owner>/<repo>/issues/<number>/timeline` with the owner, repository, and number from the issue's `url`. The timeline's `cross-referenced` and `connected` events name the issues and pull requests linked to it; each is read by its URL, an issue with the command above and a pull request with the one below.
- **Read a linked or found pull request**: `gh pr view <url> --json number,title,body,state,url`.
- **Search related issues and pull requests**: `gh issue list -R <org>/<repo> --search '<terms>' --state all --limit 20 --json number,title,state,url` for issues and `gh pr list -R <org>/<repo> --search '<terms>' --state all --limit 20 --json number,title,state,url` for pull requests, with `identity.org`/`identity.repo`. The terms hold no `'`, so the single quotes around them hold.

### `references/pulls.md`

- **Read a pull request**: `gh pr view <ref> --json number,title,body,author,baseRefName,headRefName,headRefOid,files,url`, with `-R <org>/<repo>` when `<ref>` is a bare number.
- **Its diff**: `gh pr diff <ref>`, with `-R <org>/<repo>` when `<ref>` is a bare number.
- **Its review comments and reviews**: REST reads of `repos/<owner>/<repo>/pulls/<number>/comments` and `.../reviews` with `gh api --paginate --slurp`, the owner, repository, and number taken from the pull request's `url`, merged into one JSON object `{"comments": [...], "reviews": [...]}`.
- **The authenticated user**: `gh api user --jq .login`.

### `references/ci.md`

- **List the runs for a commit**: `gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId,name,status,conclusion,attempt,url`.
- **Wait for runs to appear**: one shell command that lists the runs for the commit, as above, every 30 seconds for up to 5 minutes and stops at the first non-empty list.
- **Wait for runs to finish, in rounds**: one shell command per round that lists them every 30 seconds for up to 9 minutes, within the shell call's 10-minute limit, and stops early when no run is unfinished or one is `waiting`. Its first line carries the round number, so no two rounds are identical and the guard's duplicate-call breaker never trips.
- **A failed run's failures**: `gh run view <id> -R <org>/<repo> --json jobs`, keeping the failed jobs and their failed steps, and `gh run view <id> -R <org>/<repo> --log-failed`, trimmed to its last 300 lines.
- **Failed runs of a commit**: `gh run list -R <org>/<repo> --commit <sha> --limit 100 --json databaseId,conclusion`, filtered with `--jq` to the `databaseId` of each run concluded `failure`, `timed_out`, `startup_failure`, or `cancelled`, the conclusions the scout counts as failed (scout design, *CI_WATCH*). A run's conclusion is that of its latest attempt, so these are the runs whose latest attempt failed.
- **Re-run failed jobs**: `gh run rerun <id> -R <org>/<repo> --failed`, the only write in the file.

Both waiting commands check, before each poll, whether `.STOP_FORGE` exists at the main worktree's root (`dirname "$(git rev-parse --path-format=absolute --git-common-dir)"`) or at the workstream worktree's root, and exit printing `STOP_FORGE` when it does. A `gh` call in them that exits non-zero ends the command at once, non-zero and printing `gh`'s message, so a failure is never read as an empty or finished list. How long to keep issuing rounds, and what each conclusion counts as, is the role's to decide (scout design, *CI_WATCH*); the failed-runs filter carries the scout's list of failed conclusions and changes with it.

## How a Role Gets the Skill

A role that uses `forge-github-gh` preloads it through its agent profile's `skills:` list, so the index is in its context from the start and the skill's base directory is shown beside it; the role reads the reference files it needs from there. The skill is not picked per workstream: pull request and CI operations need it whatever the workstream's tracker, and it is the only tracker adapter Forge ships.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Delivery to a role | Preloaded through the agent profile's `skills:` list | The dispatch names the adapter skill from `identity.tracker` and its tracker index entry | Pull request and CI operations need the skill in every workstream, so a per-workstream pick would still have to deliver it always; with one adapter there is nothing to pick between. |
| Split between this skill and a role's skill | Commands and the meaning of their output here; what to write and return in the role skill | Whole procedures (files, statuses) here | Two roles carry out the same operation with different results to write; procedures here would mix every role's policy into a shared file. |
| CI waiting loops | In `references/ci.md`, with the stop-file check and round number | In the scout skill | The loops are `gh` polling, and their shape (one command within the shell's time limit, a distinct first line) is what any role waiting on runs needs. |
| Selecting failed runs to re-run | One `gh run list` whose `--jq` filter yields the failed runs' ids | The role filters the full run list itself | Re-running needs only the failed runs' ids, which one filter yields in one command, with nothing for the role to parse. |
| Searching related work | `gh issue list --search` and `gh pr list --search` | `gh search issues` and `gh search prs` | The `list` commands are scoped to one repository and return only issues or only pull requests; `gh search` searches all of GitHub, so a call that omits the repository would bring in unrelated work. |
| Repository each `gh` call names | An item known by its URL by that URL; every other command `-R identity.org/identity.repo` | `identity.org`/`identity.repo` in every call; the repository `gh` resolves (`{owner}/{repo}`, bare numbers, no `-R`) | `gh` resolves an `upstream` remote before `origin`, so a command naming no repository can reach the wrong one. Linked items can live in other repositories, so only an item's URL always names the item that was read. A command with no URL (a search, a bare number, a workflow run) concerns the workstream's repository, the one its issues are filed in and named from. |
| Helper scripts | None | A script that merges review comments and reviews, or waits for runs | Each read is one `gh` command or a short pipeline; a script would be one more thing to install and test for no check a script makes better. |

## Open Questions & Future Decisions

1. `forge-reporter` still writes its pull request commands (`gh pr list`, `gh pr view`, `gh pr create`, `gh pr edit`, review submission) inline in its own skill. They move into `references/pulls.md`, with the reporter added to the index and its profile preloading the skill, together with the issue writes (filing an issue, commenting on one) in `references/issues.md`.
2. The tracker index (`reference/trackers.json`) does not exist yet. Its `github` entry names this skill; the guard, its first script reader, brings the file with the issue-write rules it checks.

## References

- HLD, *Trackers* — the tracker adapter and this skill's place in it.
- Scout design (`docs/intent/spokes/scout/`) — the modes that use each reference file.
- Guard design (`docs/intent/guard/`) — the `gh` rules per agent.
- Installer design (`docs/intent/installer/`) — how `claude/skills/forge-*` links are copied.
