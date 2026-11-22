# Guard — EARS Specs

Design: `guard-design.md`. Implementation: `reference/scripts/guard_core.py` (decisions) and `claude/hooks/guard_paths.py` (Claude Code adapter).

## Scope

- [x] **GUARD-SCOPE-001**: When the hook input does not parse as a JSON object, has no `agent_type`, or an `agent_type` starting with neither `forge-` nor `forge:forge-`, `guard_paths.py` shall exit `0` with no output without running any check.
- [x] **GUARD-SCOPE-003**: When a guarded call's `agent_type` starts with `forge:`, `guard_paths.py` shall remove that prefix before passing `agent_type` to `guard_core.py`.
- [x] **GUARD-SCOPE-002**: If an internal error occurs while checking a call whose `agent_type` starts with `forge-`, then `guard_paths.py` shall block the call with the error as the reason.

## Active workstream

- [x] **GUARD-WS-001**: For a guarded call, `guard_core.py` shall take as the active workstream the workstream of the repository containing the call's `cwd` whose `lock.json` `holder_session` equals the hook input's `session_id`.
- [x] **GUARD-WS-002**: If no workstream matches per GUARD-WS-001, then `guard_core.py` shall block the call with `NO_ACTIVE_WORKSTREAM`.

## Guard record

- [x] **GUARD-REC-001**: While the active workstream's `lifecycle.dispatched` is set, when handling a guarded call, `guard_core.py` shall add the call's `agent_type` to the `agents` list of `envelopes/<dispatch_id>.guard.json` beside `state.json`, listing each agent once and creating the file and its directory when missing.
- [x] **GUARD-REC-002**: `guard_core.py` shall write the record per GUARD-REC-001 whether or not a later check blocks the call.
- [x] **GUARD-REC-003**: When writing the record, `guard_core.py` shall write a temporary file in the same directory and rename it over the record.
- [x] **GUARD-REC-004**: When the call's agent is already in the record's `agents` list, `guard_core.py` shall leave the record unchanged.
- [x] **GUARD-REC-005**: If the record is not a JSON object whose `agents` is a list of strings, then `guard_core.py` shall replace it with a record listing only the call's agent.
- [x] **GUARD-REC-006**: While reading and replacing the record, `guard_core.py` shall hold an exclusive `flock` on `envelopes/<dispatch_id>.guard.lock`.

## Kill switch

- [x] **GUARD-KILL-001**: While a file named `.STOP_FORGE` exists at the root of the main worktree of the repository containing the call's `cwd`, or at the root of the worktree containing the call's `cwd`, `guard_core.py` shall block every guarded call with `KILL_SWITCH`.

## Duplicate calls

- [x] **GUARD-DUP-001**: For each guarded call, `guard_core.py` shall record a fingerprint of the tool name and a hash of the canonical JSON of the tool input under `$FORGE_HOME/guard/<session_id>/<agent_id>.json`.
- [x] **GUARD-DUP-002**: If a guarded call's fingerprint equals the fingerprints of the same agent's previous three calls, then `guard_core.py` shall block it with `DUPLICATE_CALL`.
- [x] **GUARD-DUP-003**: When handling a guarded call, `guard_core.py` shall delete directories under `$FORGE_HOME/guard/` last modified more than 24 hours earlier.

## Write targets

- [x] **GUARD-WRITE-001**: For `Write`, `Edit`, and `NotebookEdit` calls, `guard_core.py` shall resolve the target path from the call's `cwd` to an absolute path with symlinks followed before checking it.
- [x] **GUARD-WRITE-002**: If a write target inside the active workstream's worktree is classified (by the gate's path classification and the workstream's test patterns) as a test path and the agent is not `forge-qa`, as a plan path and the agent is not `forge-planner`, or as a production path and the agent is not `forge-dev`, then `guard_core.py` shall block it with `BOUNDARY_VIOLATION`.
- [x] **GUARD-WRITE-003**: When blocking a `forge-dev` write to a test path, `guard_core.py` shall state in the reason that the agent must stop and return `TEST_CHANGE_REQUESTED`.
- [x] **GUARD-WRITE-004**: `guard_core.py` shall allow a write under the active workstream's `memory/<partition>/` only to the agent owning that partition: `planner` to `forge-planner`, `qa` to `forge-qa`, `dev` to `forge-dev`, `verifier` to `forge-verifier`, `reviewer` to `forge-reviewer`, and `scout` to `forge-scout`.
- [x] **GUARD-WRITE-005**: `guard_core.py` shall allow a write under the active workstream's `scout/` directory only to `forge-scout`, and under its `council/` directory only to `forge-council`.
- [x] **GUARD-WRITE-009**: `guard_core.py` shall allow a write to the active workstream's `envelopes/<dispatch_id>.json` only when `<dispatch_id>` equals `lifecycle.dispatched.dispatch_id` and the agent equals `lifecycle.dispatched.agent`.
- [x] **GUARD-WRITE-006**: `guard_core.py` shall allow a write under the hook input's `scratchpad_dir` to every Forge agent whose name does not start with `forge-council-`.
- [x] **GUARD-WRITE-007**: If a write target is not covered by GUARD-WRITE-002 through GUARD-WRITE-006 or GUARD-WRITE-009, then `guard_core.py` shall block it with `BOUNDARY_VIOLATION`.
- [x] **GUARD-WRITE-008**: `guard_core.py` shall block every write by an agent whose name starts with `forge-council-`.

