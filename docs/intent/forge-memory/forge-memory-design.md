---
parent: high-level-design
prefix: MEM
---

# Forge Memory

## Context and Design Philosophy

A spoke starts every dispatch with no memory of earlier dispatches, and the Hub starts every session with no memory of earlier sessions. `state.json` holds the hard facts — phase, anchors, scope, blockers — but not the reasoning around them: why an approach was chosen, what was tried and failed, which test command is fast. Forge memory keeps those episodic notes for the life of one workstream, so the next dispatch of the same agent does not repeat a dead end.

Memory is scoped to one workstream and disposed of with it. It neither reads nor writes any other memory tool's store, and it never promotes notes beyond the workstream.

Two parts:

- **`forge_memory.py load`** (in `reference/scripts/`, Python standard library only) reads the notes an agent may see and marks the ones that describe code no longer in the branch. Reading is mechanical, so a script does it.
- **The `forge-memory` skill** (in `reference/skills/forge-memory/`) tells each agent when to load, what is worth saving, and how to write an entry. Saving needs judgment — what a later dispatch will need — so the agent does it, writing one file with its ordinary file-write tool.

## Partitions

Memory lives in `memory/` under the workstream's state directory, one directory per partition. `resolve_state.py new` creates them all.

| Partition | Loaded by | Written by | Holds |
|---|---|---|---|
| `hub` | The Hub | The Hub | The user's intent for the workstream, the reasons behind the user's answers to pauses, and where a session stopped |
| `shared` | The Hub and every Forge agent | The Hub | Workstream-wide invariants the user stated and summaries of approved council rulings |
| `planner`, `qa`, `dev`, `verifier`, `reviewer`, `scout` | The owning agent (`forge-<partition>`) | The owning agent | That agent's decisions, dead ends, and tooling quirks |

`forge-publisher`, `forge-reporter`, and `forge-council` load `shared` only and save nothing: each of their dispatches is a single bounded action whose record is the envelope, the pull request, or the ruling. The council passes whatever invariants matter to its personas.

The guard confines file-write tool calls: a spoke may write only its own partition, and no Forge agent may write `hub` or `shared`. Shell commands that write files are outside the guard's reach (see the guard design), and nothing checks the one-entry-per-dispatch rule; both rest on the skill's instructions. Reading is not restricted beyond what `load` returns: an agent that opens another partition's files directly is not blocked, and reading cannot damage anything.

## Entry Format

Each entry is one JSON file in its partition:

```json
{
  "timestamp": "2026-11-16T09:30:00Z",
  "sha_anchor": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
  "stage": "LID-S6",
  "decisions": ["Sliding-window counter instead of token bucket: the burst rule in RATE-004 needs per-second resolution."],
  "dead_ends": ["Wrapping the handler in async middleware broke error propagation (tests/test_auth.py::test_401)."],
  "tooling_quirks": ["Run `uv run pytest tests/test_rate_limit.py -q` for a 2 s loop; the full suite takes 45 s."]
}
```

An entry is valid when all of these hold:

| Rule | Detail |
|---|---|
| File name | `<YYYYMMDDTHHMMSSZ>-<sha7>.json`: the UTC write time, then the first seven characters of `sha_anchor`, or `0000000` when `sha_anchor` is `null` |
| Size | At most 1600 bytes, about 400 tokens |
| Shape | A JSON object; keys other than the six below are ignored |
| `timestamp` | A string in the form `YYYY-MM-DDTHH:MM:SSZ` |
| `sha_anchor` | A 40-character lowercase hexadecimal SHA — `HEAD` when the entry was written — or `null`, used only in a repository with no commits |
| `stage` | A non-empty string: the dispatch's `stage`, or its `mode` for an agent that does not commit; the Hub uses the phase it is in |
| `decisions`, `dead_ends`, `tooling_quirks` | All three present, each a list of strings, at least one of them non-empty |

The size cap keeps an agent's whole partition cheap to load and forces each entry to say one thing well. An invalid file is listed by `load` with the reason and left out of the entries, so the agent sees why and writes the next entry correctly.

## Loading

```text
forge_memory.py load --state PATH --agent AGENT [--limit N]
```

`--state` is the path of the workstream's `state.json` (every spoke receives it as `state_path`). `AGENT` is one of:

| `AGENT` | Partitions returned, in this order |
|---|---|
| `hub` | `hub`, then `shared` |
| `forge-planner`, `forge-qa`, `forge-dev`, `forge-verifier`, `forge-reviewer`, `forge-scout` | Its own partition, then `shared` |
| `forge-publisher`, `forge-reporter`, `forge-council` | `shared` |

Within each partition, valid entries are sorted by file name, newest first, and the newest `N` are returned (default 20). A partition directory that does not exist counts as empty. An unknown `AGENT`, an `N` below 1, or a missing `--state` is a usage error: exit `2` with the message on stderr.

