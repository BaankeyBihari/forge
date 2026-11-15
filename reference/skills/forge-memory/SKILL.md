---
name: forge-memory
description: Load and save a Forge agent's episodic workstream memory (decisions, dead ends, tooling quirks). Every Forge agent uses it at the start of each dispatch, and agents with a partition use it again just before writing their result envelope.
---

# Forge memory

Forge memory keeps the reasoning `state.json` does not: why an approach was chosen, what was tried and failed, which command is fast. It belongs to one workstream and goes away with it.

`forge_memory.py` is in the `scripts/` directory beside this file. `<state_path>` is the `state_path` of your invocation envelope; the memory directory is `memory/` beside that file.

<!-- @spec MEM-SKILL-001, MEM-SKILL-002 -->
## 1. Load at the start of every dispatch

Before planning any work, run:

```bash
python3 <this skill's directory>/scripts/forge_memory.py load --state <state_path> --agent <your agent name>
```

`<your agent name>` is your own name, for example `forge-dev`. The output is one JSON object:

- `status` `OK`: read `entries`, newest first. Each has `decisions`, `dead_ends`, and `tooling_quirks`.
- An entry with `historical: true` describes code the branch no longer contains. Its decisions are **no longer in force**. Its dead ends may **still be relevant**: an approach that failed on rolled-back code often fails again.
- `invalid` lists entry files that break the format, with the reason. Leave them alone.
- `STATE_INVALID` or `WORKTREE_MISSING`: continue without memory and mention the status in your envelope's summary.

<!-- @spec MEM-SKILL-006 -->
## 2. `forge-publisher`, `forge-reporter`, `forge-council`

You have no partition. Load (you receive the `shared` partition) and **save nothing**.

<!-- @spec MEM-SKILL-003, MEM-SKILL-004, MEM-SKILL-005 -->
## 3. Save at most one entry, just before the result envelope

Agents with a partition — `forge-planner` (`planner`), `forge-qa` (`qa`), `forge-dev` (`dev`), `forge-verifier` (`verifier`), `forge-reviewer` (`reviewer`), `forge-scout` (`scout`) — save **at most one entry per dispatch**, just before writing the result envelope, and only when a later dispatch of the same agent would need it:

- a decision whose reason is not in the commits or specs;
- an approach that failed, and why;
- a tooling quirk that cost time.

Save nothing that the commits, the specs, or `state.json` already record. When nothing qualifies, save nothing.

Write the entry with your **file-write tool**, never a shell command, into **your own partition only**: `memory/<your partition>/` beside `state.json`. The guard blocks every other partition.

File name: `<YYYYMMDDTHHMMSSZ>-<sha7>.json` — the current UTC time, then the first seven characters of `sha_anchor`, or `0000000` when `sha_anchor` is `null`. When a file of that name already exists, use the next second.

```json
{
  "timestamp": "2026-11-16T09:30:00Z",
  "sha_anchor": "<output of git rev-parse HEAD>",
  "stage": "LID-S6",
  "decisions": ["Sliding-window counter instead of token bucket: RATE-004 needs per-second resolution."],
  "dead_ends": ["Async middleware around the handler broke error propagation (tests/test_auth.py::test_401)."],
  "tooling_quirks": ["`uv run pytest tests/test_rate_limit.py -q` runs in 2 s; the full suite takes 45 s."]
}
```

Rules `load` enforces:

- At most 1600 bytes. Say one thing well.
- `timestamp`: `YYYY-MM-DDTHH:MM:SSZ`, the same time as the file name.
- `sha_anchor`: `git rev-parse HEAD` at the time of writing (40 lowercase hex characters), or `null` in a repository with no commits.
- `stage`: your invocation's `stage`, or its `mode` when you have no `stage`.
- `decisions`, `dead_ends`, `tooling_quirks`: all three present, each a list of strings, at least one non-empty.
