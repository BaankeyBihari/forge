---
parent: high-level-design
prefix: CMD
---

# Commander

## Context and Design Philosophy

The Commander is the Hub: the `/forge` skill running in the user's main Claude Code session. It is the only part of Forge that talks to the user. It turns the user's request into a workstream, then runs a loop — ask the router what happens next, carry that out, report the outcome back to the router — until the router pauses, stops, or closes the workstream.

The Commander decides nothing the router or `resolve_state.py` can decide. Its judgment is limited to talking with the user, naming a workstream from a task description, and presenting what spokes and the council return.

Two parts:

- **`forge_hub.py`** (in `reference/scripts/`, Python standard library only) does the Commander's mechanical work: classifying the `/forge` argument, composing the resume brief, picking up a spoke's result envelope, and building the event for a user's answer. It has no Claude-specific content, so another CLI's Commander can reuse it.
- **`claude/skills/forge/SKILL.md`** is the Claude Code procedure: which tool to call for each status and decision the scripts return. Its behaviors are checked by running a workstream in the vendored sandbox, not by pytest.

Every `forge_hub.py` subcommand prints one JSON object on stdout and exits `0`; a usage error exits `2`, and `classify` or `brief` run outside a git repository exits `1`, each with the message on stderr.

## Identity Passed to Every Script

Every `resolve_state.py`, `next_step.py`, and `forge_hub.py brief` call carries `--holder-pid $CLAUDE_PID --holder-session ${CLAUDE_SESSION_ID}`. `${CLAUDE_SESSION_ID}` is substituted into the skill text by Claude Code; `CLAUDE_PID` is the `claude` process's ID in the environment of Bash commands. The guard matches the lock's `holder_session` against the `session_id` in each spoke's hook input, so the two must be the same identifier; when they are not, every spoke call is blocked with `NO_ACTIVE_WORKSTREAM`, which the first sandbox run reveals.

The skill finds the scripts at `${CLAUDE_SKILL_DIR}/scripts/`, a relative symlink in the repository from `claude/skills/forge/scripts` to `reference/scripts`.

## Arguments

```text
forge_hub.py classify [WORD ...]
```

The words of `/forge`'s argument, joined by single spaces, are classified by the first rule that matches:

| Argument | `kind` | Commander does |
|---|---|---|
| empty | `current` | Resume, waking without `--workstream` (resolves from `FORGE_WORKSTREAM`, then the current branch) |
| `list` | `list` | List the repository's workstreams |
| `pr <ref>`, or a GitHub pull request URL | `pr` | Start a `pr_review` workstream with `new --pr <ref>` |
| `adopt <branch>` | `adopt` | Start a workstream on that existing branch with `new --branch <branch>` |
| `pr` or `adopt` followed by anything but one word | `invalid` | Show the expected form |
| A GitHub issue URL, `#<n>`, or `<n>` | `issue` | Start a workstream with `new --issue <ref>` |
| One word naming a workstream directory of this repository | `resume` | Resume with `--workstream <word>` |
| One word that is a valid workstream slug but names no workstream | `unknown` | Ask whether to start a new workstream with that name or show the list |
| Anything else | `describe` | Name a workstream from the text and start it with `new --name` |

Output is `{"kind": ..., "ref"?: ..., "branch"?: ..., "workstream"?: ..., "text"?: ..., "detail"?: ...}`. The `unknown` kind keeps a mistyped workstream name from silently starting a new workstream.

## Starting a Workstream

For `issue`, `pr`, and `adopt`, the Commander runs `new` with the reference. For `describe`, it derives a slug of two to five words from the description (`rate-limit-login`), never `list`, `pr`, or `adopt`, and passes `--name`; when the description gives nothing to name, it asks the user for a name. It also writes the whole description, unchanged, to a file in its scratch directory with its file-write tool and passes that file as `--task-file`, so the planner works from the user's own words. It passes `--base` only when the user named a base branch, and `--set` only for settings the user asked for (`plan_mode=auto`). The worktree root is `.claude/worktrees`, which `resolve_state.py` resolves against the main worktree's root wherever the session is.

Before starting or resuming a different workstream, the Commander releases the lock of the one it was driving in this session (`resolve_state.py release`), so the session never holds two locks.

