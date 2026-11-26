# Workstream State — EARS Specs

Design: `workstream-state-design.md`. Implementation: `reference/scripts/resolve_state.py`.

## Storage

- [x] **WS-STORE-001**: `resolve_state.py` shall keep all Forge state under the directory named by the `FORGE_HOME` environment variable, or under `~/.forge` when `FORGE_HOME` is unset.
- [x] **WS-STORE-002**: When creating a workstream, `resolve_state.py` shall copy `mode` and `retention` from `$FORGE_HOME/config.json` into the workstream's `config`, using `collab` and `archive` respectively for any key the file lacks or when the file does not exist.
- [x] **WS-STORE-003**: If the `mode` a new workstream would get, from `$FORGE_HOME/config.json` or from `--set mode=` (which overrides the file), is not `delivery`, `collab-plan`, `collab-qa`, `collab-dev`, `collab-code`, or `collab`, then `new` shall exit with a usage error and create nothing.

## Identity

- [x] **WS-ID-001**: When `origin` is a host-based remote (scp-like `[user@]host:path`, or an `ssh://`, `https://`, `http://`, or `git://` URL) whose path has two or more segments, `resolve_state.py` shall take `<repo>` from the last path segment with any trailing `/` and `.git` removed, and `<org>` from the remaining segments joined with `-`.
- [x] **WS-ID-002**: When the repository has no `origin`, an `origin` that is a local path or `file://` URL, or a host-based `origin` whose path has a single segment, `resolve_state.py` shall use `local` as `<org>` and the basename of the directory containing the absolute git common directory as `<repo>`.
- [x] **WS-ID-003**: `resolve_state.py` shall turn `<org>`, `<repo>`, and every branch-derived workstream name into a slug by lowercasing, replacing each character outside `[a-z0-9._-]` with `-`, collapsing runs of `-`, and trimming leading and trailing `-` and `.`.
- [x] **WS-ID-004**: `resolve_state.py` shall resolve the workstream from the `--workstream` argument if given, else from the `FORGE_WORKSTREAM` environment variable if set, else from the current branch.
- [x] **WS-ID-005**: If a workstream name given by `--workstream` or `FORGE_WORKSTREAM` is not already a valid slug, then `resolve_state.py` shall exit with a usage error instead of sanitizing it.
- [x] **WS-ID-006**: While `HEAD` is detached, `resolve_state.py` shall use as the branch-derived workstream the open workstream of the current repository whose `identity.worktree_path` equals the current worktree root (`git rev-parse --show-toplevel`), or `detached-<7-character short SHA>` when no open workstream has that path.
- [x] **WS-ID-007**: While the repository has no commits, `resolve_state.py` shall take the current branch from `git symbolic-ref --short HEAD`.
- [x] **WS-ID-008**: When `wake` resolves the workstream from the current branch, that branch is `main` or `master`, and no state exists for it, `resolve_state.py` shall return status `LIST` with the repository's workstream list.
- [x] **WS-ID-009**: When `wake` finds state whose `identity.git_common_dir` differs from the current repository's absolute git common directory, `resolve_state.py` shall return `IDENTITY_MISMATCH`.
- [x] **WS-ID-010**: When `wake` resolves a `build`-flow workstream from the current branch and the state's `identity.branch` differs from the current branch, `resolve_state.py` shall return `IDENTITY_MISMATCH`.
- [x] **WS-ID-011**: When `wake` is given an explicit workstream (by `--workstream` or `FORGE_WORKSTREAM`) whose `identity.worktree_path` differs from the current worktree root, or — for a `build`-flow workstream — whose `identity.branch` differs from the current branch, `resolve_state.py` shall return `ELSEWHERE` with the recorded branch and worktree path.

## Workstream creation

