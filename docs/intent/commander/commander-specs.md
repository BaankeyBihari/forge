# Commander — EARS Specs

Design: `commander-design.md`. Implementation: `reference/scripts/forge_hub.py` and `claude/skills/forge/SKILL.md`.

Specs in the sections from *Identity* onward describe the Commander skill's procedure. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do. The `forge_hub.py` specs before them are tested with pytest.

## forge_hub.py invocation

- [x] **CMD-CLI-001**: `forge_hub.py` shall provide the subcommands `classify`, `brief`, `envelope`, and `answer`, print exactly one JSON object to stdout, and exit `0` for every status.
- [x] **CMD-CLI-002**: If `forge_hub.py` is called with an unknown subcommand or missing or invalid arguments, then it shall exit `2` with a message on stderr.
- [x] **CMD-CLI-003**: If `forge_hub.py classify` or `brief` runs in a directory that is not inside a git repository, then it shall exit `1` with a message on stderr.

## Arguments

- [x] **CMD-ARG-001**: When `classify` is given no words, `forge_hub.py` shall return kind `current`.
- [x] **CMD-ARG-002**: When `classify` is given the single word `list`, `forge_hub.py` shall return kind `list`.
- [x] **CMD-ARG-003**: When `classify` is given `pr` followed by one word, or a single GitHub pull request URL (`https://github.com/<owner>/<repo>/pull/<n>`), `forge_hub.py` shall return kind `pr` with `ref` set to that word or URL.
- [x] **CMD-ARG-004**: When `classify` is given `adopt` followed by one word, `forge_hub.py` shall return kind `adopt` with `branch` set to that word.
- [x] **CMD-ARG-005**: If `classify` is given `pr` or `adopt` as the first word followed by other than exactly one word, then `forge_hub.py` shall return kind `invalid` with a `detail` giving the expected form.
- [x] **CMD-ARG-006**: When `classify` is given a single GitHub issue URL (`https://github.com/<owner>/<repo>/issues/<n>`), `#<n>`, or `<n>`, where `<n>` is a positive integer, `forge_hub.py` shall return kind `issue` with `ref` set to that word.
- [x] **CMD-ARG-007**: When `classify` is given a single word that is a valid workstream slug and names a workstream directory of the current repository's state directory, `forge_hub.py` shall return kind `resume` with `workstream` set to that word.
- [x] **CMD-ARG-008**: When `classify` is given a single word that is a valid workstream slug but names no workstream of the current repository, `forge_hub.py` shall return kind `unknown` with `workstream` set to that word.
- [x] **CMD-ARG-009**: When no other rule matches, `forge_hub.py classify` shall return kind `describe` with `text` set to the words joined by single spaces.
- [x] **CMD-ARG-010**: `forge_hub.py classify` shall apply CMD-ARG-001 through CMD-ARG-009 in order and return the first kind that matches.

## Resume brief

