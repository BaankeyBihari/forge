---
name: forge-spoke
description: The dispatch procedure every Forge spoke follows - read the invocation, load memory, commit with Forge trailers, finish the result envelope, reply to the Hub. Every Forge spoke uses it on every dispatch, from its first action to its last.
---

# Forge spoke

You were started by the Forge Hub with a dispatch ID, the path of `state.json` (`<state_path>`), and the path of your result envelope. You do your role's work as your own role skill (`forge-<role>`) describes; this skill is how a dispatch starts, commits, and ends. Follow it in order.

`forge_spoke.py`, `verify_gate.py`, and `forge_memory.py` are in the `scripts/` directory beside this file. Every command prints one JSON object with a `status`. `<worktree>` is `identity.worktree_path` from the state: your work and every git command happen there.

<!-- @spec SPOKE-PROTO-SKILL-001 -->
## 1. Read the invocation first

Before anything else, run:

```bash
python3 <this skill's directory>/scripts/forge_spoke.py invocation --state <state_path> --dispatch <dispatch_id>
```

- `OK`: `invoke` is your invocation envelope (`agent`, `mode`, `stage`, `base_sha`, `assigned_scope`, `context_refs`, `notes`), `phase` the current phase, and `state` the whole `state.json` (`identity`, `config`, `anchors`, `scope_manifest`, `approvals`, `council_decisions`).
- `NOT_DISPATCHED`: your dispatch was replaced. **Change nothing.** End your turn saying the dispatch was replaced. The same applies whenever any later `forge_spoke.py` command returns `NOT_DISPATCHED`.
- `STATE_INVALID`: end your turn quoting `detail`.

<!-- @spec SPOKE-PROTO-SKILL-002 -->
## 2. Load memory, then read the context

Load your memory as the `forge-memory` skill says. Then read **every** file in `invoke.context_refs` before you start work. Work only on `invoke.assigned_scope`.

<!-- @spec SPOKE-PROTO-SKILL-006 -->
## 3. Running tests

- Use `config.test_command` when it is set.
- Otherwise determine the command from the repository: its build files, CI configuration, and README. Record it as a tooling quirk in your memory entry (`forge-memory`, section 3), so later dispatches reuse it.
- When you cannot find a command that runs, return `ERROR` saying so.
- Test runs can leave untracked files (caches, reports). Before finishing, list them with `git -C <worktree> status --porcelain` and delete with `rm` every one the repository does not ignore. `git clean` is blocked.

<!-- @spec SPOKE-PROTO-SKILL-007, SPOKE-PROTO-SKILL-008 -->
## 4. Human-authored lines (`forge-qa`, `forge-dev`)

Before changing an existing file, ask which of its lines are protected:

```bash
cd <worktree> && python3 <this skill's directory>/scripts/verify_gate.py human-lines --path <repo-relative path> --agent <your agent name> --state <state_path>
```

The output is `{"ranges": [[start, end], ...]}`: lines of the file at `HEAD` you **must not change** without the user's approval. Lines covered by an approval you were given no longer appear.

When your work needs a protected line:

1. Do not change it.
2. Commit the valid progress you have (section 5).
3. Run `human-lines` again on each such file, at that `HEAD`.
4. Return `HUMAN_CODE_CHANGE_REQUESTED` with the paths and line ranges you need, numbered as they stand at that `HEAD`:

```json
{
  "status": "HUMAN_CODE_CHANGE_REQUESTED",
  "summary": "Rate limiting needs the existing window check in src/limits/window.py changed.",
  "escalation": {
    "details": "src/limits/window.py lines 40-52 hard-code a 60 s window; RATE-004 needs it configurable.",
    "paths": ["src/limits/window.py"],
    "ranges": {"src/limits/window.py": [[40, 52]]}
  }
}
```

When `notes` say the user rejected such a change and the work cannot be done without those lines, return `ERROR` saying so.

<!-- @spec SPOKE-PROTO-SKILL-003 -->
## 5. Commit (`forge-planner`, `forge-qa`, `forge-dev`)

Stage with `git add`, naming the paths you changed (not `git add -A` or `git add .`). Write the commit message to a file in the session scratchpad with your file-write tool, then:

```bash
python3 <this skill's directory>/scripts/forge_spoke.py commit --state <state_path> --dispatch <dispatch_id> --message-file <message file>
```

**Commit only this way, never with `git commit`, and never write a `Forge-` trailer yourself**: the command appends them from the invocation.

- `COMMITTED`: `sha` is the new `HEAD`.
- `NOTHING_STAGED`: nothing was staged; `git add` first.
- `BOUNDARY_VIOLATION`: nothing was committed; `paths` lie outside your write boundary. Take them out of the index (`git rm --cached -q -- <path>` for a file you added) and commit again; if you cannot, return `ERROR` naming them.
- `STAGE_INVALID`: only `forge-planner` in mode `auto` passes `--stage`.

<!-- @spec SPOKE-PROTO-SKILL-004 -->
## 6. Write the envelope and finish

Save your memory entry first, if you have one (`forge-memory`, section 3). Then write the envelope to `envelopes/<dispatch_id>.json` beside `state.json` with your **file-write tool**. Write only `status`, `summary`, and the fields your status needs (`scope`, `escalation`, ...). `finish` fills in `dispatch_id`, `agent`, `head_sha`, and `files_touched`. `ERROR` is always allowed and needs `escalation.details`.

```bash
python3 <this skill's directory>/scripts/forge_spoke.py finish --state <state_path> --dispatch <dispatch_id>
```

**Run `finish` until it returns `FINISHED`.**

- `ENVELOPE_MISSING`: write the envelope at `path`.
- `INVALID`: fix every entry in `problems`, then run `finish` again: edit the envelope, commit work you meant to commit, or delete files your own test runs left.
- A problem you cannot fix that way (a commit you should not have, changes you did not make): rewrite the envelope as `ERROR` with the problem in `escalation.details`, and run `finish` again.

<!-- @spec SPOKE-PROTO-SKILL-005 -->
## 7. Reply to the Hub

End your turn with one paragraph saying what you did. Put nothing in it the router needs: everything the router reads is in the envelope.

**Never write `state.json`**, at any point of the dispatch. You read it only through `invocation`.
