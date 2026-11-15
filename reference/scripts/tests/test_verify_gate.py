"""Tests for verify_gate.py (docs/intent/gates/gates-specs.md)."""

import ast
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from conftest import (
    SCRIPTS,
    commit,
    forge_trailers,
    git,
    head,
    init_repo,
    load_module,
    run_script,
)

STAGES = {
    "forge-planner": ("LID-S1", "strict"),
    "forge-qa": ("LID-S5", "full"),
    "forge-dev": ("LID-S6", "full"),
}


def forge(cwd, agent, files, *, stage=None, mode=None, input_sha=None, delete=(), message=None):
    default_stage, default_mode = STAGES[agent]
    trailers = forge_trailers(
        agent, stage or default_stage, mode or default_mode, input_sha or head(cwd)
    )
    return commit(cwd, files, message or f"{agent} change", trailers, delete=delete)


def make_state(path, anchors=None, config=None, scope=None, approvals=None, identity=None):
    state = {
        "schema_version": 1,
        "identity": {"flow": "build", "base_branch": "main", **(identity or {})},
        "config": {**(config or {})},
        "anchors": {
            "workstream_base_sha": None,
            "plan_base_sha": None,
            "last_known_head_sha": None,
            "verifier_pass_sha": None,
            "reviewer_pass_sha": None,
            "stale_shas": [],
            **(anchors or {}),
        },
        "scope_manifest": {"assigned": [], "completed": [], "remaining": [], **(scope or {})},
        "blockers": [],
        "council_decisions": [],
        "approvals": approvals or [],
    }
    path.write_text(json.dumps(state))
    return path


def check_commits(cwd, rev_range, state_path):
    return run_script(
        "verify_gate.py", "check-commits", "--range", rev_range, "--state", state_path, cwd=cwd
    )


def kinds(result):
    return [f["kind"] for f in result.json["findings"]]


def findings_of(result, kind):
    return [f for f in result.json["findings"] if f["kind"] == kind]


@pytest.fixture
def gate():
    return load_module("verify_gate")


@pytest.fixture
def base(repo):
    """repo with a human baseline file src/a.py (lines 1-5); returns its SHA."""
    sha = commit(repo, {"src/a.py": "".join(f"line{i}\n" for i in range(1, 6))}, "human base")
    return sha


# ----------------------------------------------------------------- path classes


# @spec GATE-PATH-001, GATE-PATH-002, GATE-PATH-005
@pytest.mark.parametrize(
    "path, expected",
    [
        ("tests/test_a.py", "test"),
        ("test/a.c", "test"),
        ("web/__tests__/a.js", "test"),
        ("pkg/testdata/in.txt", "test"),
        ("pkg/fixtures/x.json", "test"),
        ("pkg/a_test.go", "test"),
        ("web/a.test.ts", "test"),
        ("web/a.spec.ts", "test"),
        ("pkg/test_a.py", "test"),
        ("pkg/conftest.py", "test"),
        ("docs/test_plan.py", "test"),
        ("PLAN.md", "plan"),
        ("docs/intent/x/x-design.md", "plan"),
        ("sub/PLAN.md", "production"),
        ("src/a.py", "production"),
        ("README.md", "production"),
    ],
)
def test_default_classification(gate, path, expected):
    assert gate.classify_path(path) == expected


# @spec GATE-PATH-003
def test_config_test_patterns_replace_defaults(gate):
    patterns = ["spec/**"]
    assert gate.classify_path("spec/a_spec.rb", patterns) == "test"
    assert gate.classify_path("tests/test_a.py", patterns) == "production"
    assert gate.classify_path("docs/a.md", patterns) == "plan"


# @spec GATE-PATH-004
@pytest.mark.parametrize(
    "pattern, path, matches",
    [
        ("**/conftest.py", "conftest.py", True),
        ("**/conftest.py", "a/b/conftest.py", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/b/a.py", False),
        ("tests/**", "tests/a/b/c.py", True),
        ("tests/**", "mytests/a.py", False),
    ],
)
def test_glob_syntax(gate, pattern, path, matches):
    assert (gate.classify_path(path, [pattern]) == "test") is matches


# -------------------------------------------------------------- commits examined


# @spec GATE-COMMIT-001
def test_examines_first_parent_chain_only(repo, base, tmp_path):
    git(repo, "checkout", "-q", "-b", "side")
    forge(repo, "forge-dev", {"tests/x.py": "bad\n"})  # boundary violation, off first-parent
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "human merge")
    bad = forge(repo, "forge-dev", {"docs/y.md": "bad\n"})
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    violations = findings_of(res, "BOUNDARY_VIOLATION")
    assert [v["path"] for v in violations] == ["docs/y.md"]
    assert violations[0]["commit"] == bad