- [x] **WS-NEW-001**: When `new` is given `--issue`, `resolve_state.py` shall read `number`, `title`, `labels`, and `url` with `gh issue view --json` and use `<number>-<title slug>` as the slug, cut at a `-` boundary to at most 48 characters, or `<number>` alone when the title slug is empty.
- [x] **WS-NEW-002**: When `new` is given `--issue` without `--type`, `resolve_state.py` shall use type `fix` if any label name contains `bug` (case-insensitive) and type `feat` otherwise.
- [x] **WS-NEW-003**: When `new` is given `--name`, `resolve_state.py` shall use it as the slug, rejecting it with a usage error if it is not already a valid slug.
- [x] **WS-NEW-004**: When `new` is given neither `--type` nor `--issue`, `resolve_state.py` shall use type `feat`.
- [x] **WS-NEW-005**: If `new` is given a `--type` other than `feat`, `fix`, `chore`, `docs`, or `refactor`, then `resolve_state.py` shall exit with a usage error.
- [x] **WS-NEW-006**: When `new` creates a `build`-flow workstream without `--branch`, `resolve_state.py` shall create branch `<type>/<slug>` and workstream `<type>-<slug>` with `git worktree add --no-track <worktree root>/<workstream> -b <branch> <base>`.
- [x] **WS-NEW-007**: When `new` creates a `build`, `idea`, or `triage` workstream without `--base`, including a `build` workstream adopting a branch with `--branch`, `resolve_state.py` shall use the default branch — the target of `refs/remotes/origin/HEAD`, else `main` or else `master` among the `origin` branches, else `main` or else `master` among the local branches, else `main` — as `origin/<default>` after `git fetch origin <default>`, or as the local branch when the repository has no `origin`.
- [x] **WS-NEW-008**: If the base for `new` is not the default branch and `--base-confirmed` is absent, then `resolve_state.py` shall return `BASE_CONFIRM_REQUIRED` and create nothing.
- [x] **WS-NEW-009**: When `new` creates a workstream, `resolve_state.py` shall record the base as a local branch name in `identity.base_branch` and the base commit in `anchors.workstream_base_sha`.
- [x] **WS-NEW-010**: When the base branch of a new workstream equals `identity.branch` of another open workstream of the same repository, `resolve_state.py` shall record that workstream in `identity.parent_workstream`.
- [x] **WS-NEW-011**: When `new` is given `--branch`, `resolve_state.py` shall add a worktree for that existing branch at `<worktree root>/<branch slug>` without creating a branch, record the branch's current `HEAD` as `anchors.workstream_base_sha`, and record `--base` or, without it, the default branch as `identity.base_branch`.
- [x] **WS-NEW-012**: If `new` is given `--branch` for a branch checked out in another worktree, then `resolve_state.py` shall return `BRANCH_CHECKED_OUT` with that worktree's path and create nothing.
- [x] **WS-NEW-013**: When `new` is given `--pr`, `resolve_state.py` shall read `number`, `url`, `headRefName`, and `baseRefName` with `gh pr view --json`, name the workstream `pr-<number>`, fetch `pull/<number>/head` from `origin`, and add a detached worktree at the fetched commit.
- [x] **WS-NEW-014**: When `new` creates a workstream from `--pr`, `resolve_state.py` shall set `identity.flow` to `pr_review`, `identity.branch` to `null`, `identity.pr` to the pull request URL, and `identity.base_branch` to the pull request's base branch; a `new` given `--issue`, `--branch`, or `--name` without `--idea` shall set `identity.flow` to `build`.
- [x] **WS-NEW-015**: If an open workstream of the same repository, of any flow, has `identity.issue`, `identity.pr`, or — for an `idea`-flow workstream — `lifecycle.report_url` equal to the issue or pull request URL `new` is asked to start from, then `resolve_state.py` shall return `DUPLICATE` naming that workstream and create nothing; a `null` field never matches.
- [x] **WS-NEW-016**: If state already exists for the workstream `new` would create, then `resolve_state.py` shall return `STATE_EXISTS` and create nothing.
- [x] **WS-NEW-017**: If the branch `new` would create already exists, then `resolve_state.py` shall return `BRANCH_EXISTS` and create nothing.
- [x] **WS-NEW-018**: If `gh` fails to read the issue or pull request given to `new`, then `resolve_state.py` shall return `GH_FAILED` with `gh`'s message and create nothing.
- [x] **WS-NEW-019**: When `new` succeeds, `resolve_state.py` shall create `state.json` with `schema_version` `1` and phase `INIT`, the memory partitions `hub`, `shared`, `planner`, `qa`, `dev`, `verifier`, `reviewer`, and `scout`, the `scout/`, `council/`, and `envelopes/` directories, and a lock held by the caller, and return `CREATED` with the identity and `worktree_path`.
- [x] **WS-NEW-020**: When `new` is given `--set KEY=VALUE`, `resolve_state.py` shall parse `VALUE` as JSON, or take it as a string when it is not valid JSON, and store the result under `KEY` in the new workstream's `config`, overriding the copied default.
- [x] **WS-NEW-021**: When `new` creates a workstream, `resolve_state.py` shall set `config.repo_mode` to `LID` if the new worktree contains `docs/high-level-design.md`, a `docs/intent/` directory, or an `AGENTS.md` or `CLAUDE.md` with a `## LID` heading line, and to `NAIVE` otherwise.
- [x] **WS-NEW-022**: `resolve_state.py` shall resolve a relative `--worktree-root` against the main worktree's root directory.
- [x] **WS-NEW-023**: If `new` is given `--branch` for a name that is not a local branch, then `resolve_state.py` shall exit with a usage error and create nothing.
- [x] **WS-NEW-024**: When `new` is given `--task-file FILE` and creates a workstream, `resolve_state.py` shall copy the file's text unchanged to `task.md` in the workstream's state directory.
- [x] **WS-NEW-025**: If `new` is given a `--task-file` that cannot be read or holds only whitespace, then `resolve_state.py` shall exit with a usage error and create no worktree, branch, or state.
- [x] **WS-NEW-026**: When `new` is given `--idea` or `--triage`, `resolve_state.py` shall add a worktree detached at the default branch, resolved per WS-NEW-007, with `git worktree add --detach <worktree root>/<workstream> <default>`, and create no branch.
- [x] **WS-NEW-027**: When `new` creates an `idea` or `triage` workstream, `resolve_state.py` shall set `identity.branch` to `null`, `identity.base_branch` to the default branch's local name, and `anchors.workstream_base_sha` to the commit the worktree is detached at.
- [x] **WS-NEW-028**: When `new` is given `--idea`, `resolve_state.py` shall name the workstream `idea-<slug>` with the slug from `--name`, and set `identity.flow` to `idea` and `identity.issue` to `null`.
- [x] **WS-NEW-029**: If `new` is given `--idea` without `--name` or without `--task-file`, then `resolve_state.py` shall exit with a usage error and create nothing.
- [x] **WS-NEW-030**: When `new` is given `--triage REF`, `resolve_state.py` shall read `number`, `title`, `labels`, and `url` with `gh issue view --json`, name the workstream `triage-<number>`, and set `identity.flow` to `triage` and `identity.issue` to the issue's URL.
- [x] **WS-NEW-031**: If `new` is given `--idea` or `--triage` together with `--base`, `--branch`, `--type`, `--base-confirmed`, `--issue`, or `--pr`, or `--idea` together with `--triage`, or `--triage` together with `--name`, then `resolve_state.py` shall exit with a usage error and create nothing.
- [x] **WS-NEW-032**: When `new` creates a workstream, `resolve_state.py` shall set `identity.tracker` to `github` when the workstream's `identity.issue` is not `null` or its `identity.flow` is `idea` or `triage`, and to `null` otherwise.
- [x] **WS-NEW-033**: When `new` creates a workstream, `resolve_state.py` shall set `anchors.last_known_head_sha` to `anchors.workstream_base_sha` for an `idea` or `triage` workstream and to `null` for every other flow.
- [x] **WS-NEW-034**: If `new` is given `--idea` or `--triage` in a repository that WS-ID-002 names `local`, then `resolve_state.py` shall return `NO_TRACKER` and create nothing.
- [x] **WS-NEW-035**: If `new` is given `--idea` or `--triage` in a repository with no commits, then `resolve_state.py` shall return `NO_COMMITS` and create nothing.
- [x] **WS-NEW-036**: If `git fetch origin <default>` fails while `new` resolves the default branch as its base per WS-NEW-007, then `resolve_state.py` shall return `FETCH_FAILED` with git's message and create nothing.
- [x] **WS-NEW-037**: If the base of `new` — `--base` when given; otherwise, for a `build`, `idea`, or `triage` workstream, the default branch as resolved per WS-NEW-007 — names no commit, then `resolve_state.py` shall return `BASE_NOT_FOUND` and create nothing.
- [x] **WS-NEW-038**: When more than one of `NO_TRACKER`, `NO_COMMITS`, `GH_FAILED`, `DUPLICATE`, `STATE_EXISTS`, `BRANCH_EXISTS`, `BASE_CONFIRM_REQUIRED`, `FETCH_FAILED`, `BASE_NOT_FOUND`, and `BRANCH_CHECKED_OUT` applies to `new`, `resolve_state.py` shall return the first of them in that order.
- [x] **WS-NEW-039**: When `new` creates a workstream, `resolve_state.py` shall set `identity.promoted_from` to `null`.

