"""Tests for forge_spoke.py (docs/intent/spokes/protocol/protocol-specs.md).

Each test builds a minimal state.json by hand in tmp_path/ws, with envelopes/ and
council/ beside it and identity.worktree_path pointing at the `repo` fixture, and
places one outstanding dispatch in lifecycle.dispatched and lifecycle.invoke.

ASSUMPTIONS about shapes the specs leave open:
- INVALID's `problems` is a list with one entry per problem found.
- `scope` lists each pattern's `files` and each commit's `paths` in any order.
"""

import ast
import copy
import json
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
    read_state,
    run_script,
    write_state,
)

DISPATCH = "d-0001"
BUILDERS = ["forge-planner", "forge-qa", "forge-dev"]

# agent -> (phase, mode, stage) of a typical dispatch
ROLES = {
    "forge-planner": ("PLAN_S1", "strict", "LID-S1"),
    "forge-qa": ("QA_FULL", "full", "LID-S5"),
    "forge-dev": ("DEV_FULL", "full", "LID-S6"),
    "forge-verifier": ("VERIFY_FINAL", "VERIFY_FINAL", None),
    "forge-reviewer": ("REVIEW_INTERNAL", "internal", None),
    "forge-scout": ("RECON", "PRE_PLAN", None),
    "forge-publisher": ("PUBLISH", "push", None),
    "forge-reporter": ("REPORT", "draft", None),
    "forge-council": ("COUNCIL", "CONCERNS", None),
}

# A path inside each builder's write boundary (default test patterns)
IN_BOUNDARY = {
    "forge-planner": "docs/plan.md",
    "forge-qa": "tests/test_a.py",
    "forge-dev": "src/a.py",
}


class Workstream:
    """A hand-built workstream state directory beside a worktree repository."""

    def __init__(self, sd, repo):
        self.sd = sd
        self.repo = repo
        self.sp = sd / "state.json"
        (sd / "envelopes").mkdir(parents=True)
        (sd / "council").mkdir()
        self.state = None

    def dispatch(self, agent="forge-dev", phase=None, mode=None, base_sha=None, config=None,
                 anchors=None, scope=None, identity=None, dispatched=True):
        d_phase, d_mode, stage = ROLES[agent]
        phase, mode = phase or d_phase, mode or d_mode
        if agent == "forge-planner" and phase.startswith("PLAN_S"):
            stage = f"LID-S{phase[-1]}"
        if mode == "repair":
            stage = "REPAIR"
        if mode == "naive":
            stage = "NAIVE-PLAN"
        base = base_sha or head(self.repo)
        invoke = {"dispatch_id": DISPATCH, "agent": agent, "mode": mode, "state_path": str(self.sp),
                  "base_sha": base, "assigned_scope": [], "context_refs": [], "notes": None}
        if agent in BUILDERS:
            invoke["stage"] = stage
        blockers, queue = [], []
        if phase == "COUNCIL":
            blockers = [{"id": "blk-1", "kind": mode, "agent": "forge-planner", "phase": "PLAN_S1",
                         "details": "concern"}]
            invoke["panel"] = ["pm", "architect", "critic"]
        if phase == "REPAIR":
            queue = [{"agent": agent, "notes": "fix", "context_refs": [], "blocker_id": "blk-1"}]
        self.state = {
            "schema_version": 1,
            "identity": {"org": "local", "repo": "project", "workstream": "feat-demo", "flow": "build",
                         "branch": "main", "base_branch": "main", "issue": None, "pr": None,
                         "parent_workstream": None, "git_common_dir": str(self.repo / ".git"),
                         "worktree_path": str(self.repo), **(identity or {})},
            "config": {"mode": "delivery" if mode == "auto" else "collab", "repo_mode": "LID",
                       **(config or {})},
            "anchors": {"workstream_base_sha": base, "plan_base_sha": None, "last_known_head_sha": base,
                        "verifier_pass_sha": None, "reviewer_pass_sha": None, "stale_shas": [],
                        **(anchors or {})},
            "scope_manifest": {"assigned": [], "completed": [], "remaining": [], **(scope or {})},
            "blockers": blockers,
            "council_decisions": [],
            "approvals": [],
            "lifecycle": {
                "current_phase": phase, "pass": None,
                "dispatched": ({"dispatch_id": DISPATCH, "agent": agent, "mode": mode, "base_sha": base}
                               if dispatched else None),
                "invoke": invoke, "awaiting": None, "resume_phase": None, "after_reconcile": None,
                "repair_queue": queue, "attempts": {phase: 1}, "blocker_counts": {},
                "last_agent": None, "last_status": None, "report_input": None,
            },
        }
        write_state(self.sp, self.state)
        return self.state

    @property
    def envelope_path(self):
        return self.sd / "envelopes" / f"{DISPATCH}.json"

    def run(self, command, *args, dispatch=DISPATCH, cwd=None, env=None):
        extra = [] if command == "scope" else ["--dispatch", dispatch]
        return run_script("forge_spoke.py", command, "--state", self.sp, *extra, *args,
                          cwd=cwd or self.repo, env=env)

    def report(self, command, *args, **kw):
        res = self.run(command, *args, **kw)
        assert res.returncode == 0, res.stderr
        return res.json

    def commit(self, message="Add work\n\nWhy it changed.\n", *args, **kw):
        msg = self.sd / "msg.txt"
        msg.write_text(message)
        return self.report("commit", "--message-file", msg, *args, **kw)

    def finish(self, envelope, **kw):
        text = envelope if isinstance(envelope, str) else json.dumps(envelope)
        self.envelope_path.write_text(text)
        return self.report("finish", **kw)

    def envelope(self):
        return json.loads(self.envelope_path.read_text())


@pytest.fixture
def ws(tmp_path, repo):
    return Workstream(tmp_path / "ws", repo)


def stage(repo, files):
    for rel, content in files.items():
        path = Path(repo, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", rel)


def trailer(repo, sha, key):
    return git(repo, "show", "-s", f"--format=%(trailers:key={key},valueonly)", sha).splitlines()


def staged(repo):
    return git(repo, "diff", "--cached", "--name-only").splitlines()


def snapshot(ws):
    files = {}
    for root in (ws.sd, ws.repo):
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".git" not in p.relative_to(root).parts:
                files[str(p)] = p.read_bytes()
    return (files, git(ws.repo, "for-each-ref"), git(ws.repo, "status", "--porcelain"),
            git(ws.repo, "ls-files", "-s"), head(ws.repo))


def spoke_source():
    path = SCRIPTS / "forge_spoke.py"
    assert path.is_file(), "forge_spoke.py is not implemented yet"
    return path.read_text()


def imported_modules(source):
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.append(node.module)
    return [n.split(".")[0] for n in names]


def ok(summary="done", **fields):
    return {"summary": summary, **fields}


# ------------------------------------------------------------------ invocation CLI


# @spec SPOKE-PROTO-CLI-001
@pytest.mark.parametrize("command, expected", [
    ("invocation", "OK"),
    ("commit", "NOTHING_STAGED"),
    ("finish", "ENVELOPE_MISSING"),
    ("scope", "OK"),
])
def test_subcommands_print_one_json_object_and_exit_zero(ws, command, expected):
    ws.dispatch("forge-dev")
    args = ["--message-file", ws.sd / "msg.txt"] if command == "commit" else []
    (ws.sd / "msg.txt").write_text("Add work\n")
    res = ws.run(command, *args)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)  # raises on anything but exactly one JSON value
    assert isinstance(out, dict) and out["status"] == expected


# @spec SPOKE-PROTO-CLI-002
@pytest.mark.parametrize("args", [
    ["bogus"],
    [],
    ["invocation", "--state", "{sp}"],
    ["commit", "--state", "{sp}", "--dispatch", DISPATCH],
    ["finish", "--dispatch", DISPATCH],
    ["scope"],
    ["finish", "--state", "{sp}", "--dispatch", DISPATCH, "--bogus-flag"],
], ids=["unknown", "none", "inv-no-dispatch", "commit-no-message", "finish-no-state",
        "scope-no-state", "unknown-flag"])
def test_usage_errors_exit_2(ws, args):
    ws.dispatch("forge-dev")
    res = run_script("forge_spoke.py", *[a.replace("{sp}", str(ws.sp)) for a in args], cwd=ws.repo)
    assert res.returncode == 2
    assert res.stderr.strip()


