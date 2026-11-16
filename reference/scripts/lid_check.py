#!/usr/bin/env python3
"""Forge LID check: workstream spec coverage and @spec annotations, from git alone.

Design: docs/intent/spokes/verifier/verifier-design.md. Specs: docs/intent/spokes/verifier/verifier-specs.md.

    lid_check.py --state STATE_JSON

Prints one JSON object and exits 0; a usage error exits 2. Reads the committed tree at HEAD and
never modifies the repository or the state file. The router imports check(state).
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_gate  # noqa: E402

ID = r"[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}"
SPEC_LINE = re.compile(rf"- \[([ xD])\] \*\*({ID})\*\*:")
ANNOTATION = re.compile(rf"@spec\s+({ID}(?:\s*,\s*{ID})*)(?![\w-])")
SPEC_FILES = "docs/intent/**/*-specs.md"
BLAME_HEADER = re.compile(r"[0-9a-f]{40,64} \d+ \d+")


def _git(root, *args, ok=(0,)):
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace")
    if proc.returncode not in ok:
        raise verify_gate.GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _finding(kind, spec_id, detail, path=None, line=None):
    f = {"kind": kind, "id": spec_id, "detail": detail}
    if path is not None:
        f.update(path=path, line=line)
    return f


# @spec SPOKE-VER-LID-001
def _spec_lines(root):
    """[(id, marker, origin)] for every spec line at HEAD; origin is its first-parent blame commit."""
    paths = [p for p in _git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD").split("\0")
             if p and verify_gate.matches(p, SPEC_FILES)]
    specs = []
    for path in paths:
        origin = None
        for line in _git(root, "blame", "--first-parent", "--porcelain", "HEAD", "--", path).split("\n"):
            if line.startswith("\t"):
                m = SPEC_LINE.match(line[1:])
                if m:
                    specs.append((m.group(2), m.group(1), origin))
            elif BLAME_HEADER.match(line):
                origin = line.split()[0]
    return specs


# @spec SPOKE-VER-LID-003
def _annotations(root):
    """[(path, line, id)] for every @spec annotation token at HEAD."""
    out = _git(root, "grep", "-z", "-n", "-I", "-F", "-e", "@spec", "HEAD", ok=(0, 1))
    found = []
    for record in out.split("\n"):
        if not record:
            continue
        path, number, text = record.split("\0", 2)
        for m in ANNOTATION.finditer(text):
            found += [(path[len("HEAD:"):], int(number), t.strip()) for t in m.group(1).split(",")]
    return found


# @spec SPOKE-VER-LID-CLI-002, SPOKE-VER-LID-CLI-004, SPOKE-VER-LID-CLI-005, SPOKE-VER-LID-002
# @spec SPOKE-VER-LID-004, SPOKE-VER-LID-005, SPOKE-VER-LID-006, SPOKE-VER-LID-007, SPOKE-VER-LID-008
# @spec SPOKE-VER-LID-009
def check(state):
    """The result lid_check.py prints for a parsed state.json object."""
    if not isinstance(state, dict):
        return {"status": "STATE_INVALID", "detail": "state.json is not a JSON object"}
    config = state.get("config") or {}
    if config.get("repo_mode") != "LID":
        return {"status": "NOT_LID"}
    root = _git(state["identity"]["worktree_path"], "rev-parse", "--show-toplevel").strip()
    base = (state.get("anchors") or {}).get("workstream_base_sha")
    rev_range = f"{base}..HEAD" if base else "HEAD"

    is_merge = {}  # first-parent commit in range -> whether it is a merge
    for line in _git(root, "rev-list", "--first-parent", "--parents", rev_range).split("\n"):
        if line:
            sha, *parents = line.split()
            is_merge[sha] = len(parents) > 1
    changed = set(_git(root, "log", "-z", "--first-parent", "--no-merges", "--name-only", "--format=",
                       rev_range).replace("\n", "").split("\0"))

    specs = _spec_lines(root)
    defined = Counter(spec_id for spec_id, _, _ in specs)
    workstream = [(spec_id, marker) for spec_id, marker, origin in specs if is_merge.get(origin) is False]
    workstream_ids = {spec_id for spec_id, _ in workstream}

    patterns = config.get("test_patterns")
    cited, tagged, findings = set(), set(), []
    for path, number, spec_id in _annotations(root):
        cls = verify_gate.classify_path(path, patterns)
        if cls == "test":
            cited.add(spec_id)
        elif cls == "production":
            tagged.add(spec_id)
        if path in changed and spec_id not in defined:
            findings.append(_finding("UNKNOWN_ID", spec_id, f"{spec_id} is not defined by any spec line",
                                     path, number))

    for spec_id in sorted(workstream_ids):
        if defined[spec_id] > 1:
            findings.append(_finding("DUPLICATE_ID", spec_id, f"{spec_id} is defined by {defined[spec_id]} spec lines"))
    for spec_id in sorted({spec_id for spec_id, marker in workstream if marker != "D"}):
        if spec_id not in cited:
            findings.append(_finding("UNTESTED", spec_id, f"no test file cites {spec_id}"))
        if spec_id not in tagged:
            findings.append(_finding("UNTAGGED", spec_id, f"no production file tags {spec_id}"))

    merged = {spec_id for spec_id, marker, origin in specs
              if is_merge.get(origin) and marker != "D" and spec_id not in workstream_ids and spec_id not in cited}
    return {"status": "OK", "workstream_specs": sorted(workstream_ids), "merged_specs": sorted(merged),
            "findings": findings}


# @spec SPOKE-VER-LID-CLI-001, SPOKE-VER-LID-CLI-003
def main(argv=None):
    parser = argparse.ArgumentParser(prog="lid_check.py")
    parser.add_argument("--state", required=True)
    args = parser.parse_args(argv)  # exits 2 on usage errors
    try:
        with open(args.state) as f:
            state = json.load(f)
    except OSError as e:
        result = {"status": "STATE_INVALID", "detail": f"cannot read {args.state}: {e.strerror}"}
    except ValueError as e:
        result = {"status": "STATE_INVALID", "detail": f"{args.state} is not valid JSON: {e}"}
    else:
        try:
            result = check(state)
        except verify_gate.GitError as e:
            print(f"lid_check.py: {e}", file=sys.stderr)
            return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
