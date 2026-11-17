#!/usr/bin/env python3
"""Forge provenance gate: checks Forge commits and publish readiness from git alone.

Design: docs/intent/gates/gates-design.md. Specs: docs/intent/gates/gates-specs.md.

    verify_gate.py check-commits --range A..B --state STATE_JSON
    verify_gate.py check-publish --target push|merge --state STATE_JSON
    verify_gate.py human-lines --path PATH --agent AGENT --state STATE_JSON

Never modifies the repository or the state file.
"""

import argparse
import json
import re
import subprocess
import sys
from functools import lru_cache

DEFAULT_TEST_PATTERNS = [
    "tests/**", "test/**", "**/__tests__/**", "**/testdata/**", "**/fixtures/**",
    "**/*_test.*", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/conftest.py",
]

TRAILERS = ["Forge-Agent", "Forge-Stage", "Forge-Mode", "Forge-Input-SHA"]
STAGES = {
    "forge-planner": {"LID-S1", "LID-S2", "LID-S3", "LID-S4", "NAIVE-PLAN", "REPAIR"},
    "forge-qa": {"COLLAB-SKEL", "LID-S5", "NAIVE-TESTS", "REPAIR"},
    "forge-dev": {"COLLAB-STUB", "LID-S6", "NAIVE-IMPL", "REPAIR"},
}
MODES = {
    "forge-planner": {"strict", "auto", "naive", "repair"},
    "forge-qa": {"skeleton", "full", "repair"},
    "forge-dev": {"stub", "full", "repair"},
}
BOUNDARY = {"forge-planner": "plan", "forge-qa": "test", "forge-dev": "production"}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


class GitError(Exception):
    pass


# ------------------------------------------------------------------ path classes


@lru_cache(maxsize=None)
def _pattern_regex(pattern):
    parts = pattern.split("/")
    out = ""
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out += ".*" if last else "(?:[^/]+/)*"
            continue
        out += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part)
        if not last:
            out += "/"
    return re.compile(out)


def matches(path, pattern):
    return _pattern_regex(pattern).fullmatch(path) is not None


# @spec GATE-PATH-001, GATE-PATH-002, GATE-PATH-003, GATE-PATH-004, GATE-PATH-005
def classify_path(path, test_patterns=None):
    """Return "test", "plan", or "production" for a repository-relative path."""
    patterns = DEFAULT_TEST_PATTERNS if test_patterns is None else test_patterns
    if any(matches(path, p) for p in patterns):
        return "test"
    if path == "PLAN.md" or path.startswith("docs/"):
        return "plan"
    return "production"


# ---------------------------------------------------------------------------- git


class Repo:
    def __init__(self, cwd):
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
        if top.returncode != 0:
            raise GitError("not a git repository")
        self.root = top.stdout.strip()
        self._trailers = {}
        self._ancestor = {}

    def run(self, *args, check=True):
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc

    def out(self, *args):
        return self.run(*args).stdout

    def resolves(self, rev):
        return self.run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False).returncode == 0

    def is_ancestor(self, a, b):
        if a is None or b is None:
            return False
        key = (a, b)
        if key not in self._ancestor:
            self._ancestor[key] = self.run("merge-base", "--is-ancestor", a, b, check=False).returncode == 0
        return self._ancestor[key]

    def trailers(self, commit):
        """{trailer: [values]} for the four Forge trailers."""
        if commit not in self._trailers:
            fmt = "%x1e".join(f"%(trailers:key={k},valueonly,separator=%x1f)" for k in TRAILERS)
            fields = self.out("show", "-s", f"--format={fmt}", commit).rstrip("\n").split("\x1e")
            self._trailers[commit] = {
                k: [v.strip() for v in f.split("\x1f") if v.strip()] for k, f in zip(TRAILERS, fields)
            }
        return self._trailers[commit]

    def is_forge(self, commit):
        return bool(self.trailers(commit)["Forge-Agent"])

    def parents(self, commit):
        return self.out("rev-list", "--parents", "-n", "1", commit).split()[1:]

    def blame(self, rev, path, start, end):
        """[(origin_commit, origin_line, final_line)] for lines start..end of path at rev."""
        proc = self.run("blame", "--porcelain", "-L", f"{start},{end}", rev, "--", path, check=False)
        if proc.returncode != 0:
            return []
        result = []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40,64}", parts[0]):
                result.append((parts[0], int(parts[1]), int(parts[2])))
        return result