# @spec GATE-COMMIT-002
def test_human_commits_are_not_checked(repo, base, tmp_path):
    commit(repo, {"tests/a.py": "x\n", "docs/a.md": "y\n"}, "human everywhere")
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert res.json == {"ok": True, "findings": []}


# @spec GATE-COMMIT-003
def test_single_revision_range_covers_all_reachable(tmp_path):
    repo = init_repo(tmp_path / "fresh")
    forge(repo, "forge-dev", {"tests/t.py": "x\n"})
    res = check_commits(repo, "HEAD", make_state(tmp_path / "s.json"))
    assert "BOUNDARY_VIOLATION" in kinds(res)


# --------------------------------------------------------------------- trailers


# @spec GATE-TRL-001
def test_missing_trailer(repo, base, tmp_path):
    commit(repo, {"src/b.py": "x\n"}, "dev", {"Forge-Agent": "forge-dev", "Forge-Mode": "full",
                                               "Forge-Input-SHA": base})
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    missing = findings_of(res, "TRAILER_MISSING")
    assert len(missing) == 1 and "Forge-Stage" in missing[0]["detail"]
    assert res.json["ok"] is False


# @spec GATE-TRL-001
def test_repeated_trailer(repo, base, tmp_path):
    trailers = forge_trailers("forge-dev", "LID-S6", "full", base)
    trailers["Forge-Mode"] = ["full", "repair"]
    commit(repo, {"src/b.py": "x\n"}, "dev", trailers)
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert "Forge-Mode" in findings_of(res, "TRAILER_MISSING")[0]["detail"]


# @spec GATE-TRL-002
def test_non_committing_agent_is_invalid(repo, base, tmp_path):
    commit(repo, {"src/b.py": "x\n"}, "v", forge_trailers("forge-verifier", "LID-S6", "full", base))
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert "TRAILER_INVALID" in kinds(res)


# @spec GATE-TRL-003, GATE-TRL-004
@pytest.mark.parametrize(
    "agent, path, stage, mode, valid",
    [
        ("forge-planner", "docs/p.md", "LID-S4", "auto", True),
        ("forge-planner", "docs/p.md", "NAIVE-PLAN", "naive", True),
        ("forge-planner", "docs/p.md", "REPAIR", "repair", True),
        ("forge-planner", "docs/p.md", "LID-S5", "strict", False),
        ("forge-planner", "docs/p.md", "LID-S1", "full", False),
        ("forge-qa", "tests/t.py", "COLLAB-SKEL", "skeleton", True),
        ("forge-qa", "tests/t.py", "NAIVE-TESTS", "full", True),
        ("forge-qa", "tests/t.py", "LID-S6", "full", False),
        ("forge-qa", "tests/t.py", "LID-S5", "stub", False),
        ("forge-dev", "src/b.py", "COLLAB-STUB", "stub", True),
        ("forge-dev", "src/b.py", "NAIVE-IMPL", "full", True),
        ("forge-dev", "src/b.py", "LID-S1", "full", False),
        ("forge-dev", "src/b.py", "LID-S6", "skeleton", False),
    ],
)
def test_stage_and_mode_per_agent(repo, base, tmp_path, agent, path, stage, mode, valid):
    forge(repo, agent, {path: "x\n"}, stage=stage, mode=mode)
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert ("TRAILER_INVALID" in kinds(res)) is not valid