# @spec SPOKE-PROTO-CLI-003
@pytest.mark.parametrize("command", ["invocation", "commit", "finish", "scope"])
def test_missing_state_is_state_invalid(ws, command):
    (ws.sd / "msg.txt").write_text("Add work\n")
    args = ["--message-file", ws.sd / "msg.txt"] if command == "commit" else []
    out = ws.report(command, *args)
    assert out["status"] == "STATE_INVALID"
    assert isinstance(out["detail"], str) and out["detail"]


# @spec SPOKE-PROTO-CLI-003
@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"'])
def test_unparsable_or_non_object_state_is_state_invalid(ws, content):
    missing = ws.report("invocation")["detail"]
    ws.sp.write_text(content)
    out = ws.report("invocation")
    assert out["status"] == "STATE_INVALID"
    assert out["detail"] and out["detail"] != missing  # says which: missing vs not a JSON object


# @spec SPOKE-PROTO-CLI-004
@pytest.mark.parametrize("command", ["invocation", "commit", "finish"])
def test_other_dispatch_id_is_not_dispatched(ws, command):
    ws.dispatch("forge-dev")
    (ws.sd / "msg.txt").write_text("Add work\n")
    args = ["--message-file", ws.sd / "msg.txt"] if command == "commit" else []
    assert ws.report(command, *args, dispatch="d-stale")["status"] == "NOT_DISPATCHED"


# @spec SPOKE-PROTO-CLI-004
@pytest.mark.parametrize("command", ["invocation", "commit", "finish"])
def test_no_outstanding_dispatch_is_not_dispatched(ws, command):
    ws.dispatch("forge-dev", dispatched=False)
    (ws.sd / "msg.txt").write_text("Add work\n")
    args = ["--message-file", ws.sd / "msg.txt"] if command == "commit" else []
    assert ws.report(command, *args)["status"] == "NOT_DISPATCHED"


# @spec SPOKE-PROTO-CLI-004
def test_not_dispatched_commit_changes_nothing(ws):
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n"})
    (ws.sd / "msg.txt").write_text("Add work\n\nWhy it changed.\n")  # the helper rewrites it unchanged
    before = snapshot(ws)
    assert ws.commit(dispatch="d-stale")["status"] == "NOT_DISPATCHED"
    assert snapshot(ws) == before


# @spec SPOKE-PROTO-CLI-004
def test_not_dispatched_finish_changes_nothing(ws):
    ws.dispatch("forge-verifier")
    ws.envelope_path.write_text(json.dumps(ok(status="PASS")))
    before = snapshot(ws)
    assert ws.report("finish", dispatch="d-stale")["status"] == "NOT_DISPATCHED"
    assert snapshot(ws) == before


# @spec SPOKE-PROTO-CLI-005
def test_commit_runs_git_in_worktree_path(ws, tmp_path):
    other = init_repo(tmp_path / "other")
    stage(other, {"src/other.py": "o\n"})
    other_head = head(other)
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n"})
    out = ws.commit(cwd=other)
    assert out["status"] == "COMMITTED"
    assert out["sha"] == head(ws.repo)
    assert head(other) == other_head and staged(other) == ["src/other.py"]


# @spec SPOKE-PROTO-CLI-005
def test_finish_reads_head_from_worktree_path(ws, tmp_path):
    ws.dispatch("forge-dev")
    commit(ws.repo, {"src/a.py": "a\n"}, "dev", forge_trailers("forge-dev", "LID-S6", "full", head(ws.repo)))
    outside = tmp_path / "outside"
    outside.mkdir()
    assert ws.finish(ok(status="IMPLEMENTATION_COMPLETE"), cwd=outside)["status"] == "FINISHED"
    assert ws.envelope()["head_sha"] == head(ws.repo)


# @spec SPOKE-PROTO-CLI-006
def test_stdlib_only_imports():
    local = {p.stem for p in SCRIPTS.glob("*.py")}
    for name in imported_modules(spoke_source()):
        assert name in sys.stdlib_module_names or name in local, name


# @spec SPOKE-PROTO-CLI-006
def test_runs_with_only_git_on_path(ws, tmp_path):
    bindir = tmp_path / "gitonly"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n"})
    assert ws.commit(env={"PATH": str(bindir)})["status"] == "COMMITTED"


# ------------------------------------------------------------------ invocation


# @spec SPOKE-PROTO-INV-001
def test_invocation_returns_invoke_phase_and_state(ws):
    state = ws.dispatch("forge-qa")
    out = ws.report("invocation")
    assert out["status"] == "OK"
    assert out["invoke"] == state["lifecycle"]["invoke"]
    assert out["phase"] == "QA_FULL"
    assert out["state"] == read_state(ws.sp)


# @spec SPOKE-PROTO-INV-002
def test_invocation_changes_no_file(ws):
    ws.dispatch("forge-dev")
    (ws.repo / "src").mkdir()
    (ws.repo / "src" / "wip.py").write_text("w\n")
    before = snapshot(ws)
    ws.report("invocation")
    assert snapshot(ws) == before


# ------------------------------------------------------------------ commit


# @spec SPOKE-PROTO-COMMIT-001
@pytest.mark.parametrize("agent", BUILDERS)
def test_commit_appends_trailers_from_invocation(ws, agent):
    state = ws.dispatch(agent)
    base = state["lifecycle"]["invoke"]["base_sha"]
    commit(ws.repo, {"other.txt": "human\n"}, "human commit while the spoke ran")
    stage(ws.repo, {IN_BOUNDARY[agent]: "x\n"})
    out = ws.commit("Add work\n\nWhy it changed.\n")
    assert out["status"] == "COMMITTED"
    assert out["sha"] == head(ws.repo)
    assert git(ws.repo, "log", "-1", "--format=%B").startswith("Add work\n\nWhy it changed.")
    invoke = state["lifecycle"]["invoke"]
    assert trailer(ws.repo, out["sha"], "Forge-Agent") == [agent]
    assert trailer(ws.repo, out["sha"], "Forge-Stage") == [invoke["stage"]]
    assert trailer(ws.repo, out["sha"], "Forge-Mode") == [invoke["mode"]]
    assert trailer(ws.repo, out["sha"], "Forge-Input-SHA") == [base]


# @spec SPOKE-PROTO-COMMIT-001
def test_commit_stage_option_sets_forge_stage(ws):
    ws.dispatch("forge-planner", phase="PLAN_S1", mode="auto")
    stage(ws.repo, {"docs/plan.md": "x\n"})
    out = ws.commit("Stage 3\n", "--stage", "LID-S3")
    assert out["status"] == "COMMITTED"
    assert trailer(ws.repo, out["sha"], "Forge-Stage") == ["LID-S3"]
    assert trailer(ws.repo, out["sha"], "Forge-Mode") == ["auto"]


# @spec SPOKE-PROTO-COMMIT-001, SPOKE-PROTO-COMMIT-005
def test_strict_revision_may_label_earlier_stage(ws):
    state = ws.dispatch("forge-planner", phase="PLAN_S3", mode="strict")
    state["lifecycle"]["invoke"]["notes"] = "Rename the S1 requirement"
    write_state(ws.sp, state)
    stage(ws.repo, {"docs/plan.md": "x\n"})
    out = ws.commit("Revise stage 1\n", "--stage", "LID-S1")
    assert out["status"] == "COMMITTED"
    assert trailer(ws.repo, out["sha"], "Forge-Stage") == ["LID-S1"]
    assert trailer(ws.repo, out["sha"], "Forge-Mode") == ["strict"]


# @spec SPOKE-PROTO-COMMIT-002
@pytest.mark.parametrize("agent", ["forge-verifier", "forge-reviewer", "forge-scout", "forge-publisher",
                                   "forge-reporter", "forge-council"])