| `new` status | Commander does |
|---|---|
| `CREATED` | Move the session into `worktree_path`, then route with a `start` event |
| `DUPLICATE` | Offer to resume the named workstream instead |
| `BASE_CONFIRM_REQUIRED` | Ask the user to confirm stacking on that base; on yes, run `new` again with `--base-confirmed` |
| `BRANCH_CHECKED_OUT` | Tell the user the branch is checked out at the reported path and must be switched away there first |
| `STATE_EXISTS`, `BRANCH_EXISTS`, `GH_FAILED` | Report the status and its detail; for `STATE_EXISTS`, offer to resume |

Moving the session uses Claude Code's `EnterWorktree` with the worktree path, which changes the session's working directory without changing any checkout.

## Resuming a Workstream

The Commander runs `brief`, which wakes the workstream, and acts on the status:

| Status | Commander does |
|---|---|
| `OK` | Show the five lines and ask whether to continue (see *Continuing*) |
| `ELSEWHERE` | Move the session into the reported worktree and run `brief` again |
| `LIST` | Show the list (the session is on `main` or `master` with no state) |
| `NO_STATE` | Say no workstream matches, show the list, and mention `/forge adopt <branch>` for starting on an existing branch |
| `LOCKED` | Show the holder; on the user's confirmation, wake with `--steal` and run `brief` again |
| `IDENTITY_MISMATCH`, `STATE_INVALID`, `DRIFT_UNKNOWN` | Report the status and its detail and stop |

`list` shows each workstream's name, phase, last status, last update, lock holder (and whether it is alive), and branch, most recent first, and asks which to resume.

### Resume brief

```text
forge_hub.py brief --holder-pid PID --holder-session ID [--workstream SLUG]
```

`brief` wakes the workstream, asks the router for its next decision with `--dry-run`, loads the `hub` memory partition, and returns `{"status": "OK", "lines": [...], "pending_envelope": bool}`:

```text
Workstream: acme/widgets/feat-rate-limit (LID, collaborative, plan strict)
Phase: PRE_DEV (forge-verifier → PARTIAL_REMAINING)
Progress: 1 of 3 scope patterns complete; remaining: src/limits/**, src/api/**
Hub context: User wants per-IP limits only; account limits are a follow-up.
Next step: Dispatch forge-dev (stub) for DEV_STUB
```

| Line | Source |
|---|---|
| Workstream | `identity` org, repo, workstream; `config.repo_mode`; `config.collaborative` as `collaborative` or `sequential`; `config.plan_mode` |
| Phase | `lifecycle.current_phase`, with `last_agent → last_status`, or `nothing run yet` |
| Progress | Counts of `scope_manifest.completed` and `assigned`, then the `remaining` patterns; `no plan yet` while `assigned` is empty; the number of open blockers when there are any |
| Hub context | The first decision of the newest non-historical `hub` entry that has one, or `none recorded` |
| Next step | See below |

The next-step line, by the first case that applies:

| Case | Line |
|---|---|
| A dispatch is outstanding and its envelope file exists | `Apply the result <agent> left (<status>)`; `pending_envelope` is `true` |
| Phase is `INIT` | `Start routing (nothing has run yet)` |
| The dry-run decision is a pause | `Waiting for you:` and the pause's question |
| The dry-run decision is a stop | `Stopped: <status>` and its detail |
| Otherwise | The dry-run decision's `summary` |

When the wake status is not `OK`, `brief` returns that wake report unchanged instead of lines.

### Continuing

When the user continues, the Commander routes once, by the first case that applies: with the picked-up envelope (`envelope`, below) when `pending_envelope` is `true`; with a `start` event at `INIT`; otherwise with no event. Then it enters the routing loop. When the user declines, it writes a `hub` memory entry recording where the workstream stands and releases the lock.

## The Routing Loop

Each step calls `next_step.py` with at most one event and acts on its decision:

| Decision | Commander does |
|---|---|
| `dispatch` | Dispatch the subagent; pick up its envelope; route with it |
| `pause` | Present the pause (see *Pauses*) |
| `stop` | Report the status and detail; for `ENVELOPE_INVALID`, offer a retry (see *Bad envelopes*); end the loop |
| `close` | Close the workstream (see *Close*) |
| `done` | Close the workstream: the router finished it but the state directory still exists, so an earlier close did not complete |

### Kill switch