## State file

- [x] **WS-STATE-001**: `resolve_state.py` shall write `state.json` by writing a temporary file in the same directory and renaming it over `state.json` with `os.replace`.
- [x] **WS-STATE-002**: If `state.json` does not parse or its `schema_version` is greater than the script supports, then `resolve_state.py` shall return `STATE_INVALID` and leave the file unchanged.

## Lock

- [x] **WS-LOCK-001**: When `wake` finds no lock, `resolve_state.py` shall acquire it by creating `lock.json` with an exclusive create, recording `holder_pid`, `holder_session`, `host`, and `acquired_at` from the caller's arguments and the local host.
- [x] **WS-LOCK-002**: If the exclusive create of `lock.json` fails because another process created it first, then `resolve_state.py` shall evaluate that lock as an existing one.
- [x] **WS-LOCK-003**: When `wake` finds a lock whose `holder_pid` and `holder_session` equal the caller's, `resolve_state.py` shall keep it.
- [x] **WS-LOCK-004**: When `wake` finds a lock held on this host by a process that is no longer running, `resolve_state.py` shall delete it, acquire the lock for the caller, and report that it cleared a dead lock.
- [x] **WS-LOCK-005**: If `wake` finds a lock held by another running process on this host, or by any holder on another host, then `resolve_state.py` shall return `LOCKED` with the holder's details.
- [x] **WS-LOCK-006**: When `wake` is given `--steal`, `resolve_state.py` shall replace any existing lock with one held by the caller.
- [x] **WS-LOCK-007**: When `release` is called by the lock's holder, `resolve_state.py` shall delete the lock.
- [x] **WS-LOCK-008**: If `release`, `close`, or `promote` is called by a process that does not hold the lock, then `resolve_state.py` shall return `NOT_HOLDER` and change nothing.

