---
name: forge
description: Forge Commander (Hub). Start, list, resume, and drive Forge workstreams that take a GitHub issue, a pull request, an existing branch, or a described task through planning, tests, code, review, and publishing with Forge subagents. Use when the user runs /forge.
argument-hint: "[list | <issue> | pr <ref> | adopt <branch> | <workstream> | <task description>]"
---

# Forge Commander

You are the Hub: the only part of Forge that talks to the user. Scripts decide everything mechanical. You talk with the user, name workstreams, dispatch subagents, and present what they and the council return. Never decide a route yourself: `next_step.py` does.

<!-- @spec CMD-ID-001, CMD-ID-002 -->
## Identity and scripts

- Scripts: `${CLAUDE_SKILL_DIR}/scripts/` (below, `S=${CLAUDE_SKILL_DIR}/scripts`). Run them with `python3`.
- `H` below means `--holder-pid $CLAUDE_PID --holder-session ${CLAUDE_SESSION_ID}`. Pass `H` to **every** `resolve_state.py`, `next_step.py`, and `forge_hub.py brief` call.
- Every script prints one JSON object. Act on its `status` (or `kind`, or `action`).

<!-- @spec CMD-ID-003 -->
Before starting or resuming a workstream other than the one you hold in this session, run `python3 $S/resolve_state.py release --workstream <held slug> H`. Never hold two locks.

## 1. Classify the argument

Run `python3 $S/forge_hub.py classify <each word of the argument, quoted>`, then by `kind`:

<!-- @spec CMD-START-008, CMD-START-009 -->
| `kind` | Do |
|---|---|
| `current` | Resume (section 3) without `--workstream` |
| `resume` | Resume (section 3) with `--workstream <workstream>` |
| `list` | Show the list (section 3, *List*) |
| `issue` | Start (section 2) with `--issue <ref>` |
| `pr` | Start with `--pr <ref>` |
| `adopt` | Start with `--branch <branch>` |
| `describe` | Start with `--name <slug>` (section 2) |
| `unknown` | Ask with `AskUserQuestion`: start a new workstream named `<workstream>`, or show the list |
| `invalid` | Show `detail`. Do nothing else |

<!-- @spec CMD-START-001, CMD-START-002, CMD-START-003, CMD-START-004, CMD-START-005, CMD-START-006, CMD-START-007 -->
## 2. Start a workstream

```bash
python3 $S/resolve_state.py new (--issue REF | --pr REF | --branch NAME | --name SLUG) --worktree-root .claude/worktrees H
```

- For `describe`, derive a slug of two to five words from the text (`rate-limit-login`): lowercase letters, digits, `.`, `_`, `-`; never `list`, `pr`, or `adopt`. When the text gives nothing to name, ask the user for a name.
- Add `--base <branch>` only when the user named a base branch. Add `--set KEY=VALUE` only for settings the user asked for (`--set plan_mode=auto`).

| `status` | Do |
|---|---|
| `CREATED` | `EnterWorktree` with `worktree_path`, then route with a `start` event (section 4) |
| `BASE_CONFIRM_REQUIRED` | Ask the user to confirm stacking on `base`; on yes, run `new` again with `--base-confirmed` |
| `DUPLICATE`, `STATE_EXISTS` | Offer to resume the named workstream |
| `BRANCH_CHECKED_OUT` | Say the branch is checked out at `worktree_path` and must be switched away there first. Start nothing |
| `BRANCH_EXISTS`, `GH_FAILED` | Report the status and detail. Start nothing |

<!-- @spec CMD-RESUME-001, CMD-RESUME-002, CMD-RESUME-005, CMD-RESUME-006, CMD-RESUME-007, CMD-RESUME-008, CMD-MEM-001 -->
## 3. Resume a workstream

Run `python3 $S/forge_hub.py brief H [--workstream SLUG]`. It wakes the workstream (taking its lock) and reads the `hub` memory for you. Also run `python3 $S/forge_memory.py load --state <state_path> --agent hub` and keep the `hub` and `shared` entries in mind while driving the workstream.

