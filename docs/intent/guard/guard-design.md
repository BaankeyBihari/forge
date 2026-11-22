---
parent: high-level-design
prefix: GUARD
---

# Guard

## Context and Design Philosophy

The gate checks commits after they exist; the guard stops a Forge agent's tool call before it happens. It is the layer that keeps `forge-dev` from opening a test file in an editor tool, a read-only spoke from writing anywhere, any spoke from pushing, and a confused agent from repeating the same call forever.

The guard splits into two parts:

- **`guard_core.py`** (`reference/scripts/`) — a CLI-neutral decision function: given the acting agent, the kind of call, the paths or command involved, and the session, return allow or block with a reason.
- **`guard_paths.py`** (`claude/hooks/`) — the Claude Code `PreToolUse` adapter: read the hook input, translate it into a guard call, and report a block the way Claude Code expects. The installer copies it into the Commander skill's `scripts/` directory beside `guard_core.py`.

The installer registers the hook in every Forge agent profile's frontmatter for a plain-files install, and as one plugin-wide hook for the plugin, where it fires on every tool call of every session that has the plugin enabled. Either way, its first duty is to do nothing at all unless the caller is a Forge agent.

The guard is best effort for shell commands. Write tools name their target path, so path rules on them are exact. A shell command can write anywhere in ways no parser fully understands, so the guard checks only the git and GitHub commands that publish, rewrite history, or act externally; anything a shell command does to files is caught afterward by the router's HEAD checks and the gate.

## Scope

A tool call is guarded only when the hook input's `agent_type` starts with `forge-`. Without `agent_type` (the user's main session, which is also the Hub) or with any other value, the adapter exits `0` with no output before doing any other work, so non-Forge sessions see no effect and no measurable delay.

Hook input that does not parse as a JSON object cannot be attributed to any agent, so the adapter lets it through like any other unguarded call: Claude Code always sends valid input, and blocking would break the user's own sessions on a harness bug. Once a call is known to come from a Forge agent, any internal error — malformed tool input, an exception — blocks it. A guard that fails open there would silently drop every rule at the moment something unusual is happening.

## Active Workstream

Path rules depend on the workstream: its repository root, its test patterns, and its state directory. The guard finds the workstream through the lock: it scans `state/<org>/<repo>/*/lock.json` for the repository the call's `cwd` belongs to and picks the one whose `holder_session` equals the hook input's `session_id`. The Hub passes its own session ID when it takes the lock, and subagents run within the Hub's session.

A guarded call with no matching workstream is blocked with `NO_ACTIVE_WORKSTREAM`: a Forge agent running outside a workstream the Hub owns has no rules the guard can apply.

## Guard Record

Claude Code can skip the hook without any error: for a project-scope install, in `-p` sessions and in folders whose workspace trust was not accepted, since Claude Code runs a project agent's frontmatter hooks only once the folder is trusted and `-p` does not count; and for every install, under a managed policy that allows only managed hooks. Frontmatter hooks of user agents (`~/.claude/agents/`) and plugin hooks run under `-p`. The guard therefore leaves evidence that it ran, and the router refuses a spoke's result when the evidence is missing (`GUARD_INACTIVE`, router design).

When the workstream's `lifecycle.dispatched` is set, every guarded call adds its `agent_type` to `envelopes/<dispatch_id>.guard.json` beside `state.json`, as `{"agents": ["forge-dev", ...]}` with each agent listed once. The record is written whether or not a later check blocks the call: it shows that the guard ran, not that the call was allowed. The file is replaced through a temporary file and a rename, so a reader never sees it half written. Council personas run in parallel, so the guard holds an exclusive `flock` on `envelopes/<dispatch_id>.guard.lock` from reading the record to renaming the new one; without it, two calls could each add their agent to the same old list and one name would be lost. A call whose agent is already listed leaves the file untouched. A record that is not a JSON object with an `agents` list of strings is replaced by one listing only the current agent: the record is the guard's own evidence, and blocking on it would stop every later call of the dispatch until someone deletes the file.

Every spoke makes at least one tool call per dispatch, since it writes its result envelope with a file-write tool, so a dispatch whose agent is missing from the record had no guard. The record names agents, not subagent instances: a spoke left running from a replaced dispatch with the same agent name could add itself to the new record, which is accepted because ending the Hub's session ends its subagents.

## Checks

Checks run in this order — active-workstream lookup, guard record, kill switch, the `NO_ACTIVE_WORKSTREAM` block, circuit breaker, then the write-target or command check — and the first block wins. The record comes before the kill switch so that a call the kill switch blocks still shows the guard ran; otherwise a result routed after the kill switch is lifted would be refused as `GUARD_INACTIVE` instead of showing the real cause.

### 1. Kill switch

A file named `.STOP_FORGE` at the root of the main worktree, or at the root of the worktree containing the call's `cwd`, blocks every guarded call with `KILL_SWITCH`. Removing the file lifts it. The main worktree's file stops every workstream of the repository at once; a file in one workstream's worktree stops only that workstream.

