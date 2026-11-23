---
parent: high-level-design
prefix: WS
---

# Workstream State

## Context and Design Philosophy

Every `/forge` session starts by answering the same questions: which workstream is this, does it already have state, is another session driving it, and has the repository moved since Forge last looked? `resolve_state.py` answers them deterministically from git and the filesystem, so the Hub never infers identity or drift from conversation context.

The script reports and the Hub decides. It never routes, never asks the user anything, and never changes the repository. Where a situation is ambiguous — a contested lock, an identity mismatch, history it cannot reconstruct — it returns a status naming the problem and leaves the choice to the Hub and the user.

The script lives in `reference/scripts/`, uses only the Python standard library, and takes every harness-specific value (such as the holder process ID) as an argument, so any CLI can call it.

## Storage Root

All Forge state lives under `FORGE_HOME`, which defaults to `~/.forge` and is overridden by the `FORGE_HOME` environment variable. The override exists for the test suite and the vendored sandbox, which must never touch the user's real state.

```text
$FORGE_HOME/
  config.json                         # global defaults
  state/<org>/<repo>/<workstream>/
    state.json  lock.json  task.md  proposal.md
    scout/  council/  envelopes/
    memory/{hub,shared,planner,qa,dev,verifier,reviewer,scout}/
  archive/<org>/<repo>/<workstream>-<UTC timestamp>/
```

`config.json` holds defaults copied into a new workstream's `config` section: `mode` (one of `delivery`, `collab-plan`, `collab-qa`, `collab-dev`, `collab-code`, `collab`; default `collab`) and `retention` (`archive` or `delete`, default `archive`). A missing file means all defaults. The router design defines what each mode does.

## Identity Derivation

### `<org>` and `<repo>`

From `git remote get-url origin`, parsed as one of:

- scp-like SSH: `[user@]host:path`
- URL form: `ssh://`, `https://`, `http://`, `git://` with optional user and port
- a local path or `file://` URL

A **hosted repository** has a host-based `origin`. Its path is stripped of a trailing `/` and `.git`; `<repo>` is the last segment and `<org>` is the remaining segments joined with `-` (so a GitLab subgroup path `group/sub/repo` gives org `group-sub`).

A **local repository** has no `origin`, a local-path or `file://` `origin`, or a host-based `origin` whose path has a single segment. It resolves to `local/<project-slug>`, where the project slug is the name of the project directory: the basename of the directory containing the absolute `git rev-parse --path-format=absolute --git-common-dir`. The remote's own path never names a local repository. Because the common dir is shared by all linked worktrees, every worktree of a repository resolves to the same `<org>/<repo>`.

`<org>` and `<repo>` are both passed through the same slug rule as branch names (see *`<workstream>`*), so every path segment under `state/` is a valid slug. Two local projects whose directories have the same name share a state directory; the identity check below reports that as `IDENTITY_MISMATCH`.

### `<workstream>`

Resolved in priority order:

1. An explicit argument (`/forge <workstream>`).
2. The `FORGE_WORKSTREAM` environment variable.
3. The current branch from `git rev-parse --abbrev-ref HEAD`. On a detached HEAD, the open workstream whose `identity.worktree_path` is the current worktree (`git rev-parse --show-toplevel`), so a session inside a `pr_review`, `idea`, or `triage` worktree finds its workstream; with no such workstream, `detached-<7-char short SHA>`. In a repository with no commits yet, the branch comes from `git symbolic-ref --short HEAD`.

A branch name is turned into a slug by lowercasing, replacing every character outside `[a-z0-9._-]` with `-`, collapsing runs of `-`, and trimming leading and trailing `-` and `.`. An explicit argument or environment value must already be a valid slug; anything else (including `..` or a `/`) is rejected rather than sanitized, so a typed name always means exactly that workstream.

When the workstream comes from the current branch and that branch is `main` or `master` with no existing state, the script returns the workstream list instead of a workstream.

### Identity record

A workstream's `state.json` records `org`, `repo`, `workstream`, `flow` (`build`, `pr_review`, `idea`, or `triage`), `tracker`, `branch`, `base_branch`, `issue`, `pr`, `parent_workstream`, `git_common_dir`, and `worktree_path` at creation (see *Workstream Creation*). `tracker` names the tracker index entry the workstream's issue operations use: `github`, the only entry Forge ships, for a workstream with an issue and for every idea or triage workstream; `null` for one with no issue operations. On every wake the script compares the recorded identity with the current checkout:

- **Different `git_common_dir`** — another clone of the same repository resolved to the same state directory. Status `IDENTITY_MISMATCH`.
- **Different branch, when the workstream came from the current branch** — two branch names sanitized to the same slug (`feat/x` and `feat-x`). Status `IDENTITY_MISMATCH`.
- **A detached workstream** (`identity.flow` is `pr_review`, `idea`, or `triage`) skips both branch comparisons: its worktree is detached and has no branch. The worktree comparison below still applies.
- **Different worktree, or (for a `build` workstream) different branch, when the workstream was named explicitly** — the user is resuming a workstream that lives elsewhere. Status `ELSEWHERE`, reporting the recorded branch and worktree path, so the Hub can move the session into that worktree.

## Workstream Creation

Every workstream lives in its own git worktree under a **worktree root** the caller passes in (`--worktree-root`); the Claude Code Commander passes `.claude/worktrees` under the main worktree. The script never assumes a harness's directory layout.

`new` creates the worktree, the branch, and the workstream state in one step. Its inputs decide the name, branch, and base:

| Input | Effect |
|---|---|
| `--issue REF` | Reads `number`, `title`, `labels`, and `url` with `gh issue view --json`. The slug is `<number>-<title slug>`, cut at a `-` to at most 48 characters, or `<number>` alone when the title slugs to nothing. The type is `fix` when any label name contains `bug`, otherwise `feat`. |
| `--name SLUG` | The slug, validated like an explicit workstream name. Used when there is no issue. |
| `--task-file FILE` | The user's task description. Its text is copied to `task.md` in the workstream directory, where the router hands it to the planner. A file that cannot be read or is empty is a usage error, and `new` creates nothing. |
| `--type TYPE` | One of `feat`, `fix`, `chore`, `docs`, `refactor`; overrides the type derived from an issue. Default `feat`. |
| `--branch NAME` | Adopt an existing local branch instead of creating one; the slug comes from the branch name. |
| `--pr REF` | Start a `pr_review` workstream (see below). |
| `--idea` | Start an `idea` workstream named by `--name`, with the idea in `--task-file` (see below). |
| `--triage REF` | Start a `triage` workstream for an existing issue (see below). |
| `--base REF` | The branch to start from. Default: the default branch (`origin/<default>` after `git fetch origin <default>`, or the local default branch when there is no `origin`). The default branch is the target of `origin/HEAD`; without one, `main` or else `master` among the `origin` branches, then among the local branches, and `main` when neither exists. |
| `--base-confirmed` | Required whenever the base is not the default branch. |

The new branch is `<type>/<slug>` and the workstream is that branch's slug (`<type>-<slug>`). The worktree is created with `git worktree add --no-track <root>/<workstream> -b <branch> <base>`, so the new branch has no upstream until the publisher pushes it, then the workstream state is initialized inside it, as described under *Command Interface*. `identity.base_branch` records the base as a local branch name (`main`, or `feat/parent-change`); `anchors.workstream_base_sha` records the base commit. When the base branch is the branch of another open workstream, `identity.parent_workstream` names it.

**Stacked work.** A base other than the default branch returns `BASE_CONFIRM_REQUIRED` and creates nothing unless `--base-confirmed` is passed. Stacking is legitimate but easy to do by accident, so the user confirms it explicitly; the Hub passes the flag only after asking.

**Adopting a branch.** With `--branch`, the worktree is added for the existing branch (`git worktree add <root>/<workstream> <branch>`), and `workstream_base_sha` is the branch's current HEAD. `identity.base_branch` is `--base` when given, otherwise the default branch. A `--branch` that names no local branch is a usage error. A branch already checked out in another worktree — typically the user's main checkout — returns `BRANCH_CHECKED_OUT` with that worktree's path, because git allows a branch in only one worktree; the user switches that checkout away first.