- [x] **CMD-BRIEF-001**: When `brief` runs, `forge_hub.py` shall run the workstream-state wake with the given `--workstream`, `--holder-pid`, and `--holder-session`, and when the wake status is not `OK`, return the wake report unchanged.
- [x] **CMD-BRIEF-002**: When the wake status is `OK`, `forge_hub.py brief` shall return `status` `OK`, `lines` holding exactly five strings, and `pending_envelope`.
- [x] **CMD-BRIEF-003**: `forge_hub.py brief` shall make line 1 `Workstream: <org>/<repo>/<workstream> (<repo_mode>, <collaborative or sequential>, plan <plan_mode>)` from `identity` and `config`.
- [x] **CMD-BRIEF-004**: `forge_hub.py brief` shall make line 2 `Phase: <current_phase> (<last_agent> → <last_status>)`, or `Phase: <current_phase> (nothing run yet)` while `lifecycle.last_agent` is `null`.
- [x] **CMD-BRIEF-005**: `forge_hub.py brief` shall make line 3 `Progress: <completed count> of <assigned count> scope patterns complete; remaining: <remaining patterns joined by ", ", or none>`, or `Progress: no plan yet` while `scope_manifest.assigned` is empty, followed by `; open blockers: <n>` when `blockers` is not empty.
- [x] **CMD-BRIEF-006**: `forge_hub.py brief` shall make line 4 `Hub context: ` followed by the first decision of the newest non-historical `hub` memory entry that has a decision, or `none recorded` when there is none.
- [x] **CMD-BRIEF-007**: While `lifecycle.dispatched` is set and `envelopes/<dispatch_id>.json` exists, `forge_hub.py brief` shall make line 5 `Next step: Apply the result <agent> left (<status>)`, using `(unreadable)` when the file is not a JSON object with a `status`, and set `pending_envelope` to `true`.
- [x] **CMD-BRIEF-008**: While CMD-BRIEF-007 does not apply and the phase is `INIT`, `forge_hub.py brief` shall make line 5 `Next step: Start routing (nothing has run yet)`.
- [x] **CMD-BRIEF-009**: While CMD-BRIEF-007 and CMD-BRIEF-008 do not apply, `forge_hub.py brief` shall make line 5 from the router's `--dry-run` decision: `Next step: Waiting for you: <question>` for a pause, `Next step: Stopped: <status>: <detail>` for a stop, and `Next step: <summary>` otherwise.
- [x] **CMD-BRIEF-010**: `forge_hub.py brief` shall set `pending_envelope` to `false` whenever CMD-BRIEF-007 does not apply.
- [x] **CMD-BRIEF-011**: `forge_hub.py brief` shall not change `state.json` other than through the wake.

## Envelope pickup

- [x] **CMD-ENV-001**: When `envelope` runs while `lifecycle.dispatched` is set and `envelopes/<dispatch_id>.json` holds a JSON object, `forge_hub.py` shall write `{"type": "result", "envelope": <that object>}` to `envelopes/<dispatch_id>.event.json` and return `status` `OK` with its absolute path in `event_path`.
- [x] **CMD-ENV-002**: If `envelope` runs while `lifecycle.dispatched` is `null`, then `forge_hub.py` shall return `status` `NO_DISPATCH`.
- [x] **CMD-ENV-003**: If `envelope` runs while `envelopes/<dispatch_id>.json` does not exist, then `forge_hub.py` shall return `status` `ENVELOPE_MISSING` with the expected path.
- [x] **CMD-ENV-004**: If `envelopes/<dispatch_id>.json` does not parse as a JSON object, then `forge_hub.py envelope` shall return `status` `ENVELOPE_UNREADABLE` with the parse error in `detail` and write no event.
- [x] **CMD-ENV-005**: When `envelope` is given `--discard` and `envelopes/<dispatch_id>.json` exists, `forge_hub.py` shall rename it to `<dispatch_id>.rejected-<YYYYMMDDTHHMMSSZ>.json` in the same directory and return `status` `DISCARDED` with the new path.
- [x] **CMD-ENV-006**: If the `--state` file of `envelope` or `answer` is missing or does not parse as a JSON object, then `forge_hub.py` shall return `status` `STATE_INVALID` with a `detail` saying which.
- [x] **CMD-ENV-007**: `forge_hub.py envelope` shall read the envelope from, and write the event to, the `envelopes/` directory beside the `--state` file, creating that directory when it is missing.

## Answer events