def test_non_builder_cannot_commit(ws, agent):
    ws.dispatch(agent)
    stage(ws.repo, {"src/a.py": "a\n"})
    before = head(ws.repo)
    assert ws.commit()["status"] == "NOT_A_BUILDER"
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-003
def test_nothing_staged(ws):
    ws.dispatch("forge-dev")
    (ws.repo / "README.md").write_text("unstaged edit\n")
    before = head(ws.repo)
    assert ws.commit()["status"] == "NOTHING_STAGED"
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-004
@pytest.mark.parametrize("agent, inside, outside", [
    ("forge-dev", "src/a.py", "tests/test_a.py"),
    ("forge-qa", "tests/test_a.py", "src/a.py"),
    ("forge-planner", "docs/plan.md", "src/a.py"),
])
def test_staged_path_outside_boundary(ws, agent, inside, outside):
    ws.dispatch(agent)
    stage(ws.repo, {inside: "x\n", outside: "y\n"})
    before = head(ws.repo)
    out = ws.commit()
    assert out["status"] == "BOUNDARY_VIOLATION"
    assert out["paths"] == [outside]
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-004
def test_rename_counts_both_sides(ws):
    commit(ws.repo, {"tests/helper.py": "h\n", "src/__init__.py": ""}, "human test helper")
    ws.dispatch("forge-dev")
    git(ws.repo, "mv", "tests/helper.py", "src/helper.py")
    out = ws.commit()
    assert out["status"] == "BOUNDARY_VIOLATION"
    assert out["paths"] == ["tests/helper.py"]


# @spec SPOKE-PROTO-COMMIT-004
def test_config_test_patterns_decide_qa_boundary(ws):
    ws.dispatch("forge-qa", config={"test_patterns": ["spec/**"]})
    stage(ws.repo, {"spec/a_spec.rb": "s\n"})
    assert ws.commit()["status"] == "COMMITTED"
    stage(ws.repo, {"tests/test_a.py": "t\n"})
    out = ws.commit()
    assert out["status"] == "BOUNDARY_VIOLATION" and out["paths"] == ["tests/test_a.py"]


# @spec SPOKE-PROTO-COMMIT-005
@pytest.mark.parametrize("agent, phase, mode, value", [
    ("forge-dev", "DEV_FULL", "full", "LID-S6"),
    ("forge-qa", "QA_FULL", "full", "LID-S1"),
    ("forge-planner", "PLAN_S1", "strict", "LID-S1"),  # no notes: not a revision
    ("forge-planner", "PLAN", "naive", "LID-S1"),
    ("forge-planner", "REPAIR", "repair", "LID-S1"),
    ("forge-planner", "PLAN_S1", "auto", "LID-S5"),
    ("forge-planner", "PLAN_S1", "auto", "REPAIR"),
    ("forge-planner", "PLAN_S1", "auto", "lid-s1"),
], ids=["dev", "qa", "planner-strict", "planner-naive", "planner-repair", "auto-S5", "auto-REPAIR",
        "auto-lowercase"])
def test_stage_option_invalid(ws, agent, phase, mode, value):
    ws.dispatch(agent, phase=phase, mode=mode)
    stage(ws.repo, {IN_BOUNDARY[agent]: "x\n"})
    before = head(ws.repo)
    assert ws.commit("Work\n", "--stage", value)["status"] == "STAGE_INVALID"
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-006
def test_unreadable_message_file_is_usage_error(ws):
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n"})
    before = head(ws.repo)
    res = ws.run("commit", "--message-file", ws.sd / "no-such-file.txt")
    assert res.returncode == 2 and res.stderr.strip()
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-006
def test_empty_message_file_is_usage_error(ws):
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n"})
    (ws.sd / "empty.txt").write_text("")
    before = head(ws.repo)
    res = ws.run("commit", "--message-file", ws.sd / "empty.txt")
    assert res.returncode == 2 and res.stderr.strip()
    assert head(ws.repo) == before


# @spec SPOKE-PROTO-COMMIT-007
def test_commit_takes_only_the_index(ws):
    ws.dispatch("forge-dev")
    commit(ws.repo, {"src/b.py": "b1\n"}, "human b")
    stage(ws.repo, {"src/a.py": "staged\n"})
    (ws.repo / "src" / "a.py").write_text("unstaged\n")
    (ws.repo / "src" / "b.py").write_text("b2\n")
    (ws.repo / "src" / "c.py").write_text("untracked\n")
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert git(ws.repo, "show", "--name-only", "--format=", out["sha"]).splitlines() == ["src/a.py"]
    assert git(ws.repo, "show", f"{out['sha']}:src/a.py") == "staged"
    assert staged(ws.repo) == []
    assert git(ws.repo, "diff", "--name-only").splitlines() == ["src/a.py", "src/b.py"]
    assert git(ws.repo, "ls-files", "--others", "--exclude-standard").splitlines() == ["src/c.py"]


# @spec SPOKE-PROTO-COMMIT-007
def test_refused_commit_leaves_index_alone(ws):
    ws.dispatch("forge-dev")
    stage(ws.repo, {"src/a.py": "a\n", "tests/test_a.py": "t\n"})
    (ws.repo / "src" / "b.py").write_text("untracked\n")
    before = git(ws.repo, "ls-files", "-s"), git(ws.repo, "status", "--porcelain")
    assert ws.commit()["status"] == "BOUNDARY_VIOLATION"
    assert (git(ws.repo, "ls-files", "-s"), git(ws.repo, "status", "--porcelain")) == before


# ------------------------------------------------------------------ commit: spec markers

A_SPECS = "docs/intent/a/a-specs.md"
B_SPECS = "docs/intent/b/b-specs.md"


def spec_text(*lines):
    return "# A specs\n\n" + "\n".join(lines) + "\n"


def flip(text, spec_id, to):
    """text with spec_id's marker set to `to` (" " or "x")."""
    for frm in (" ", "x"):
        text = text.replace(f"- [{frm}] **{spec_id}**", f"- [{to}] **{spec_id}**")
    return text


def plan_specs(ws, files, agent="forge-dev", mode=None, **kw):
    """Commit files as human work after the workstream base, then dispatch agent."""
    base = head(ws.repo)
    commit(ws.repo, files, "planned specs and earlier work")
    phase = "REPAIR" if mode == "repair" else None
    ws.dispatch(agent, phase=phase, mode=mode, anchors={"workstream_base_sha": base}, **kw)


def committed(repo, path, rev="HEAD"):
    return git(repo, "show", f"{rev}:{path}") + "\n"