Before every call to the router, the Commander checks for `.STOP_FORGE` at the main worktree's root and at the workstream worktree's root. When either exists, it ends the loop and tells the user which file to remove, before any question is asked or anything is committed — a `.STOP_FORGE` in the worktree makes the tree dirty, and answering the resulting `dirty_tree` pause with `checkpoint` would commit the file. The guard blocks every spoke call under the same rule.

### Dispatch

The Commander starts the subagent named by `invoke.agent` with a prompt that carries only the dispatch ID and two paths:

```text
Forge dispatch <dispatch_id>. Your invocation is lifecycle.invoke in <state_path>.
Write your result envelope to <state dir>/envelopes/<dispatch_id>.json.
```

The spoke reads the full invocation envelope from `state.json` (the router stores it in `lifecycle.invoke`) and writes its result envelope to the named file, which the guard allows for that agent and dispatch only. Neither envelope passes through the Commander's own words, so no JSON is retyped by a model. The spoke side of this contract is designed with the spokes.

When the subagent returns:

```text
forge_hub.py envelope --state PATH [--discard]
```

`envelope` reads `lifecycle.dispatched`, loads `<state dir>/envelopes/<dispatch_id>.json`, wraps it as a `result` event, and writes the event to `<state dir>/envelopes/<dispatch_id>.event.json`. The Commander then routes with `--event <event_path>`. The router validates everything about the envelope itself.

| `envelope` status | Meaning | Commander does |
|---|---|---|
| `OK` | Event written; `event_path` names it | Route with it |
| `NO_DISPATCH` | No dispatch is outstanding | Route with no event |
| `ENVELOPE_MISSING` | The spoke wrote no envelope | Tell the user; on retry, route with no event: the router returns the same dispatch, or reconciliation when the spoke committed before failing |
| `ENVELOPE_UNREADABLE` | The file is not a JSON object; `detail` has the parse error | As for a bad envelope, below |
| `STATE_INVALID` | `state.json` is missing or does not parse | Report it and stop |

### Bad envelopes

When the router stops with `ENVELOPE_INVALID`, or `envelope` returns `ENVELOPE_UNREADABLE`, the dispatch is still outstanding and the file would be rejected again. The Commander shows the reason and asks whether to retry. On retry it runs `envelope --discard`, which renames the file to `<dispatch_id>.rejected-<YYYYMMDDTHHMMSSZ>.json` and returns `DISCARDED` (or `ENVELOPE_MISSING` when there is no file), then routes with no event, so the spoke runs again and writes a fresh envelope.

### Pauses

Pauses fall into three groups:

| Group | Pauses | Commander does |
|---|---|---|
| Questions | `plan_approval`, `council_signoff`, `findings_signoff`, `human_code_approval`, `publish_approval`, `report_approval`, `ci_none`, `dirty_tree`, `attempt_limit` | Asks with `AskUserQuestion`, then routes the answer |
| Outside work | `edit_window`, `manual_merge` | Shows the question and what to do (edit and commit; merge the branch), then ends its turn. A later message saying the work is done, or `/forge`, answers it; any other message is handled normally and the pause stays |
| Not yet | Any pause that routing an answer returns again with the same kind (`publish_approval` after `hold`; `manual_merge` before the merge) | Shows the router's question and ends its turn |

Each answer becomes an event built by the script, not typed by the model:

```text
forge_hub.py answer --state PATH --decision DECISION [--option OPTION] [--notes-file FILE]
```

`answer` reads `lifecycle.awaiting.kind`, builds `{"type": "user", "awaiting", "decision", "option"?, "notes"?}` with the notes read from the file, writes it to `<state dir>/envelopes/answer-<YYYYMMDDTHHMMSSZ>.event.json`, and returns `{"status": "OK", "event_path": ...}`, or `NOT_PAUSED` when nothing is awaiting. The Commander writes notes with its file-write tool, so the user's text needs no quoting. `answer` does not check the decision; the router does.

| Pause | How the answer is gathered |
|---|---|
| `publish_approval` | Three choices: approve and push, approve and merge, hold; approve carries `option` `push` or `merge` |
| `council_signoff` | The ruling's decision matrix (its `matrix_ref` file) is shown; approve carries the chosen option's label; reject asks for notes. When the options do not fit the question tool, the matrix is shown as text and the choice asked in prose |
| `plan_approval`, `report_approval` | `revise` asks for notes |
| `human_code_approval` | The paths and line ranges are shown; reject asks for notes |
| `dirty_tree` | `checkpoint`: the Commander commits every change as `wip(user): partial implementation` with no Forge trailers; `stash`: it runs `git stash push --include-untracked`. When the git command fails, it reports git's message and asks again without routing |
| Any other | The router's options, as labels, verbatim |