# @spec GATE-TRL-005
def test_input_sha_must_resolve(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/b.py": "x\n"}, input_sha="0" * 40)
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert "INPUT_SHA_UNKNOWN" in kinds(res)


# @spec GATE-TRL-005
def test_input_sha_must_be_ancestor(repo, base, tmp_path):
    git(repo, "checkout", "-q", "-b", "other")
    other = commit(repo, {"o.txt": "o\n"}, "elsewhere")
    git(repo, "checkout", "-q", "main")
    forge(repo, "forge-dev", {"src/b.py": "x\n"}, input_sha=other)
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert "INPUT_SHA_UNKNOWN" in kinds(res)


# @spec GATE-TRL-006
def test_forge_merge_commit(repo, base, tmp_path):
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {"src/side.py": "s\n"}, "human side")
    git(repo, "checkout", "-q", "main")
    t = forge_trailers("forge-dev", "LID-S6", "full", base)
    msg = "merge\n\n" + "\n".join(f"{k}: {v}" for k, v in t.items())
    git(repo, "merge", "-q", "--no-ff", "side", "-m", msg)
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert "FORGE_MERGE" in kinds(res)


# ------------------------------------------------------------------- boundaries


# @spec GATE-BND-002
@pytest.mark.parametrize(
    "agent, path, violates",
    [
        ("forge-planner", "docs/x.md", False),
        ("forge-planner", "PLAN.md", False),
        ("forge-planner", "src/x.py", True),
        ("forge-qa", "tests/x.py", False),
        ("forge-qa", "docs/x.md", True),
        ("forge-dev", "src/x.py", False),
        ("forge-dev", "tests/x.py", True),
        ("forge-dev", "docs/x.md", True),
    ],
)
def test_write_boundaries(repo, base, tmp_path, agent, path, violates):
    forge(repo, agent, {path: "x\n"})
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    got = [f["path"] for f in findings_of(res, "BOUNDARY_VIOLATION")]
    assert got == ([path] if violates else [])


# @spec GATE-BND-001
def test_rename_counts_old_and_new_path(repo, tmp_path):
    base = commit(repo, {"src/mover.py": "content that is long enough to be a rename\n" * 5}, "h")
    Path(repo, "tests").mkdir()
    git(repo, "mv", "src/mover.py", "tests/test_mover.py")
    commit(repo, {}, "qa moves", forge_trailers("forge-qa", "LID-S5", "full", base))
    res = check_commits(repo, f"{base}..HEAD", make_state(tmp_path / "s.json"))
    assert [f["path"] for f in findings_of(res, "BOUNDARY_VIOLATION")] == ["src/mover.py"]


# ----------------------------------------------------------- human-authored lines


# @spec GATE-HUM-001, GATE-HUM-002, GATE-HUM-005
def test_modifying_human_line_is_error(repo, base, tmp_path):
    dev = forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nCHANGED\nline4\nline5\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = check_commits(repo, f"{base}..HEAD", state)
    [f] = findings_of(res, "HUMAN_CODE_MODIFIED")
    assert f["severity"] == "error"
    assert (f["commit"], f["path"], f["lines"]) == (dev, "src/a.py", [[3, 3]])
    assert res.json["ok"] is False


# @spec GATE-HUM-005
def test_deleting_human_line_is_error(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nline4\nline5\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = check_commits(repo, f"{base}..HEAD", state)
    assert findings_of(res, "HUMAN_CODE_MODIFIED")[0]["lines"] == [[3, 3]]


# @spec GATE-HUM-002
def test_forge_authored_lines_are_free(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/new.py": "a\nb\n"})
    forge(repo, "forge-dev", {"src/new.py": "a\nB\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = check_commits(repo, f"{base}..HEAD", state)
    assert res.json == {"ok": True, "findings": []}


# @spec GATE-HUM-003, GATE-HUM-004
def test_baseline_line_in_assigned_path_after_plan_approval(repo, base, tmp_path):
    plan = head(repo)
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nCHANGED\nline4\nline5\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base, "plan_base_sha": plan},
        scope={"assigned": ["src/**"]},
    )
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED") == []


# @spec GATE-HUM-004
def test_baseline_line_in_assigned_path_before_plan_approval(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nCHANGED\nline4\nline5\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base},
        scope={"assigned": ["src/**"]},
    )
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")


# @spec GATE-HUM-004
def test_baseline_line_outside_assigned_path(repo, base, tmp_path):
    plan = head(repo)
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nCHANGED\nline4\nline5\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base, "plan_base_sha": plan},
        scope={"assigned": ["lib/**"]},
    )
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")