## Commands

- [x] **GUARD-CMD-001**: For `Bash` calls, `guard_core.py` shall split the command on `;`, `&&`, `||`, `|`, and newlines, tokenize each part with shell rules, skip leading `VAR=value` words and a leading `env` command with its options and assignments, and check each part whose command word is `git` or `gh`, comparing every command word (including `env`, `sh`, `bash`, and `zsh`) by its basename.
- [x] **GUARD-CMD-010**: When a command part's command word is `sh`, `bash`, or `zsh` and it has an option cluster containing `c`, `guard_core.py` shall check that option's command-string argument as a command under GUARD-CMD-001 through GUARD-CMD-009.
- [x] **GUARD-CMD-011**: When identifying a `git` subcommand, `guard_core.py` shall skip git's global options — every word starting with `-`, plus the following word after `-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`, `--exec-path`, or `--config-env` written without `=` — and take the next word as the subcommand.
- [x] **GUARD-CMD-002**: If a command part is `git push` and the agent is not `forge-publisher`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [x] **GUARD-CMD-003**: If a `git push` part contains an option starting with `--force`, a short-option group containing `f` (such as `-f` or `-fu`), `--mirror`, `--delete`, or a refspec with `+` before its source or its destination, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [x] **GUARD-CMD-004**: If a command part is `git commit` and the agent is not `forge-planner`, `forge-qa`, or `forge-dev`, or the part contains `--amend`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [x] **GUARD-CMD-005**: If a command part is `git merge`, `git checkout`, `git switch`, `git worktree`, `git rebase`, `git reset`, `git clean`, `git stash`, `git restore`, `git branch -d`, `git branch -D`, `git tag -d`, `git update-ref`, or `git filter-branch`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [x] **GUARD-CMD-006**: If a command part is `gh pr create`, `gh pr edit`, `gh pr review`, `gh pr comment`, `gh issue comment`, or `gh api` with a method other than `GET`, and the agent is not `forge-reporter`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [x] **GUARD-CMD-007**: If a command part is `gh run rerun` and the agent is not `forge-scout`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [x] **GUARD-CMD-008**: If a command part is `gh pr merge`, `gh release`, or a `gh repo` subcommand other than `view` and `clone`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [x] **GUARD-CMD-009**: When blocking a command, `guard_core.py` shall name the command part that matched in the reason.

## Order

- [x] **GUARD-ORDER-001**: `guard_core.py` shall run the active-workstream lookup, then the guard record, then the kill switch, then the `NO_ACTIVE_WORKSTREAM` block, then the duplicate-call check, then the write-target or command check, and report the first block.

## Claude Code adapter

- [x] **GUARD-ADAPT-001**: `guard_paths.py` shall read the `PreToolUse` hook input JSON from stdin and pass `agent_type`, `agent_id`, `session_id`, `cwd`, `scratchpad_dir`, `tool_name`, and the write target or command to `guard_core.py`.
- [x] **GUARD-ADAPT-002**: `guard_paths.py` shall take the write target from `tool_input.file_path` for `Write` and `Edit`, and from `tool_input.notebook_path` for `NotebookEdit`.
- [x] **GUARD-ADAPT-003**: When `guard_core.py` allows a call, `guard_paths.py` shall exit `0` with no output.
- [x] **GUARD-ADAPT-004**: When `guard_core.py` blocks a call, `guard_paths.py` shall write the reason to stderr and exit `2`.
- [x] **GUARD-ADAPT-005**: If the Python running `guard_paths.py` is older than 3.11 and the call's `agent_type` starts with `forge-`, then `guard_paths.py` shall block the call with `PYTHON_TOO_OLD` in the reason before importing `guard_core.py`.
- [x] **GUARD-ADAPT-006**: `guard_paths.py` shall import `guard_core.py` from the directory containing `guard_paths.py`.