## Drift

- [x] **WS-DRIFT-001**: When `anchors.last_known_head_sha` is `null`, equals `HEAD`, or the repository has no commits, `resolve_state.py` shall report drift `none`.
- [x] **WS-DRIFT-002**: When `anchors.last_known_head_sha` is a proper ancestor of `HEAD`, `resolve_state.py` shall report drift `ahead` with the new commits.
- [x] **WS-DRIFT-003**: When `HEAD` is a proper ancestor of `anchors.last_known_head_sha`, `resolve_state.py` shall report drift `behind` with the orphaned commits.
- [x] **WS-DRIFT-004**: When neither `HEAD` nor `anchors.last_known_head_sha` is an ancestor of the other, `resolve_state.py` shall report drift `diverged` with both the new and the orphaned commits.
- [x] **WS-DRIFT-005**: If `anchors.last_known_head_sha` does not exist in the repository, then `resolve_state.py` shall return `DRIFT_UNKNOWN`.
- [x] **WS-DRIFT-006**: When reporting new commits for `ahead` or `diverged` drift, `resolve_state.py` shall list the first-parent commits from `last_known_head_sha` (exclusive) to `HEAD`, oldest first, each marked `forge` if it carries a `Forge-Agent` trailer and `human` otherwise.
- [x] **WS-DRIFT-007**: When drift is `behind` or `diverged`, `resolve_state.py` shall append every commit reachable from `last_known_head_sha` but not from `HEAD` to `anchors.stale_shas` as a full SHA, skipping SHAs already present.
- [x] **WS-DRIFT-008**: After `new` creates a workstream, `resolve_state.py` shall never change its `anchors.last_known_head_sha`, and `resolve_state.py` shall never change any memory file.
- [x] **WS-DRIFT-009**: When `wake` returns `OK`, `resolve_state.py` shall report as `dirty` every path listed by `git status --porcelain`, including untracked files.

## Wake