### Close

To close, the Commander asks whether to remove the worktree. It leaves the worktree with `ExitWorktree` (keeping it on disk), then runs `resolve_state.py close --workstream <slug>`, adding `--remove-worktree` when the user said yes. `WORKTREE_DIRTY` is reported with git's message; `/forge <workstream>` later moves back into the worktree, the router returns `done`, and the close runs again.

### Memory

The Commander writes `hub` and `shared` memory entries at the points the forge-memory design names. It never writes `state.json`, never commits except the `dirty_tree` checkpoint, and never pushes.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Commander shape | Thin skill plus `forge_hub.py` for the mechanical steps | Everything in skill prose | *Scripts over prompts* and *Shared first*: argument classification, the brief, envelope pickup, and answer events are testable with pytest and reusable by another CLI's Commander. |
| Envelope transport | Spokes write `envelopes/<dispatch_id>.json`; the spoke reads its invocation from `lifecycle.invoke` | The Commander copies JSON between its context and the subagent's prompt and reply | A model retyping JSON can change it silently. Files keep both envelopes byte-for-byte. |
| User answer events | Built by `forge_hub.py answer`, notes read from a file | The model writes the JSON in a heredoc | The same reason: free-text notes with quotes and newlines are where hand-written JSON breaks. |
| Lock holder identity | `${CLAUDE_SESSION_ID}` and `$CLAUDE_PID` | Guard matches the workstream by worktree and ignores the session | Keeps the rule that only the session holding the lock may drive agents. A wrong identifier fails closed and loudly on the first sandbox run. |
| One lock per session | Release before switching workstreams and when the user declines to continue | Hold every lock until the session ends | The guard identifies the active workstream by the session's lock; two locks make that ambiguous. |
| Resume confirmation | Show the brief and ask before routing | Route immediately | Resuming may come days later; the user should see where things stand before an agent runs. |
| Envelope left by a crashed session | Applied on resume before anything else | Route with no event, which re-dispatches the spoke | The spoke's work is done and its envelope on disk; re-dispatching repeats it. |
| Pauses that wait on outside work | End the turn; the next message answers | Keep the session blocked in a question until the user is done | The user edits code or merges a branch outside the conversation; a blocked question invites answering before the work is done, and a "not yet" answer would otherwise be asked again at once. |
| Kill switch location | Main worktree root or the workstream worktree root | The worktree root only | Users create the file where they work, usually the main checkout; one file there stops every workstream. |
| Kill switch timing | Checked before every router call | Checked before each dispatch; checkpoint commits skip `.STOP_FORGE` | Stopping before any question means one rule covers every path, including the dirty-tree pause the file itself causes. |
| Brief's hub context line | Taken from the newest `hub` memory entry by the script | Written by the Commander each time | The brief stays fully mechanical, so it is the same in every session. |
| Task description handoff | The Commander writes the description to a file and passes `new --task-file` | Keep only the derived slug; pass the text as `start` event notes | The slug loses every decision the user stated; event notes are gone after the first dispatch and on resume. |

## Open Questions & Future Decisions

### Unverified
1. `CLAUDE_PID` and `CLAUDE_CODE_SESSION_ID` appear in the environment of Bash commands but are not documented; `${CLAUDE_SESSION_ID}` is. Whether a subagent's hook input carries the main session's `session_id` is not documented either. The first sandbox run checks both.
2. `EnterWorktree` documents switching into an existing worktree under `.claude/worktrees/` by path; whether the tool accepts the switch when the instruction comes from a skill rather than the user is checked in the sandbox.

### Deferred
1. Cross-repository `/forge list`.

## References

- `docs/high-level-design.md` — *Hub and spokes*, *Listing and resuming*.
- Router design (`docs/intent/router/`) — decisions, pauses, events, `lifecycle.invoke`.
- Workstream state design (`docs/intent/workstream-state/`) — `wake`, `new`, `list`, `release`, `close` and their statuses.
- Forge memory design (`docs/intent/forge-memory/`) — `hub` and `shared` partitions, when the Hub writes them.
- Guard design (`docs/intent/guard/`) — envelope file write rule, kill switch.
