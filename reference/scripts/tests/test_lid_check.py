"""Tests for lid_check.py (docs/intent/spokes/verifier/verifier-specs.md)."""

import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import SCRIPTS, commit, git, head, init_repo, load_module, run_script

A_SPECS = "docs/intent/a/a-specs.md"
B_SPECS = "docs/intent/b/b-specs.md"


def make_state(path, worktree, base=None, repo_mode="LID", config=None):
    state = {
        "schema_version": 1,
        "identity": {"flow": "build", "base_branch": "main", "worktree_path": str(worktree)},
        "config": {"repo_mode": repo_mode, **(config or {})},
        "anchors": {
            "workstream_base_sha": base,
            "plan_base_sha": None,
            "last_known_head_sha": None,
            "verifier_pass_sha": None,
            "reviewer_pass_sha": None,
            "stale_shas": [],
        },
        "scope_manifest": {"assigned": [], "completed": [], "remaining": []},
        "blockers": [],
        "council_decisions": [],
        "approvals": [],
    }
    path.write_text(json.dumps(state))
    return path


def lid(state, cwd):
    return run_script("lid_check.py", "--state", state, cwd=cwd)


def specs(*lines):
    return "# Specs\n\n" + "\n".join(lines) + "\n"


def covered(*ids):
    """A test file and a production file both citing ids."""
    tag = "# @" + "spec " + ", ".join(ids) + "\n"
    return {"tests/test_cov.py": tag + "def test_x():\n    pass\n", "src/cov.py": tag + "X = 1\n"}


def findings_of(res, kind):
    return [f for f in res.json["findings"] if f["kind"] == kind]


def ids_of(res, kind):
    return sorted(f["id"] for f in findings_of(res, kind))


def snapshot(root, *extra):
    entries = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] == ".git":
            continue
        if p.is_file():
            entries[str(rel)] = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            entries[str(rel)] = "dir"
    return (
        entries,
        git(root, "status", "--porcelain", "--ignored"),
        git(root, "for-each-ref"),
        git(root, "rev-parse", "HEAD"),
        [(Path(e).stat().st_mtime_ns, Path(e).read_bytes()) for e in extra],
    )


# ------------------------------------------------------------------- invocation


# @spec SPOKE-VER-LID-CLI-001
def test_prints_one_json_object_with_status_and_exits_0(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: untested and untagged.")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.returncode == 0
    out = json.loads(res.stdout)  # raises on anything but exactly one JSON value
    assert isinstance(out, dict) and "status" in out


# @spec SPOKE-VER-LID-CLI-001
@pytest.mark.parametrize("args", [[], ["--bogus"], ["--state"]], ids=["no-args", "unknown-flag", "no-value"])
def test_usage_error_exits_2_with_stderr(tmp_path, args):
    res = run_script("lid_check.py", *args, cwd=tmp_path)
    assert res.returncode == 2
    assert res.stderr.strip()


# @spec SPOKE-VER-LID-CLI-002
def test_naive_repo_mode_is_not_lid(repo, tmp_path):
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x.")})
    res = lid(make_state(tmp_path / "s.json", repo, None, repo_mode="NAIVE"), repo)
    assert res.returncode == 0
    assert res.json["status"] == "NOT_LID"


# @spec SPOKE-VER-LID-CLI-003
def test_missing_state_is_state_invalid(repo, tmp_path):
    res = lid(tmp_path / "absent.json", repo)
    assert res.json["status"] == "STATE_INVALID"
    assert res.json["detail"]


# @spec SPOKE-VER-LID-CLI-003
@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "{not json"], ids=["array", "string", "unparsable"])
def test_non_object_state_is_state_invalid(repo, tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content)
    res = lid(path, repo)
    assert res.json["status"] == "STATE_INVALID"
    assert res.json["detail"]


# @spec SPOKE-VER-LID-CLI-003
def test_state_invalid_detail_says_which(repo, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    missing = lid(tmp_path / "absent.json", repo).json["detail"]
    not_object = lid(bad, repo).json["detail"]
    assert missing != not_object


# @spec SPOKE-VER-LID-CLI-004
def test_runs_in_worktree_path_not_cwd(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), **covered("A-001")})
    elsewhere = init_repo(tmp_path / "elsewhere")
    res = lid(make_state(tmp_path / "s.json", repo, base), elsewhere)
    assert res.json["status"] == "OK"
    assert res.json["workstream_specs"] == ["A-001"]


# @spec SPOKE-VER-LID-CLI-004
def test_stdlib_only_imports():
    path = SCRIPTS / "lid_check.py"
    assert path.is_file(), "lid_check.py is not implemented yet"
    siblings = {p.stem for p in SCRIPTS.glob("*.py")}  # e.g. verify_gate's path classifier
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or top in siblings, name


