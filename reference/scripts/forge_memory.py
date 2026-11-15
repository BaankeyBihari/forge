#!/usr/bin/env python3
"""Forge workstream memory loader.

Design: docs/intent/forge-memory/forge-memory-design.md.
Specs: docs/intent/forge-memory/forge-memory-specs.md.

    forge_memory.py load --state PATH --agent AGENT [--limit N]

Prints one JSON object and exits 0 for every status; usage errors exit 2.
Reads only: entries are written by agents with their file-write tool.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

OWN = {"forge-planner": "planner", "forge-qa": "qa", "forge-dev": "dev", "forge-verifier": "verifier",
       "forge-reviewer": "reviewer", "forge-scout": "scout"}
PARTITIONS = {"hub": ["hub", "shared"], **{a: [p, "shared"] for a, p in OWN.items()},
              "forge-publisher": ["shared"], "forge-reporter": ["shared"], "forge-council": ["shared"]}
MAX_BYTES = 1600
NAME = re.compile(r"^\d{8}T\d{6}Z-([0-9a-f]{7})\.json$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA = re.compile(r"^[0-9a-f]{40}$")
LISTS = ("decisions", "dead_ends", "tooling_quirks")
FIELDS = ("timestamp", "sha_anchor", "stage", *LISTS)


def git_ok(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


# @spec MEM-ENTRY-001, MEM-ENTRY-002, MEM-ENTRY-003, MEM-ENTRY-004, MEM-ENTRY-005, MEM-ENTRY-006
# @spec MEM-ENTRY-007
def parse_entry(path):
    """Return (entry, None) with only the six known fields, or (None, reason)."""
    if path.stat().st_size > MAX_BYTES:
        return None, f"size: larger than {MAX_BYTES} bytes"
    try:
        body = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return None, f"not valid JSON: {e}"
    if not isinstance(body, dict):
        return None, "not a JSON object"
    if not isinstance(body.get("timestamp"), str) or not TIMESTAMP.match(body["timestamp"]):
        return None, "timestamp must be a string YYYY-MM-DDTHH:MM:SSZ"
    if not isinstance(body.get("stage"), str) or not body["stage"]:
        return None, "stage must be a non-empty string"
    sha = body.get("sha_anchor", 0)
    if sha is not None and not (isinstance(sha, str) and SHA.match(sha)):
        return None, "sha_anchor must be a 40-character lowercase hex SHA or null"
    for key in LISTS:
        value = body.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None, f"{key} must be a list of strings"
    if not any(body[key] for key in LISTS):
        return None, "decisions, dead_ends and tooling_quirks are all empty"
    m = NAME.match(path.name)
    if not m or m.group(1) != (sha[:7] if sha else "0000000"):
        return None, "file name must be <YYYYMMDDTHHMMSSZ>-<first 7 of sha_anchor, or 0000000>.json"
    return {k: body[k] for k in FIELDS}, None


# @spec MEM-HIST-001, MEM-HIST-002, MEM-HIST-003, MEM-HIST-004
def historical(sha, stale, worktree):
    if sha is None:
        return False
    if sha in stale:
        return True
    return not (git_ok(worktree, "cat-file", "-e", f"{sha}^{{commit}}")
                and git_ok(worktree, "merge-base", "--is-ancestor", sha, "HEAD"))


# @spec MEM-LOAD-001, MEM-LOAD-002, MEM-LOAD-003, MEM-LOAD-004, MEM-LOAD-005
# @spec MEM-OUT-001, MEM-OUT-002, MEM-OUT-003, MEM-CLI-003, MEM-ENTRY-008
def load(state_path, agent, limit):
    state_path = Path(state_path).absolute()
    try:
        state = json.loads(state_path.read_text())
    except FileNotFoundError:
        return {"status": "STATE_INVALID", "detail": f"state file missing: {state_path}"}
    except (OSError, ValueError) as e:
        return {"status": "STATE_INVALID", "detail": f"state file does not parse: {e}"}
    if not isinstance(state, dict):
        return {"status": "STATE_INVALID", "detail": "state file is not a JSON object"}
    worktree = (state.get("identity") or {}).get("worktree_path")
    if not worktree or not Path(worktree).is_dir() or not git_ok(worktree, "rev-parse", "--is-inside-work-tree"):
        return {"status": "WORKTREE_MISSING", "worktree_path": worktree}
    stale = set((state.get("anchors") or {}).get("stale_shas") or [])

    entries, invalid = [], []
    for part in PARTITIONS[agent]:
        directory = state_path.parent / "memory" / part
        files = sorted(directory.glob("*.json"), reverse=True) if directory.is_dir() else []
        valid = []
        for path in files:
            entry, reason = parse_entry(path)
            if reason:
                invalid.append({"file": str(path), "reason": reason})
            else:
                valid.append((path, entry))
        for path, entry in valid[:limit]:
            entries.append({"partition": part, "file": str(path),
                            "historical": historical(entry["sha_anchor"], stale, worktree), **entry})
    return {"status": "OK", "entries": entries, "invalid": invalid}


# @spec MEM-CLI-001, MEM-CLI-002
def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_memory.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("load")
    p.add_argument("--state", required=True)
    p.add_argument("--agent", required=True, choices=list(PARTITIONS))
    p.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    print(json.dumps(load(args.state, args.agent, args.limit)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
