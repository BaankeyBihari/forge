# Forge Memory — EARS Specs

Design: `forge-memory-design.md`. Implementation: `reference/scripts/forge_memory.py` and `reference/skills/forge-memory/SKILL.md`.

Specs under *Skill instructions* describe what the skill tells agents to do. Deferred (`[D]`) until the sandbox scenario run checks them: no automated test can verify what a skill tells an agent to do. Every other spec is tested with pytest.

## Invocation

- [x] **MEM-CLI-001**: `forge_memory.py load` shall require `--state PATH` and `--agent AGENT`, accept an optional `--limit N` defaulting to `20`, and exit `2` with a message on stderr when `--state` is missing, `AGENT` is not `hub` or a Forge agent named in MEM-LOAD-001, or `N` is below `1`.
- [x] **MEM-CLI-002**: `forge_memory.py load` shall print exactly one JSON object to stdout and exit `0` for every status.
- [x] **MEM-CLI-003**: `forge_memory.py load` shall not create, change, or delete any file.

## Partitions loaded

- [x] **MEM-LOAD-001**: When `load` runs, `forge_memory.py` shall return entries from `hub` then `shared` for agent `hub`; from the agent's own partition then `shared` for `forge-planner`, `forge-qa`, `forge-dev`, `forge-verifier`, `forge-reviewer`, and `forge-scout` (partitions `planner`, `qa`, `dev`, `verifier`, `reviewer`, `scout`); and from `shared` alone for `forge-publisher`, `forge-reporter`, and `forge-council`.
- [x] **MEM-LOAD-002**: `forge_memory.py` shall read each partition from `memory/<partition>/` in the directory containing the `--state` file, treating a missing partition directory as empty.
- [x] **MEM-LOAD-003**: Within each partition, `forge_memory.py` shall sort valid entries by file name, newest first, and return at most the newest `N`.
- [x] **MEM-LOAD-004**: `forge_memory.py` shall return each entry with `partition`, `file` (its absolute path), `historical`, and the entry's `timestamp`, `sha_anchor`, `stage`, `decisions`, `dead_ends`, and `tooling_quirks`.
- [x] **MEM-LOAD-005**: `forge_memory.py` shall consider only files ending in `.json` in a partition directory.

## Entry validity

- [x] **MEM-ENTRY-001**: If an entry file's name does not match `<YYYYMMDDTHHMMSSZ>-<sha7>.json`, where `sha7` is the first seven characters of its `sha_anchor` or `0000000` when `sha_anchor` is `null`, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-002**: If an entry file is larger than 1600 bytes, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-003**: If an entry file does not parse as a JSON object, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-004**: If an entry's `timestamp` is not a string of the form `YYYY-MM-DDTHH:MM:SSZ`, or its `stage` is not a non-empty string, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-005**: If an entry's `sha_anchor` is neither a 40-character lowercase hexadecimal string nor `null`, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-006**: If any of an entry's `decisions`, `dead_ends`, or `tooling_quirks` is missing or not a list of strings, or all three are empty, then `forge_memory.py` shall report it as invalid.
- [x] **MEM-ENTRY-007**: `forge_memory.py` shall ignore keys of an entry other than `timestamp`, `sha_anchor`, `stage`, `decisions`, `dead_ends`, and `tooling_quirks`.
- [x] **MEM-ENTRY-008**: When an entry is invalid, `forge_memory.py` shall leave it out of `entries` and list it in `invalid` with its `file` and a `reason` naming the broken rule.

## Historical entries

- [x] **MEM-HIST-001**: When an entry's `sha_anchor` is in `anchors.stale_shas`, `forge_memory.py` shall return it with `historical: true`.
- [x] **MEM-HIST-002**: When an entry's `sha_anchor` is not a commit in the repository at `identity.worktree_path`, or is not an ancestor of that worktree's `HEAD`, `forge_memory.py` shall return it with `historical: true`.
- [x] **MEM-HIST-003**: `forge_memory.py` shall return an entry whose `sha_anchor` is `null` with `historical: false`.
- [x] **MEM-HIST-004**: `forge_memory.py` shall return every other valid entry with `historical: false`.

## Statuses

- [x] **MEM-OUT-001**: When entries are loaded, `forge_memory.py` shall return `status` `OK` with `entries` and `invalid`.
- [x] **MEM-OUT-002**: If the `--state` file is missing or does not parse as a JSON object, then `forge_memory.py` shall return `status` `STATE_INVALID` with a `detail` saying which.
- [x] **MEM-OUT-003**: If `identity.worktree_path` is not a git working tree, then `forge_memory.py` shall return `status` `WORKTREE_MISSING` with the path.

## Skill instructions

- [D] **MEM-SKILL-001**: The `forge-memory` skill shall tell every Forge agent to run `forge_memory.py load` with its own name and `state_path` at the start of every dispatch, before planning any work.
- [D] **MEM-SKILL-002**: The `forge-memory` skill shall tell agents to treat `historical` decisions as no longer in force and `historical` dead ends as possibly still relevant.
- [D] **MEM-SKILL-003**: The `forge-memory` skill shall tell agents with a partition to save at most one entry per dispatch, just before writing the result envelope, and only for a decision, dead end, or tooling quirk that a later dispatch would need and that the commits, specs, and `state.json` do not already record.
- [D] **MEM-SKILL-004**: The `forge-memory` skill shall tell agents to write an entry with the file-write tool into their own partition only, in the format of MEM-ENTRY-001 through MEM-ENTRY-006, with `sha_anchor` from `git rev-parse HEAD` or `null` in a repository with no commits.
- [D] **MEM-SKILL-005**: The `forge-memory` skill shall tell agents to use the next second in the file name when a file of the same name already exists.
- [D] **MEM-SKILL-006**: The `forge-memory` skill shall tell `forge-publisher`, `forge-reporter`, and `forge-council` to load `shared` and save nothing.