| `status` | Do |
|---|---|
| `OK` | Show the five `lines` verbatim, then ask whether to continue (*Continue*, below) |
| `ELSEWHERE` | `EnterWorktree` with `elsewhere.worktree_path`, then run `brief` again |
| `LOCKED` | Show `lock` (holder, host, whether alive). Only if the user confirms, run `resolve_state.py wake --steal [--workstream SLUG] H`, then `brief` again |
| `LIST` | Show the list |
| `NO_STATE` | Say no workstream matches, show the list, and mention `/forge adopt <branch>` for an existing branch |
| `IDENTITY_MISMATCH`, `STATE_INVALID`, `DRIFT_UNKNOWN` | Report the status and `detail`. Stop |

**List.** Run `python3 $S/resolve_state.py list`. Show each workstream's name, phase, last status, last update, lock holder and whether it is alive, and branch, most recent first. Ask which to resume; resume it with `/forge <workstream>`.

<!-- @spec CMD-RESUME-003, CMD-RESUME-004 -->
**Continue.** When the user continues, route once, by the first case that applies, then enter the loop:

1. `pending_envelope` is `true`: run `forge_hub.py envelope --state <state_path>` and route with its `event_path` (section 4, *After a dispatch*).
2. The phase is `INIT`: route with a `start` event.
3. Otherwise: route with no event.

When the user declines, write a `hub` memory entry recording where the workstream stands (section 8), then run `resolve_state.py release --workstream <slug> H`.

<!-- @spec CMD-LOOP-001, CMD-LOOP-002, CMD-LOOP-007, CMD-LOOP-008 -->
## 4. The routing loop

**Kill switch, before every router call.** Check for `.STOP_FORGE` at the main worktree's root (the first path of `git worktree list`) and at the workstream worktree's root. If either exists, end the loop and tell the user which file to remove. Ask nothing and commit nothing first.

Route with at most one event:

```bash
python3 $S/next_step.py --workstream <slug> H                                  # no event
python3 $S/next_step.py --workstream <slug> H --event <event_path>             # a file from forge_hub.py
echo '{"type": "start"}' | python3 $S/next_step.py --workstream <slug> H --event -   # start
```

Then act on `action`, and repeat until one ends the loop:

| `action` | Do |
|---|---|
| `dispatch` | Dispatch (below) |
| `pause` | Present the pause (section 5) |
| `stop` | Report `status` and `detail`. For `ENVELOPE_INVALID`, offer a retry (section 6). End the loop |
| `close`, `done` | Close the workstream (section 7) |

<!-- @spec CMD-LOOP-003 -->
**Dispatch.** Start the subagent named by `invoke.agent` with exactly this prompt and nothing else:

```text
Forge dispatch <invoke.dispatch_id>. Your invocation is lifecycle.invoke in <invoke.state_path>.
Write your result envelope to <state dir>/envelopes/<invoke.dispatch_id>.json.
```

`<state dir>` is the directory containing `state_path`. Never copy envelope JSON into a prompt or out of a reply.

<!-- @spec CMD-LOOP-004, CMD-LOOP-005 -->
**After a dispatch.** When the subagent returns, run `python3 $S/forge_hub.py envelope --state <state_path>`:

| `status` | Do |
|---|---|
| `OK` | Route with `--event <event_path>` |
| `NO_DISPATCH` | Route with no event |
| `ENVELOPE_MISSING` | Tell the user the agent wrote no envelope. On retry, route with no event |
| `ENVELOPE_UNREADABLE` | Section 6 |
| `STATE_INVALID` | Report `detail`. Stop |

<!-- @spec CMD-PAUSE-001, CMD-PAUSE-002, CMD-PAUSE-003, CMD-PAUSE-004 -->
## 5. Pauses