**Review workstreams.** With `--pr`, the script reads `number`, `url`, `headRefName`, and `baseRefName` with `gh pr view --json`, names the workstream `pr-<number>`, fetches the pull request head (`git fetch origin pull/<number>/head`), and adds a detached worktree at it, so the reviewer can read whole files and run tests. `identity.flow` is `pr_review`, `branch` is `null`, and `base_branch` is the pull request's base.

**Idea and triage workstreams.** Both study a change without making it, so each gets a worktree detached at the default branch (`git worktree add --detach <root>/<workstream> <default>`, with the default branch resolved as for `--base`), where the planner reads the code as it stands. `branch` is `null` and `base_branch` is the default branch. `--base`, `--branch`, `--type`, and `--base-confirmed` are usage errors with either flag. Nothing should commit in these worktrees, so `new` also sets `anchors.last_known_head_sha` to `workstream_base_sha`, and any commit, even one made before the first dispatch, is drift. The code studied is the default branch as it stood at creation and is never refreshed; for these workstreams `wake` reports `base_behind`, the number of commits on the default branch (its last fetched `origin/<default>`, or the local default branch without an `origin`) not reachable from `workstream_base_sha`, so a resumed study shows how stale it is.

- `--idea` requires `--name` and `--task-file`. The workstream is `idea-<slug>`, `identity.flow` is `idea`, and `identity.issue` is `null`.
- `--triage REF` reads the issue as `--issue` does. The workstream is `triage-<number>`, `identity.flow` is `triage`, and `identity.issue` is the issue's URL. `--task-file` is optional and carries the user's own words about the issue.

**Refusals.** `new` creates nothing and returns the first of these that applies, checked in this order:

- `NO_TRACKER` for an idea or triage workstream in a local repository, where no tracker can file or read an issue;
- `NO_COMMITS` for an idea or triage workstream in a repository with no commits, where there is nothing to detach a worktree at;
- `GH_FAILED` with `gh`'s message when an issue or pull request cannot be read;
- `DUPLICATE`, naming the existing workstream, when an open workstream tracks the same issue or pull request URL, whatever either workstream's flow: its `identity.issue` or `identity.pr`, or, for an open idea workstream, the issue it filed (`lifecycle.report_url`). A `null` URL never matches;
- `STATE_EXISTS` when the workstream slug already has state;
- `BRANCH_EXISTS` when the new branch name already exists (unless adopting it with `--branch`);
- `BASE_CONFIRM_REQUIRED` as above;
- `FETCH_FAILED` with git's message when `git fetch origin <default>` fails;
- `BASE_NOT_FOUND` when the base (from `--base`, or the default branch as resolved) names no commit;
- `BRANCH_CHECKED_OUT` as above.

On success it returns `CREATED` with the identity and `worktree_path`.

## state.json

`state.json` is the authoritative record of a workstream. This section fixes its top-level shape and which component writes each section; the router design defines the fields it writes.

| Section | Contents | Written by |
|---|---|---|
| `schema_version` | Integer, currently `1` | `resolve_state.py` at creation |
| `identity` | See *Identity record* and *Workstream Creation* | `resolve_state.py` at creation |
| `config` | `repo_mode`, `mode`, `retention`, and optional `test_patterns` (see the gates design) and `test_command` (the command spokes run tests with; see the spokes design) | `resolve_state.py` at creation; `next_step.py` changes `mode` when the user switches it |
| `anchors` | `workstream_base_sha`, `plan_base_sha`, `last_known_head_sha`, `verifier_pass_sha`, `reviewer_pass_sha`, `stale_shas` | `next_step.py`, except `workstream_base_sha` (set by `resolve_state.py` at creation to `HEAD`, or `null` in a repository with no commits), `last_known_head_sha` in an idea or triage workstream (set at creation to `workstream_base_sha`), and `stale_shas`, which `resolve_state.py` appends to |
| `lifecycle` | Phase, flow, dispatch, pause, and repair bookkeeping; fields listed in the router design | `next_step.py` |
| `scope_manifest`, `blockers`, `council_decisions`, `approvals` | Routing and gate data | `next_step.py` |
| `dropped` | `{reason, at}`, present only on a workstream the user abandoned | `resolve_state.py close --reason-file` |

Every write goes to a temporary file in the same directory followed by `os.replace`, so a reader never sees a partial file. A file whose `schema_version` is newer than the script supports, or that does not parse, returns `STATE_INVALID` and is never overwritten.