# @spec SPOKE-PROTO-MARK-001, SPOKE-PROTO-MARK-004, SPOKE-PROTO-MARK-007
@pytest.mark.parametrize("agent", ["forge-qa", "forge-dev"])
@pytest.mark.parametrize("mode", ["full", "repair"])
def test_commit_marks_open_specs_that_are_tested_and_tagged(ws, agent, mode):
    text = spec_text("- [ ] **A-001**: tested and tagged after this commit.", "- [ ] **A-002**: untested.")
    other = {"forge-qa": ("src/a.py", "# @" + "spec A-001, A-002\n"),
             "forge-dev": ("tests/test_a.py", "# @" + "spec A-001\n")}[agent]
    plan_specs(ws, {A_SPECS: text, other[0]: other[1]}, agent, mode)
    stage(ws.repo, {IN_BOUNDARY[agent]: "# @" + "spec A-001\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert (out["marked"], out["unmarked"]) == (["A-001"], [])
    assert committed(ws.repo, A_SPECS) == flip(text, "A-001", "x")
    assert (ws.repo / A_SPECS).read_text() == flip(text, "A-001", "x")
    assert git(ws.repo, "status", "--porcelain") == ""


# @spec SPOKE-PROTO-MARK-002, SPOKE-PROTO-MARK-004
@pytest.mark.parametrize("agent", ["forge-qa", "forge-dev"])
def test_commit_unmarks_done_specs_that_lack_a_test_or_tag(ws, agent):
    text = spec_text("- [x] **A-001**: loses its evidence in this commit.", "- [x] **A-002**: tagged, untested.",
                     "- [x] **A-003**: tested and tagged.", "- [ ] **A-004**: untested and untagged.")
    plan_specs(ws, {A_SPECS: text, "tests/test_a.py": "# @" + "spec A-001, A-003\n",
                    "src/a.py": "# @" + "spec A-001, A-002, A-003\n"}, agent, "repair")
    stage(ws.repo, {IN_BOUNDARY[agent]: "# @" + "spec A-003\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert (out["marked"], sorted(out["unmarked"])) == ([], ["A-001", "A-002"])
    assert committed(ws.repo, A_SPECS) == flip(flip(text, "A-001", " "), "A-002", " ")


# @spec SPOKE-PROTO-MARK-003
def test_markers_follow_the_staged_tree_not_unstaged_edits(ws):
    text = spec_text("- [ ] **A-001**: tag only in an unstaged edit.", "- [x] **A-002**: tag removed only unstaged.")
    plan_specs(ws, {A_SPECS: text, "tests/test_a.py": "# @" + "spec A-001, A-002\n", "src/b.py": "# @" + "spec A-002\n"})
    stage(ws.repo, {"src/a.py": "a = 1\n"})
    (ws.repo / "src" / "a.py").write_text("# @" + "spec A-001\na = 1\n")
    (ws.repo / "src" / "b.py").write_text("b = 1\n")
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert (out["marked"], out["unmarked"]) == ([], [])
    assert committed(ws.repo, A_SPECS) == text


# @spec SPOKE-PROTO-MARK-003
def test_markers_follow_lid_check_rules_for_specs_ids_and_annotations(ws):
    older = spec_text("- [ ] **B-001**: covered, but older than the workstream.", "- [x] **A-004**: older duplicate.")
    commit(ws.repo, {B_SPECS: older}, "older specs")
    text = spec_text(
        "- [ ] **A-001**: tested and tagged.",
        "- [D] **A-002**: deferred, tested and tagged.",
        "- [ ] **A-003**: tested, tagged only in a plan path.",
        "- [ ] **A-004**: tested and tagged, duplicated.",
        "- [ ] **A-005**: tested, named in code only after non-ID text.",
    )
    plan_specs(ws, {A_SPECS: text, "docs/notes.md": "@" + "spec A-003\n",
                    "tests/test_a.py": "# @" + "spec A-001, A-002, A-003, A-004, A-005, B-001\n"})
    stage(ws.repo, {"src/a.py": "# @" + "spec A-001, A-002, A-004, B-001\n# see @" + "spec <ID> A-005\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert (out["marked"], out["unmarked"]) == (["A-001"], [])
    assert committed(ws.repo, A_SPECS) == flip(text, "A-001", "x")
    assert committed(ws.repo, B_SPECS) == older


# @spec SPOKE-PROTO-MARK-007, SPOKE-PROTO-COMMIT-007
def test_marker_change_keeps_unstaged_edits_and_other_paths(ws):
    text = spec_text("- [ ] **A-001**: tested and tagged after this commit.", "- [ ] **A-002**: untested.")
    b_text = spec_text("- [ ] **B-001**: untested.")
    plan_specs(ws, {A_SPECS: text, B_SPECS: b_text, "tests/test_a.py": "# @" + "spec A-001\n", "src/b.py": "b1\n"})
    edited = "Draft note above every spec.\n" + text.replace("A-002**: untested.", "A-002**: reworded, unstaged.")
    (ws.repo / A_SPECS).write_text(edited)
    (ws.repo / B_SPECS).write_text(b_text + "Unstaged note.\n")
    (ws.repo / "src" / "b.py").write_text("b2\n")
    (ws.repo / "src" / "u.py").write_text("untracked\n")
    stage(ws.repo, {"src/a.py": "# @" + "spec A-001\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED" and out["marked"] == ["A-001"]
    assert git(ws.repo, "show", "--name-only", "--format=", out["sha"]).splitlines() == [A_SPECS, "src/a.py"]
    assert committed(ws.repo, A_SPECS) == flip(text, "A-001", "x")
    assert (ws.repo / A_SPECS).read_text() == flip(edited, "A-001", "x")
    assert staged(ws.repo) == []
    assert git(ws.repo, "diff", "--name-only").splitlines() == [A_SPECS, B_SPECS, "src/b.py"]
    assert git(ws.repo, "ls-files", "--others", "--exclude-standard").splitlines() == ["src/u.py"]


# @spec SPOKE-PROTO-MARK-007
def test_marker_change_keeps_unstaged_deletion_of_the_spec_line(ws):
    text = spec_text("- [ ] **A-001**: tested and tagged after this commit.", "- [ ] **A-002**: untested.")
    plan_specs(ws, {A_SPECS: text, "tests/test_a.py": "# @" + "spec A-001\n"})
    deleted = "".join(line for line in text.splitlines(keepends=True) if "**A-001**" not in line)
    (ws.repo / A_SPECS).write_text(deleted)
    stage(ws.repo, {"src/a.py": "# @" + "spec A-001\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED" and out["marked"] == ["A-001"]
    assert committed(ws.repo, A_SPECS) == flip(text, "A-001", "x")
    assert (ws.repo / A_SPECS).read_text() == deleted


# @spec SPOKE-PROTO-MARK-004
def test_committed_without_marker_changes_carries_empty_lists(ws):
    plan_specs(ws, {A_SPECS: spec_text("- [ ] **A-001**: untested.")})
    stage(ws.repo, {"src/a.py": "a\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert (out["marked"], out["unmarked"]) == ([], [])


# @spec SPOKE-PROTO-MARK-004
@pytest.mark.parametrize("files, expected", [
    ({}, "NOTHING_STAGED"),
    ({"src/a.py": "# @" + "spec A-001\n", "docs/x.md": "x\n"}, "BOUNDARY_VIOLATION"),
], ids=["nothing-staged", "boundary-violation"])
def test_other_statuses_carry_no_marker_lists(ws, files, expected):
    plan_specs(ws, {A_SPECS: spec_text("- [ ] **A-001**: tested."), "tests/test_a.py": "# @" + "spec A-001\n"})
    stage(ws.repo, files)
    out = ws.commit()
    assert out["status"] == expected
    assert "marked" not in out and "unmarked" not in out


# @spec SPOKE-PROTO-MARK-004, SPOKE-PROTO-MARK-006
@pytest.mark.parametrize("agent, phase, mode, config", [
    ("forge-planner", "PLAN_S4", "strict", None),
    ("forge-planner", "REPAIR", "repair", None),
    ("forge-qa", "QA_SKEL", "skeleton", None),
    ("forge-dev", "DEV_STUB", "stub", None),
    ("forge-dev", "DEV_FULL", "full", {"repo_mode": "NAIVE"}),
], ids=["planner", "planner-repair", "qa-skeleton", "dev-stub", "naive-repo"])
def test_no_marker_changes_outside_lid_qa_and_dev_full_and_repair(ws, agent, phase, mode, config):
    text = spec_text("- [ ] **A-001**: tested and tagged.", "- [x] **A-002**: untested.")
    base = head(ws.repo)
    commit(ws.repo, {A_SPECS: text, "tests/test_a.py": "# @" + "spec A-001\n", "src/b.py": "# @" + "spec A-001, A-002\n"},
           "planned specs and earlier work")
    ws.dispatch(agent, phase=phase, mode=mode, config=config, anchors={"workstream_base_sha": base})
    stage(ws.repo, {IN_BOUNDARY[agent]: "# @" + "spec A-001\nX = 1\n"})
    out = ws.commit()
    assert out["status"] == "COMMITTED"
    assert "marked" not in out and "unmarked" not in out
    assert committed(ws.repo, A_SPECS) == text
    assert (ws.repo / A_SPECS).read_text() == text


# @spec SPOKE-PROTO-MARK-005
def test_failed_commit_restores_changed_spec_files(ws, tmp_path):
    text = spec_text("- [ ] **A-001**: tested and tagged after this commit.")
    plan_specs(ws, {A_SPECS: text, "tests/test_a.py": "# @" + "spec A-001\n"})
    (ws.repo / A_SPECS).write_text("Unstaged note.\n" + text)
    stage(ws.repo, {"src/a.py": "# @" + "spec A-001\n"})
    hooks, seen = tmp_path / "hooks", tmp_path / "seen-by-hook.md"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(f"#!/bin/sh\ngit show :{A_SPECS} > '{seen}'\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    git(ws.repo, "config", "core.hooksPath", str(hooks))
    before = head(ws.repo), git(ws.repo, "ls-files", "-s"), (ws.repo / A_SPECS).read_text()
    (ws.sd / "msg.txt").write_text("Work\n")
    ws.run("commit", "--message-file", ws.sd / "msg.txt")  # outcome on a failed commit is not specified
    assert "- [x] **A-001**" in seen.read_text(), "commit failed before changing any marker"
    assert (head(ws.repo), git(ws.repo, "ls-files", "-s"), (ws.repo / A_SPECS).read_text()) == before


# ------------------------------------------------------------------ finish: computed fields


# @spec SPOKE-PROTO-FIN-001
def test_envelope_missing(ws):
    ws.dispatch("forge-verifier")
    out = ws.report("finish")
    assert out["status"] == "ENVELOPE_MISSING"
    assert Path(out["path"]).is_absolute()
    assert Path(out["path"]).resolve() == ws.envelope_path.resolve()


# @spec SPOKE-PROTO-FIN-002
def test_finish_sets_dispatch_id_and_agent(ws):
    ws.dispatch("forge-verifier")
    assert ws.finish(ok(status="PASS"))["status"] == "FINISHED"
    env = ws.envelope()
    assert env["dispatch_id"] == DISPATCH
    assert env["agent"] == "forge-verifier"


# @spec SPOKE-PROTO-FIN-003
def test_head_sha_is_head_for_builder(ws):
    state = ws.dispatch("forge-dev")
    base = state["lifecycle"]["dispatched"]["base_sha"]
    commit(ws.repo, {"src/a.py": "a\n"}, "dev", forge_trailers("forge-dev", "LID-S6", "full", base))
    assert ws.finish(ok(status="IMPLEMENTATION_COMPLETE"))["status"] == "FINISHED"
    assert ws.envelope()["head_sha"] == head(ws.repo) != base


# @spec SPOKE-PROTO-FIN-003
def test_head_sha_is_base_sha_for_non_committing_agent(ws):
    state = ws.dispatch("forge-verifier")
    base = state["lifecycle"]["dispatched"]["base_sha"]
    commit(ws.repo, {"src/h.py": "h\n"}, "human commit while the verifier ran")
    assert ws.finish(ok(status="PASS"))["status"] == "FINISHED"
    assert ws.envelope()["head_sha"] == base != head(ws.repo)


# @spec SPOKE-PROTO-FIN-004
def test_files_touched_from_own_first_parent_commits(ws):
    state = ws.dispatch("forge-dev")
    base = state["lifecycle"]["dispatched"]["base_sha"]
    t = forge_trailers("forge-dev", "LID-S6", "full", base)
    commit(ws.repo, {"src/a.py": "a\n"}, "dev a", t)
    commit(ws.repo, {"src/h.py": "h\n"}, "human h")
    commit(ws.repo, {"src/b.py": "b\n"}, "dev b", t)
    assert ws.finish(ok(status="IMPLEMENTATION_COMPLETE"))["status"] == "FINISHED"
    assert sorted(ws.envelope()["files_touched"]) == ["src/a.py", "src/b.py"]


# @spec SPOKE-PROTO-FIN-004
def test_files_touched_ignores_merged_side_branch(ws):
    state = ws.dispatch("forge-dev")
    base = state["lifecycle"]["dispatched"]["base_sha"]
    t = forge_trailers("forge-dev", "LID-S6", "full", base)
    git(ws.repo, "checkout", "-q", "-b", "side")
    commit(ws.repo, {"src/side.py": "s\n"}, "dev on side", t)
    git(ws.repo, "checkout", "-q", "main")
    commit(ws.repo, {"src/a.py": "a\n"}, "dev a", t)
    git(ws.repo, "merge", "-q", "--no-ff", "-m", "human merge", "side")
    assert ws.finish(ok(status="IMPLEMENTATION_COMPLETE"))["status"] == "FINISHED"
    assert ws.envelope()["files_touched"] == ["src/a.py"]


# @spec SPOKE-PROTO-FIN-004
def test_files_touched_empty_for_non_committing_agent(ws):
    ws.dispatch("forge-verifier")
    commit(ws.repo, {"src/h.py": "h\n"}, "human commit while the verifier ran")
    assert ws.finish(ok(status="PASS"))["status"] == "FINISHED"
    assert ws.envelope()["files_touched"] == []


# ------------------------------------------------------------------ finish: general checks


# @spec SPOKE-PROTO-FIN-005
@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    json.dumps({"summary": "s"}),
    json.dumps({"status": "PASS"}),
    json.dumps({"status": 1, "summary": "s"}),
    json.dumps({"status": "PASS", "summary": ["s"]}),
], ids=["not-json", "array", "no-status", "no-summary", "status-not-string", "summary-not-string"])
def test_malformed_envelope_is_invalid(ws, content):
    ws.dispatch("forge-verifier")
    out = ws.finish(content)
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-006
@pytest.mark.parametrize("agent, phase, mode, status", [
    ("forge-dev", "DEV_FULL", "full", "TESTS_COMMITTED"),
    ("forge-verifier", "VERIFY_FINAL", "VERIFY_FINAL", "REVIEW_APPROVED"),
    ("forge-qa", "QA_FULL", "full", "MADE_UP"),
], ids=["dev-full", "verifier", "unknown"])
def test_status_not_allowed_in_phase_is_invalid(ws, agent, phase, mode, status):
    ws.dispatch(agent, phase=phase, mode=mode)
    out = ws.finish(ok(status=status))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-006
@pytest.mark.parametrize("agent, phase, mode, envelope", [
    ("forge-reporter", "REPORT", "draft", ok(status="REPORT_SENT", report={"url": "https://x/pr/1"})),
    ("forge-planner", "PLAN_S2", "strict", ok(status="READY_FOR_QA", scope={"assigned": []})),
    ("forge-planner", "PLAN_S2", "auto", ok(status="STAGE_COMPLETE")),
], ids=["report-sent-in-draft", "strict-ready-before-s4", "auto-stage-complete"])
def test_status_not_allowed_in_mode_is_invalid(ws, agent, phase, mode, envelope):
    ws.dispatch(agent, phase=phase, mode=mode)
    out = ws.finish(envelope)
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-006
@pytest.mark.parametrize("agent, phase, mode, envelope", [
    ("forge-dev", "DEV_FULL", "full", ok(status="IMPLEMENTATION_COMPLETE")),
    ("forge-planner", "PLAN_S2", "strict", ok(status="STAGE_COMPLETE")),
    ("forge-scout", "RECON", "PRE_PLAN", ok(status="ERROR", escalation={"details": "gh missing"})),
], ids=["dev-full", "strict-stage-complete", "error-always"])
def test_allowed_status_finishes(ws, agent, phase, mode, envelope):
    ws.dispatch(agent, phase=phase, mode=mode)
    assert ws.finish(envelope)["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-006
def test_status_check_uses_router_tables():
    assert "next_step" in imported_modules(spoke_source())


def make_dirty(repo, kind):
    if kind == "untracked":
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "new.py").write_text("n\n")
    elif kind == "unstaged":
        (repo / "README.md").write_text("edited\n")
    else:
        stage(repo, {"src/staged.py": "s\n"})


# @spec SPOKE-PROTO-FIN-007
@pytest.mark.parametrize("agent", BUILDERS)
@pytest.mark.parametrize("kind", ["untracked", "unstaged", "staged"])
def test_dirty_tree_is_invalid_for_builder(ws, agent, kind):
    status = "STAGE_COMPLETE" if agent == "forge-planner" else (
        "TESTS_COMMITTED" if agent == "forge-qa" else "IMPLEMENTATION_COMPLETE")
    ws.dispatch(agent)
    make_dirty(ws.repo, kind)
    out = ws.finish(ok(status=status))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-007
def test_ignored_untracked_file_is_not_dirty(ws):
    commit(ws.repo, {".gitignore": "*.log\n"}, "ignore logs")
    ws.dispatch("forge-dev")
    (ws.repo / "run.log").write_text("log\n")
    assert ws.finish(ok(status="IMPLEMENTATION_COMPLETE"))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-007
def test_dirty_tree_allowed_with_error(ws):
    ws.dispatch("forge-dev")
    make_dirty(ws.repo, "untracked")
    assert ws.finish(ok(status="ERROR", escalation={"details": "cannot run tests"}))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-007
def test_dirty_tree_not_checked_for_non_committing_agent(ws):
    ws.dispatch("forge-verifier")
    make_dirty(ws.repo, "untracked")
    assert ws.finish(ok(status="PASS"))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-008
def test_own_commit_by_non_committing_agent_is_invalid(ws):
    ws.dispatch("forge-verifier")
    commit(ws.repo, {"src/v.py": "v\n"}, "verifier commit", {"Forge-Agent": "forge-verifier"})
    out = ws.finish(ok(status="PASS"))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-008
def test_own_commit_by_non_committing_agent_allowed_with_error(ws):
    ws.dispatch("forge-verifier")
    commit(ws.repo, {"src/v.py": "v\n"}, "verifier commit", {"Forge-Agent": "forge-verifier"})
    out = ws.finish(ok(status="ERROR", escalation={"details": "I committed by mistake"}))
    assert out["status"] == "FINISHED"


# ------------------------------------------------------------------ finish: required fields

HUMAN_CODE_ESCALATION = {"paths": ["README.md"], "ranges": {"README.md": [[1, 1]]}}

# status -> (agent, phase, mode, extra envelope fields, extra escalation fields)
ESCALATING = {
    "ERROR": ("forge-verifier", "VERIFY_FINAL", "VERIFY_FINAL", {}, {}),
    "CONCERNS": ("forge-planner", "PLAN_S1", "strict", {}, {}),
    "TEST_CHANGE_REQUESTED": ("forge-dev", "DEV_FULL", "full", {}, {}),
    "CONTRACT_MISMATCH": ("forge-dev", "DEV_FULL", "full", {}, {}),
    "AUDIT_FAILED": ("forge-verifier", "VERIFY_FINAL", "VERIFY_FINAL", {}, {}),
    "TESTS_FAILED": ("forge-verifier", "VERIFY_FINAL", "VERIFY_FINAL", {}, {}),
    "CI_FAILED": ("forge-scout", "CI_WATCH", "CI_WATCH", {}, {}),
    "CHANGES_REQUESTED": ("forge-reviewer", "REVIEW_INTERNAL", "internal", {},
                          {"findings": [{"route_to": "forge-dev", "detail": "rename x"}]}),
    "HUMAN_CODE_CHANGE_REQUESTED": ("forge-dev", "DEV_FULL", "full", {}, HUMAN_CODE_ESCALATION),
    "PUBLISH_REFUSED": ("forge-publisher", "PUBLISH", "push", {"publish_target": "push"}, {}),
    "MERGE_BLOCKED": ("forge-publisher", "PUBLISH", "merge", {"publish_target": "merge"}, {}),
}


# @spec SPOKE-PROTO-FIN-009
@pytest.mark.parametrize("status", list(ESCALATING))
def test_escalation_details_required(ws, status):
    agent, phase, mode, extra, esc = ESCALATING[status]
    ws.dispatch(agent, phase=phase, mode=mode)
    for bad in [{}, {"details": ""}, {"details": 3}]:
        out = ws.finish(ok(status=status, escalation={**esc, **bad}, **extra))
        assert out["status"] == "INVALID" and out["problems"], bad
    assert ws.finish(ok(status=status, escalation={**esc, "details": "why"}, **extra))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-009
def test_escalation_missing_entirely(ws):
    ws.dispatch("forge-dev")
    out = ws.finish(ok(status="TEST_CHANGE_REQUESTED"))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-010
@pytest.mark.parametrize("escalation", [
    {"ranges": {"README.md": [[1, 1]]}},
    {"paths": "README.md", "ranges": {"README.md": [[1, 1]]}},
    {"paths": [1], "ranges": {"1": [[1, 1]]}},
    {"paths": ["README.md"]},
    {"paths": ["README.md"], "ranges": {}},
    {"paths": ["README.md"], "ranges": [["README.md", [1, 1]]]},
    {"paths": ["README.md"], "ranges": {"README.md": [1, 1]}},
    {"paths": ["README.md"], "ranges": {"README.md": [[1]]}},
    {"paths": ["README.md"], "ranges": {"README.md": [[1, 2, 3]]}},
    {"paths": ["README.md"], "ranges": {"README.md": [["1", "2"]]}},
    {"paths": ["README.md", "src/a.py"], "ranges": {"README.md": [[1, 1]]}},
], ids=["no-paths", "paths-string", "paths-not-strings", "no-ranges", "ranges-empty", "ranges-list",
        "ranges-flat", "pair-short", "pair-long", "pair-strings", "path-unmapped"])
def test_human_code_paths_and_ranges_required(ws, escalation):
    ws.dispatch("forge-dev")
    out = ws.finish(ok(status="HUMAN_CODE_CHANGE_REQUESTED", escalation={"details": "need it", **escalation}))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-010
def test_human_code_well_formed_finishes(ws):
    ws.dispatch("forge-qa")
    escalation = {"details": "need it", "paths": ["README.md", "src/a.py"],
                  "ranges": {"README.md": [[1, 1]], "src/a.py": [[2, 4], [9, 9]]}}
    assert ws.finish(ok(status="HUMAN_CODE_CHANGE_REQUESTED", escalation=escalation))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-011
@pytest.mark.parametrize("findings", [
    None,
    [],
    ["rename x"],
    [{"route_to": "forge-verifier", "detail": "x"}],
    [{"route_to": ["forge-dev"], "detail": "x"}],
    [{"route_to": "forge-dev"}],
    [{"route_to": "forge-dev", "detail": ""}],
    [{"route_to": "forge-dev", "detail": "ok"}, {"detail": "no route"}],
], ids=["missing", "empty", "not-objects", "bad-route", "route-list", "no-detail", "empty-detail",
        "one-bad"])
def test_changes_requested_findings_required(ws, findings):
    ws.dispatch("forge-reviewer")
    escalation = {"details": "see findings"}
    if findings is not None:
        escalation["findings"] = findings
    out = ws.finish(ok(status="CHANGES_REQUESTED", escalation=escalation))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-011
def test_changes_requested_well_formed_finishes(ws):
    ws.dispatch("forge-reviewer")
    findings = [{"route_to": a, "detail": f"fix for {a}"} for a in BUILDERS]
    escalation = {"details": "see findings", "findings": findings}
    assert ws.finish(ok(status="CHANGES_REQUESTED", escalation=escalation))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-012
@pytest.mark.parametrize("scope, valid", [
    (None, False),
    ({}, False),
    ({"assigned": "src/**"}, False),
    ({"assigned": [1]}, False),
    ({"assigned": []}, True),
    ({"assigned": ["src/limits/**", "tests/**"]}, True),
], ids=["no-scope", "no-assigned", "string", "not-strings", "empty", "patterns"])
def test_ready_for_qa_scope_assigned(ws, scope, valid):
    ws.dispatch("forge-planner", phase="PLAN_S4", mode="strict")
    envelope = ok(status="READY_FOR_QA")
    if scope is not None:
        envelope["scope"] = scope
    out = ws.finish(envelope)
    assert out["status"] == ("FINISHED" if valid else "INVALID")


# @spec SPOKE-PROTO-FIN-012
@pytest.mark.parametrize("scope, valid", [
    (None, True),
    ({"assigned": []}, True),
    ({"assigned": ["src/**"]}, True),
    ({"assigned": "src/**"}, False),
    ({"assigned": [None]}, False),
], ids=["absent", "empty", "patterns", "string", "not-strings"])
def test_planner_repair_scope_assigned(ws, scope, valid):
    ws.dispatch("forge-planner", phase="REPAIR", mode="repair")
    envelope = ok(status="REPAIR_COMMITTED")
    if scope is not None:
        envelope["scope"] = scope
    out = ws.finish(envelope)
    assert out["status"] == ("FINISHED" if valid else "INVALID")


ASSIGNED = ["src/a/**", "src/b/**"]


# @spec SPOKE-PROTO-FIN-013
@pytest.mark.parametrize("scope", [
    None,
    {"remaining": ASSIGNED},
    {"completed": ["src/a/**"], "remaining": []},
    {"completed": ["src/a/**"], "remaining": ["src/a/**", "src/b/**"]},
    {"completed": ["src/a/**", "src/a/**"], "remaining": ["src/b/**"]},
    {"completed": ["src/a/**"], "remaining": ["src/b/**", "src/c/**"]},
    {"completed": "src/a/**", "remaining": ["src/b/**"]},
    {"completed": [1], "remaining": ASSIGNED},
], ids=["no-scope", "no-completed", "missing-pattern", "in-both", "repeated", "extra", "string",
        "not-strings"])
def test_partial_remaining_scope_must_partition_assigned(ws, scope):
    ws.dispatch("forge-verifier", phase="PRE_QA", mode="PRE_QA", scope={"assigned": ASSIGNED})
    envelope = ok(status="PARTIAL_REMAINING")
    if scope is not None:
        envelope["scope"] = scope
    out = ws.finish(envelope)
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-013
@pytest.mark.parametrize("phase, status, scope", [
    ("PRE_QA", "PARTIAL_REMAINING", {"completed": ["src/b/**"], "remaining": ["src/a/**"]}),
    ("PRE_DEV", "NOTHING_REMAINING", {"completed": ["src/b/**", "src/a/**"], "remaining": []}),
    ("RECONCILE", "PASS", {"completed": [], "remaining": ASSIGNED}),
])
def test_scope_partition_finishes(ws, phase, status, scope):
    ws.dispatch("forge-verifier", phase=phase, mode=phase, scope={"assigned": ASSIGNED})
    assert ws.finish(ok(status=status, scope=scope))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-013
@pytest.mark.parametrize("phase, status", [
    ("PRE_DEV", "NOTHING_REMAINING"),
    ("RECONCILE", "PASS"),
])
def test_scope_required_for_nothing_remaining_and_reconcile_pass(ws, phase, status):
    ws.dispatch("forge-verifier", phase=phase, mode=phase, scope={"assigned": ASSIGNED})
    out = ws.finish(ok(status=status))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-013
def test_pass_outside_reconcile_needs_no_scope(ws):
    ws.dispatch("forge-verifier", phase="VERIFY_FINAL", scope={"assigned": ASSIGNED})
    assert ws.finish(ok(status="PASS"))["status"] == "FINISHED"


def valid_ruling(sd):
    matrix = sd / "council" / "matrix.md"
    matrix.write_text("matrix\n")
    return {
        "matrix_ref": str(matrix),
        "requires_user": True,
        "panel": ["pm", "architect", "critic"],
        "options": [
            {"label": "A", "summary": "split the module", "route_to": ["forge-planner"], "delta": "split"},
            {"label": "B", "summary": "keep it", "route_to": [], "delta": "keep"},
        ],
        "recommended": "A",
        "route_to": ["forge-planner"],
        "delta": "split",
    }


def _outside_matrix(r, sd):
    (sd / "matrix.md").write_text("m\n")
    r["matrix_ref"] = str(sd / "matrix.md")


RULING_BREAKS = {
    "no-matrix": lambda r, sd: r.pop("matrix_ref"),
    "matrix-relative": lambda r, sd: r.update(matrix_ref="council/matrix.md"),
    "matrix-missing-file": lambda r, sd: r.update(matrix_ref=str(sd / "council" / "nope.md")),
    "matrix-outside-council": _outside_matrix,
    "no-requires-user": lambda r, sd: r.pop("requires_user"),
    "requires-user-string": lambda r, sd: r.update(requires_user="yes"),
    "no-panel": lambda r, sd: r.pop("panel"),
    "panel-empty": lambda r, sd: r.update(panel=[]),
    "panel-not-strings": lambda r, sd: r.update(panel=[1]),
    "no-options": lambda r, sd: r.pop("options"),
    "options-empty": lambda r, sd: r.update(options=[]),
    "labels-repeated": lambda r, sd: r["options"][1].update(label="A"),
    "label-not-string": lambda r, sd: r["options"][1].update(label=2),
    "summary-not-string": lambda r, sd: r["options"][1].update(summary=None),
    "option-no-route": lambda r, sd: r["options"][1].pop("route_to"),
    "option-route-bad-agent": lambda r, sd: r["options"][1].update(route_to=["forge-verifier"]),
    "option-route-string": lambda r, sd: r["options"][1].update(route_to="forge-dev"),
    "option-delta-not-string": lambda r, sd: r["options"][1].update(delta=["keep"]),
    "no-recommended": lambda r, sd: r.pop("recommended"),
    "recommended-unknown": lambda r, sd: r.update(recommended="Z"),
    "route-differs": lambda r, sd: r.update(route_to=["forge-dev"]),
    "delta-differs": lambda r, sd: r.update(delta="keep"),
    "no-route": lambda r, sd: r.pop("route_to"),
    "no-delta": lambda r, sd: r.pop("delta"),
}


# @spec SPOKE-PROTO-FIN-014
@pytest.mark.parametrize("name", list(RULING_BREAKS))
def test_council_ruling_malformed(ws, name):
    ws.dispatch("forge-council")
    ruling = valid_ruling(ws.sd)
    RULING_BREAKS[name](ruling, ws.sd)
    out = ws.finish(ok(status="COUNCIL_RULING", ruling=ruling))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-014
def test_council_ruling_missing(ws):
    ws.dispatch("forge-council")
    out = ws.finish(ok(status="COUNCIL_RULING"))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-014
def test_council_ruling_well_formed_finishes(ws):
    ws.dispatch("forge-council")
    ruling = valid_ruling(ws.sd)
    assert ws.finish(ok(status="COUNCIL_RULING", ruling=ruling))["status"] == "FINISHED"
    ruling = copy.deepcopy(ruling)
    ruling.update(recommended="B", route_to=[], delta="keep", requires_user=False)
    assert ws.finish(ok(status="COUNCIL_RULING", ruling=ruling))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-015
@pytest.mark.parametrize("status, mode, target, valid", [
    ("PUBLISHED", "push", "push", True),
    ("PUBLISHED", "push", "merge", False),
    ("PUBLISHED", "push", None, False),
    ("MERGE_READY", "merge", "merge", True),
    ("MERGE_READY", "merge", "push", False),
    ("MERGE_BLOCKED", "merge", "push", False),
    ("PUBLISH_REFUSED", "push", "merge", False),
    ("PUBLISH_REFUSED", "push", None, False),
])
def test_publish_target_must_equal_mode(ws, status, mode, target, valid):
    ws.dispatch("forge-publisher", mode=mode)
    envelope = ok(status=status, escalation={"details": "why"})
    if target is not None:
        envelope["publish_target"] = target
    out = ws.finish(envelope)
    assert out["status"] == ("FINISHED" if valid else "INVALID")


def valid_review():
    return {"verdict": "request_changes", "body": "Two issues.",
            "comments": [{"path": "src/a.py", "line": 3, "body": "off by one"}]}


REVIEW_BREAKS = {
    "verdict-unknown": lambda r: r.update(verdict="lgtm"),
    "no-verdict": lambda r: r.pop("verdict"),
    "body-not-string": lambda r: r.update(body=1),
    "no-body": lambda r: r.pop("body"),
    "comments-not-list": lambda r: r.update(comments={}),
    "no-comments": lambda r: r.pop("comments"),
    "comment-no-path": lambda r: r["comments"][0].pop("path"),
    "line-zero": lambda r: r["comments"][0].update(line=0),
    "line-string": lambda r: r["comments"][0].update(line="3"),
    "comment-body-not-string": lambda r: r["comments"][0].update(body=None),
}


# @spec SPOKE-PROTO-FIN-016
@pytest.mark.parametrize("name", list(REVIEW_BREAKS))
def test_pr_review_ready_review_malformed(ws, name):
    ws.dispatch("forge-reviewer", phase="PR_REVIEW", mode="external_pr", identity={"flow": "pr_review"})
    review = valid_review()
    REVIEW_BREAKS[name](review)
    out = ws.finish(ok(status="PR_REVIEW_READY", review=review))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-016
@pytest.mark.parametrize("verdict", ["approve", "request_changes", "comment"])
def test_pr_review_ready_well_formed_finishes(ws, verdict):
    ws.dispatch("forge-reviewer", phase="PR_REVIEW", mode="external_pr", identity={"flow": "pr_review"})
    review = {**valid_review(), "verdict": verdict}
    assert ws.finish(ok(status="PR_REVIEW_READY", review=review))["status"] == "FINISHED"
    assert ws.finish(ok(status="PR_REVIEW_READY", review={**review, "comments": []}))["status"] == "FINISHED"


REPORTS = {
    "pr_create": {"kind": "pr_create", "title": "Add limits", "body": "b", "base": "main", "head": "feat/x"},
    "pr_update": {"kind": "pr_update", "number": 12, "title": "Add limits", "body": "b"},
    "pr_review": {"kind": "pr_review", "number": 12, "commit_id": "abc123", "event": "REQUEST_CHANGES",
                  "body": "b", "comments": [{"path": "src/a.py", "line": 3, "side": "RIGHT", "body": "c"}]},
}

REPORT_BREAKS = [
    ("kind-unknown", "pr_create", lambda r: r.update(kind="issue_comment")),
    ("no-kind", "pr_create", lambda r: r.pop("kind")),
    ("create-no-head", "pr_create", lambda r: r.pop("head")),
    ("create-base-not-string", "pr_create", lambda r: r.update(base=1)),
    ("update-number-string", "pr_update", lambda r: r.update(number="12")),
    ("update-no-body", "pr_update", lambda r: r.pop("body")),
    ("review-no-commit-id", "pr_review", lambda r: r.pop("commit_id")),
    ("review-event-lowercase", "pr_review", lambda r: r.update(event="approve")),
    ("review-comments-not-list", "pr_review", lambda r: r.update(comments="c")),
    ("review-comment-no-side", "pr_review", lambda r: r["comments"][0].pop("side")),
    ("review-number-missing", "pr_review", lambda r: r.pop("number")),
]


# @spec SPOKE-PROTO-FIN-017
@pytest.mark.parametrize("name, kind, mutate", REPORT_BREAKS, ids=[b[0] for b in REPORT_BREAKS])
def test_report_drafted_report_malformed(ws, name, kind, mutate):
    ws.dispatch("forge-reporter", mode="draft")
    report = copy.deepcopy(REPORTS[kind])
    mutate(report)
    out = ws.finish(ok(status="REPORT_DRAFTED", report=report))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-017
def test_report_drafted_missing_report(ws):
    ws.dispatch("forge-reporter", mode="draft")
    out = ws.finish(ok(status="REPORT_DRAFTED"))
    assert out["status"] == "INVALID" and out["problems"]


# @spec SPOKE-PROTO-FIN-017
@pytest.mark.parametrize("kind", list(REPORTS))
def test_report_drafted_well_formed_finishes(ws, kind):
    ws.dispatch("forge-reporter", mode="draft")
    assert ws.finish(ok(status="REPORT_DRAFTED", report=REPORTS[kind]))["status"] == "FINISHED"


# @spec SPOKE-PROTO-FIN-018
@pytest.mark.parametrize("report, valid", [
    (None, False),
    ({}, False),
    ({"url": ""}, False),
    ({"url": 5}, False),
    ({"url": "https://github.com/o/r/pull/12"}, True),
], ids=["no-report", "no-url", "empty-url", "url-not-string", "url"])
def test_report_sent_url(ws, report, valid):
    ws.dispatch("forge-reporter", mode="send")
    envelope = ok(status="REPORT_SENT")
    if report is not None:
        envelope["report"] = report
    assert ws.finish(envelope)["status"] == ("FINISHED" if valid else "INVALID")


# ------------------------------------------------------------------ finish: outcome


# @spec SPOKE-PROTO-FIN-019
def test_finished_writes_completed_envelope_back(ws):
    state = ws.dispatch("forge-dev")
    base = state["lifecycle"]["dispatched"]["base_sha"]
    commit(ws.repo, {"src/a.py": "a\n"}, "dev", forge_trailers("forge-dev", "LID-S6", "full", base))
    written = ok(status="IMPLEMENTATION_COMPLETE", summary="Implemented the window.")
    assert ws.finish(written)["status"] == "FINISHED"
    env = ws.envelope()
    assert {k: env[k] for k in written} == written
    assert env["dispatch_id"] == DISPATCH and env["agent"] == "forge-dev"
    assert env["head_sha"] == head(ws.repo)
    assert env["files_touched"] == ["src/a.py"]


# @spec SPOKE-PROTO-FIN-020
def test_invalid_lists_every_problem_and_leaves_file(ws):
    ws.dispatch("forge-dev")
    make_dirty(ws.repo, "untracked")
    text = json.dumps({"status": "TESTS_COMMITTED"})  # no summary; not allowed in DEV_FULL; dirty tree
    out = ws.finish(text)
    assert out["status"] == "INVALID"
    assert isinstance(out["problems"], list) and len(out["problems"]) >= 3
    assert ws.envelope_path.read_text() == text


# ------------------------------------------------------------------ scope


@pytest.fixture
def scoped(ws):
    repo = ws.repo
    commit(repo, {"src/limits/window.py": "w\n", "src/limits/window_test.py": "t\n", "docs/plan.md": "p\n"},
           "human before plan")
    plan_base = head(repo)
    dev = commit(repo, {"src/limits/window.py": "w2\n"}, "dev",
                 forge_trailers("forge-dev", "LID-S6", "full", plan_base))
    human = commit(repo, {"src/limits/new.py": "n\n", "src/other.py": "o\n"}, "human")
    qa = commit(repo, {"tests/test_window.py": "t\n"}, "qa", forge_trailers("forge-qa", "LID-S5", "full", human))
    (repo / "src" / "limits" / "untracked.py").write_text("u\n")
    manifest = {"assigned": ["tests/**", "src/limits/**", "src/none/**"],
                "completed": ["tests/**"], "remaining": ["src/limits/**", "src/none/**"]}
    ws.dispatch("forge-verifier", anchors={"plan_base_sha": plan_base}, scope=manifest)
    return {"plan_base": plan_base, "dev": dev, "human": human, "qa": qa, "manifest": manifest}


def by_pattern(out):
    return {p["pattern"]: p for p in out["patterns"]}


def commit_facts(entry):
    return [{"sha": c["sha"], "kind": c["kind"], "agent": c["agent"], "paths": sorted(c["paths"])}
            for c in entry["commits"]]


# @spec SPOKE-PROTO-SCOPE-001
def test_scope_files_per_pattern_in_order(ws, scoped):
    out = ws.report("scope")
    assert out["status"] == "OK"
    assert [p["pattern"] for p in out["patterns"]] == scoped["manifest"]["assigned"]
    pats = by_pattern(out)
    assert sorted(pats["src/limits/**"]["files"], key=lambda f: f["path"]) == [
        {"path": "src/limits/new.py", "class": "production"},
        {"path": "src/limits/window.py", "class": "production"},
        {"path": "src/limits/window_test.py", "class": "test"},
    ]
    assert pats["tests/**"]["files"] == [{"path": "tests/test_window.py", "class": "test"}]
    assert pats["src/none/**"]["files"] == []


# @spec SPOKE-PROTO-SCOPE-002
def test_scope_commits_per_pattern(ws, scoped):
    pats = by_pattern(ws.report("scope"))
    assert commit_facts(pats["src/limits/**"]) == [
        {"sha": scoped["dev"], "kind": "forge", "agent": "forge-dev", "paths": ["src/limits/window.py"]},
        {"sha": scoped["human"], "kind": "human", "agent": None, "paths": ["src/limits/new.py"]},
    ]
    assert commit_facts(pats["tests/**"]) == [
        {"sha": scoped["qa"], "kind": "forge", "agent": "forge-qa", "paths": ["tests/test_window.py"]},
    ]
    assert pats["src/none/**"]["commits"] == []


# @spec SPOKE-PROTO-SCOPE-002
def test_scope_commits_first_parent_only(ws, scoped):
    repo = ws.repo
    git(repo, "checkout", "-q", "-b", "side")
    side = commit(repo, {"src/limits/side.py": "s\n"}, "human on side")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    shas = [c["sha"] for c in by_pattern(ws.report("scope"))["src/limits/**"]["commits"]]
    assert side not in shas


# @spec SPOKE-PROTO-SCOPE-003
def test_scope_commits_empty_without_plan_base(ws, scoped):
    state = read_state(ws.sp)
    state["anchors"]["plan_base_sha"] = None
    write_state(ws.sp, state)
    out = ws.report("scope")
    assert out["status"] == "OK"
    assert all(p["commits"] == [] for p in out["patterns"])
    assert by_pattern(out)["src/limits/**"]["files"]


# @spec SPOKE-PROTO-SCOPE-004
def test_scope_repeats_completed_and_remaining(ws, scoped):
    out = ws.report("scope")
    assert out["completed"] == scoped["manifest"]["completed"]
    assert out["remaining"] == scoped["manifest"]["remaining"]


# @spec SPOKE-PROTO-SCOPE-005
def test_scope_changes_no_file(ws, scoped):
    before = snapshot(ws)
    ws.report("scope")
    assert snapshot(ws) == before
