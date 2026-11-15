"""Tests for resolve_state.py (docs/intent/workstream-state/workstream-state-specs.md).

ASSUMPTIONS about report shapes the specs leave open:
- wake drift: {"kind", "new_commits": [{"sha", "kind": "human"|"forge"}], "orphaned_commits": [sha]}
- a cleared dead lock is reported as "cleared_dead_lock": true
- LIST and `list` carry entries under "workstreams"
- ELSEWHERE carries the recorded location under "elsewhere": {"branch", "worktree_path"}
- DUPLICATE names the other workstream under "workstream"; GH_FAILED carries "detail"
- successful release / close report "RELEASED" / "CLOSED"
"""

import ast
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import (
    HOLDER_SESSION,
    SCRIPTS,
    commit,
    forge_trailers,
    git,
    head,
    holder_args,
    init_repo,
    read_state,
    run_script,
    set_origin,
    state_dir,
    write_state,
)


def rs(cwd, *args, env=None):
    return run_script("resolve_state.py", *args, cwd=cwd, env=env)


def new(cwd, *args, env=None):
    return rs(cwd, "new", *args, "--worktree-root", ".claude/worktrees", *holder_args(), env=env)


def wake(cwd, *args, env=None, pid=None, session=HOLDER_SESSION):
    return rs(cwd, "wake", *args, *holder_args(pid, session), env=env)


@pytest.fixture
def live_pid():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc.pid
    proc.kill()
    proc.wait()


def lock_path(ws):
    return ws["state_path"].parent / "lock.json"


def set_anchor(ws, **anchors):
    state = read_state(ws["state_path"])
    state["anchors"].update(anchors)
    write_state(ws["state_path"], state)


def write_lock(ws, pid, session="other-session", host=None):
    lock_path(ws).write_text(json.dumps({
        "holder_pid": pid, "holder_session": session,
        "host": host or socket.gethostname(), "acquired_at": "2026-01-01T00:00:00Z",
    }))


# ---------------------------------------------------------------------- storage


# @spec WS-STORE-001
def test_state_lives_under_forge_home(workstream, forge_home):
    assert workstream["state_path"].is_file()
    assert forge_home in workstream["state_path"].parents