### Repository mode

At creation the script sets `config.repo_mode` to `LID` when the repository root contains `docs/high-level-design.md`, a `docs/intent/` directory, or an `AGENTS.md` or `CLAUDE.md` with a `## LID` heading; otherwise `NAIVE`.

## Lock

`lock.json` records the session driving the workstream: `holder_pid`, `holder_session`, `host`, and `acquired_at`. The Hub passes its own process ID and session ID as arguments; the script never discovers them itself.

On wake the script:

- **acquires** the lock when none exists;
- **keeps** it when the holder PID and session match the caller;
- **replaces** it when the holder is on this host and its PID is no longer running, reporting that it cleared a dead lock;
- otherwise returns `LOCKED` with the holder's details — a live holder on this host, or any holder on another host, whose liveness cannot be checked.

The lock file is created with an exclusive create (`O_CREAT | O_EXCL`), so when two sessions wake at the same moment exactly one acquires it and the other gets `LOCKED`. Replacing a dead lock deletes it and retries the exclusive create.

`--steal` replaces a held lock unconditionally; the Hub passes it only after the user confirms. `release` deletes the lock when the caller holds it and returns `NOT_HOLDER` otherwise, leaving the lock in place.

## Drift Detection

Drift compares the current `HEAD` with `anchors.last_known_head_sha`, the HEAD at the end of the last Forge turn. The script reports drift and updates only `stale_shas`; deciding what to do — including when to advance `last_known_head_sha` — belongs to the router.

| Kind | Condition | Reported |
|---|---|---|
| `none` | `last_known_head_sha` is null, equals `HEAD`, or the repository has no commits | — |
| `ahead` | `last_known_head_sha` is an ancestor of `HEAD` | The new commits, oldest first, each marked `human` or `forge` by the presence of a `Forge-Agent` trailer |
| `behind` | `HEAD` is an ancestor of `last_known_head_sha` | The orphaned commits |
| `diverged` | Neither is an ancestor of the other (rebase, amend, reset to another line) | Both the new commits and the orphaned commits |
| `unknown` | `last_known_head_sha` is no longer in the repository | Nothing; status `DRIFT_UNKNOWN` |

New commits are listed along the first-parent chain (`git rev-list --first-parent`), so merging another branch in shows up as the single merge commit — classified by its own trailer — rather than as every commit of the merged branch.

For `behind` and `diverged`, the orphaned commits — those reachable from `last_known_head_sha` but not from `HEAD` — are appended to `anchors.stale_shas` as full SHAs, without duplicates. Forge memory treats any entry anchored to one of these SHAs as historical. Memory files themselves are never edited.

Uncommitted changes are reported separately as `dirty`: the paths from `git status --porcelain`, including untracked files. A dirty tree does not change the status; the Hub must resolve it before dispatching any spoke that writes.

## Listing