# @spec GATE-HUM-003, GATE-HUM-004
def test_workstream_human_line_protected_even_in_assigned_path(repo, base, tmp_path):
    commit(repo, {"src/w.py": "mine\n"}, "human during workstream")
    plan = head(repo)
    forge(repo, "forge-dev", {"src/w.py": "theirs\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base, "plan_base_sha": plan},
        scope={"assigned": ["src/**"]},
    )
    [f] = findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")
    assert f["path"] == "src/w.py"


# @spec GATE-HUM-006
def test_insertion_between_protected_lines_is_warning(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nNEW\nline3\nline4\nline5\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = check_commits(repo, f"{base}..HEAD", state)
    [f] = findings_of(res, "HUMAN_CODE_INSERTION")
    assert f["severity"] == "warning"
    assert res.json["ok"] is True
    assert res.returncode == 0


# @spec GATE-HUM-006
def test_insertion_at_end_of_file_is_not_warned(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nline3\nline4\nline5\nNEW\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    assert check_commits(repo, f"{base}..HEAD", state).json == {"ok": True, "findings": []}


# @spec GATE-HUM-007
def test_binary_human_file(repo, tmp_path):
    base = commit(repo, {"src/logo.bin": b"\x00\x01\x02human"}, "human binary")
    forge(repo, "forge-dev", {"src/logo.bin": b"\x00\x01\x02forge"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    [f] = findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")
    assert f["path"] == "src/logo.bin"


# @spec GATE-HUM-008
def test_root_forge_commit_skips_human_checks(tmp_path):
    repo = init_repo(tmp_path / "fresh", initial=False)
    commit(repo, {"src/a.py": "x\n"}, "dev root", forge_trailers("forge-dev", "LID-S6", "full", "0" * 40))
    res = check_commits(repo, "HEAD", make_state(tmp_path / "s.json"))
    assert not [k for k in kinds(res) if k.startswith("HUMAN_CODE")]


# @spec GATE-HUM-009
def test_planning_commits_skip_human_checks_but_not_boundaries(repo, tmp_path):
    base = commit(repo, {"docs/design.md": "human intent\n"}, "human docs")
    forge(repo, "forge-planner", {"docs/design.md": "planner rewrite\n"})
    stray = forge(repo, "forge-planner", {"src/stray.py": "x\n"})
    plan = head(repo)
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base, "plan_base_sha": plan})
    res = check_commits(repo, f"{base}..HEAD", state)
    assert findings_of(res, "HUMAN_CODE_MODIFIED") == []
    [b] = findings_of(res, "BOUNDARY_VIOLATION")
    assert b["commit"] == stray


# @spec GATE-HUM-009
def test_planner_repair_after_plan_approval_is_checked(repo, tmp_path):
    base = commit(repo, {"docs/design.md": "human intent\n"}, "human docs")
    plan = head(repo)
    forge(repo, "forge-planner", {"docs/design.md": "repair rewrite\n"}, stage="REPAIR", mode="repair")
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base, "plan_base_sha": plan})
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")


# -------------------------------------------------------------------- approvals

# ASSUMPTION: an approval's `ranges` maps each path to 1-based inclusive
# [start, end] pairs, e.g. {"src/a.py": [[3, 3]]}.


def approval(agent, base_sha, ranges):
    return {"id": "apr-1", "agent": agent, "paths": list(ranges), "ranges": ranges,
            "base_sha": base_sha, "approved_by": "user"}


# @spec GATE-APR-001, GATE-APR-002
def test_approval_follows_line_identity_across_shifts(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "top\nline1\nline2\nline3\nline4\nline5\n"})
    forge(repo, "forge-dev", {"src/a.py": "top\nline1\nline2\nAPPROVED\nline4\nline5\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base},
        approvals=[approval("forge-dev", base, {"src/a.py": [[3, 3]]})],
    )
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED") == []


# @spec GATE-APR-002
def test_approval_covers_only_its_lines(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nline3\nline4\nX\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base},
        approvals=[approval("forge-dev", base, {"src/a.py": [[3, 3]]})],
    )
    [f] = findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")
    assert f["lines"] == [[5, 5]]


# @spec GATE-APR-002
def test_approval_for_another_agent_does_not_cover(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "line1\nline2\nX\nline4\nline5\n"})
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base},
        approvals=[approval("forge-qa", base, {"src/a.py": [[3, 3]]})],
    )
    assert findings_of(check_commits(repo, f"{base}..HEAD", state), "HUMAN_CODE_MODIFIED")