- [x] **CMD-ANS-001**: When `answer` runs while `lifecycle.awaiting` is set, `forge_hub.py` shall write `{"type": "user", "awaiting": <lifecycle.awaiting.kind>, "decision": <--decision>}`, plus `option` when `--option` is given and `notes` (the file's text) when `--notes-file` is given, to `envelopes/answer-<YYYYMMDDTHHMMSSZ>.event.json`, and return `status` `OK` with its absolute path in `event_path`.
- [x] **CMD-ANS-002**: If `answer` runs while `lifecycle.awaiting` is `null`, then `forge_hub.py` shall return `status` `NOT_PAUSED` and write nothing.
- [x] **CMD-ANS-003**: If `answer`'s `--notes-file` cannot be read, then `forge_hub.py` shall exit `2` with a message on stderr and write nothing.

## Identity

- [D] **CMD-ID-001**: The Commander shall pass `--holder-pid $CLAUDE_PID --holder-session ${CLAUDE_SESSION_ID}` to every `resolve_state.py`, `next_step.py`, and `forge_hub.py brief` call.
- [D] **CMD-ID-002**: The Commander shall run Forge scripts from `${CLAUDE_SKILL_DIR}/scripts/`.
- [D] **CMD-ID-003**: Before starting or resuming a workstream other than the one it holds in this session, the Commander shall run `resolve_state.py release` for the held workstream.

## Starting a workstream

- [D] **CMD-START-001**: When `/forge` is classified `issue`, `pr`, or `adopt`, the Commander shall run `resolve_state.py new` with `--issue`, `--pr`, or `--branch` and the classified value, and `--worktree-root .claude/worktrees`.
- [D] **CMD-START-002**: When `/forge` is classified `describe`, the Commander shall derive a valid slug of two to five words from the text, other than `list`, `pr`, and `adopt`, and run `new --name` with it, or ask the user for a name when the text gives nothing to name.
- [D] **CMD-START-003**: The Commander shall pass `--base` only when the user named a base branch, and `--set` only for settings the user asked for.
- [D] **CMD-START-004**: When `new` returns `CREATED`, the Commander shall move the session into `worktree_path` with `EnterWorktree` and route with a `start` event.
- [D] **CMD-START-005**: When `new` returns `BASE_CONFIRM_REQUIRED`, the Commander shall ask the user to confirm the base and, on confirmation, run `new` again with `--base-confirmed`.
- [D] **CMD-START-006**: When `new` returns `DUPLICATE` or `STATE_EXISTS`, the Commander shall offer to resume the named workstream.
- [D] **CMD-START-007**: When `new` returns `BRANCH_CHECKED_OUT`, `BRANCH_EXISTS`, or `GH_FAILED`, the Commander shall report the status and its detail and start nothing.
- [D] **CMD-START-008**: When `/forge` is classified `unknown`, the Commander shall ask whether to start a new workstream with that name or show the list.
- [D] **CMD-START-009**: When `/forge` is classified `invalid`, the Commander shall show the detail and do nothing else.

## Resuming

- [D] **CMD-RESUME-001**: When `/forge` is classified `current` or `resume`, the Commander shall run `forge_hub.py brief`, with `--workstream` for `resume`.
- [D] **CMD-RESUME-002**: When `brief` returns `OK`, the Commander shall show the five lines and ask whether to continue before routing.
- [D] **CMD-RESUME-003**: When the user continues, the Commander shall route once with the event from `forge_hub.py envelope` when `pending_envelope` is `true`, with a `start` event when the phase is `INIT`, and with no event otherwise, then enter the routing loop.
- [D] **CMD-RESUME-004**: When the user declines to continue, the Commander shall write a `hub` memory entry recording where the workstream stands and run `resolve_state.py release`.
- [D] **CMD-RESUME-005**: When `brief` returns `ELSEWHERE`, the Commander shall move the session into the reported worktree with `EnterWorktree` and run `brief` again.
- [D] **CMD-RESUME-006**: When `brief` returns `LOCKED`, the Commander shall show the holder and, only on the user's confirmation, run `resolve_state.py wake --steal` and then `brief` again.
- [D] **CMD-RESUME-007**: When `brief` returns `LIST` or `NO_STATE`, or `/forge` is classified `list`, the Commander shall show the repository's workstreams from `resolve_state.py list` with name, phase, last status, last update, lock holder and liveness, and branch, and ask which to resume; for `NO_STATE` it shall also mention `/forge adopt <branch>`.
- [D] **CMD-RESUME-008**: When `brief` returns `IDENTITY_MISMATCH`, `STATE_INVALID`, or `DRIFT_UNKNOWN`, the Commander shall report the status and its detail and stop.

## Routing loop

- [D] **CMD-LOOP-001**: While routing, the Commander shall call `next_step.py` with at most one event and act on each decision until a decision ends the loop.
- [D] **CMD-LOOP-002**: Before every `next_step.py` call, if `.STOP_FORGE` exists at the root of the main worktree or of the workstream's worktree, then the Commander shall end the loop and tell the user which file to remove.
- [D] **CMD-LOOP-003**: When the router returns `dispatch`, the Commander shall start the subagent named by `invoke.agent` with a prompt giving only the `dispatch_id`, the `state_path`, and the envelope path `<state dir>/envelopes/<dispatch_id>.json`.
- [D] **CMD-LOOP-004**: When a dispatched subagent returns, the Commander shall run `forge_hub.py envelope` and route with `--event <event_path>` on `OK`, or with no event on `NO_DISPATCH`.
- [D] **CMD-LOOP-005**: When `envelope` returns `ENVELOPE_MISSING`, the Commander shall tell the user and, on retry, route with no event.
- [D] **CMD-LOOP-006**: When `envelope` returns `ENVELOPE_UNREADABLE` or the router stops with `ENVELOPE_INVALID`, the Commander shall show the reason and, on retry, run `forge_hub.py envelope --discard` and route with no event.
- [D] **CMD-LOOP-007**: When the router returns `stop`, the Commander shall report the status and detail and end the loop.
- [D] **CMD-LOOP-008**: When the router returns `close` or `done`, the Commander shall close the workstream per CMD-CLOSE-001.
- [D] **CMD-LOOP-009**: The Commander shall not write `state.json`, push, or commit other than the `dirty_tree` checkpoint.

## Pauses

- [D] **CMD-PAUSE-001**: When the router pauses with `plan_approval`, `council_signoff`, `findings_signoff`, `human_code_approval`, `publish_approval`, `report_approval`, `ci_none`, `dirty_tree`, or `attempt_limit`, the Commander shall ask the user with `AskUserQuestion` and route the answer.
- [D] **CMD-PAUSE-002**: When the router pauses with `edit_window` or `manual_merge`, the Commander shall show the question and what the user should do, and end its turn without asking.
- [D] **CMD-PAUSE-003**: When routing an answer returns a pause of the same kind, the Commander shall show the router's question and end its turn.
- [D] **CMD-PAUSE-004**: The Commander shall route every answer as the event written by `forge_hub.py answer`, writing any notes to a file with the file-write tool and passing it as `--notes-file`.
- [D] **CMD-PAUSE-005**: When presenting `publish_approval`, the Commander shall offer approve and push, approve and merge, and hold, sending `approve` with `option` `push` or `merge` for the first two.
- [D] **CMD-PAUSE-006**: When presenting `council_signoff`, the Commander shall show the ruling's decision matrix and send `approve` with the chosen option's label, asking for notes on `reject`.
- [D] **CMD-PAUSE-007**: When the user answers `revise` to `plan_approval` or `report_approval`, or `reject` to `human_code_approval` or `council_signoff`, the Commander shall ask for notes before routing the answer.
- [D] **CMD-PAUSE-008**: When the user answers `dirty_tree` with `checkpoint`, the Commander shall commit every change as `wip(user): partial implementation` with no Forge trailers; with `stash`, it shall run `git stash push --include-untracked`; and when the git command fails, it shall report git's message and ask again without routing.

## Close

- [D] **CMD-CLOSE-001**: When closing a workstream, the Commander shall ask whether to remove the worktree, leave the worktree with `ExitWorktree` keeping it, and run `resolve_state.py close --workstream <slug>`, adding `--remove-worktree` only when the user said yes.
- [D] **CMD-CLOSE-002**: When `close` returns `WORKTREE_DIRTY`, the Commander shall report git's message and tell the user that `/forge <workstream>` retries the close.

## Hub memory

- [D] **CMD-MEM-001**: When resuming a workstream, the Commander shall load `hub` and `shared` memory with `forge_memory.py load --agent hub`.
- [D] **CMD-MEM-002**: After routing a user answer that carried notes, rejected a ruling, or held a publish, the Commander shall write a `hub` entry anchored to `HEAD` after the answer was applied, with `stage` set to the phase the router returned.
- [D] **CMD-MEM-003**: When the user tells the Commander to stop mid-workstream, the Commander shall write a `hub` entry recording where the workstream stands.
- [D] **CMD-MEM-004**: After the user approves a council ruling, or states an invariant every agent must respect, the Commander shall write a `shared` entry recording it.
