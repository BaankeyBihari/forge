---
parent: high-level-design
prefix: SPOKE
---

# Spokes

## Context and Design Philosophy

A spoke is a Claude Code subagent with one role. The Hub starts it with a dispatch ID and the path of `state.json`; the spoke does its work inside its write boundary and leaves a result envelope; the router decides what happens next. Spokes never talk to the user, to each other, or to the router directly.

Every spoke shares one contract — how a dispatch starts, how work is committed, how the result is reported — and that contract is the same whichever role runs it. What differs between spokes is the judgment they apply and the statuses they may return. This node holds the shared contract and the parts of every spoke that are alike; each child leaf holds one role.

Two tenets shape every spoke:

- **Scripts over prompts.** Every value a script can compute — the dispatch ID, `HEAD`, the files touched, commit trailers, structural spec coverage, the publish gate — is computed by `forge_spoke.py`, `lid_check.py`, or `verify_gate.py`. The spoke writes only its judgment.
- **Stop over guess.** A spoke that cannot do its job returns `ERROR`; a spoke that finds the plan, a test, or human-authored code in its way returns the escalation status for that case. It never works around a boundary.

## Children

| Leaf | Prefix | Owns |
|---|---|---|
| `protocol/` | `SPOKE-PROTO` | `forge_spoke.py` and the shared `forge-spoke` skill: reading the invocation, committing with trailers, finishing the envelope, scope facts |
| `planner/` | `SPOKE-PLAN` | `forge-planner`: LID stages 1–4 or a `PLAN.md`; a proposal in idea and triage workstreams |
| `qa/` | `SPOKE-QA` | `forge-qa`: test skeletons, full tests, test repairs |
| `dev/` | `SPOKE-DEV` | `forge-dev`: stubs, implementation, production repairs |
| `verifier/` | `SPOKE-VER` | `forge-verifier` and `lid_check.py`: mechanical checks and the scope delta |
| `reviewer/` | `SPOKE-REV` | `forge-reviewer`: internal review and external pull request review |
| `scout/` | `SPOKE-SCOUT` | `forge-scout`: issue, related-work, and pull request recon, CI watching |
| `publisher/` | `SPOKE-PUB` | `forge-publisher`: push, or confirm a merge is possible |
| `reporter/` | `SPOKE-REP` | `forge-reporter`: pull request text and review submissions |
| `council/` | `SPOKE-COUNCIL` | `forge-council` and its six personas |

## A Dispatch, End to End

1. The Hub starts `forge-<role>` with the dispatch ID, `state_path`, and the envelope path (Commander design, *Dispatch*).
2. The spoke runs `forge_spoke.py invocation`, which prints `lifecycle.invoke` and the whole `state.json` after checking that the dispatch ID is still outstanding. Spokes read state only this way and never write it.
3. It loads its memory with `forge_memory.py load` (forge-memory design).
4. It reads every file in `context_refs`, works on `assigned_scope`, and, if it commits, commits only with `forge_spoke.py commit`.
5. If it has a memory partition, it saves at most one memory entry.
6. It writes the judgment part of its result envelope to `envelopes/<dispatch_id>.json` with its file-write tool, then runs `forge_spoke.py finish`, which fills in the computed fields and checks the result. It fixes what `finish` reports and runs it again until it passes.
7. It ends its turn with a one-paragraph reply for the Hub. The reply carries no data the router needs.

## What Every Spoke Shares

### Boundaries

The guard (guard design) confines every spoke's file writes and shell commands; agent profile tool lists narrow them further. Write boundaries come from the gates design's path classes: the planner writes plan paths, `forge-qa` test paths, `forge-dev` production paths, and nobody else writes the repository. In idea and triage workstreams nobody writes the repository; the planner writes `proposal.md` in the workstream's state directory instead. Every spoke may write its envelope file and the session scratchpad, and each spoke that has a memory partition (forge-memory design) may write that partition; the scout also writes the workstream's `scout/` directory and the council its `council/` directory.

### Human-authored code

Before changing an existing file, `forge-qa` and `forge-dev` run `verify_gate.py human-lines --agent <self>` on it. When the change needs protected human-authored lines, the spoke does not make it: it commits any valid progress, runs `human-lines` again at that `HEAD`, and returns `HUMAN_CODE_CHANGE_REQUESTED` with the paths and line ranges as they stand at its own final `HEAD`, which is the `head_sha` the approval is recorded against. After the user approves, the same phase is dispatched again with the approval ID in `notes`, and `human-lines` no longer reports the approved lines. After the user rejects, the phase is dispatched again with the user's notes; when the work cannot be done without those lines, the spoke returns `ERROR` saying so, and the workstream stops for the user.

The planner needs no such check: its commits are exempt from the human-code rule (gates design).

### Test commands