# @spec WS-STORE-001
def test_default_root_is_home_dot_forge(repo, tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_HOME", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    res = new(repo, "--name", "demo", env={"HOME": str(home)})
    assert res.json["status"] == "CREATED"
    assert (home / ".forge/state/local/project/feat-demo/state.json").is_file()


# @spec WS-STORE-002
def test_config_defaults(workstream):
    config = read_state(workstream["state_path"])["config"]
    assert (config["plan_mode"], config["collaborative"], config["council_auto_resolve"],
            config["retention"]) == ("strict", True, False, "archive")


# @spec WS-STORE-002
def test_config_from_global_file(repo, forge_home):
    (forge_home / "config.json").write_text(json.dumps({"plan_mode": "auto", "retention": "delete"}))
    new(repo, "--name", "demo")
    config = read_state(state_dir(forge_home, "local", "project", "feat-demo") / "state.json")["config"]
    assert (config["plan_mode"], config["collaborative"], config["council_auto_resolve"],
            config["retention"]) == ("auto", True, False, "delete")


# --------------------------------------------------------------------- identity


@pytest.fixture
def feature_repo(repo, forge_home):
    git(repo, "checkout", "-q", "-b", "feat/x")
    return repo


# @spec WS-ID-001
@pytest.mark.parametrize(
    "url, org, name",
    [
        ("git@github.com:acme/widgets.git", "acme", "widgets"),
        ("github.com:acme/widgets", "acme", "widgets"),
        ("ssh://git@github.com:22/acme/widgets.git", "acme", "widgets"),
        ("https://github.com/acme/widgets/", "acme", "widgets"),
        ("http://user@example.com/acme/widgets.git", "acme", "widgets"),
        ("git://example.com/group/sub/repo.git", "group-sub", "repo"),
    ],
)
def test_hosted_identity(feature_repo, url, org, name):
    set_origin(feature_repo, url)
    ident = wake(feature_repo).json["identity"]
    assert (ident["org"], ident["repo"]) == (org, name)


# @spec WS-ID-002
@pytest.mark.parametrize(
    "url", [None, "/srv/mirrors/unrelated.git", "file:///srv/mirrors/unrelated.git",
            "https://example.com/single.git"],
)
def test_local_identity(feature_repo, url):
    if url:
        set_origin(feature_repo, url)
    ident = wake(feature_repo).json["identity"]
    assert (ident["org"], ident["repo"]) == ("local", "project")


# @spec WS-ID-002
def test_local_identity_same_from_linked_worktree(workstream):
    ident = wake(workstream["worktree"]).json["identity"]
    assert (ident["org"], ident["repo"]) == ("local", "project")


# @spec WS-ID-003
def test_slug_rule(feature_repo):
    set_origin(feature_repo, "https://github.com/Acme Corp/My.Repo!.git")
    git(feature_repo, "checkout", "-q", "-b", "Feature/Foo__Bar--x-")
    ident = wake(feature_repo).json["identity"]
    assert (ident["org"], ident["repo"], ident["workstream"]) == ("acme-corp", "my.repo", "feature-foo__bar-x")


# @spec WS-ID-004
def test_workstream_precedence(feature_repo):
    assert wake(feature_repo).json["identity"]["workstream"] == "feat-x"
    assert wake(feature_repo, env={"FORGE_WORKSTREAM": "from-env"}).json["identity"]["workstream"] == "from-env"
    res = wake(feature_repo, "--workstream", "from-arg", env={"FORGE_WORKSTREAM": "from-env"})
    assert res.json["identity"]["workstream"] == "from-arg"


# @spec WS-ID-005
@pytest.mark.parametrize("name", ["Bad Name", "../etc", "feat/x", "UPPER"])
def test_explicit_names_are_validated_not_sanitized(feature_repo, name):
    res = wake(feature_repo, "--workstream", name)
    assert res.returncode != 0 and res.stderr
    assert wake(feature_repo, env={"FORGE_WORKSTREAM": name}).returncode != 0


# @spec WS-ID-006
def test_detached_head_name(feature_repo):
    sha = head(feature_repo)
    git(feature_repo, "checkout", "-q", "--detach")
    assert wake(feature_repo).json["identity"]["workstream"] == f"detached-{sha[:7]}"


# @spec WS-ID-007, WS-DRIFT-001
def test_unborn_repository(empty_repo, forge_home):
    git(empty_repo, "symbolic-ref", "HEAD", "refs/heads/trunk")
    res = wake(empty_repo)
    assert res.json["status"] == "NO_STATE"
    assert res.json["identity"]["workstream"] == "trunk"
    # A state recorded earlier with an anchor still reports no drift in a repo with no commits.
    common = git(empty_repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    sp = state_dir(forge_home, "local", "empty", "trunk")
    sp.mkdir(parents=True)
    state = json.loads(json.dumps(read_state_template()))
    state["identity"].update({"org": "local", "repo": "empty", "workstream": "trunk", "branch": "trunk",
                              "git_common_dir": common, "worktree_path": str(empty_repo)})
    state["anchors"]["last_known_head_sha"] = "a" * 40
    write_state(sp / "state.json", state)
    res = wake(empty_repo)
    assert res.json["status"] == "OK" and res.json["drift"]["kind"] == "none"


def read_state_template():
    return {
        "schema_version": 1,
        "identity": {"flow": "build", "base_branch": "trunk", "issue": None, "pr": None,
                     "parent_workstream": None},
        "config": {"repo_mode": "NAIVE", "plan_mode": "strict", "collaborative": True,
                   "council_auto_resolve": False, "retention": "archive"},
        "anchors": {"workstream_base_sha": None, "plan_base_sha": None, "last_known_head_sha": None,
                    "verifier_pass_sha": None, "reviewer_pass_sha": None, "stale_shas": []},
        "lifecycle": {"current_phase": "INIT"},
        "scope_manifest": {"assigned": [], "completed": [], "remaining": []},
        "blockers": [], "council_decisions": [], "approvals": [],
    }


# @spec WS-ID-008
@pytest.mark.parametrize("branch", ["main", "master"])
def test_default_branch_without_state_lists(repo, forge_home, branch):
    new(repo, "--name", "demo")
    if branch == "master":
        git(repo, "branch", "-m", "main", "master")
    res = wake(repo)
    assert res.json["status"] == "LIST"
    assert [w["workstream"] for w in res.json["workstreams"]] == ["feat-demo"]


# @spec WS-ID-009
def test_identity_mismatch_on_other_clone(workstream):
    state = read_state(workstream["state_path"])
    state["identity"]["git_common_dir"] = "/somewhere/else/.git"
    write_state(workstream["state_path"], state)
    assert wake(workstream["worktree"]).json["status"] == "IDENTITY_MISMATCH"


# @spec WS-ID-010
def test_identity_mismatch_on_slug_collision(workstream):
    state = read_state(workstream["state_path"])
    state["identity"]["branch"] = "feat-demo"  # e.g. a different branch that slugs the same
    write_state(workstream["state_path"], state)
    assert wake(workstream["worktree"]).json["status"] == "IDENTITY_MISMATCH"


# @spec WS-ID-011
def test_explicit_workstream_elsewhere(workstream):
    res = wake(workstream["repo"], "--workstream", "feat-demo")
    assert res.json["status"] == "ELSEWHERE"
    assert res.json["elsewhere"] == {"branch": "feat/demo", "worktree_path": str(workstream["worktree"])}


# @spec WS-ID-011
def test_explicit_workstream_in_its_worktree_is_ok(workstream):
    assert wake(workstream["worktree"], "--workstream", "feat-demo").json["status"] == "OK"


# ------------------------------------------------------------ workstream creation


ISSUE = {"number": 42, "title": "Fix the Login Bug! (urgent)", "labels": [{"name": "Bug"}],
         "url": "https://github.com/acme/widgets/issues/42"}


# @spec WS-NEW-001, WS-NEW-002
def test_new_from_issue(repo, forge_home, fake_gh):
    fake_gh.set("issue view", ISSUE)
    res = new(repo, "--issue", "42")
    assert res.json["status"] == "CREATED"
    assert res.json["identity"]["workstream"] == "fix-42-fix-the-login-bug-urgent"
    assert res.json["identity"]["branch"] == "fix/42-fix-the-login-bug-urgent"
    assert res.json["identity"]["issue"] == ISSUE["url"]
    assert any(c[:2] == ["issue", "view"] and "--json" in c for c in fake_gh.calls())


# @spec WS-NEW-001
def test_issue_slug_cut_at_dash(repo, forge_home, fake_gh):
    title = "implement the extremely long feature title that keeps going and going"
    fake_gh.set("issue view", {**ISSUE, "title": title, "labels": []})
    slug = new(repo, "--issue", "42").json["identity"]["branch"].split("/", 1)[1]
    assert len(slug) <= 48 and not slug.endswith("-")
    assert ("42-" + "-".join(title.split())).startswith(slug)
    assert ("42-" + "-".join(title.split()))[len(slug)] == "-"


# @spec WS-NEW-001
def test_issue_with_empty_title_slug(repo, forge_home, fake_gh):
    fake_gh.set("issue view", {**ISSUE, "title": "!!!", "labels": []})
    assert new(repo, "--issue", "42").json["identity"]["branch"] == "feat/42"


# @spec WS-NEW-002, WS-NEW-004
@pytest.mark.parametrize("labels, branch_type", [([], "feat"), ([{"name": "enhancement"}], "feat"),
                                                 ([{"name": "type:BUG"}], "fix")])
def test_issue_type_from_labels(repo, forge_home, fake_gh, labels, branch_type):
    fake_gh.set("issue view", {**ISSUE, "labels": labels})
    assert new(repo, "--issue", "42").json["identity"]["branch"].startswith(branch_type + "/")


# @spec WS-NEW-002
def test_explicit_type_overrides_issue(repo, forge_home, fake_gh):
    fake_gh.set("issue view", ISSUE)
    assert new(repo, "--issue", "42", "--type", "chore").json["identity"]["branch"].startswith("chore/")


# @spec WS-NEW-003, WS-NEW-004, WS-NEW-006
def test_new_from_name(workstream):
    ident = workstream["report"]["identity"]
    assert (ident["workstream"], ident["branch"]) == ("feat-demo", "feat/demo")
    assert workstream["worktree"] == workstream["repo"] / ".claude/worktrees/feat-demo"
    assert git(workstream["worktree"], "rev-parse", "--abbrev-ref", "HEAD") == "feat/demo"
    assert str(workstream["worktree"]) in git(workstream["repo"], "worktree", "list")
    assert not git(workstream["repo"], "config", "--get", "branch.feat/demo.merge", check=False)  # --no-track


# @spec WS-NEW-003, WS-NEW-005
@pytest.mark.parametrize("args", [["--name", "Bad Name"], ["--name", "x", "--type", "bogus"]])
def test_new_usage_errors(repo, forge_home, args):
    res = new(repo, *args)
    assert res.returncode != 0 and res.stderr
    assert not (forge_home / "state").exists() or not any((forge_home / "state").rglob("state.json"))


# @spec WS-NEW-007
def test_default_base_is_fetched_origin_default(cloned_repo, forge_home, tmp_path):
    clone, bare = cloned_repo
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(bare), str(other))
    git(other, "config", "user.email", "t@example.invalid")
    git(other, "config", "user.name", "T")
    upstream = commit(other, {"up.txt": "up\n"}, "upstream moved")
    git(other, "push", "-q", "origin", "main")
    res = new(clone, "--name", "demo")
    assert res.json["status"] == "CREATED"
    state = read_state(state_dir(forge_home, "local", "widgets", "feat-demo") / "state.json")
    assert state["anchors"]["workstream_base_sha"] == upstream
    assert state["identity"]["base_branch"] == "main"


# @spec WS-NEW-007
def test_default_branch_from_origin_head(cloned_repo, forge_home):
    clone, bare = cloned_repo
    git(clone, "push", "-q", "origin", "main:develop")
    git(clone, "remote", "set-head", "origin", "develop")
    res = new(clone, "--name", "demo")
    assert res.json["status"] == "CREATED"
    assert res.json["identity"]["base_branch"] == "develop"


# @spec WS-NEW-007, WS-NEW-009
def test_default_base_without_origin_is_local_default(workstream):
    state = read_state(workstream["state_path"])
    assert state["identity"]["base_branch"] == "main"
    assert state["anchors"]["workstream_base_sha"] == head(workstream["repo"])


# @spec WS-NEW-008
def test_non_default_base_needs_confirmation(repo, forge_home):
    git(repo, "branch", "feat/parent")
    res = new(repo, "--name", "child", "--base", "feat/parent")
    assert res.json["status"] == "BASE_CONFIRM_REQUIRED"
    assert not git(repo, "branch", "--list", "feat/child")
    assert not (repo / ".claude/worktrees/feat-child").exists()
    assert not state_dir(forge_home, "local", "project", "feat-child").exists()


# @spec WS-NEW-009, WS-NEW-010
def test_stacked_workstream_records_parent(workstream, forge_home):
    commit(workstream["worktree"], {"p.txt": "p\n"}, "parent work")
    parent_head = head(workstream["worktree"])
    res = new(workstream["repo"], "--name", "child", "--base", "feat/demo", "--base-confirmed")
    assert res.json["status"] == "CREATED"
    state = read_state(state_dir(forge_home, "local", "project", "feat-child") / "state.json")
    assert state["identity"]["base_branch"] == "feat/demo"
    assert state["identity"]["parent_workstream"] == "feat-demo"
    assert state["anchors"]["workstream_base_sha"] == parent_head


# @spec WS-NEW-011
def test_adopt_existing_branch(repo, forge_home):
    git(repo, "branch", "feat/existing")
    git(repo, "checkout", "-q", "feat/existing")
    tip = commit(repo, {"e.txt": "e\n"}, "existing work")
    git(repo, "checkout", "-q", "main")
    branches_before = git(repo, "branch", "--format=%(refname)")
    res = new(repo, "--branch", "feat/existing")
    assert res.json["status"] == "CREATED"
    assert Path(res.json["worktree_path"]) == repo / ".claude/worktrees/feat-existing"
    assert git(repo, "branch", "--format=%(refname)") == branches_before
    state = read_state(state_dir(forge_home, "local", "project", "feat-existing") / "state.json")
    assert state["anchors"]["workstream_base_sha"] == tip
    assert state["identity"]["base_branch"] == "main"


# @spec WS-NEW-023
def test_adopt_missing_branch_is_usage_error(repo, forge_home):
    res = new(repo, "--branch", "feat/nowhere")
    assert res.returncode == 2 and res.stderr
    assert not (repo / ".claude/worktrees").exists() or not any((repo / ".claude/worktrees").iterdir())
    assert not (forge_home / "state").exists() or not any((forge_home / "state").rglob("state.json"))


# @spec WS-NEW-007
def test_default_branch_falls_back_to_origin_master(cloned_repo, forge_home):
    clone, bare = cloned_repo
    git(clone, "push", "-q", "origin", "main:master")
    git(bare, "symbolic-ref", "HEAD", "refs/heads/master")
    git(clone, "push", "-q", "origin", ":main")
    git(clone, "remote", "set-head", "origin", "-d")
    git(clone, "fetch", "-q", "--prune", "origin")
    res = new(clone, "--name", "demo")
    assert res.json["status"] == "CREATED"
    assert res.json["identity"]["base_branch"] == "master"


# @spec WS-NEW-012
def test_adopt_branch_checked_out_elsewhere(repo, forge_home):
    git(repo, "checkout", "-q", "-b", "feat/busy")
    res = new(repo, "--branch", "feat/busy")
    assert res.json["status"] == "BRANCH_CHECKED_OUT"
    assert Path(res.json["worktree_path"]) == repo
    assert not state_dir(forge_home, "local", "project", "feat-busy").exists()


@pytest.fixture
def pr_repo(cloned_repo, fake_gh, tmp_path):
    clone, bare = cloned_repo
    git(clone, "checkout", "-q", "-b", "feature")
    pr_head = commit(clone, {"pr.txt": "pr\n"}, "pr work")
    git(clone, "push", "-q", "origin", "feature:refs/pull/7/head")
    git(clone, "checkout", "-q", "main")
    git(clone, "branch", "-q", "-D", "feature")
    fake_gh.set("pr view", {"number": 7, "url": "https://github.com/acme/widgets/pull/7",
                            "headRefName": "feature", "baseRefName": "main"})
    return clone, pr_head


# @spec WS-NEW-013, WS-NEW-014
def test_new_pr_review(pr_repo, forge_home, fake_gh):
    clone, pr_head = pr_repo
    res = new(clone, "--pr", "7")
    assert res.json["status"] == "CREATED"
    worktree = Path(res.json["worktree_path"])
    assert worktree == clone / ".claude/worktrees/pr-7"
    assert head(worktree) == pr_head
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached
    ident = read_state(state_dir(forge_home, "local", "widgets", "pr-7") / "state.json")["identity"]
    assert (ident["flow"], ident["branch"], ident["pr"], ident["base_branch"]) == (
        "pr_review", None, "https://github.com/acme/widgets/pull/7", "main")
    assert any(c[:2] == ["pr", "view"] and "--json" in c for c in fake_gh.calls())


# @spec WS-ID-011, WS-NEW-014
def test_pr_review_wake_skips_branch_checks(pr_repo, forge_home):
    clone, _ = pr_repo
    worktree = Path(new(clone, "--pr", "7").json["worktree_path"])
    assert wake(worktree, "--workstream", "pr-7").json["status"] == "OK"


# @spec WS-NEW-014
def test_build_flow_for_other_new(workstream):
    assert read_state(workstream["state_path"])["identity"]["flow"] == "build"


# @spec WS-NEW-015
def test_duplicate_issue(repo, forge_home, fake_gh):
    fake_gh.set("issue view", ISSUE)
    new(repo, "--issue", "42")
    fake_gh.set("issue view", {**ISSUE, "title": "renamed meanwhile"})
    res = new(repo, "--issue", "42")
    assert res.json["status"] == "DUPLICATE"
    assert res.json["workstream"] == "fix-42-fix-the-login-bug-urgent"
    assert not state_dir(forge_home, "local", "project", "fix-42-renamed-meanwhile").exists()


# @spec WS-NEW-016
def test_state_exists(repo, forge_home):
    sd = state_dir(forge_home, "local", "project", "feat-taken")
    sd.mkdir(parents=True)
    write_state(sd / "state.json", read_state_template())
    res = new(repo, "--name", "taken")
    assert res.json["status"] == "STATE_EXISTS"
    assert not git(repo, "branch", "--list", "feat/taken")


# @spec WS-NEW-017
def test_branch_exists(repo, forge_home):
    git(repo, "branch", "feat/x")
    res = new(repo, "--name", "x")
    assert res.json["status"] == "BRANCH_EXISTS"
    assert not state_dir(forge_home, "local", "project", "feat-x").exists()


# @spec WS-NEW-018
def test_gh_failure(repo, forge_home, fake_gh):
    fake_gh.fail("issue view", "HTTP 404: Not Found")
    res = new(repo, "--issue", "999")
    assert res.json["status"] == "GH_FAILED"
    assert "HTTP 404" in res.json["detail"]
    assert not (forge_home / "state").exists() or not any((forge_home / "state").rglob("state.json"))


# @spec WS-NEW-019
def test_new_initializes_everything(workstream):
    sd = workstream["state_path"].parent
    state = read_state(workstream["state_path"])
    assert state["schema_version"] == 1
    assert state["lifecycle"]["current_phase"] == "INIT"
    for part in ["hub", "shared", "planner", "qa", "dev", "verifier", "reviewer", "scout"]:
        assert (sd / "memory" / part).is_dir()
    assert (sd / "scout").is_dir() and (sd / "council").is_dir() and (sd / "envelopes").is_dir()
    lock = json.loads((sd / "lock.json").read_text())
    assert (lock["holder_pid"], lock["holder_session"]) == (os.getpid(), HOLDER_SESSION)
    report = workstream["report"]
    assert report["status"] == "CREATED" and report["worktree_path"] == str(workstream["worktree"])
    for key in ["org", "repo", "workstream", "flow", "branch", "base_branch", "issue", "pr",
                "parent_workstream", "git_common_dir", "worktree_path"]:
        assert key in state["identity"], key


# @spec WS-NEW-020
def test_set_overrides(repo, forge_home):
    new(repo, "--name", "demo", "--set", "collaborative=false", "--set", 'test_patterns=["spec/**"]',
        "--set", "plan_mode=auto")
    config = read_state(state_dir(forge_home, "local", "project", "feat-demo") / "state.json")["config"]
    assert config["collaborative"] is False
    assert config["test_patterns"] == ["spec/**"]
    assert config["plan_mode"] == "auto"


# @spec WS-NEW-021
@pytest.mark.parametrize(
    "files, mode",
    [
        ({}, "NAIVE"),
        ({"docs/high-level-design.md": "# HLD\n"}, "LID"),
        ({"docs/intent/x/x-design.md": "x\n"}, "LID"),
        ({"AGENTS.md": "# Repo\n\n## LID\n- Mode: Full\n"}, "LID"),
        ({"CLAUDE.md": "## LID\n"}, "LID"),
        ({"CLAUDE.md": "## LIDAR notes\n"}, "NAIVE"),
        ({"AGENTS.md": "mentions ## LID inline\n"}, "NAIVE"),
    ],
)
def test_repo_mode(repo, forge_home, files, mode):
    if files:
        commit(repo, files, "layout")
    new(repo, "--name", "demo")
    config = read_state(state_dir(forge_home, "local", "project", "feat-demo") / "state.json")["config"]
    assert config["repo_mode"] == mode


# @spec WS-NEW-022
def test_relative_worktree_root_from_subdirectory(repo, forge_home):
    sub = repo / "deep/dir"
    sub.mkdir(parents=True)
    res = new(sub, "--name", "demo")
    assert Path(res.json["worktree_path"]) == repo / ".claude/worktrees/feat-demo"


# ------------------------------------------------------------------- state file


# @spec WS-STATE-001
def test_state_written_by_rename(workstream):
    sp = workstream["state_path"]
    commit(workstream["worktree"], {"a.txt": "a\n"}, "a")
    orphan = head(workstream["worktree"])
    git(workstream["worktree"], "reset", "-q", "--hard", "HEAD~1")
    set_anchor(workstream, last_known_head_sha=orphan)
    link = sp.with_name("old-state.json")
    os.link(sp, link)
    wake(workstream["worktree"])  # appends to stale_shas, so it writes state.json
    assert orphan in read_state(sp)["anchors"]["stale_shas"]
    assert orphan not in read_state(link)["anchors"]["stale_shas"]  # old inode untouched
    assert sorted(p.name for p in sp.parent.iterdir() if p.is_file()) == ["lock.json", "old-state.json",
                                                                          "state.json"]


# @spec WS-STATE-002
@pytest.mark.parametrize("content", ["{not json", json.dumps({**read_state_template(), "schema_version": 99})])
def test_invalid_state(workstream, content):
    workstream["state_path"].write_text(content)
    assert wake(workstream["worktree"]).json["status"] == "STATE_INVALID"
    assert workstream["state_path"].read_text() == content


# ------------------------------------------------------------------------- lock


@pytest.fixture
def released(workstream):
    assert rs(workstream["worktree"], "release", "--workstream", "feat-demo", *holder_args()).json["status"] == "RELEASED"
    return workstream


# @spec WS-LOCK-001, WS-LOCK-007
def test_wake_acquires_free_lock(released):
    assert not lock_path(released).exists()
    assert wake(released["worktree"]).json["status"] == "OK"
    lock = json.loads(lock_path(released).read_text())
    assert (lock["holder_pid"], lock["holder_session"], lock["host"]) == (
        os.getpid(), HOLDER_SESSION, socket.gethostname())
    assert lock["acquired_at"]


# @spec WS-LOCK-002
def test_simultaneous_wakes_one_winner(released, live_pid):
    for _ in range(5):
        lock_path(released).unlink(missing_ok=True)
        cmd = [sys.executable, str(SCRIPTS / "resolve_state.py"), "wake"]
        procs = [
            subprocess.Popen(cmd + holder_args(os.getpid(), "s-a"), cwd=released["worktree"],
                             stdout=subprocess.PIPE, text=True),
            subprocess.Popen(cmd + holder_args(live_pid, "s-b"), cwd=released["worktree"],
                             stdout=subprocess.PIPE, text=True),
        ]
        statuses = sorted(json.loads(p.communicate()[0])["status"] for p in procs)
        assert statuses == ["LOCKED", "OK"]


# @spec WS-LOCK-003
def test_same_holder_keeps_lock(workstream):
    before = lock_path(workstream).read_text()
    assert wake(workstream["worktree"]).json["status"] == "OK"
    assert lock_path(workstream).read_text() == before


# @spec WS-LOCK-004
def test_dead_lock_is_replaced(workstream, dead_pid):
    write_lock(workstream, dead_pid)
    res = wake(workstream["worktree"])
    assert res.json["status"] == "OK" and res.json["cleared_dead_lock"] is True
    assert json.loads(lock_path(workstream).read_text())["holder_session"] == HOLDER_SESSION


# @spec WS-LOCK-005
def test_live_holder_is_locked(workstream, live_pid):
    write_lock(workstream, live_pid)
    res = wake(workstream["worktree"])
    assert res.json["status"] == "LOCKED"
    assert res.json["lock"]["holder_pid"] == live_pid


# @spec WS-LOCK-005
def test_other_host_is_locked(workstream, dead_pid):
    write_lock(workstream, dead_pid, host="some-other-host")
    assert wake(workstream["worktree"]).json["status"] == "LOCKED"


# @spec WS-LOCK-006
def test_steal(workstream, live_pid):
    write_lock(workstream, live_pid)
    assert wake(workstream["worktree"], "--steal").json["status"] == "OK"
    assert json.loads(lock_path(workstream).read_text())["holder_session"] == HOLDER_SESSION


# @spec WS-LOCK-008
@pytest.mark.parametrize("command", ["release", "close"])
def test_non_holder_release_or_close(workstream, live_pid, command):
    before = lock_path(workstream).read_text()
    res = rs(workstream["worktree"], command, "--workstream", "feat-demo", *holder_args(live_pid, "intruder"))
    assert res.json["status"] == "NOT_HOLDER"
    assert lock_path(workstream).read_text() == before
    assert workstream["state_path"].is_file()


# ------------------------------------------------------------------------ drift


# @spec WS-DRIFT-001
def test_no_drift(workstream):
    assert wake(workstream["worktree"]).json["drift"]["kind"] == "none"  # last_known null
    set_anchor(workstream, last_known_head_sha=head(workstream["worktree"]))
    assert wake(workstream["worktree"]).json["drift"]["kind"] == "none"


# @spec WS-DRIFT-002, WS-DRIFT-006
def test_ahead(workstream):
    wt = workstream["worktree"]
    set_anchor(workstream, last_known_head_sha=head(wt))
    human = commit(wt, {"h.txt": "h\n"}, "human")
    forge = commit(wt, {"src/f.py": "f\n"}, "dev", forge_trailers("forge-dev", "LID-S6", "full", human))
    drift = wake(wt).json["drift"]
    assert drift["kind"] == "ahead"
    assert drift["new_commits"] == [{"sha": human, "kind": "human"}, {"sha": forge, "kind": "forge"}]


# @spec WS-DRIFT-006
def test_ahead_lists_first_parent_only(workstream):
    wt = workstream["worktree"]
    set_anchor(workstream, last_known_head_sha=head(wt))
    git(wt, "checkout", "-q", "-b", "side")
    commit(wt, {"s1.txt": "1\n"}, "s1")
    commit(wt, {"s2.txt": "2\n"}, "s2")
    git(wt, "checkout", "-q", "feat/demo")
    git(wt, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    drift = wake(wt).json["drift"]
    assert drift["new_commits"] == [{"sha": head(wt), "kind": "human"}]


# @spec WS-DRIFT-003, WS-DRIFT-007
def test_behind(workstream):
    wt = workstream["worktree"]
    a = commit(wt, {"a.txt": "a\n"}, "a")
    b = commit(wt, {"b.txt": "b\n"}, "b")
    set_anchor(workstream, last_known_head_sha=b)
    git(wt, "reset", "-q", "--hard", "HEAD~2")
    drift = wake(wt).json["drift"]
    assert drift["kind"] == "behind"
    assert sorted(drift["orphaned_commits"]) == sorted([a, b])
    assert sorted(read_state(workstream["state_path"])["anchors"]["stale_shas"]) == sorted([a, b])


# @spec WS-DRIFT-004, WS-DRIFT-007
def test_diverged(workstream):
    wt = workstream["worktree"]
    a = commit(wt, {"a.txt": "a\n"}, "a")
    set_anchor(workstream, last_known_head_sha=a)
    git(wt, "reset", "-q", "--hard", "HEAD~1")
    b = commit(wt, {"b.txt": "b\n"}, "b")
    drift = wake(wt).json["drift"]
    assert drift["kind"] == "diverged"
    assert drift["new_commits"] == [{"sha": b, "kind": "human"}]
    assert drift["orphaned_commits"] == [a]
    wake(wt)
    assert read_state(workstream["state_path"])["anchors"]["stale_shas"] == [a]  # no duplicates
    assert len(a) == 40


# @spec WS-DRIFT-005
def test_unknown_anchor(workstream):
    set_anchor(workstream, last_known_head_sha="f" * 40)
    assert wake(workstream["worktree"]).json["status"] == "DRIFT_UNKNOWN"


# @spec WS-DRIFT-008
def test_wake_never_moves_anchor_or_memory(workstream):
    wt = workstream["worktree"]
    start = head(wt)
    set_anchor(workstream, last_known_head_sha=start)
    note = workstream["state_path"].parent / "memory/dev/note.md"
    note.write_text("anchored to old sha\n")
    commit(wt, {"a.txt": "a\n"}, "a")
    git(wt, "reset", "-q", "--hard", start)
    commit(wt, {"b.txt": "b\n"}, "b")
    wake(wt)
    assert read_state(workstream["state_path"])["anchors"]["last_known_head_sha"] == start
    assert note.read_text() == "anchored to old sha\n"


# @spec WS-DRIFT-009
def test_dirty_paths(workstream):
    wt = workstream["worktree"]
    Path(wt, "README.md").write_text("edited\n")
    Path(wt, "new.txt").write_text("untracked\n")
    res = wake(wt)
    assert res.json["status"] == "OK"
    assert sorted(res.json["dirty"]) == ["README.md", "new.txt"]


# ------------------------------------------------------------------------- wake


# @spec WS-WAKE-001, WS-WAKE-002
def test_state_invalid_beats_everything(workstream, live_pid):
    write_lock(workstream, live_pid)
    before = lock_path(workstream).read_text()
    workstream["state_path"].write_text("{broken")
    assert wake(workstream["repo"], "--workstream", "feat-demo").json["status"] == "STATE_INVALID"
    assert lock_path(workstream).read_text() == before


# @spec WS-WAKE-001, WS-WAKE-002
def test_identity_mismatch_beats_elsewhere_and_lock(workstream, live_pid):
    write_lock(workstream, live_pid)
    before = lock_path(workstream).read_text()
    state = read_state(workstream["state_path"])
    state["identity"]["git_common_dir"] = "/elsewhere/.git"
    write_state(workstream["state_path"], state)
    assert wake(workstream["repo"], "--workstream", "feat-demo").json["status"] == "IDENTITY_MISMATCH"
    assert lock_path(workstream).read_text() == before


# @spec WS-WAKE-001, WS-WAKE-002
def test_elsewhere_beats_lock_and_acquires_nothing(released):
    assert wake(released["repo"], "--workstream", "feat-demo").json["status"] == "ELSEWHERE"
    assert not lock_path(released).exists()


# @spec WS-WAKE-001
def test_locked_beats_drift_unknown(workstream, live_pid):
    write_lock(workstream, live_pid)
    set_anchor(workstream, last_known_head_sha="f" * 40)
    assert wake(workstream["worktree"]).json["status"] == "LOCKED"


# @spec WS-WAKE-003
def test_no_state(feature_repo, forge_home):
    res = wake(feature_repo)
    assert res.json["status"] == "NO_STATE"
    assert not any(forge_home.rglob("state.json"))


# @spec WS-WAKE-004
def test_wake_report_keys(workstream, live_pid):
    ok = wake(workstream["worktree"]).json
    assert {"status", "identity", "state_path", "lock", "drift", "dirty"} <= set(ok)
    assert ok["state_path"] == str(workstream["state_path"])
    write_lock(workstream, live_pid)
    locked = wake(workstream["worktree"]).json
    assert {"identity", "state_path", "lock"} <= set(locked)
    assert "drift" not in locked and "dirty" not in locked


# ---------------------------------------------------------------------- listing


# @spec WS-LIST-001, WS-LIST-002, WS-LIST-003
def test_list(workstream, forge_home, live_pid):
    new(workstream["repo"], "--name", "second")
    second = state_dir(forge_home, "local", "project", "feat-second")
    broken = state_dir(forge_home, "local", "project", "feat-broken")
    broken.mkdir(parents=True)
    (broken / "state.json").write_text("{nope")
    write_lock({"state_path": second / "state.json"}, live_pid, session="someone")
    now = time.time()
    os.utime(workstream["state_path"], (now - 300, now - 300))
    os.utime(second / "state.json", (now - 100, now - 100))
    os.utime(broken / "state.json", (now - 200, now - 200))

    res = rs(workstream["repo"], "list")
    assert res.returncode == 0
    entries = res.json["workstreams"]
    assert [e["workstream"] for e in entries] == ["feat-second", "feat-broken", "feat-demo"]
    demo = entries[2]
    assert (demo["branch"], demo["worktree_path"], demo["current_phase"]) == (
        "feat/demo", str(workstream["worktree"]), "INIT")
    assert "last_status" in demo and "updated_at" in demo and demo["status"] == "OK"
    assert demo["lock"]["holder_session"] == HOLDER_SESSION and demo["lock"]["alive"] is True
    assert entries[0]["lock"]["holder_pid"] == live_pid
    assert entries[1]["status"] == "STATE_INVALID"


# --------------------------------------------------------------------- closeout


def close(ws, *extra):
    return rs(ws["worktree"], "close", "--workstream", "feat-demo", *holder_args(), *extra)


# @spec WS-CLOSE-001, WS-CLOSE-004
def test_close_archives(workstream, forge_home):
    assert close(workstream).json["status"] == "CLOSED"
    assert not workstream["state_path"].parent.exists()
    [archived] = list((forge_home / "archive/local/project").iterdir())
    assert archived.name.startswith("feat-demo-")
    assert (archived / "state.json").is_file()
    assert git(workstream["repo"], "branch", "--list", "feat/demo")


# @spec WS-CLOSE-002
def test_close_deletes(repo, forge_home):
    new(repo, "--name", "demo", "--set", "retention=delete")
    sd = state_dir(forge_home, "local", "project", "feat-demo")
    res = rs(repo / ".claude/worktrees/feat-demo", "close", "--workstream", "feat-demo", *holder_args())
    assert res.json["status"] == "CLOSED"
    assert not sd.exists()
    assert not (forge_home / "archive").exists() or not any((forge_home / "archive").rglob("*"))


# @spec WS-CLOSE-003, WS-CLOSE-004
def test_close_removes_worktree(workstream):
    res = rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(), "--remove-worktree")
    assert res.json["status"] == "CLOSED"
    assert not workstream["worktree"].exists()
    assert git(workstream["repo"], "branch", "--list", "feat/demo")


# @spec WS-CLOSE-005
def test_close_with_dirty_worktree_changes_nothing(workstream):
    Path(workstream["worktree"], "README.md").write_text("dirty\n")
    lock_before = lock_path(workstream).read_text()
    res = rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(), "--remove-worktree")
    assert res.returncode == 0 and res.json["status"] == "WORKTREE_DIRTY"
    assert res.json["detail"]
    assert workstream["worktree"].exists() and workstream["state_path"].is_file()
    assert lock_path(workstream).read_text() == lock_before


# ------------------------------------------------------------ command interface


# @spec WS-CLI-001
def test_every_status_is_json_exit_zero(workstream, live_pid):
    for res in [wake(workstream["worktree"]), rs(workstream["repo"], "list"),
                wake(workstream["worktree"], pid=live_pid, session="other")]:
        assert res.returncode == 0
        assert "status" in res.json


# @spec WS-CLI-002
def test_errors_exit_nonzero(tmp_path, forge_home, repo):
    outside = tmp_path / "plain"
    outside.mkdir()
    res = rs(outside, "wake", *holder_args())
    assert res.returncode != 0 and res.stderr and not res.stdout.strip()
    res = rs(repo, "wake")  # missing --holder-pid / --holder-session
    assert res.returncode != 0 and res.stderr


# @spec WS-CLI-003
def test_stdlib_only_imports():
    tree = ast.parse((SCRIPTS / "resolve_state.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names, name
