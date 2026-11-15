# Guard — EARS Specs

Design: `guard-design.md`. Implementation: `reference/scripts/guard_core.py` (decisions) and `claude/hooks/guard_paths.py` (Claude Code adapter).

## Scope

- [ ] **GUARD-SCOPE-001**: When the hook input does not parse as a JSON object, has no `agent_type`, or an `agent_type` not starting with `forge-`, `guard_paths.py` shall exit `0` with no output without running any check.
- [ ] **GUARD-SCOPE-002**: If an internal error occurs while checking a call whose `agent_type` starts with `forge-`, then `guard_paths.py` shall block the call with the error as the reason.

## Active workstream

- [ ] **GUARD-WS-001**: For a guarded call, `guard_core.py` shall take as the active workstream the workstream of the repository containing the call's `cwd` whose `lock.json` `holder_session` equals the hook input's `session_id`.
- [ ] **GUARD-WS-002**: If no workstream matches per GUARD-WS-001, then `guard_core.py` shall block the call with `NO_ACTIVE_WORKSTREAM`.

## Kill switch

- [ ] **GUARD-KILL-001**: While a file named `.STOP_FORGE` exists at the root of the main worktree of the repository containing the call's `cwd`, or at the root of the worktree containing the call's `cwd`, `guard_core.py` shall block every guarded call with `KILL_SWITCH`.

## Duplicate calls

- [ ] **GUARD-DUP-001**: For each guarded call, `guard_core.py` shall record a fingerprint of the tool name and a hash of the canonical JSON of the tool input under `$FORGE_HOME/guard/<session_id>/<agent_id>.json`.
- [ ] **GUARD-DUP-002**: If a guarded call's fingerprint equals the fingerprints of the same agent's previous three calls, then `guard_core.py` shall block it with `DUPLICATE_CALL`.
- [ ] **GUARD-DUP-003**: When handling a guarded call, `guard_core.py` shall delete directories under `$FORGE_HOME/guard/` last modified more than 24 hours earlier.

## Write targets

- [ ] **GUARD-WRITE-001**: For `Write`, `Edit`, and `NotebookEdit` calls, `guard_core.py` shall resolve the target path from the call's `cwd` to an absolute path with symlinks followed before checking it.
- [ ] **GUARD-WRITE-002**: If a write target inside the active workstream's worktree is classified (by the gate's path classification and the workstream's test patterns) as a test path and the agent is not `forge-qa`, as a plan path and the agent is not `forge-planner`, or as a production path and the agent is not `forge-dev`, then `guard_core.py` shall block it with `BOUNDARY_VIOLATION`.
- [ ] **GUARD-WRITE-003**: When blocking a `forge-dev` write to a test path, `guard_core.py` shall state in the reason that the agent must stop and return `TEST_CHANGE_REQUESTED`.
- [ ] **GUARD-WRITE-004**: `guard_core.py` shall allow a write under the active workstream's `memory/<partition>/` only to the agent owning that partition: `planner` to `forge-planner`, `qa` to `forge-qa`, `dev` to `forge-dev`, `verifier` to `forge-verifier`, `reviewer` to `forge-reviewer`, and `scout` to `forge-scout`.
- [ ] **GUARD-WRITE-005**: `guard_core.py` shall allow a write under the active workstream's `scout/` directory only to `forge-scout`, and under its `council/` directory only to `forge-council`.
- [ ] **GUARD-WRITE-009**: `guard_core.py` shall allow a write to the active workstream's `envelopes/<dispatch_id>.json` only when `<dispatch_id>` equals `lifecycle.dispatched.dispatch_id` and the agent equals `lifecycle.dispatched.agent`.
- [ ] **GUARD-WRITE-006**: `guard_core.py` shall allow a write under the hook input's `scratchpad_dir` to every Forge agent whose name does not start with `forge-council-`.
- [ ] **GUARD-WRITE-007**: If a write target is not covered by GUARD-WRITE-002 through GUARD-WRITE-006 or GUARD-WRITE-009, then `guard_core.py` shall block it with `BOUNDARY_VIOLATION`.
- [ ] **GUARD-WRITE-008**: `guard_core.py` shall block every write by an agent whose name starts with `forge-council-`.

## Commands

- [ ] **GUARD-CMD-001**: For `Bash` calls, `guard_core.py` shall split the command on `;`, `&&`, `||`, `|`, and newlines, tokenize each part with shell rules, skip leading `VAR=value` words and a leading `env` command with its options and assignments, and check each part whose command word is `git` or `gh`, comparing every command word (including `env`, `sh`, `bash`, and `zsh`) by its basename.
- [ ] **GUARD-CMD-010**: When a command part's command word is `sh`, `bash`, or `zsh` and it has an option cluster containing `c`, `guard_core.py` shall check that option's command-string argument as a command under GUARD-CMD-001 through GUARD-CMD-009.
- [ ] **GUARD-CMD-011**: When identifying a `git` subcommand, `guard_core.py` shall skip git's global options — every word starting with `-`, plus the following word after `-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`, `--exec-path`, or `--config-env` written without `=` — and take the next word as the subcommand.
- [ ] **GUARD-CMD-002**: If a command part is `git push` and the agent is not `forge-publisher`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [ ] **GUARD-CMD-003**: If a `git push` part contains an option starting with `--force`, a short-option group containing `f` (such as `-f` or `-fu`), `--mirror`, `--delete`, or a refspec with `+` before its source or its destination, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [ ] **GUARD-CMD-004**: If a command part is `git commit` and the agent is not `forge-planner`, `forge-qa`, or `forge-dev`, or the part contains `--amend`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [ ] **GUARD-CMD-005**: If a command part is `git merge`, `git checkout`, `git switch`, `git worktree`, `git rebase`, `git reset`, `git clean`, `git stash`, `git restore`, `git branch -d`, `git branch -D`, `git tag -d`, `git update-ref`, or `git filter-branch`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [ ] **GUARD-CMD-006**: If a command part is `gh pr create`, `gh pr edit`, `gh pr review`, `gh pr comment`, `gh issue comment`, or `gh api` with a method other than `GET`, and the agent is not `forge-reporter`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [ ] **GUARD-CMD-007**: If a command part is `gh run rerun` and the agent is not `forge-scout`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED`.
- [ ] **GUARD-CMD-008**: If a command part is `gh pr merge`, `gh release`, or a `gh repo` subcommand other than `view` and `clone`, then `guard_core.py` shall block the call with `COMMAND_NOT_ALLOWED` for every agent.
- [ ] **GUARD-CMD-009**: When blocking a command, `guard_core.py` shall name the command part that matched in the reason.

## Order

- [ ] **GUARD-ORDER-001**: `guard_core.py` shall run the kill switch, then the active-workstream lookup, then the duplicate-call check, then the write-target or command check, and report the first block.

## Claude Code adapter

- [ ] **GUARD-ADAPT-001**: `guard_paths.py` shall read the `PreToolUse` hook input JSON from stdin and pass `agent_type`, `agent_id`, `session_id`, `cwd`, `scratchpad_dir`, `tool_name`, and the write target or command to `guard_core.py`.
- [ ] **GUARD-ADAPT-002**: `guard_paths.py` shall take the write target from `tool_input.file_path` for `Write` and `Edit`, and from `tool_input.notebook_path` for `NotebookEdit`.
- [ ] **GUARD-ADAPT-003**: When `guard_core.py` allows a call, `guard_paths.py` shall exit `0` with no output.
- [ ] **GUARD-ADAPT-004**: When `guard_core.py` blocks a call, `guard_paths.py` shall write the reason to stderr and exit `2`.