**Questions** — `plan_approval`, `council_signoff`, `findings_signoff`, `human_code_approval`, `publish_approval`, `report_approval`, `ci_none`, `dirty_tree`, `attempt_limit`: ask with `AskUserQuestion`, showing `question` and the files in `context_refs` that matter, then route the answer.

**Outside work** — `edit_window`, `manual_merge`: show `question` and what to do (make edits and commit them; merge the branch into its base yourself). Do not ask. End your turn. A later message saying the work is done, or `/forge`, answers it with `continue` or `done`; any other message is handled normally and the pause stays.

**Not yet** — when routing an answer returns a pause of the same kind (`publish_approval` after `hold`; `manual_merge` before the merge): show the router's `question` and end your turn.

**Every answer** goes through the script; never write event JSON yourself:

```bash
python3 $S/forge_hub.py answer --state <state_path> --decision <decision> [--option <option>] [--notes-file <file>]
```

Write the user's notes to a file in the session scratchpad with your file-write tool and pass it as `--notes-file`. Route with the returned `event_path`. `NOT_PAUSED` means nothing is waiting: route with no event.

<!-- @spec CMD-PAUSE-005, CMD-PAUSE-006, CMD-PAUSE-007, CMD-PAUSE-008 -->
| Pause | How to ask |
|---|---|
| `publish_approval` | Three choices: approve and push (`approve`, `--option push`), approve and merge (`approve`, `--option merge`), hold (`hold`) |
| `council_signoff` | Show the decision matrix from the ruling's `matrix_ref` file. Approve sends `approve` with `--option <chosen option's label>`. Reject asks for notes. When the options do not fit `AskUserQuestion`, show the matrix as text and ask in prose |
| `plan_approval`, `report_approval` | `revise` asks for notes first |
| `human_code_approval` | Show the paths and line ranges. `reject` asks for notes first |
| `dirty_tree` | `checkpoint`: in the workstream worktree run `git add -A && git commit -m "wip(user): partial implementation"` (no Forge trailers). `stash`: run `git stash push --include-untracked`. If git fails, report git's message and ask again without routing. Then answer with the choice |
| Any other | The router's `options` as labels, verbatim |

<!-- @spec CMD-LOOP-006 -->
## 6. Bad envelopes

When `envelope` returns `ENVELOPE_UNREADABLE`, or the router stops with `ENVELOPE_INVALID`, the dispatch is still outstanding. Show the reason (`detail`) and ask whether to retry. On retry, run `python3 $S/forge_hub.py envelope --state <state_path> --discard`, then route with no event: the agent runs again and writes a fresh envelope.

<!-- @spec CMD-CLOSE-001, CMD-CLOSE-002 -->
## 7. Close

Ask whether to remove the worktree. Leave it with `ExitWorktree`, keeping it on disk. Then run `python3 $S/resolve_state.py close --workstream <slug> H`, adding `--remove-worktree` only when the user said yes. On `WORKTREE_DIRTY`, report git's message and say that `/forge <workstream>` retries the close.

<!-- @spec CMD-MEM-002, CMD-MEM-003, CMD-MEM-004 -->
## 8. Hub memory

Write entries with your file-write tool in the format of the `forge-memory` skill (one JSON file, at most 1600 bytes, named `<YYYYMMDDTHHMMSSZ>-<sha7>.json`), into `memory/hub/` or `memory/shared/` beside `state.json`:

- **`hub`**, after routing a user answer that carried notes, rejected a ruling, or held a publish: record the reasoning, with `sha_anchor` from `git rev-parse HEAD` after the answer was applied and `stage` set to the `phase` the router returned.
- **`hub`**, when the user tells you to stop mid-workstream or declines to continue: record where the workstream stands.
- **`shared`**, after the user approves a council ruling, or states an invariant every agent must respect: record it.

<!-- @spec CMD-LOOP-009 -->
## Never

- Write `state.json`. Push. Commit, except the `dirty_tree` checkpoint.
- Retype envelope or event JSON.
- Route around a `stop`, or answer a pause for the user.