A spoke that runs tests uses `config.test_command` when it is set. Otherwise it determines the command from the repository (its build files, CI configuration, and README) and records it in its memory partition as a tooling quirk, so later dispatches reuse it. When it cannot determine a command that runs, it returns `ERROR`. A spoke removes files its test runs leave untracked in the worktree (caches, reports) unless the repository ignores them, because an untracked file makes the tree dirty.

### Statuses

Each leaf lists the statuses its role returns. Every spoke may also return `ERROR` with `escalation.details` when it cannot do its job at all; the router stops on it.

## Agent Profiles

Each spoke has a Claude Code agent profile, `claude/agents/forge-<role>.md`:

| Agent | Model | Tools | Preloaded skills |
|---|---|---|---|
| `forge-planner` | `opus` | Read, Grep, Glob, Bash, Write, Edit | `forge-spoke`, `forge-memory`, `forge-planner`, `linked-intent-dev` |
| `forge-qa` | `sonnet` | Read, Grep, Glob, Bash, Write, Edit | `forge-spoke`, `forge-memory`, `forge-qa` |
| `forge-dev` | `sonnet` | Read, Grep, Glob, Bash, Write, Edit | `forge-spoke`, `forge-memory`, `forge-dev` |
| `forge-verifier` | `sonnet` | Read, Grep, Glob, Bash, Write | `forge-spoke`, `forge-memory`, `forge-verifier` |
| `forge-reviewer` | `opus` | Read, Grep, Glob, Bash, Write | `forge-spoke`, `forge-memory`, `forge-reviewer` |
| `forge-scout` | `haiku` | Read, Grep, Glob, Bash, Write | `forge-spoke`, `forge-memory`, `forge-scout`, `forge-github-gh` |
| `forge-publisher` | `haiku` | Read, Bash, Write | `forge-spoke`, `forge-memory`, `forge-publisher` |
| `forge-reporter` | `haiku` | Read, Grep, Glob, Bash, Write | `forge-spoke`, `forge-memory`, `forge-reporter` |
| `forge-council` | `sonnet` | Read, Grep, Glob, Bash, Write, Agent | `forge-spoke`, `forge-memory`, `forge-council` |
| `forge-council-<persona>` | `opus` | Read, Grep, Glob | `forge-council` persona text for that persona |

The profile's body is short: "You are `forge-<role>`. Follow the `forge-spoke` skill for the dispatch you receive." The role's procedure lives in its skill under `reference/skills/forge-<role>/`, so another CLI can reuse it.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Shared contract location | One `protocol` leaf owning `forge_spoke.py` and a `forge-spoke` skill preloaded into every spoke | Repeat the procedure in each spoke skill; a `PROTOCOL.md` reference file | One copy of the procedure cannot drift between roles. A skill is preloaded by the agent profile; a reference file has to be found and read on every dispatch. |
| Spoke tree shape | A sub-HLD over one leaf per role plus council | One LLD for all spokes; leaves grouped by kind (builders, auditors, outbound) | Each role has its own statuses, boundary, and failure cases; a grouped leaf mixes them. The shared contract is real intent, so the parent carries it. |
| Read-only spokes get the Write tool | Yes, confined by the guard | No Write tool for verifier, reviewer, scout, publisher, reporter | They must write their envelope file and memory entry; the guard already limits writes to exactly those places. |
| Models | `opus` for planning, review, and council personas; `sonnet` for building, verifying, and the council orchestrator; `haiku` for scout, publisher, reporter | One model for all; per-workstream model config | Matches the reasoning each role needs to its cost; aliases track current models (HLD). A config knob is added when a user needs one. |
| Test command | `config.test_command`, else discovered and remembered | Always discovered; always configured | Most repositories reveal their test command; a fixed value overrides discovery when it guesses wrong. |

## Open Questions & Future Decisions

### Resolved
1. ✅ A subagent's preloaded skill finds `scripts/` beside its own `SKILL.md`: Claude Code shows the skill's base directory when it preloads the skill, and spokes run `forge_spoke.py` from there by absolute path, for project skills and for plugin skills alike (sandbox runs 1 to 4, second sandbox probe).

### Deferred
1. Bootstrapping LID in a repository that has none is not part of Forge; the user does it with their own tools before starting a workstream.

## References

- `docs/high-level-design.md` — *Hub and spokes*, *Human-authored code*, tenets.
- Router design (`docs/intent/router/`) — phases, modes, statuses, invocation and result envelopes.
- Gates design (`docs/intent/gates/`) — path classes, trailers, human-line query, publish check.
- Guard design (`docs/intent/guard/`) — write and command rules every spoke runs under.
- Forge memory design (`docs/intent/forge-memory/`) — loading and saving at each dispatch.
- Commander design (`docs/intent/commander/`) — how the Hub dispatches and picks up envelopes.