### 2. Duplicate-call circuit breaker

The guard records each guarded call's fingerprint — tool name and a hash of the canonical JSON of its input — per (`session_id`, `agent_id`). When a call's fingerprint equals each of the previous three calls by the same agent, it is blocked with `DUPLICATE_CALL`: the fourth identical call in a row is a loop, not progress. Any different call resets the run.

Fingerprints live in `$FORGE_HOME/guard/<session_id>/<agent_id>.json`. Directories under `$FORGE_HOME/guard/` older than 24 hours are removed on the next guarded call.

### 3. Write targets

Write tools — Claude Code's `Write`, `Edit`, and `NotebookEdit` — are checked against the target path, resolved to an absolute real path (following symlinks) from the call's `cwd`. Every target falls into one of these places:

| Target | Allowed for |
|---|---|
| Inside the workstream's worktree, test path | `forge-qa` |
| Inside the workstream's worktree, plan path | `forge-planner` |
| Inside the workstream's worktree, production path | `forge-dev` |
| The workstream's `memory/<partition>/` | The agent owning that partition: `forge-planner` → `planner`, `forge-qa` → `qa`, `forge-dev` → `dev`, `forge-verifier` → `verifier`, `forge-reviewer` → `reviewer`, `forge-scout` → `scout` |
| The workstream's `scout/` | `forge-scout` |
| The workstream's `council/` | `forge-council` |
| The workstream's `envelopes/<dispatch_id>.json`, where `<dispatch_id>` is `lifecycle.dispatched.dispatch_id` | The agent named in `lifecycle.dispatched.agent` |
| The session's scratchpad directory (hook input `scratchpad_dir`) | Every Forge agent except council personas |
| Anywhere else | Nobody |

Path classes (test, plan, production) are the gate's classification, using the workstream's test patterns, so the guard and gate never disagree about a path. Council persona agents (`forge-council-*`) may write nowhere. A blocked write names the target and the class, with `BOUNDARY_VIOLATION`; for `forge-dev` on a test path the reason tells the agent to stop and return `TEST_CHANGE_REQUESTED`.

### 4. Commands

Shell commands are split on `;`, `&&`, `||`, `|`, and newlines, and each part is tokenized with shell rules. Before the command word, the guard skips `VAR=value` assignments and an `env` command with its options and assignments. A part whose command word is `sh`, `bash`, or `zsh` with an option cluster containing `c` (`-c`, `-lc`, `-ec`) has its command-string argument checked as a command in its own right, by the same rules.

Command words — including `env`, `sh`, `bash`, and `zsh` — are compared by basename, so `/usr/bin/git` and `./bin/gh` match `git` and `gh`.

A part is checked when its command word is `git` or `gh`. For `git`, the subcommand is the first word after git's global options: every word starting with `-`, plus the separate value of `-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`, `--exec-path`, and `--config-env` when written without `=`. So `git -C ../other push` is checked as `git push`.

| Command | Allowed for |
|---|---|
| `git push` | `forge-publisher`, and never with an option starting with `--force` (including `--force-with-lease`), `-f` alone or in a group of short options such as `-fu`, `--mirror`, `--delete`, or a `+` refspec (`+` before the source or the destination) |
| `git commit` | `forge-planner`, `forge-qa`, `forge-dev`, and never with `--amend` |
| `git merge`, `git checkout`, `git switch`, `git worktree`, `git rebase`, `git reset`, `git clean`, `git stash`, `git restore`, `git branch -D`/`-d`, `git tag -d`, `git update-ref`, `git filter-branch` | Nobody |
| `gh pr create`, `gh pr edit`, `gh pr review`, `gh pr comment`, `gh issue comment`, `gh api` with a non-`GET` method | `forge-reporter` |
| `gh run rerun` | `forge-scout` |
| `gh pr merge`, `gh repo` subcommands other than `view` and `clone`, `gh release` | Nobody |

Every other command is allowed. A blocked command is reported with `COMMAND_NOT_ALLOWED`, naming the part that matched.

### 5. Everything else

Read tools, search tools, and other calls pass after checks 1 and 2.

## Adapter Contract

`guard_paths.py` reads the hook input JSON from stdin and maps it:

| Hook input | Guard input |
|---|---|
| `agent_type` | acting agent |
| `agent_id` | circuit-breaker key |
| `session_id` | workstream lookup and circuit-breaker key |
| `cwd` | path resolution and repository lookup |
| `scratchpad_dir` | scratchpad target |
| `tool_name` `Write`, `Edit` → `tool_input.file_path`; `NotebookEdit` → `tool_input.notebook_path` | write target |
| `tool_name` `Bash` → `tool_input.command` | command |

On allow it exits `0` with no output. On block it writes the reason to stderr and exits `2`, which blocks the call and shows the reason to the agent.