- [x] **WS-WAKE-001**: When more than one of `STATE_INVALID`, `IDENTITY_MISMATCH`, `ELSEWHERE`, `LOCKED`, and `DRIFT_UNKNOWN` applies, `resolve_state.py` shall return the first of them in that order.
- [x] **WS-WAKE-002**: When `wake` returns `STATE_INVALID`, `IDENTITY_MISMATCH`, or `ELSEWHERE`, `resolve_state.py` shall leave any existing lock unchanged and acquire none.
- [x] **WS-WAKE-003**: When `wake` finds no state for the resolved workstream and `LIST` does not apply, `resolve_state.py` shall return `NO_STATE` and create nothing.
- [x] **WS-WAKE-004**: `resolve_state.py` shall include `identity` (as derived from the current checkout), `state_path`, and `lock` in every `wake` report, also `drift` and `dirty` when the status is `OK`, and also `base_behind` (WS-WAKE-005, WS-WAKE-006), present but possibly `null`, when the status is `OK` and the workstream's `identity.flow` is `idea` or `triage`.
- [x] **WS-WAKE-005**: When `wake` returns `OK` for an `idea` or `triage` workstream, `resolve_state.py` shall report as `base_behind` the number of commits reachable from `origin/<identity.base_branch>` — or, in a repository with no `origin`, from the local branch `<identity.base_branch>` — and not from `anchors.workstream_base_sha`, without fetching and without re-resolving the default branch (`identity.base_branch` is the default branch's name at creation, per WS-NEW-027).
- [x] **WS-WAKE-006**: If the ref WS-WAKE-005 counts from does not exist when `wake` reports on an `idea` or `triage` workstream, then `resolve_state.py` shall report `base_behind` as `null`, meaning unknown rather than `0`, and keep the status `OK`.

## Listing

- [x] **WS-LIST-001**: When `list` runs, `resolve_state.py` shall return, for every workstream directory under `state/<org>/<repo>/` of the current repository, its `workstream`, `status` `OK`, `branch`, `worktree_path`, `current_phase`, `last_status`, `updated_at` (the modification time of its `state.json`), and its lock holder with whether that holder is running on this host.
- [x] **WS-LIST-002**: `resolve_state.py` shall sort `list` entries by `state.json` modification time, most recent first.
- [x] **WS-LIST-003**: If a workstream's `state.json` does not parse, then `list` shall include that workstream with status `STATE_INVALID`.

## Closeout

- [x] **WS-CLOSE-001**: When `close` is called by the lock holder and the workstream's `config.retention` is `archive`, `resolve_state.py` shall move the workstream directory to `archive/<org>/<repo>/<workstream>-<UTC timestamp>/`.
- [x] **WS-CLOSE-002**: When `close` is called by the lock holder and the workstream's `config.retention` is `delete`, `resolve_state.py` shall delete the workstream directory.
- [x] **WS-CLOSE-003**: When `close` is given `--remove-worktree`, `resolve_state.py` shall run `git worktree remove` on the workstream's worktree before archiving or deleting the workstream directory.
- [x] **WS-CLOSE-005**: If `git worktree remove` refuses during `close`, then `resolve_state.py` shall return `WORKTREE_DIRTY` with git's message and leave the workstream directory and lock unchanged.
- [x] **WS-CLOSE-004**: `resolve_state.py` shall never delete a branch.
- [x] **WS-CLOSE-006**: When `close` is given `--reason-file FILE`, `resolve_state.py` shall, after any worktree removal succeeds, write `dropped` `{reason, at}` into `state.json`, with `reason` the file's text with surrounding whitespace removed and `at` the current UTC time in ISO 8601, and then archive the workstream directory per WS-CLOSE-001 whatever `config.retention` says.
- [x] **WS-CLOSE-007**: If `close` is given a `--reason-file` that cannot be read or whose text is empty after removing surrounding whitespace, then `resolve_state.py` shall exit with a usage error and change nothing.
- [x] **WS-CLOSE-008**: When `close` runs without `--reason-file`, `resolve_state.py` shall write no `dropped` field.

## Promotion

- [x] **WS-PROMOTE-001**: If `promote` is called for a source workstream whose `lifecycle.promote` is `null` or absent, then `resolve_state.py` shall return `PROMOTE_NOT_REQUESTED` and change nothing.
- [x] **WS-PROMOTE-002**: When `promote` is given `--remove-worktree` and `git status --porcelain` lists any path in the source workstream's worktree, `resolve_state.py` shall return `WORKTREE_DIRTY` with those paths before creating anything, and change nothing.
- [x] **WS-PROMOTE-003**: When `promote` creates the build workstream, `resolve_state.py` shall create it as `new --issue <lifecycle.promote.issue> --task-file <lifecycle.promote.proposal>` would, with `promote`'s `--worktree-root`, `--holder-pid`, and `--holder-session`, with `config.mode` set to the source workstream's `config.mode`, and with `identity.promoted_from` set to the source workstream's name.
- [x] **WS-PROMOTE-004**: When `promote` checks the new build workstream for `DUPLICATE` (WS-NEW-015), `resolve_state.py` shall ignore the source workstream.
- [x] **WS-PROMOTE-005**: If creating the build workstream during `promote` returns a status other than `CREATED`, then `resolve_state.py` shall return that status with its details and leave the source workstream unchanged.
- [x] **WS-PROMOTE-006**: When `promote` has created the build workstream, `resolve_state.py` shall close the source workstream as `close` does (WS-CLOSE-001 to WS-CLOSE-005), with `--remove-worktree` when `promote` was given it.
- [x] **WS-PROMOTE-007**: When `promote` has closed the source workstream, `resolve_state.py` shall return `PROMOTED` with the build workstream's identity and `worktree_path`, the caller holding the build workstream's lock.
- [x] **WS-PROMOTE-008**: If creating the build workstream during `promote` meets a usage error of `new` (such as a `lifecycle.promote.proposal` that cannot be read or holds only whitespace, per WS-NEW-025), then `resolve_state.py` shall exit with that usage error and leave the source workstream unchanged.
- [x] **WS-PROMOTE-009**: When more than one of `NOT_HOLDER`, `STATE_INVALID`, `PROMOTE_NOT_REQUESTED`, `WORKTREE_DIRTY` (WS-PROMOTE-002), `LOCKED` (WS-PROMOTE-012) or a status of `new` other than `CREATED` (WS-PROMOTE-005), and a failure to close the source (WS-PROMOTE-013) applies to `promote`, `resolve_state.py` shall return the first of them in that order.
- [x] **WS-PROMOTE-010**: When an open `build`-flow workstream of the same repository has `identity.promoted_from` equal to the name of the workstream `promote` is called for, `resolve_state.py` shall take that workstream as the build workstream instead of creating one per WS-PROMOTE-003, so no refusal of `new` (including `DUPLICATE`) applies, and leave its `task.md` and `config` unchanged.
- [x] **WS-PROMOTE-011**: When `promote` takes an existing build workstream per WS-PROMOTE-010, `resolve_state.py` shall acquire that workstream's lock for the caller as `wake` does per WS-LOCK-001 to WS-LOCK-004.
- [x] **WS-PROMOTE-012**: If the build workstream `promote` takes per WS-PROMOTE-010 has a lock held by another running process on this host, or by any holder on another host, then `resolve_state.py` shall return `LOCKED` with the holder's details and leave the source and the build workstream unchanged.
- [x] **WS-PROMOTE-013**: If closing the source workstream during `promote` fails, then `resolve_state.py` shall return the status `close` returned (such as `WORKTREE_DIRTY`) with git's message and the build workstream's name, leaving the build workstream open with its lock held by the caller.
- [ ] **WS-PROMOTE-014**: If `promote` is called for a source workstream whose `lifecycle.promote` is present and not `null` but is not an object holding a non-empty string `issue` and a non-empty string `proposal`, then `resolve_state.py` shall return `STATE_INVALID` with a `detail` naming the missing or wrong field, and change nothing.

## Command interface

- [x] **WS-CLI-001**: `resolve_state.py` shall print exactly one JSON object to stdout and exit `0` for every command outcome that has a status.
- [x] **WS-CLI-002**: If a command has invalid arguments or the working directory is not inside a git repository, then `resolve_state.py` shall print an error to stderr and exit non-zero.
- [x] **WS-CLI-003**: `resolve_state.py` shall use only the Python standard library and external `git` and `gh` commands.