def _ranges(numbers):
    out = []
    for n in sorted(set(numbers)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def _finding(kind, severity="error", **fields):
    return {"kind": kind, "severity": severity, **{k: v for k, v in fields.items() if v is not None}}


def _result(findings):
    return {"ok": not any(f["severity"] == "error" for f in findings), "findings": findings}


# ------------------------------------------------------------- human-line rules


class Protection:
    """Decides whether a human-authored line is protected, and whether an approval covers it."""

    def __init__(self, repo, state):
        self.repo = repo
        anchors = state.get("anchors") or {}
        self.base = anchors.get("workstream_base_sha")
        self.plan = anchors.get("plan_base_sha")
        self.assigned = (state.get("scope_manifest") or {}).get("assigned") or []
        self.approvals = state.get("approvals") or []
        self._identities = {}

    def protected(self, origin, path):
        if self.repo.is_forge(origin):
            return False
        baseline = self.base is not None and self.repo.is_ancestor(origin, self.base)
        if baseline and self.plan and any(matches(path, p) for p in self.assigned):
            return False
        return True

    def _approval_identities(self, index, path):
        key = (index, path)
        if key not in self._identities:
            approval = self.approvals[index]
            ranges = (approval.get("ranges") or {}).get(path, [])
            ids = set()
            for start, end in ranges:
                ids |= {(c, o) for c, o, _ in self.repo.blame(approval["base_sha"], path, start, end)}
            self._identities[key] = ids
        return self._identities[key]

    def covered(self, agent, path, identity):
        for i, approval in enumerate(self.approvals):
            if approval.get("agent") == agent and path in (approval.get("paths") or []):
                if identity is None or identity in self._approval_identities(i, path):
                    return True
        return False


# @spec GATE-HUM-006, GATE-HUM-007
def _human_findings(repo, rules, commit, parent, agent, path):
    diff = repo.out("diff", "-U0", "--no-renames", "--no-color", parent, commit, "--", path)
    findings = []
    if re.search(r"^Binary files ", diff, re.M):
        if not repo.run("cat-file", "-e", f"{parent}:{path}", check=False).returncode == 0:
            return []  # added: nothing human to change
        last = repo.out("log", "-1", "--format=%H", parent, "--", path).strip()
        if last and rules.protected(last, path) and not rules.covered(agent, path, None):
            findings.append(_finding("HUMAN_CODE_MODIFIED", commit=commit, path=path,
                                     detail="binary file last changed by a human commit"))
        return findings

    modified = []
    for line in diff.splitlines():
        m = HUNK.match(line)
        if not m:
            continue
        start, count = int(m.group(1)), 1 if m.group(2) is None else int(m.group(2))
        if count:
            for origin, orig_line, final in repo.blame(parent, path, start, start + count - 1):
                if rules.protected(origin, path) and not rules.covered(agent, path, (origin, orig_line)):
                    modified.append(final)
        elif start >= 1:
            around = repo.blame(parent, path, start, start + 1)
            if len(around) == 2 and all(rules.protected(o, path) for o, _, _ in around):
                findings.append(_finding("HUMAN_CODE_INSERTION", "warning", commit=commit, path=path,
                                         lines=[[start, start + 1]],
                                         detail="lines inserted between protected human-authored lines"))
    if modified:
        findings.insert(0, _finding("HUMAN_CODE_MODIFIED", commit=commit, path=path, lines=_ranges(modified),
                                    detail="protected human-authored lines modified or deleted without approval"))
    return findings


# ---------------------------------------------------------------- commit checks

SPEC_FILES = "docs/intent/**/*-specs.md"
SPEC_LINE = re.compile(r"- \[[ xD]\] \*\*[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}\*\*:")
FLIPS = {"- [ ]": "- [x]", "- [x]": "- [ ]"}


# @spec GATE-BND-003
def _marker_only(repo, parent, commit, path):
    """Whether every hunk of the commit's change to path only flips spec markers between [ ] and [x]."""
    if not repo.run("cat-file", "-e", f"{parent}:{path}", check=False).returncode == 0:
        return False
    if not repo.run("cat-file", "-e", f"{commit}:{path}", check=False).returncode == 0:
        return False
    hunks = []
    for line in repo.out("diff", "-U0", "--no-renames", "--no-color", parent, commit, "--", path).splitlines():
        if HUNK.match(line):
            hunks.append(([], []))
        elif hunks and line.startswith("-") and not line.startswith("---"):
            hunks[-1][0].append(line[1:])
        elif hunks and line.startswith("+") and not line.startswith("+++"):
            hunks[-1][1].append(line[1:])
    if not hunks:
        return False
    for removed, added in hunks:
        if len(removed) != len(added):
            return False
        for old, new in zip(removed, added):
            head = old[:5]
            if not SPEC_LINE.match(old) or head not in FLIPS or new != FLIPS[head] + old[5:]:
                return False
    return True


# @spec GATE-TRL-002, GATE-TRL-003, GATE-TRL-004, GATE-TRL-005
def _check_trailers(repo, commit, trailers):
    findings = []
    for key in TRAILERS:
        if len(trailers[key]) != 1:
            findings.append(_finding("TRAILER_MISSING", commit=commit,
                                     detail=f"{key} appears {len(trailers[key])} times; exactly one required"))
    agent = trailers["Forge-Agent"][0] if len(trailers["Forge-Agent"]) == 1 else None
    if agent is None:
        return findings, None
    if agent not in BOUNDARY:
        findings.append(_finding("TRAILER_INVALID", commit=commit, detail=f"Forge-Agent {agent} may not commit"))
        return findings, None
    for key, allowed in (("Forge-Stage", STAGES[agent]), ("Forge-Mode", MODES[agent])):
        if len(trailers[key]) == 1 and trailers[key][0] not in allowed:
            findings.append(_finding("TRAILER_INVALID", commit=commit,
                                     detail=f"{key} {trailers[key][0]} is not allowed for {agent}"))
    if len(trailers["Forge-Input-SHA"]) == 1:
        sha = trailers["Forge-Input-SHA"][0]
        if not repo.resolves(sha) or not repo.is_ancestor(sha, commit):
            findings.append(_finding("INPUT_SHA_UNKNOWN", commit=commit,
                                     detail=f"Forge-Input-SHA {sha} is not an ancestor of the commit"))
    return findings, agent


# @spec GATE-COMMIT-001, GATE-COMMIT-002, GATE-COMMIT-003, GATE-TRL-001, GATE-TRL-006, GATE-BND-001, GATE-BND-002
# @spec GATE-HUM-001, GATE-HUM-005, GATE-HUM-008, GATE-HUM-009, GATE-HUM-010, GATE-APR-001, GATE-APR-002
# @spec GATE-CLI-005, GATE-HUM-011
def check_commits(rev_range, state, cwd="."):
    """Check every Forge commit on the first-parent chain of rev_range ("A..B" or a single revision)."""
    repo = Repo(cwd)
    rules = Protection(repo, state)
    patterns = (state.get("config") or {}).get("test_patterns")
    commits = repo.out("rev-list", "--first-parent", "--reverse", rev_range).split()
    findings = []
    for commit in commits:
        trailers = repo.trailers(commit)
        if not trailers["Forge-Agent"]:
            continue  # human commit
        found, agent = _check_trailers(repo, commit, trailers)
        findings += found
        parents = repo.parents(commit)
        if len(parents) > 1:
            findings.append(_finding("FORGE_MERGE", commit=commit, detail="Forge commits may not be merges"))
            continue
        if parents:
            paths = repo.out("diff", "--no-renames", "--name-only", parents[0], commit).split("\n")
        else:
            paths = repo.out("diff-tree", "--root", "-r", "--no-renames", "--no-commit-id", "--name-only",
                             commit).split("\n")
        paths = [p for p in paths if p]
        boundary_agent = agent or trailers["Forge-Agent"][0]
        marker_paths = set()
        if parents and boundary_agent in ("forge-qa", "forge-dev") and trailers["Forge-Mode"][-1:] in (["full"], ["repair"]):
            marker_paths = {p for p in paths if matches(p, SPEC_FILES) and _marker_only(repo, parents[0], commit, p)}
        if boundary_agent in BOUNDARY:
            for path in paths:
                if path not in marker_paths and classify_path(path, patterns) != BOUNDARY[boundary_agent]:
                    findings.append(_finding("BOUNDARY_VIOLATION", commit=commit, path=path,
                                             detail=f"{boundary_agent} may change {BOUNDARY[boundary_agent]} paths only"))
        if not parents or boundary_agent == "forge-planner":
            continue  # planner commits get no human-line checks
        if rules.plan and repo.is_ancestor(commit, rules.plan):
            continue  # planning commit approved with the plan
        for path in paths:
            if path not in marker_paths:  # marker flips skip the human-line checks
                findings += _human_findings(repo, rules, commit, parents[0], boundary_agent, path)
    return _result(findings)


# ---------------------------------------------------------------- publish check


# @spec GATE-PUB-001, GATE-PUB-002, GATE-PUB-003, GATE-PUB-004, GATE-PUB-005, GATE-PUB-006
def check_publish(target, state, cwd="."):
    repo = Repo(cwd)
    findings = []
    if repo.out("status", "--porcelain").strip():
        findings.append(_finding("DIRTY_TREE", detail="uncommitted changes"))
    head = repo.out("rev-parse", "HEAD").strip()
    anchors = state.get("anchors") or {}
    for key in ("verifier_pass_sha", "reviewer_pass_sha"):
        if anchors.get(key) != head:
            findings.append(_finding("SHA_MISMATCH", detail=f"{key} is {anchors.get(key)}, HEAD is {head}"))
    if target == "push":
        upstream = repo.run("rev-parse", "--verify", "--quiet", "@{upstream}", check=False)
        if upstream.returncode != 0:
            branch = repo.out("rev-parse", "--abbrev-ref", "HEAD").strip()
            upstream = repo.run("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}", check=False)
        if upstream.returncode == 0 and not repo.is_ancestor(upstream.stdout.strip(), head):
            findings.append(_finding("NON_FAST_FORWARD", detail="the remote branch is not an ancestor of HEAD"))
    else:
        base = (state.get("identity") or {}).get("base_branch")
        if not base or not repo.resolves(f"refs/heads/{base}"):
            findings.append(_finding("NO_MERGE_TARGET", detail=f"no local branch {base}"))
        else:
            merge = repo.run("merge-tree", "--write-tree", "--name-only", "--no-messages", base, head, check=False)
            if merge.returncode == 1:
                conflicted = [p for p in merge.stdout.splitlines()[1:] if p]
                findings.append(_finding("MERGE_CONFLICT", detail="conflicts in: " + ", ".join(conflicted)))
            elif merge.returncode != 0:
                raise GitError(merge.stderr.strip())
    return _result(findings)


# ------------------------------------------------------------- human-line query


# @spec GATE-QRY-001, GATE-QRY-002, GATE-HUM-002, GATE-HUM-003, GATE-HUM-004
def human_lines(path, state, agent, cwd="."):
    if agent == "forge-planner":
        return {"ranges": []}
    repo = Repo(cwd)
    rules = Protection(repo, state)
    count = len(repo.out("show", f"HEAD:{path}").splitlines())
    blame = repo.blame("HEAD", path, 1, count) if count else []
    lines = [final for origin, orig_line, final in blame
             if rules.protected(origin, path) and not rules.covered(agent, path, (origin, orig_line))]
    return {"ranges": _ranges(lines)}


# --------------------------------------------------------------------------- CLI


# @spec GATE-CLI-001, GATE-CLI-002, GATE-CLI-003, GATE-CLI-004, GATE-CLI-006, GATE-CLI-007, GATE-QRY-003
def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-commits")
    p.add_argument("--range", required=True)
    p.add_argument("--state", required=True)
    p = sub.add_parser("check-publish")
    p.add_argument("--target", required=True, choices=["push", "merge"])
    p.add_argument("--state", required=True)
    p = sub.add_parser("human-lines")
    p.add_argument("--path", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--state", required=True)
    args = parser.parse_args(argv)  # exits 2 on usage errors
    try:
        with open(args.state) as f:
            state = json.load(f)
        if args.command == "check-commits":
            result = check_commits(args.range, state)
        elif args.command == "check-publish":
            result = check_publish(args.target, state)
        else:
            print(json.dumps(human_lines(args.path, state, args.agent)))
            return 0
    except (GitError, OSError, ValueError) as e:
        print(f"verify_gate.py: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