The hook runs whichever `python3` is first on the session's `PATH`, which need not be the one that ran the installer. For a call by a Forge agent, when that Python is older than 3.11, the adapter blocks the call with `PYTHON_TOO_OLD` before importing `guard_core.py`. The scope check before it uses nothing newer than the oldest `python3` a machine is likely to have.

The adapter imports `guard_core.py` from its own directory, where the installer places both.

The installer registers the adapter under `PreToolUse` with no matcher, so every tool is covered by the kill switch and circuit breaker.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Who is guarded | Only `agent_type` starting with `forge-` | Every session | The plugin's hook fires in every session; guarding the user's own sessions would break their normal work. Frontmatter hooks fire only in Forge agents, and the check costs nothing there. |
| Evidence the guard ran | Each guarded call adds its agent to `envelopes/<dispatch_id>.guard.json` | A field in `state.json`; a bare marker file; a log of every call | `state.json` has one writer, the router. A list of agents lets the router check that the dispatched agent itself was guarded, not just some nested agent. A per-call log grows without bound for no extra check. |
| Concurrent record updates | `flock` on a lock file beside the record | Accept a lost persona name; one record file per agent | Parallel personas would otherwise race; a lock is a few lines of standard library, while per-agent files make the router list a directory. |
| Record before kill switch | Find the workstream and write the record, then check the kill switch | Kill switch first, as the cheapest check | A kill-switch block with no record surfaces later as `GUARD_INACTIVE`, hiding the real cause; the workstream scan costs little. |
| Python too old | Block Forge agents' calls with `PYTHON_TOO_OLD` | Let the import fail and block as an internal error; allow the call | A named reason tells the user what to fix; allowing would run the agent unguarded. |
| Finding `guard_core.py` | The adapter's own directory | A path into the Forge checkout | An install never depends on the checkout staying in place. |
| Failure mode | Block on internal error, for Forge agents only; unparseable input passes | Allow on error; block unparseable input | A guard that fails open removes every rule exactly when something is wrong. Input that cannot be parsed cannot be attributed to a Forge agent, and blocking it would stop the user's own sessions over a harness bug. |
| Finding the workstream | Match the hook's `session_id` to the lock's `holder_session` | `FORGE_WORKSTREAM` environment variable; current branch | The lock already records the owning session; environment variables cannot be set per subagent, and the branch does not identify an explicitly named workstream. |
| Circuit-breaker rule | Four identical calls in a row by one agent | Four identical calls in total | Agents legitimately repeat calls like `git status` across a task; consecutive repeats are the loop signal. |
| Branch movement | No Forge agent may check out, switch, merge, or add worktrees | Allow the publisher to merge | Moving HEAD to another branch breaks drift tracking for the workstream; a local merge into `main` is left to the user. |
| Wrapped and prefixed commands | Unwrap `env`, assignments, `sh`/`bash`/`zsh -c`, and git global options before matching, and compare command words by basename | Match only the first word as written; also unwrap `command`, `exec`, `nohup`, `time`, `xargs` | `git -C . push`, `env X=1 git push`, `bash -c "git push"`, and `/usr/bin/git push` are the obvious ways around a first-word match; handling them costs a few lines. The rarer wrappers, `$(which git)`, `eval`, and scripts stay outside what a command parser can cover. |
| Shell command coverage | Only git and gh commands that publish, rewrite history, or act externally | Parse redirects and file-writing commands | No parser covers every way a shell can write a file; the router's HEAD checks and the gate catch file changes afterward, while outward and destructive actions cannot be undone afterward. |
| Block mechanism | Exit `2` with the reason on stderr | JSON `permissionDecision: deny` | Both block; exit `2` keeps the adapter to a few lines. |
| Spoke memory writes | Each spoke writes only its own partition | Spokes write any partition | Enforces the memory partition rules at the point of writing. |

## Open Questions & Future Decisions

### Resolved
1. ✅ Hook input carries `agent_type` and `agent_id` inside subagents, and settings-level hooks fire inside subagents (Claude Code hooks documentation).

### Deferred
1. The workstream lookup assumes a subagent's hook input carries the Hub's `session_id`, the same value the Hub reads from its environment and stores as `holder_session`. The first test of the adapter confirms this against a real session; if it does not hold, the lookup key changes before any other guard work lands.
2. Plugin agents report `agent_type` as `forge:forge-<role>`. The scope check and every per-agent rule accept that form once the plugin probe confirms it (installer design, plugin mode).

## References

- `docs/high-level-design.md` — guardrails, *Scripts over prompts*, *Stop over guess*.
- Gates design (`docs/intent/gates/`) — path classes and test patterns.
- Workstream state design (`docs/intent/workstream-state/`) — lock fields and the state directory layout.
- Forge memory design (`docs/intent/forge-memory/`) — partition ownership.
- Claude Code hooks documentation — `PreToolUse` input, `agent_type`, exit code `2`.