# ---------------------------------------------------------------- publish check


@pytest.fixture
def publishable(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "feat/x")
    commit(repo, {"src/f.py": "f\n"}, "work")
    sha = head(repo)
    state = make_state(tmp_path / "s.json", anchors={"verifier_pass_sha": sha, "reviewer_pass_sha": sha})
    return repo, state


def publish(cwd, target, state):
    return run_script("verify_gate.py", "check-publish", "--target", target, "--state", state, cwd=cwd)


# @spec GATE-PUB-001
def test_publish_dirty_tree(publishable):
    repo, state = publishable
    Path(repo, "untracked.txt").write_text("u\n")
    assert "DIRTY_TREE" in kinds(publish(repo, "push", state))


# @spec GATE-PUB-002
@pytest.mark.parametrize("field", ["verifier_pass_sha", "reviewer_pass_sha"])
def test_publish_sha_mismatch(publishable, field):
    repo, state = publishable
    data = json.loads(state.read_text())
    data["anchors"][field] = None
    state.write_text(json.dumps(data))
    assert "SHA_MISMATCH" in kinds(publish(repo, "push", state))


# @spec GATE-PUB-003
def test_publish_push_without_remote_branch_passes(publishable):
    repo, state = publishable
    res = publish(repo, "push", state)
    assert res.json == {"ok": True, "findings": []} and res.returncode == 0


# @spec GATE-PUB-003
def test_publish_push_non_fast_forward(cloned_repo, tmp_path):
    clone, _ = cloned_repo
    git(clone, "checkout", "-q", "-b", "feat/x")
    commit(clone, {"a.txt": "pushed\n"}, "pushed")
    git(clone, "push", "-q", "-u", "origin", "feat/x")
    git(clone, "reset", "-q", "--hard", "HEAD~1")
    commit(clone, {"a.txt": "local\n"}, "rewritten")
    sha = head(clone)
    state = make_state(tmp_path / "s.json", anchors={"verifier_pass_sha": sha, "reviewer_pass_sha": sha})
    assert "NON_FAST_FORWARD" in kinds(publish(clone, "push", state))


# @spec GATE-PUB-004
def test_publish_merge_needs_local_base(publishable):
    repo, state = publishable
    data = json.loads(state.read_text())
    data["identity"]["base_branch"] = "no-such-branch"
    state.write_text(json.dumps(data))
    assert "NO_MERGE_TARGET" in kinds(publish(repo, "merge", state))


# @spec GATE-PUB-005
def test_publish_merge_conflict(repo, tmp_path):
    commit(repo, {"c.txt": "base\n"}, "base")
    git(repo, "checkout", "-q", "-b", "feat/x")
    commit(repo, {"c.txt": "branch\n"}, "branch side")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"c.txt": "main\n"}, "main side")
    git(repo, "checkout", "-q", "feat/x")
    sha = head(repo)
    state = make_state(tmp_path / "s.json", anchors={"verifier_pass_sha": sha, "reviewer_pass_sha": sha})
    [f] = findings_of(publish(repo, "merge", state), "MERGE_CONFLICT")
    assert "c.txt" in f["detail"]


# @spec GATE-PUB-005
def test_publish_merge_clean(publishable):
    repo, state = publishable
    assert publish(repo, "merge", state).json == {"ok": True, "findings": []}


# @spec GATE-PUB-006
def test_publish_changes_nothing(repo, tmp_path):
    commit(repo, {"c.txt": "base\n"}, "base")
    git(repo, "checkout", "-q", "-b", "feat/x")
    commit(repo, {"c.txt": "branch\n"}, "branch side")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"c.txt": "main\n"}, "main side")
    git(repo, "checkout", "-q", "feat/x")
    sha = head(repo)
    state = make_state(tmp_path / "s.json", anchors={"verifier_pass_sha": sha, "reviewer_pass_sha": sha})
    before = (git(repo, "for-each-ref"), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"),
              git(repo, "ls-files", "-s"))
    publish(repo, "merge", state)
    publish(repo, "push", state)
    after = (git(repo, "for-each-ref"), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"),
             git(repo, "ls-files", "-s"))
    assert before == after


# ------------------------------------------------------------- human-line query

# ASSUMPTION: `ranges` is a list of 1-based inclusive [start, end] pairs.