# @spec SPOKE-VER-LID-CLI-004
def test_runs_with_only_git_on_path(repo, tmp_path):
    bindir = tmp_path / "gitonly"
    bindir.mkdir()
    (bindir / "git").symlink_to(shutil.which("git"))
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x.")})
    res = run_script("lid_check.py", "--state", make_state(tmp_path / "s.json", repo, base),
                     cwd=repo, env={"PATH": str(bindir)})
    assert res.json["status"] == "OK"
    assert ids_of(res, "UNTESTED") == ["A-001"]


# @spec SPOKE-VER-LID-CLI-004
def test_changes_no_file(repo, tmp_path):
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: x.", "- [ ] **A-002**: y."),
        B_SPECS: specs("- [ ] **A-001**: dup."),
        "src/x.py": "# @" + "spec A-001, NOPE-001\n",
    })
    (repo / "untracked.txt").write_text("left alone\n")
    state = make_state(tmp_path / "s.json", repo, base)
    before = snapshot(repo, state)
    lid(state, repo)
    assert snapshot(repo, state) == before


# -------------------------------------------------------- specs and annotations


# @spec SPOKE-VER-LID-001
def test_spec_line_syntax(repo, tmp_path):
    commit(repo, {A_SPECS: specs(
        "- [ ] **A-001**: open.",
        "- [x] **A-002**: done.",
        "- [D] **A-003**: deferred.",
        "- [ ] **AB-CD2-E-004**: several segments.",
        "- [X] **A-005**: upper-case X is not a marker.",
        "- [?] **A-006**: unknown marker.",
        "- [ ] **a-007**: lower-case ID.",
        "- [ ] **A-08**: two-digit number.",
        "* [ ] **A-009**: not a dash bullet.",
        "- [ ] A-010: ID not bold.",
        "Prose naming **A-011**: in passing.",
    )})
    res = lid(make_state(tmp_path / "s.json", repo, None), repo)
    assert sorted(res.json["workstream_specs"]) == ["A-001", "A-002", "A-003", "AB-CD2-E-004"]


# @spec SPOKE-VER-LID-001
def test_spec_files_found_by_intent_glob(repo, tmp_path):
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: depth one."),
        "docs/intent/n/m/m-specs.md": specs("- [ ] **N-M-001**: nested."),
        "docs/intent/n/notes.md": specs("- [ ] **N-002**: not a specs file."),
        "docs/other/o-specs.md": specs("- [ ] **O-001**: outside docs/intent."),
    })
    res = lid(make_state(tmp_path / "s.json", repo, None), repo)
    assert sorted(res.json["workstream_specs"]) == ["A-001", "N-M-001"]


# @spec SPOKE-VER-LID-CLI-005
def test_importable_check_matches_command(repo, tmp_path):
    assert (SCRIPTS / "lid_check.py").is_file(), "lid_check.py is not implemented yet"
    base = merge_from_main(repo, {B_SPECS: specs("- [ ] **B-001**: from main.")})
    state_path = make_state(tmp_path / "s.json", repo, base)
    module = load_module("lid_check")
    assert module.check(json.loads(state_path.read_text())) == lid(state_path, tmp_path).json


# @spec SPOKE-VER-LID-002
def test_workstream_specs_are_lines_added_or_changed_after_base(repo, tmp_path):
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: old, untouched.", "- [ ] **A-002**: old, before.")})
    base = head(repo)
    commit(repo, {A_SPECS: specs(
        "- [ ] **A-001**: old, untouched.",
        "- [ ] **A-002**: old, changed after base.",
        "- [ ] **A-003**: new.",
    )})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert sorted(res.json["workstream_specs"]) == ["A-002", "A-003"]


# @spec SPOKE-VER-LID-002
def test_null_base_counts_every_first_parent_commit(repo, tmp_path):
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: first.")})
    commit(repo, {B_SPECS: specs("- [ ] **B-001**: second.")})
    res = lid(make_state(tmp_path / "s.json", repo, None), repo)
    assert sorted(res.json["workstream_specs"]) == ["A-001", "B-001"]


def merge_from_main(repo, main_files, feat_files=None):
    """Branch feat off main, commit feat_files there, commit main_files on main, merge main into feat."""
    base = head(repo)
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, feat_files or {A_SPECS: specs("- [ ] **A-001**: workstream.")}, "feat spec")
    git(repo, "checkout", "-q", "main")
    commit(repo, main_files, "main spec")
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-ff", "main", "-m", "merge main")
    return base