`list` reads every `state.json` under `state/<org>/<repo>/` for the current repository and returns, for each workstream: `workstream`, `status` (`OK`), `branch`, `worktree_path`, `current_phase`, `last_status`, `updated_at` (the file's modification time), and the lock holder with whether it is alive on this host. Entries are sorted most recently updated first. A workstream whose state does not parse is listed with status `STATE_INVALID` rather than hidden.

## Closeout

`close` ends a workstream. With `retention: archive` it moves the workstream directory to `archive/<org>/<repo>/<workstream>-<UTC timestamp>/`; with `delete` it removes it. It refuses when the caller does not hold the lock. With `--remove-worktree` it first runs `git worktree remove` on the workstream's worktree. Git refuses when the worktree has uncommitted changes; `close` then returns `WORKTREE_DIRTY` with git's message and changes nothing else, keeping the state directory and the lock, so the user can clean up and close again. The branch is never deleted. The Hub asks the user before passing `--remove-worktree`.

A workstream the user abandons before it finishes is closed the same way with `--reason-file FILE`: after any worktree removal succeeds and before archiving, `close` writes `dropped: {reason, at}` (`reason` the file's text with surrounding whitespace removed, `at` a UTC ISO 8601 timestamp) into `state.json`, so the archive records why the work stopped. A drop always archives, whatever `retention` says, so the reason is kept. An unreadable file or an empty reason is a usage error and changes nothing. Without `--reason-file`, `close` writes no `dropped` field.

### Promotion

`promote` turns a finished idea or triage workstream into a build workstream in one call, creating before it closes, so a failure never loses the study. It acts only on the request the router recorded: it refuses with `PROMOTE_NOT_REQUESTED` when the source's `lifecycle.promote` is `null`, and otherwise takes `issue` and `proposal` from it. It requires the caller to hold the source's lock, like `close`. In order:

1. With `--remove-worktree`, it checks the source worktree for uncommitted changes and returns `WORKTREE_DIRTY` with the paths when there are any.
2. It creates the build workstream exactly as `new --issue <issue> --task-file <proposal>` would, with `--worktree-root` and the holder arguments passed through and `config.mode` taken from the source; the source is ignored when checking `DUPLICATE`. Any refusal from `new` is returned as it is.
3. It closes the source as `close` does, with `--remove-worktree` when given.

Any failure before the last step leaves the source open and unchanged. On success it returns `PROMOTED` with the new workstream's identity and `worktree_path`, and the caller holds the new workstream's lock.

## Command Interface

```text
resolve_state.py wake  [--workstream SLUG] --holder-pid PID --holder-session ID [--steal]
resolve_state.py new   (--issue REF | --name SLUG | --branch NAME | --pr REF | --idea --name SLUG | --triage REF)
                       [--type TYPE] [--task-file FILE]
                       [--base REF [--base-confirmed]] --worktree-root DIR
                       --holder-pid PID --holder-session ID [--set KEY=VALUE ...]
resolve_state.py list
resolve_state.py release --workstream SLUG --holder-pid PID --holder-session ID
resolve_state.py close   --workstream SLUG --holder-pid PID --holder-session ID [--remove-worktree] [--reason-file FILE]
resolve_state.py promote --workstream SLUG --worktree-root DIR --holder-pid PID --holder-session ID [--remove-worktree]
```

After creating the worktree, `new` creates the workstream directory, `state.json` (phase `INIT`), the memory partitions, the lock, and `task.md` when `--task-file` is given; `--set` overrides `config` keys for this workstream; each `VALUE` is parsed as JSON (so `test_patterns=["spec/**"]` stores a list), and a value that is not valid JSON is stored as a string (`mode=delivery`). A `mode` that is not one of the six names, from `--set` or from `config.json`, is a usage error and nothing is created. `wake` never creates state: with no state it returns `NO_STATE`, and the Hub decides whether to start a workstream with `new`.

Every command prints one JSON object to stdout and exits `0`, including for every status above. A non-zero exit means a usage error or that the working directory is not a git repository; the message goes to stderr.

`wake` statuses: `OK`, `NO_STATE`, `LIST`, `ELSEWHERE`, `LOCKED`, `IDENTITY_MISMATCH`, `DRIFT_UNKNOWN`, `STATE_INVALID`. When several apply, the first in this order is reported: `STATE_INVALID`, `IDENTITY_MISMATCH`, `ELSEWHERE`, `LOCKED`, `DRIFT_UNKNOWN`, `OK`. The lock is acquired only on a path that reaches `LOCKED`'s check and passes it, so `STATE_INVALID`, `IDENTITY_MISMATCH`, and `ELSEWHERE` leave any lock untouched. The report always includes `identity` (as derived now), `state_path`, and `lock`; with `OK` it also includes `drift` and `dirty`, and `base_behind` for an idea or triage workstream.

Bare repositories and submodule-specific behavior are not supported; the script runs against the working tree it is invoked in.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Stale memory marking | Append orphaned SHAs to `anchors.stale_shas`; memory files untouched | Rewrite `stale: true` into each memory file | One writer per file; the memory component owns its files and filters on load. Also avoids parsing memory entries here. |
| Who advances `last_known_head_sha` | The router, when it handles the drift | `resolve_state.py` on detection | If the session dies after detection, the drift is still reported on the next wake instead of being silently absorbed. |
| Lock liveness | Holder PID and session passed in by the Hub; PID checked on the same host | Walking parent processes to find the harness; time-based leases | Passing values keeps the script CLI-neutral. A lease would expire during long human pauses and let a second session take over a live one. |
| Worktree root | Passed in by the caller | Hardcode `.claude/worktrees` | The script is shared across CLIs; only the Claude Code adapter knows Claude Code's layout. |
| Non-default base | Refused without `--base-confirmed` | Hub prompt asks before choosing a base | *Scripts over prompts*: the script guarantees stacking never happens unconfirmed. |
| Review worktree | Detached at the pull request head | No worktree; diff only | The reviewer can read whole files and run the tests, and every workstream has the same shape. |
| Idea and triage worktree | Detached at the default branch | A new branch; no worktree, reading the main checkout | Neither flow commits, so a branch would be left empty; reading the main checkout would see the user's uncommitted edits and whatever branch is checked out there. |
| Reading an issue for naming | `gh issue view` in the script, per tracker | A command template in the tracker index entry; the scout reads the issue before the workstream exists | An MCP tracker cannot be read by any script, so a template would cover only some trackers; the scout writes into workstream state, which does not exist yet. |
| One issue, one open workstream | `DUPLICATE` across flows, including the issue an idea workstream filed, so a triage and a build workstream never track the same issue at once | Allow a triage beside a build workstream for the same issue | Two open workstreams on one issue could both post to it. `promote` ignores its own source, so the rule never blocks promotion. |
| Promotion | One `promote` command: create the build workstream, then close the source | The Commander closes the source, then runs `new`; create with `new` first | Closing first archives the proposal and loses the study when `new` fails; `new` alone is refused as `DUPLICATE` while the source is open. One call that creates before closing leaves either the source unchanged or the promotion finished. |
| Idea or triage without a tracker | `NO_TRACKER` at creation in a local repository | Create, and fail at the scout or the send | Failing after the study is done wastes the whole flow; whether a tracker exists is known before anything starts. |
| Finding a detached workstream | By `identity.worktree_path` before falling back to `detached-<sha>` | Require `/forge <workstream>` inside detached worktrees | A detached worktree has no branch to name its workstream, but its path is recorded and unique. |
| Local repository naming | `local/<project-slug>` from the project directory name | Name from the local remote's path | The project directory is the name the user knows the repository by; a local remote's path is often a bare mirror with an unrelated name. |
| Explicit workstream names | Validated, never sanitized | Sanitize like branch names | A name the user types should address exactly one workstream; sanitizing could silently map a typo onto another one. |
| `wake` does not create state | Return `NO_STATE`; create with `new` | Create on first wake | Starting a workstream is the user's choice; an accidental `/forge` on a scratch branch should not leave state behind. |
| Last-update time in `list` | `state.json` modification time | An `updated_at` field | Every write already updates the mtime; a field would need every writer to maintain it. |
| Repository mode detection | Done here, at creation | Left to the planner | It is a mechanical file check, and the router needs `repo_mode` before the planner runs. |
| State root override | `FORGE_HOME` environment variable | Fixed `~/.forge` | Tests and the sandbox must run without touching the user's real state. |
| Pacing setting | One `mode` name | `plan_mode`, `collaborative`, and `council_auto_resolve` as separate keys | One name is what the user picks and switches; the router derives each pause from it, so no combination can be stored that no mode describes. |
| Abandoning a workstream | `close --reason-file` records `dropped` in the archived state | A separate `drop` command; deleting the state | Dropping is closing plus a reason; one command keeps the lock, worktree, and retention rules in one place. |
| Task description | Copied to `task.md` beside `state.json` by `new --task-file` | A `state.json` field; carried only in the `start` event | A file is read like the scout's brief and survives resume and re-dispatch; a state field would mix free text into routing state, and an event is gone after the first dispatch. |

## Open Questions & Future Decisions

### Deferred
1. A `FORGE_HOME` shared between machines (network home directory): locks from another host are always treated as live, so the user must confirm a steal.
2. Retention of archived workstreams (age-based pruning) — `archive/` grows without bound.

## References

- `docs/high-level-design.md` — workstream state outside the repo, listing and resuming, tenet *Stop over guess*.
- Router design (`docs/intent/router/`) — consumes the wake report and owns all other `state.json` writes.
- Forge memory design (`docs/intent/forge-memory/`) — filters entries anchored to `anchors.stale_shas`.