### Historical entries

An entry is returned with `historical: true` when its notes describe code the branch no longer contains:

- its `sha_anchor` is in `anchors.stale_shas`; or
- its `sha_anchor` is not a commit of the workstream's repository, or is not an ancestor of the worktree's `HEAD`.

Both checks run in `identity.worktree_path`. The second catches commits that were rewritten or reset away without ever being the recorded `last_known_head_sha`, which `stale_shas` alone would miss. An entry with a `null` anchor is never historical.

A historical decision is no longer in force; a historical dead end may still be worth knowing. Entries are never edited or deleted: staleness is computed at every load.

### Output

One JSON object on stdout, exit `0`:

```json
{"status": "OK",
 "entries": [{"partition": "dev", "file": "...", "historical": false, "timestamp": "...", "sha_anchor": "...",
              "stage": "...", "decisions": [], "dead_ends": [], "tooling_quirks": []}],
 "invalid": [{"file": "...", "reason": "..."}]}
```

| Status | When |
|---|---|
| `OK` | Entries loaded |
| `STATE_INVALID` | `state.json` is missing or does not parse; `detail` says which |
| `WORKTREE_MISSING` | `identity.worktree_path` is not a git working tree, so ancestry cannot be checked |

`load` never writes anything.

## Saving

The skill tells each agent with a partition:

1. **Load first.** Run `load` at the start of every dispatch, before planning any work. Treat `historical` entries as notes about code that is gone.
2. **Save at most one entry per dispatch**, just before writing the result envelope, and only when a later dispatch of the same agent would need it: a decision whose reason is not in the commits or specs, an approach that failed and why, a tooling quirk that cost time. Nothing that the commits, the specs, or `state.json` already record.
3. **Write only your own partition**, with `sha_anchor` set to `git rev-parse HEAD` at the time of writing, or `null` when the repository has no commits. When a file with the same name already exists, use the next second in the name.

Agents that load only `shared` read it at the start of every dispatch and save nothing.

The Hub loads `hub` and `shared` when it resumes a workstream. It writes a `hub` entry:

- after routing a user answer that carried reasoning (notes, a rejected ruling, a held publish), anchored to `HEAD` after the answer was applied and staged with the phase the router returned;
- when the user declines to continue a resumed workstream or tells the Hub to stop, recording where the workstream stands.

A session that ends without another turn — a closed terminal — writes nothing. The Hub writes a `shared` entry after the user approves a council ruling, and when the user states an invariant every agent must respect.

## Disposal

Memory goes with the workstream directory when `resolve_state.py close` archives or deletes it. Nothing in this component runs at closeout.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Load mechanism | `forge_memory.py load` selects partitions, sorts, validates, and marks historical entries | Agents read the files directly following prose rules | *Scripts over prompts*: sorting, size limits, and the ancestry check are mechanical, and a model filtering by SHA would get it wrong some of the time. |
| Save mechanism | The agent writes one file with its file-write tool, which the guard confines to its partition | A `save` subcommand | A script run through the shell is invisible to the guard's write check, so it would have to trust an agent-supplied `--agent`. |
| Entry format | One JSON file per entry | Markdown with header lines; one append-only file per partition | JSON parses with the standard library and validates strictly. One file per entry means a failed write damages only itself and nothing needs merging. |
| Rolled-back entries | Returned with `historical: true` | Dropped | A dead end found on rolled-back code often still applies; dropping it invites repeating it. |
| Historical test | `stale_shas` or not an ancestor of `HEAD` | `stale_shas` only; rewrite `stale: true` into the files | `stale_shas` records only commits the recorded anchor reached; the ancestry check covers every rewrite. Computing at load keeps one writer per file. |
| Read access | Restricted only by what `load` returns | Guard blocks reads of other partitions | Reading cannot damage anything; blocking reads would need the guard to inspect every read and shell command for paths. |
| Entry size | 1600 bytes per file, 20 entries per partition per load | No limits | Keeps every load to a few thousand tokens for the life of a workstream. |
| Publisher, reporter, council | Load `shared`, no partition | A partition each; no memory at all | Invariants the user states apply to the pull request and rulings too; their single-action dispatches have nothing to carry forward themselves. |

## Open Questions & Future Decisions

### Deferred
1. Summarizing old entries when a partition exceeds the load limit — the newest 20 are returned, older ones are skipped. Revisit if real workstreams routinely exceed it.

## References

- `docs/high-level-design.md` — *Forge memory*; non-goal *Repo-wide knowledge management*.
- Workstream state design (`docs/intent/workstream-state/`) — partition directories, `anchors.stale_shas`, closeout.
- Guard design (`docs/intent/guard/`) — partition write rules; shell-command coverage.