# @spec GATE-QRY-001
def test_human_lines_query(repo, tmp_path):
    base = commit(repo, {"src/q.py": "h1\nh2\nh3\n"}, "human")
    forge(repo, "forge-dev", {"src/q.py": "h1\nh2\nforge\nh3\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = run_script("verify_gate.py", "human-lines", "--path", "src/q.py", "--state", state, cwd=repo)
    assert res.json == {"ranges": [[1, 2], [4, 4]]}


# @spec GATE-QRY-001
def test_human_lines_query_applies_baseline_rule(repo, tmp_path):
    base = commit(repo, {"src/q.py": "h1\nh2\n"}, "human")
    plan = head(repo)
    state = make_state(
        tmp_path / "s.json",
        anchors={"workstream_base_sha": base, "plan_base_sha": plan},
        scope={"assigned": ["src/**"]},
    )
    res = run_script("verify_gate.py", "human-lines", "--path", "src/q.py", "--state", state, cwd=repo)
    assert res.json == {"ranges": []}


# ------------------------------------------------------------ command interface


# @spec GATE-CLI-001, GATE-CLI-002, GATE-CLI-003, GATE-CLI-004
def test_output_shape_and_exit_codes(repo, base, tmp_path):
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    ok = check_commits(repo, f"{base}..HEAD", state)
    assert ok.returncode == 0 and ok.json == {"ok": True, "findings": []}

    forge(repo, "forge-dev", {"tests/t.py": "x\n"})
    bad = check_commits(repo, f"{base}..HEAD", state)
    assert bad.returncode == 1 and bad.json["ok"] is False
    for f in bad.json["findings"]:
        assert f["severity"] == "error"
        assert {"kind", "severity", "commit", "path"} <= set(f)


# @spec GATE-CLI-004
def test_usage_error_and_outside_repo_exit_2(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    state = make_state(tmp_path / "s.json")
    assert run_script("verify_gate.py", "check-commits", "--range", "a..b", "--state", state,
                      cwd=outside).returncode == 2
    assert run_script("verify_gate.py", "bogus-command", cwd=tmp_path).returncode == 2


# @spec GATE-CLI-005
def test_importable_functions_match_commands(gate, repo, base, tmp_path, monkeypatch):
    forge(repo, "forge-dev", {"tests/t.py": "x\n"})
    state_path = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    state = json.loads(state_path.read_text())
    sha = head(repo)
    pub_state = make_state(tmp_path / "p.json", anchors={"verifier_pass_sha": sha, "reviewer_pass_sha": sha})
    monkeypatch.chdir(repo)
    # ASSUMPTION: check_commits(rev_range, state, cwd) and check_publish(target, state, cwd)
    assert gate.check_commits(f"{base}..HEAD", state, str(repo)) == check_commits(
        repo, f"{base}..HEAD", state_path).json
    assert gate.check_publish("merge", json.loads(pub_state.read_text()), str(repo)) == publish(
        repo, "merge", pub_state).json


# @spec GATE-CLI-006
def test_check_commits_changes_nothing(repo, base, tmp_path):
    forge(repo, "forge-dev", {"src/a.py": "changed\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    before = (state.read_bytes(), git(repo, "for-each-ref"), git(repo, "status", "--porcelain"))
    check_commits(repo, f"{base}..HEAD", state)
    run_script("verify_gate.py", "human-lines", "--path", "src/a.py", "--state", state, cwd=repo)
    assert (state.read_bytes(), git(repo, "for-each-ref"), git(repo, "status", "--porcelain")) == before


# @spec GATE-CLI-007
def test_stdlib_only_imports():
    tree = ast.parse((SCRIPTS / "verify_gate.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names, name


# @spec GATE-CLI-007
def test_runs_with_only_git_on_path(repo, base, tmp_path):
    bindir = tmp_path / "gitonly"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    forge(repo, "forge-dev", {"src/a.py": "changed\n"})
    state = make_state(tmp_path / "s.json", anchors={"workstream_base_sha": base})
    res = run_script("verify_gate.py", "check-commits", "--range", f"{base}..HEAD", "--state", state,
                     cwd=repo, env={"PATH": str(bindir)})
    assert res.returncode == 1 and "HUMAN_CODE_MODIFIED" in kinds(res)