# @spec SPOKE-VER-LID-002
def test_specs_brought_in_by_a_merge_commit_are_not_workstream_specs(repo, tmp_path):
    base = merge_from_main(repo, {B_SPECS: specs("- [ ] **B-001**: from main.")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["workstream_specs"] == ["A-001"]
    assert "B-001" not in {f["id"] for f in res.json["findings"]}


# @spec SPOKE-VER-LID-002
def test_merged_spec_changed_after_the_merge_is_a_workstream_spec(repo, tmp_path):
    base = merge_from_main(repo, {B_SPECS: specs("- [ ] **B-001**: from main.")})
    commit(repo, {B_SPECS: specs("- [ ] **B-001**: from main, revised here.")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert sorted(res.json["workstream_specs"]) == ["A-001", "B-001"]


# @spec SPOKE-VER-LID-009
def test_untested_merged_specs_are_listed_without_findings(repo, tmp_path):
    base = merge_from_main(repo, {
        B_SPECS: specs("- [ ] **B-001**: untested.", "- [x] **B-002**: tested.",
                       "- [D] **B-003**: deferred.", "- [ ] **B-004**: tested, no tag."),
        "tests/test_b.py": "# @" + "spec B-002, B-004\ndef test_b():\n    pass\n",
        "src/b.py": "# @" + "spec B-002\nX = 1\n",
    }, {A_SPECS: specs("- [ ] **A-001**: workstream."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["merged_specs"] == ["B-001"]
    assert res.json["findings"] == []


# @spec SPOKE-VER-LID-009
def test_specs_from_before_the_base_are_not_merged_specs(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [ ] **B-001**: old and untested.")})
    base = merge_from_main(repo, {"src/m.py": "m\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["merged_specs"] == []


# @spec SPOKE-VER-LID-003
def test_test_file_annotation_is_citation_and_production_file_is_tag(repo, tmp_path):
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: tested only.", "- [ ] **A-002**: tagged only."),
        "tests/test_a.py": "# @" + "spec A-001\ndef test_a():\n    pass\n",
        "src/a.py": "# @" + "spec A-002\nX = 1\n",
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert ids_of(res, "UNTESTED") == ["A-002"]
    assert ids_of(res, "UNTAGGED") == ["A-001"]


# @spec SPOKE-VER-LID-003
def test_comma_separated_tokens_each_count(repo, tmp_path):
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: one.", "- [ ] **A-002**: two."),
        **covered("A-001", "A-002"),
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["findings"] == []


# @spec SPOKE-VER-LID-003
def test_spec_followed_by_non_id_is_not_an_annotation(repo, tmp_path):
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: x."),
        "tests/test_a.py": "# put @" + "spec <ID> above each test; see A-001\n",
        "src/a.py": "# @" + "spec A-001\n",
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert ids_of(res, "UNTESTED") == ["A-001"]
    assert findings_of(res, "UNKNOWN_ID") == []


# @spec SPOKE-VER-LID-003
def test_config_test_patterns_decide_test_class(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), "spec/a_spec.rb": "# @" + "spec A-001\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base, config={"test_patterns": ["spec/**"]}), repo)
    assert ids_of(res, "UNTESTED") == []
    assert ids_of(res, "UNTAGGED") == ["A-001"]


# @spec SPOKE-VER-LID-003
def test_plan_class_annotation_is_neither_citation_nor_tag(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), "docs/notes.md": "@" + "spec A-001\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert ids_of(res, "UNTESTED") == ["A-001"]
    assert ids_of(res, "UNTAGGED") == ["A-001"]


# ---------------------------------------------------------------------- findings


# @spec SPOKE-VER-LID-004
def test_duplicate_definition_elsewhere_is_reported(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [x] **A-001**: older definition.")})
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: new definition."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert "A-001" in ids_of(res, "DUPLICATE_ID")


# @spec SPOKE-VER-LID-004
def test_mention_in_spec_text_is_not_duplicate(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [x] **B-001**: after **A-001** and A-001 hold, do more.")})
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert findings_of(res, "DUPLICATE_ID") == []


# @spec SPOKE-VER-LID-004
def test_duplicate_among_non_workstream_specs_is_not_reported(repo, tmp_path):
    commit(repo, {A_SPECS: specs("- [x] **A-001**: one."), B_SPECS: specs("- [x] **A-001**: two.")})
    base = head(repo)
    commit(repo, {"src/other.py": "X = 1\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert findings_of(res, "DUPLICATE_ID") == []


# @spec SPOKE-VER-LID-005
def test_unknown_id_in_changed_file_reports_path_and_line(repo, tmp_path):
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: x."),
        "tests/test_cov.py": "# @" + "spec A-001\n",
        "src/x.py": "import os\n\n# @" + "spec A-001, NOPE-001\n",
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    unknown = findings_of(res, "UNKNOWN_ID")
    assert len(unknown) == 1
    assert (unknown[0]["id"], unknown[0]["path"], unknown[0]["line"]) == ("NOPE-001", "src/x.py", 3)


# @spec SPOKE-VER-LID-005
def test_unknown_id_in_unchanged_file_is_not_reported(repo, tmp_path):
    commit(repo, {"src/old.py": "# @" + "spec NOPE-001\n"})
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert findings_of(res, "UNKNOWN_ID") == []


# @spec SPOKE-VER-LID-006
@pytest.mark.parametrize("marker", [" ", "x"])
def test_workstream_spec_without_test_citation_is_untested(repo, tmp_path, marker):
    base = head(repo)
    commit(repo, {A_SPECS: specs(f"- [{marker}] **A-001**: x."), "src/a.py": "# @" + "spec A-001\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert ids_of(res, "UNTESTED") == ["A-001"]


# @spec SPOKE-VER-LID-006, SPOKE-VER-LID-007
def test_deferred_spec_needs_no_test_or_tag(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [D] **A-001**: deferred.")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["workstream_specs"] == ["A-001"]
    assert findings_of(res, "UNTESTED") == [] and findings_of(res, "UNTAGGED") == []


# @spec SPOKE-VER-LID-006, SPOKE-VER-LID-007
def test_non_workstream_spec_gaps_are_not_reported(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [ ] **B-001**: older gap.")})
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [ ] **A-001**: x."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["findings"] == []


# @spec SPOKE-VER-LID-007
@pytest.mark.parametrize("marker", [" ", "x"])
def test_workstream_spec_without_code_tag_is_untagged(repo, tmp_path, marker):
    base = head(repo)
    commit(repo, {A_SPECS: specs(f"- [{marker}] **A-001**: x."), "tests/test_a.py": "# @" + "spec A-001\n"})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert ids_of(res, "UNTAGGED") == ["A-001"]


# @spec SPOKE-VER-LID-008, SPOKE-VER-LID-010
def test_clean_workstream_is_ok_with_no_findings(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [x] **A-001**: x.", "- [ ] **A-002**: y."), **covered("A-001", "A-002")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json == {"status": "OK", "workstream_specs": ["A-001", "A-002"], "merged_specs": [],
                        "unmarked_specs": ["A-002"], "findings": []}


# @spec SPOKE-VER-LID-008
def test_every_finding_carries_kind_id_detail_and_path_line_for_annotations(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [x] **A-001**: older.")})
    base = head(repo)
    commit(repo, {
        A_SPECS: specs("- [ ] **A-001**: dup.", "- [ ] **A-002**: tested only.", "- [ ] **A-003**: tagged only."),
        "tests/test_a.py": "# @" + "spec A-001, A-002\n",
        "src/a.py": "# @" + "spec A-001, A-003, NOPE-001\n",
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["status"] == "OK"
    findings = res.json["findings"]
    assert {f["kind"] for f in findings} == {"DUPLICATE_ID", "UNKNOWN_ID", "UNTESTED", "UNTAGGED"}
    for f in findings:
        assert isinstance(f["id"], str) and isinstance(f["detail"], str) and f["detail"]
    for f in findings_of(res, "UNKNOWN_ID"):
        assert f["path"] == "src/a.py" and f["line"] == 1


# @spec SPOKE-VER-LID-010
def test_unmarked_specs_lists_open_workstream_specs_with_test_and_tag(repo, tmp_path):
    commit(repo, {B_SPECS: specs("- [ ] **B-001**: older, covered, not a workstream spec.",
                                 "- [x] **A-005**: older definition of a duplicated ID.")})
    base = head(repo)
    commit(repo, {
        A_SPECS: specs(
            "- [ ] **A-001**: open, tested and tagged.",
            "- [x] **A-002**: marked, tested and tagged.",
            "- [D] **A-003**: deferred, tested and tagged.",
            "- [ ] **A-004**: open, tested only.",
            "- [ ] **A-005**: open, tested and tagged, duplicated.",
            "- [ ] **A-006**: open, tested, tagged only in a plan path.",
        ),
        "tests/test_a.py": "# @" + "spec A-001, A-002, A-003, A-004, A-005, A-006, B-001\n",
        "src/a.py": "# @" + "spec A-001, A-002, A-003, A-005, B-001\n",
        "docs/notes.md": "@" + "spec A-006\n",
    })
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["status"] == "OK"
    assert res.json["unmarked_specs"] == ["A-001"]
    assert "A-001" not in {f["id"] for f in res.json["findings"]}
    assert {f["kind"] for f in res.json["findings"]} <= {"DUPLICATE_ID", "UNTAGGED"}


# @spec SPOKE-VER-LID-010
def test_unmarked_specs_is_empty_list_when_none(repo, tmp_path):
    base = head(repo)
    commit(repo, {A_SPECS: specs("- [x] **A-001**: x."), **covered("A-001")})
    res = lid(make_state(tmp_path / "s.json", repo, base), repo)
    assert res.json["unmarked_specs"] == []
