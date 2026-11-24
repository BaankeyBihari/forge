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
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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


MODES = ["delivery", "collab-plan", "collab-qa", "collab-dev", "collab-code", "collab"]


def demo_config(forge_home):
    return read_state(state_dir(forge_home, "local", "project", "feat-demo") / "state.json")["config"]


def nothing_created(repo, forge_home, branch="feat/demo"):
    assert not git(repo, "branch", "--list", branch)
    assert not (repo / ".claude/worktrees").exists() or not any((repo / ".claude/worktrees").iterdir())
    assert not (forge_home / "state").exists() or not any((forge_home / "state").rglob("state.json"))


# @spec WS-STORE-002
def test_config_defaults(workstream):
    config = read_state(workstream["state_path"])["config"]
    assert (config["mode"], config["retention"]) == ("collab", "archive")
    assert not {"plan_mode", "collaborative", "council_auto_resolve"} & set(config)


# @spec WS-STORE-002
@pytest.mark.parametrize("file, mode, retention", [
    ({"mode": "delivery", "retention": "delete"}, "delivery", "delete"),
    ({"retention": "delete"}, "collab", "delete"),
    ({"mode": "collab-qa"}, "collab-qa", "archive"),
])
def test_config_from_global_file(repo, forge_home, file, mode, retention):
    (forge_home / "config.json").write_text(json.dumps(file))
    assert new(repo, "--name", "demo").json["status"] == "CREATED"
    config = demo_config(forge_home)
    assert (config["mode"], config["retention"]) == (mode, retention)


# @spec WS-STORE-002, WS-STORE-003
@pytest.mark.parametrize("mode", MODES)
def test_every_mode_name_is_accepted(repo, forge_home, mode):
    (forge_home / "config.json").write_text(json.dumps({"mode": mode}))
    assert new(repo, "--name", "demo").json["status"] == "CREATED"
    assert demo_config(forge_home)["mode"] == mode


# @spec WS-STORE-003
@pytest.mark.parametrize("file, sets", [
    ({"mode": "bogus"}, []),
    ({"mode": "Collab"}, []),
    ({}, ["--set", "mode=bogus"]),
    ({"mode": "delivery"}, ["--set", "mode=sequential"]),
    ({}, ["--set", "mode=42"]),
], ids=["file", "file-case", "set", "set-over-good-file", "set-json-number"])
def test_bad_mode_is_usage_error(repo, forge_home, file, sets):
    (forge_home / "config.json").write_text(json.dumps(file))
    res = new(repo, "--name", "demo", *sets)
    assert res.returncode == 2 and res.stderr
    nothing_created(repo, forge_home)


# @spec WS-STORE-003
def test_set_mode_overrides_bad_file_mode(repo, forge_home):
    (forge_home / "config.json").write_text(json.dumps({"mode": "bogus"}))
    assert new(repo, "--name", "demo", "--set", "mode=delivery").json["status"] == "CREATED"
    assert demo_config(forge_home)["mode"] == "delivery"


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
        "config": {"repo_mode": "NAIVE", "mode": "collab", "retention": "archive"},
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


# @spec WS-NEW-024
def test_new_copies_task_file(repo, forge_home, tmp_path):
    text = "Add top_words(text, n).\n\nTies broken alphabetically; return (word, count) pairs.\n"
    task = tmp_path / "task.txt"
    task.write_text(text)
    res = new(repo, "--name", "top-words", "--task-file", task)
    assert res.json["status"] == "CREATED"
    assert (Path(res.json["state_path"]).parent / "task.md").read_text() == text


# @spec WS-NEW-019, WS-NEW-024
def test_new_without_task_file_writes_no_task(workstream):
    assert not (workstream["state_path"].parent / "task.md").exists()


# @spec WS-NEW-025
@pytest.mark.parametrize("content", [None, "", "  \n\t\n", b"\xff\xfe bad"], ids=["missing", "empty", "blank", "not-utf8"])
def test_bad_task_file_is_usage_error(repo, forge_home, tmp_path, content):
    task = tmp_path / "task.txt"
    if isinstance(content, bytes):
        task.write_bytes(content)
    elif content is not None:
        task.write_text(content)
    res = new(repo, "--name", "top-words", "--task-file", task)
    assert res.returncode == 2 and res.stderr
    assert "feat/top-words" not in git(repo, "branch", "--list")
    assert not (repo / ".claude/worktrees").exists() or not any((repo / ".claude/worktrees").iterdir())
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
    new(repo, "--name", "demo", "--set", "retention=delete", "--set", 'test_patterns=["spec/**"]',
        "--set", "mode=delivery")
    config = read_state(state_dir(forge_home, "local", "project", "feat-demo") / "state.json")["config"]
    assert config["retention"] == "delete"
    assert config["test_patterns"] == ["spec/**"]
    assert config["mode"] == "delivery"


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


def reason_file(tmp_path, text="  Superseded by the auth rewrite.\n\n"):
    path = tmp_path / "reason.txt"
    path.write_text(text)
    return path


def archived_state(forge_home):
    [archived] = list((forge_home / "archive/local/project").iterdir())
    assert archived.name.startswith("feat-demo-")
    return read_state(archived / "state.json")


# @spec WS-CLOSE-006
def test_close_with_reason_records_dropped(workstream, forge_home, tmp_path):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    res = close(workstream, "--reason-file", reason_file(tmp_path))
    assert res.returncode == 0 and res.json["status"] == "CLOSED"
    assert not workstream["state_path"].parent.exists()
    dropped = archived_state(forge_home)["dropped"]
    assert set(dropped) == {"reason", "at"}
    assert dropped["reason"] == "Superseded by the auth rewrite."
    at = datetime.fromisoformat(dropped["at"])
    assert at.utcoffset() == timedelta(0)
    assert before <= at <= datetime.now(timezone.utc)


# @spec WS-CLOSE-006
def test_drop_archives_even_with_retention_delete(repo, forge_home, tmp_path):
    new(repo, "--name", "demo", "--set", "retention=delete")
    res = rs(repo / ".claude/worktrees/feat-demo", "close", "--workstream", "feat-demo", *holder_args(),
             "--reason-file", reason_file(tmp_path, "not needed"))
    assert res.returncode == 0, res.stderr
    assert res.json["status"] == "CLOSED"
    assert not state_dir(forge_home, "local", "project", "feat-demo").exists()
    assert archived_state(forge_home)["dropped"]["reason"] == "not needed"


# @spec WS-CLOSE-006, WS-CLOSE-003
def test_drop_with_worktree_removal(workstream, forge_home, tmp_path):
    res = rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(), "--remove-worktree",
             "--reason-file", reason_file(tmp_path))
    assert res.returncode == 0, res.stderr
    assert res.json["status"] == "CLOSED"
    assert not workstream["worktree"].exists()
    assert git(workstream["repo"], "branch", "--list", "feat/demo")
    assert archived_state(forge_home)["dropped"]["reason"] == "Superseded by the auth rewrite."


# @spec WS-CLOSE-006, WS-CLOSE-005
def test_drop_with_dirty_worktree_writes_no_reason(workstream, tmp_path):
    Path(workstream["worktree"], "README.md").write_text("dirty\n")
    before = workstream["state_path"].read_bytes()
    res = rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(), "--remove-worktree",
             "--reason-file", reason_file(tmp_path))
    assert res.returncode == 0 and res.json["status"] == "WORKTREE_DIRTY"
    assert workstream["state_path"].read_bytes() == before


# @spec WS-CLOSE-007
@pytest.mark.parametrize("content", [None, "", "  \n\t\n"], ids=["missing", "empty", "blank"])
def test_bad_reason_file_is_usage_error(workstream, forge_home, tmp_path, content):
    bad = tmp_path / "bad-reason.txt"
    if content is not None:
        bad.write_text(content)
    state_before = workstream["state_path"].read_bytes()
    lock_before = lock_path(workstream).read_text()
    res = rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(), "--remove-worktree",
             "--reason-file", bad)
    assert res.returncode == 2 and res.stderr
    assert workstream["worktree"].exists()
    assert workstream["state_path"].read_bytes() == state_before
    assert lock_path(workstream).read_text() == lock_before
    assert not (forge_home / "archive").exists()
    # The refusal was about the file, not the option: a readable reason then closes.
    res = close(workstream, "--reason-file", reason_file(tmp_path))
    assert res.returncode == 0, res.stderr
    assert res.json["status"] == "CLOSED"


# @spec WS-CLOSE-008
@pytest.mark.parametrize("extra", [[], ["--remove-worktree"]])
def test_close_without_reason_writes_no_dropped(workstream, forge_home, extra):
    assert rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args(),
              *extra).json["status"] == "CLOSED"
    assert "dropped" not in archived_state(forge_home)


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


# ----------------------------------------- idea/triage workstreams, base_behind, promote

HOSTED_URL = "https://github.com/acme/widgets.git"
STUDY_ISSUE = {**ISSUE, "labels": []}
BUILD_WS = "feat-42-fix-the-login-bug-urgent"  # what `new --issue 42` names STUDY_ISSUE's workstream
STUDY_WS = {"idea": "idea-study", "triage": "triage-42"}


@pytest.fixture
def hosted(cloned_repo, forge_home, monkeypatch, tmp_path):
    """A clone whose origin looks hosted (identity acme/widgets) but fetches and pushes to the local bare repo.

    A `git` shim on PATH rewrites the hosted URL to the bare repository for `git fetch` and `git push`, so
    `remote get-url` still reports the hosted URL. Returns (clone, bare).
    """
    clone, bare = cloned_repo
    git(clone, "remote", "set-url", "origin", HOSTED_URL)
    real = shutil.which("git")
    shim_dir = tmp_path / "gitshim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:1] in (['fetch'], ['push']):\n"
        f"    args = ['-c', 'url.{bare}.insteadOf={HOSTED_URL}'] + args\n"
        f"os.execv({real!r}, [{real!r}] + args)\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    return clone, bare


def hosted_state_dir(forge_home, ws):
    return state_dir(forge_home, "acme", "widgets", ws)


def task_file(tmp_path, text="Study the thing.\n", name="idea-task.txt"):
    path = tmp_path / name
    path.write_text(text)
    return path


def study_args(kind, tmp_path):
    if kind == "idea":
        return ["--idea", "--name", "study", "--task-file", task_file(tmp_path)]
    return ["--triage", "42"]


def start_study(kind, clone, tmp_path, fake_gh):
    fake_gh.set("issue view", STUDY_ISSUE)
    res = new(clone, *study_args(kind, tmp_path))
    assert res.json["status"] == "CREATED", res.json
    return res


def push_upstream(tmp_path, bare, count=1, name="other"):
    """Push `count` new commits to the bare origin's main; return the new tip."""
    other = tmp_path / name
    git(tmp_path, "clone", "-q", str(bare), str(other))
    git(other, "config", "user.email", "t@example.invalid")
    git(other, "config", "user.name", "T")
    tip = None
    for i in range(count):
        tip = commit(other, {f"up{i}.txt": f"{i}\n"}, f"upstream {i}")
    git(other, "push", "-q", "origin", "main")
    return tip


def nothing_created(forge_home, clone):
    assert not (forge_home / "state").exists() or not any((forge_home / "state").rglob("state.json"))
    wt = clone / ".claude/worktrees"
    assert not wt.exists() or not any(wt.iterdir())


# @spec WS-ID-006
def test_ws_id_006_detached_head_uses_open_workstream_at_worktree_path_else_detached_sha(workstream):
    wt = workstream["worktree"]
    git(wt, "checkout", "-q", "--detach")
    assert wake(wt).json["identity"]["workstream"] == "feat-demo"
    # a detached checkout that no open workstream owns falls back to detached-<sha>
    repo = workstream["repo"]
    git(repo, "checkout", "-q", "--detach")
    assert wake(repo).json["identity"]["workstream"] == f"detached-{head(repo)[:7]}"


# @spec WS-ID-006
def test_ws_id_006_detached_idea_worktree_resolves_its_workstream(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    res = start_study("idea", clone, tmp_path, fake_gh)
    res = wake(Path(res.json["worktree_path"]))
    assert res.json["identity"]["workstream"] == "idea-study"
    assert res.json["status"] == "OK"


# @spec WS-ID-006
def test_ws_id_006_closed_workstream_is_not_matched(workstream):
    wt = workstream["worktree"]
    git(wt, "checkout", "-q", "--detach")
    sha = head(wt)
    assert rs(workstream["repo"], "close", "--workstream", "feat-demo", *holder_args()).json["status"] == "CLOSED"
    assert wake(wt).json["identity"]["workstream"] == f"detached-{sha[:7]}"


# @spec WS-NEW-007
def test_ws_new_007_default_base_applies_to_idea_triage_and_branch_adoption(hosted, forge_home, fake_gh, tmp_path):
    clone, bare = hosted
    upstream = push_upstream(tmp_path, bare)
    res = start_study("idea", clone, tmp_path, fake_gh)  # fetches origin/main
    assert git(clone, "rev-parse", "origin/main") == upstream
    state = read_state(Path(res.json["state_path"]))
    assert state["anchors"]["workstream_base_sha"] == upstream
    assert state["identity"]["base_branch"] == "main"


# @spec WS-NEW-007
def test_ws_new_007_branch_adoption_fetches_default_base(hosted, forge_home, tmp_path):
    clone, bare = hosted
    git(clone, "branch", "feat/existing")
    upstream = push_upstream(tmp_path, bare)
    assert git(clone, "rev-parse", "origin/main") != upstream
    res = new(clone, "--branch", "feat/existing")
    assert res.json["status"] == "CREATED"
    assert git(clone, "rev-parse", "origin/main") == upstream  # fetched
    assert read_state(Path(res.json["state_path"]))["identity"]["base_branch"] == "main"


# @spec WS-NEW-007
def test_ws_new_007_default_branch_is_origin_head_target_for_triage(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    git(clone, "push", "-q", "origin", "main:develop")
    git(clone, "remote", "set-head", "origin", "develop")
    res = start_study("triage", clone, tmp_path, fake_gh)
    assert res.json["identity"]["base_branch"] == "develop"


# @spec WS-NEW-015
def test_ws_new_015_duplicate_matches_any_flow_and_idea_report_url_and_ignores_null(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    start_study("idea", clone, tmp_path, fake_gh)
    sp = hosted_state_dir(forge_home, "idea-study") / "state.json"
    # an idea workstream that filed the issue
    state = read_state(sp)
    state["lifecycle"]["report_url"] = STUDY_ISSUE["url"]
    write_state(sp, state)
    res = new(clone, "--issue", "42")
    assert res.json["status"] == "DUPLICATE" and res.json["workstream"] == "idea-study"
    # a null report_url never matches
    state["lifecycle"]["report_url"] = None
    write_state(sp, state)
    assert new(clone, "--issue", "42").json["status"] == "CREATED"


# @spec WS-NEW-015
def test_ws_new_015_triage_workstream_issue_is_a_duplicate(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    start_study("triage", clone, tmp_path, fake_gh)
    res = new(clone, "--issue", "42")
    assert res.json["status"] == "DUPLICATE" and res.json["workstream"] == "triage-42"
    res = new(clone, "--triage", "42")
    assert res.json["status"] == "DUPLICATE" and res.json["workstream"] == "triage-42"


# @spec WS-NEW-015
def test_ws_new_015_report_url_of_a_non_idea_workstream_is_ignored(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    assert new(clone, "--name", "demo").json["status"] == "CREATED"
    sp = hosted_state_dir(forge_home, "feat-demo") / "state.json"
    state = read_state(sp)
    state["lifecycle"]["report_url"] = STUDY_ISSUE["url"]
    write_state(sp, state)
    assert new(clone, "--issue", "42").json["status"] == "CREATED"


# @spec WS-NEW-015
def test_ws_new_015_null_issue_of_idea_workstream_never_matches(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    start_study("idea", clone, tmp_path, fake_gh)  # issue null, pr null, no report_url
    assert new(clone, "--issue", "42").json["status"] == "CREATED"


# @spec WS-NEW-026
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_new_026_idea_triage_worktree_detached_at_default_branch_no_branch(hosted, forge_home, fake_gh, tmp_path, kind):
    clone, bare = hosted
    upstream = push_upstream(tmp_path, bare)
    branches = git(clone, "branch", "--format=%(refname)")
    res = start_study(kind, clone, tmp_path, fake_gh)
    wt = Path(res.json["worktree_path"])
    assert wt == clone / ".claude/worktrees" / STUDY_WS[kind]
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached
    assert head(wt) == upstream  # at origin/<default> after the fetch
    assert git(clone, "branch", "--format=%(refname)") == branches
    assert str(wt) in git(clone, "worktree", "list")


# @spec WS-NEW-027
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_new_027_idea_triage_identity_branch_null_base_branch_and_base_sha(hosted, forge_home, fake_gh, tmp_path, kind):
    clone, bare = hosted
    upstream = push_upstream(tmp_path, bare)
    res = start_study(kind, clone, tmp_path, fake_gh)
    state = read_state(Path(res.json["state_path"]))
    assert state["identity"]["branch"] is None
    assert state["identity"]["base_branch"] == "main"
    assert state["anchors"]["workstream_base_sha"] == upstream == head(res.json["worktree_path"])


# @spec WS-NEW-028
def test_ws_new_028_idea_named_idea_slug_flow_idea_issue_null(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    res = new(clone, "--idea", "--name", "faster-search", "--task-file", task_file(tmp_path, "Make search faster.\n"))
    assert res.json["status"] == "CREATED"
    ident = res.json["identity"]
    assert (ident["workstream"], ident["flow"], ident["issue"]) == ("idea-faster-search", "idea", None)
    assert (Path(res.json["state_path"]).parent / "task.md").read_text() == "Make search faster.\n"


# @spec WS-NEW-029
@pytest.mark.parametrize("which", ["no-name", "no-task-file"])
def test_ws_new_029_idea_without_name_or_task_file_is_usage_error(hosted, forge_home, tmp_path, which):
    clone, _ = hosted
    args = ["--idea", "--task-file", task_file(tmp_path)] if which == "no-name" else ["--idea", "--name", "study"]
    res = new(clone, *args)
    assert res.returncode != 0 and res.stderr
    nothing_created(forge_home, clone)


# @spec WS-NEW-030
def test_ws_new_030_triage_reads_issue_via_gh_names_triage_number(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    fake_gh.set("issue view", {**STUDY_ISSUE, "labels": [{"name": "bug"}]})
    res = new(clone, "--triage", "42")
    assert res.json["status"] == "CREATED"
    ident = res.json["identity"]
    assert (ident["workstream"], ident["flow"], ident["issue"]) == ("triage-42", "triage", STUDY_ISSUE["url"])
    assert ident["branch"] is None
    assert any(c[:3] == ["issue", "view", "42"] and "--json" in c for c in fake_gh.calls())
    assert not (Path(res.json["state_path"]).parent / "task.md").exists()  # --task-file is optional


# @spec WS-NEW-030
def test_ws_new_030_triage_task_file_is_copied(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    res = new(clone, "--triage", "42", "--task-file", task_file(tmp_path, "My own words.\n"))
    assert (Path(res.json["state_path"]).parent / "task.md").read_text() == "My own words.\n"


# @spec WS-NEW-031
@pytest.mark.parametrize("kind", ["idea", "triage"])
@pytest.mark.parametrize("extra", [["--base", "main"], ["--branch", "feat/x"], ["--type", "feat"],
                                   ["--base-confirmed"], ["--issue", "42"], ["--pr", "7"]])
def test_ws_new_031_idea_triage_conflicting_options_are_usage_error(hosted, forge_home, fake_gh, tmp_path, kind, extra):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    git(clone, "branch", "feat/x")
    res = new(clone, *study_args(kind, tmp_path), *extra)
    assert res.returncode != 0 and res.stderr
    nothing_created(forge_home, clone)


# @spec WS-NEW-031
@pytest.mark.parametrize("args", [["--idea", "--name", "study", "--triage", "42"], ["--triage", "42", "--name", "study"]])
def test_ws_new_031_idea_with_triage_or_triage_with_name_is_usage_error(hosted, forge_home, fake_gh, tmp_path, args):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    res = new(clone, *args, "--task-file", task_file(tmp_path))
    assert res.returncode != 0 and res.stderr
    nothing_created(forge_home, clone)


# @spec WS-NEW-032
@pytest.mark.parametrize("args, tracker", [(["--name", "demo"], None), (["--issue", "42"], "github"),
                                           (["--idea", "--name", "study", "--task-file", "T"], "github"),
                                           (["--triage", "42"], "github")])
def test_ws_new_032_tracker_github_for_issue_idea_triage_else_null(hosted, forge_home, fake_gh, tmp_path, args, tracker):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    args = [task_file(tmp_path) if a == "T" else a for a in args]
    res = new(clone, *args)
    assert res.json["status"] == "CREATED"
    assert read_state(Path(res.json["state_path"]))["identity"]["tracker"] == tracker


# @spec WS-NEW-032
def test_ws_new_032_tracker_follows_issue_in_a_local_repository(repo, forge_home, fake_gh):
    fake_gh.set("issue view", STUDY_ISSUE)
    assert new(repo, "--issue", "42").json["identity"]["tracker"] == "github"
    assert new(repo, "--name", "demo").json["identity"]["tracker"] is None


# @spec WS-NEW-033
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_new_033_last_known_head_sha_set_for_idea_triage_null_otherwise(hosted, forge_home, fake_gh, tmp_path, kind):
    clone, _ = hosted
    res = start_study(kind, clone, tmp_path, fake_gh)
    anchors = read_state(Path(res.json["state_path"]))["anchors"]
    assert anchors["last_known_head_sha"] == anchors["workstream_base_sha"] == head(res.json["worktree_path"])


# @spec WS-NEW-033
def test_ws_new_033_last_known_head_sha_null_for_build(hosted, forge_home, fake_gh):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    for args in (["--name", "demo"], ["--issue", "42"]):
        res = new(clone, *args)
        assert read_state(Path(res.json["state_path"]))["anchors"]["last_known_head_sha"] is None


# @spec WS-NEW-033, WS-DRIFT-008
def test_ws_new_033_commit_in_study_worktree_is_drift_and_anchor_is_kept(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    res = start_study("idea", clone, tmp_path, fake_gh)
    wt = Path(res.json["worktree_path"])
    base = head(wt)
    made = commit(wt, {"x.txt": "x\n"}, "stray commit")
    report = wake(wt, "--workstream", "idea-study").json
    assert report["status"] == "OK"
    assert report["drift"]["kind"] == "ahead"
    assert [c["sha"] for c in report["drift"]["new_commits"]] == [made]
    assert read_state(Path(res.json["state_path"]))["anchors"]["last_known_head_sha"] == base


# @spec WS-NEW-034
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_new_034_idea_triage_in_local_repo_returns_no_tracker(repo, forge_home, fake_gh, tmp_path, kind):
    fake_gh.set("issue view", STUDY_ISSUE)
    res = new(repo, *study_args(kind, tmp_path))
    assert res.json["status"] == "NO_TRACKER"
    nothing_created(forge_home, repo)


# @spec WS-NEW-034
def test_ws_new_034_origin_that_is_not_hosted_is_local(cloned_repo, forge_home, tmp_path):
    clone, _ = cloned_repo  # origin is a local path
    assert new(clone, *study_args("idea", tmp_path)).json["status"] == "NO_TRACKER"


# @spec WS-NEW-035
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_new_035_idea_triage_without_commits_returns_no_commits(empty_repo, forge_home, fake_gh, tmp_path, kind):
    set_origin(empty_repo, HOSTED_URL)
    fake_gh.set("issue view", STUDY_ISSUE)
    res = new(empty_repo, *study_args(kind, tmp_path))
    assert res.json["status"] == "NO_COMMITS"
    nothing_created(forge_home, empty_repo)


# @spec WS-NEW-036
@pytest.mark.parametrize("args", [["--name", "demo"], ["--idea", "--name", "study", "--task-file", "T"]])
def test_ws_new_036_default_base_fetch_failure_returns_fetch_failed(hosted, forge_home, tmp_path, args):
    clone, bare = hosted
    shutil.rmtree(bare)
    args = [task_file(tmp_path) if a == "T" else a for a in args]
    res = new(clone, *args)
    assert res.json["status"] == "FETCH_FAILED" and res.json["detail"]
    nothing_created(forge_home, clone)
    assert not git(clone, "branch", "--list", "feat/demo")


# @spec WS-NEW-037
def test_ws_new_037_base_naming_no_commit_returns_base_not_found(repo, forge_home):
    res = new(repo, "--name", "demo", "--base", "ghost", "--base-confirmed")
    assert res.json["status"] == "BASE_NOT_FOUND"
    nothing_created(forge_home, repo)
    assert not git(repo, "branch", "--list", "feat/demo")


# @spec WS-NEW-037
def test_ws_new_037_default_branch_naming_no_commit_returns_base_not_found(repo, forge_home):
    git(repo, "branch", "-m", "main", "trunk")  # neither main nor master exists: the default resolves to main
    res = new(repo, "--name", "demo")
    assert res.json["status"] == "BASE_NOT_FOUND"
    nothing_created(forge_home, repo)


# @spec WS-NEW-038
def test_ws_new_038_new_refusals_returned_in_documented_order(hosted, empty_repo, forge_home, fake_gh, tmp_path):
    clone, bare = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    # NO_TRACKER before NO_COMMITS: a local repository with no commits
    assert new(empty_repo, *study_args("idea", tmp_path)).json["status"] == "NO_TRACKER"
    # GH_FAILED before BASE_CONFIRM_REQUIRED
    git(clone, "branch", "feat/parent")
    fake_gh.fail("issue view", "boom")
    assert new(clone, "--issue", "42", "--base", "feat/parent").json["status"] == "GH_FAILED"
    fake_gh.set("issue view", STUDY_ISSUE)
    # DUPLICATE before STATE_EXISTS and BRANCH_EXISTS (all three apply to a repeated `new --issue`)
    assert new(clone, "--issue", "42").json["status"] == "CREATED"
    assert new(clone, "--issue", "42").json["status"] == "DUPLICATE"
    # STATE_EXISTS before BRANCH_EXISTS
    assert new(clone, "--name", "demo").json["status"] == "CREATED"
    assert new(clone, "--name", "demo").json["status"] == "STATE_EXISTS"
    # BRANCH_EXISTS before BASE_CONFIRM_REQUIRED
    git(clone, "branch", "feat/taken")
    assert new(clone, "--name", "taken", "--base", "feat/parent").json["status"] == "BRANCH_EXISTS"
    # BASE_CONFIRM_REQUIRED before BASE_NOT_FOUND
    assert new(clone, "--name", "fresh", "--base", "ghost").json["status"] == "BASE_CONFIRM_REQUIRED"


# @spec WS-NEW-038
def test_ws_new_038_no_commits_before_gh_failed(empty_repo, forge_home, fake_gh, tmp_path):
    set_origin(empty_repo, HOSTED_URL)
    fake_gh.fail("issue view", "boom")
    assert new(empty_repo, "--triage", "42").json["status"] == "NO_COMMITS"


# @spec WS-NEW-038
def test_ws_new_038_fetch_failed_before_base_not_found(hosted, forge_home, tmp_path):
    clone, bare = hosted
    shutil.rmtree(bare)
    git(clone, "update-ref", "-d", "refs/remotes/origin/main")  # the default base now names no commit
    assert new(clone, "--name", "demo").json["status"] == "FETCH_FAILED"


# @spec WS-NEW-038
def test_ws_new_038_base_not_found_before_branch_checked_out(repo, forge_home):
    git(repo, "checkout", "-q", "-b", "feat/x")  # checked out in the main checkout
    res = new(repo, "--branch", "feat/x", "--base", "ghost", "--base-confirmed")
    assert res.json["status"] == "BASE_NOT_FOUND"
    assert new(repo, "--branch", "feat/x").json["status"] == "BRANCH_CHECKED_OUT"


# @spec WS-NEW-039
def test_ws_new_039_new_sets_promoted_from_null(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    fake_gh.set("issue view", STUDY_ISSUE)
    for args in (["--name", "demo"], ["--issue", "42"], study_args("idea", tmp_path)):
        ident = new(clone, *args).json["identity"]
        assert "promoted_from" in ident and ident["promoted_from"] is None
    fake_gh.set("issue view", {**STUDY_ISSUE, "number": 43, "url": "https://github.com/acme/widgets/issues/43"})
    assert new(clone, "--triage", "43").json["identity"]["promoted_from"] is None  # 42 is taken by the build one


# ------------------------------------------------------------------- base_behind


# @spec WS-WAKE-004
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_wake_004_wake_includes_base_behind_for_idea_triage_ok(hosted, forge_home, fake_gh, tmp_path, kind):
    clone, _ = hosted
    res = start_study(kind, clone, tmp_path, fake_gh)
    report = wake(Path(res.json["worktree_path"]), "--workstream", STUDY_WS[kind]).json
    assert report["status"] == "OK"
    assert {"identity", "state_path", "lock", "drift", "dirty", "base_behind"} <= set(report)


# @spec WS-WAKE-004
def test_ws_wake_004_build_wake_has_no_base_behind(workstream):
    report = wake(workstream["worktree"]).json
    assert report["status"] == "OK" and "base_behind" not in report


# @spec WS-WAKE-004
def test_ws_wake_004_base_behind_only_when_status_ok(hosted, forge_home, fake_gh, tmp_path, live_pid):
    clone, _ = hosted
    res = start_study("idea", clone, tmp_path, fake_gh)
    report = wake(Path(res.json["worktree_path"]), "--workstream", "idea-study", pid=live_pid, session="other").json
    assert report["status"] == "LOCKED" and "base_behind" not in report


# @spec WS-WAKE-005
def test_ws_wake_005_wake_base_behind_counts_commits_from_origin_base_branch(hosted, forge_home, fake_gh, tmp_path):
    clone, bare = hosted
    res = start_study("idea", clone, tmp_path, fake_gh)
    wt = Path(res.json["worktree_path"])
    assert wake(wt, "--workstream", "idea-study").json["base_behind"] == 0
    push_upstream(tmp_path, bare, count=2)
    # wake never fetches: the count only moves once origin/main does
    assert wake(wt, "--workstream", "idea-study").json["base_behind"] == 0
    git(clone, "fetch", "-q", "origin")
    assert wake(wt, "--workstream", "idea-study").json["base_behind"] == 2
    assert git(clone, "rev-parse", "main") != git(clone, "rev-parse", "origin/main")  # the local branch is not used


# @spec WS-WAKE-005
def test_ws_wake_005_uses_recorded_base_branch_not_current_default(hosted, forge_home, fake_gh, tmp_path):
    clone, bare = hosted
    res = start_study("triage", clone, tmp_path, fake_gh)
    git(clone, "push", "-q", "origin", "main:develop")
    git(clone, "remote", "set-head", "origin", "develop")  # the default branch is now develop
    push_upstream(tmp_path, bare, count=1)
    git(clone, "fetch", "-q", "origin")
    report = wake(Path(res.json["worktree_path"]), "--workstream", "triage-42").json
    assert report["base_behind"] == 1


def retag_as_study(workstream, base_branch="main"):
    """A local repository cannot create a study workstream, so retag a build workstream as one."""
    state = read_state(workstream["state_path"])
    state["identity"].update(flow="idea", branch=None, base_branch=base_branch)
    write_state(workstream["state_path"], state)
    git(workstream["worktree"], "checkout", "-q", "--detach")


# @spec WS-WAKE-005
def test_ws_wake_005_without_origin_counts_from_local_branch(workstream):
    retag_as_study(workstream)
    commit(workstream["repo"], {"a.txt": "a\n"}, "a")
    commit(workstream["repo"], {"b.txt": "b\n"}, "b")
    report = wake(workstream["worktree"], "--workstream", "feat-demo").json
    assert report["status"] == "OK" and report["base_behind"] == 2


# @spec WS-WAKE-006
def test_ws_wake_006_wake_base_behind_null_when_ref_missing_status_ok(hosted, forge_home, fake_gh, tmp_path):
    clone, _ = hosted
    res = start_study("idea", clone, tmp_path, fake_gh)
    git(clone, "update-ref", "-d", "refs/remotes/origin/main")
    report = wake(Path(res.json["worktree_path"]), "--workstream", "idea-study").json
    assert report["status"] == "OK"
    assert "base_behind" in report and report["base_behind"] is None


# @spec WS-WAKE-006
def test_ws_wake_006_without_origin_missing_local_branch_is_null(workstream):
    retag_as_study(workstream, base_branch="gone")
    report = wake(workstream["worktree"], "--workstream", "feat-demo").json
    assert report["status"] == "OK"
    assert "base_behind" in report and report["base_behind"] is None


# ------------------------------------------------------------------------ promote


def promote(cwd, ws, *extra, pid=None, session=HOLDER_SESSION):
    return rs(cwd, "promote", "--workstream", ws, "--worktree-root", ".claude/worktrees",
              *holder_args(pid, session), *extra)


def make_source(clone, forge_home, fake_gh, tmp_path, kind="idea", mode="collab-plan"):
    """An idea or triage workstream, held by the test process, with a promote request recorded."""
    res = start_study(kind, clone, tmp_path, fake_gh)
    sp = Path(res.json["state_path"])
    proposal = task_file(tmp_path, "Proposal: do the thing.\n", "proposal.md")
    state = read_state(sp)
    state["config"]["mode"] = mode
    state["lifecycle"]["promote"] = {"issue": STUDY_ISSUE["url"], "proposal": str(proposal)}
    write_state(sp, state)
    return {"clone": clone, "ws": STUDY_WS[kind], "state_path": sp, "worktree": Path(res.json["worktree_path"]),
            "proposal": proposal, "forge_home": forge_home, "tmp": tmp_path}


@pytest.fixture
def source(hosted, forge_home, fake_gh, tmp_path):
    return make_source(hosted[0], forge_home, fake_gh, tmp_path)


def build_dir(src):
    return hosted_state_dir(src["forge_home"], BUILD_WS)


def archived(src, ws):
    root = src["forge_home"] / "archive/acme/widgets"
    return sorted(root.glob(f"{ws}-*")) if root.exists() else []


def source_untouched(src, before):
    assert src["state_path"].is_file() and src["state_path"].read_text() == before
    assert (src["state_path"].parent / "lock.json").is_file()
    assert src["worktree"].exists()
    assert not archived(src, src["ws"])
    assert not build_dir(src).exists()


# @spec WS-LOCK-008
def test_ws_lock_008_promote_by_non_holder_returns_not_holder(source, live_pid):
    before = source["state_path"].read_text()
    lock_before = (source["state_path"].parent / "lock.json").read_text()
    res = promote(source["clone"], source["ws"], pid=live_pid, session="intruder")
    assert res.json["status"] == "NOT_HOLDER"
    assert (source["state_path"].parent / "lock.json").read_text() == lock_before
    source_untouched(source, before)


# @spec WS-PROMOTE-001
@pytest.mark.parametrize("request_value", ["absent", None])
def test_ws_promote_001_promote_without_lifecycle_promote_returns_promote_not_requested(source, request_value):
    state = read_state(source["state_path"])
    if request_value == "absent":
        del state["lifecycle"]["promote"]
    else:
        state["lifecycle"]["promote"] = None
    write_state(source["state_path"], state)
    before = source["state_path"].read_text()
    assert promote(source["clone"], source["ws"]).json["status"] == "PROMOTE_NOT_REQUESTED"
    source_untouched(source, before)


# @spec WS-PROMOTE-002
def test_ws_promote_002_promote_remove_worktree_dirty_returns_worktree_dirty(source):
    (source["worktree"] / "scratch.txt").write_text("uncommitted\n")
    before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"], "--remove-worktree")
    assert res.returncode == 0 and res.json["status"] == "WORKTREE_DIRTY"
    assert "scratch.txt" in json.dumps(res.json)
    source_untouched(source, before)
    assert not (source["clone"] / ".claude/worktrees" / BUILD_WS).exists()
    assert not git(source["clone"], "branch", "--list", "feat/42-*")


# @spec WS-PROMOTE-002
def test_ws_promote_002_dirty_source_without_remove_worktree_is_promoted(source):
    (source["worktree"] / "scratch.txt").write_text("uncommitted\n")
    assert promote(source["clone"], source["ws"]).json["status"] == "PROMOTED"


# @spec WS-PROMOTE-003
def test_ws_promote_003_promote_creates_build_workstream_with_promoted_from(source):
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "PROMOTED", res.json
    state = read_state(build_dir(source) / "state.json")
    ident = state["identity"]
    assert (ident["workstream"], ident["flow"], ident["issue"]) == (BUILD_WS, "build", STUDY_ISSUE["url"])
    assert ident["branch"] == "feat/42-fix-the-login-bug-urgent"
    assert ident["promoted_from"] == "idea-study"
    assert state["config"]["mode"] == "collab-plan"  # the source's, not the default
    assert (build_dir(source) / "task.md").read_text() == "Proposal: do the thing.\n"
    wt = source["clone"] / ".claude/worktrees" / BUILD_WS
    assert wt.is_dir() and git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "feat/42-fix-the-login-bug-urgent"


# @spec WS-PROMOTE-004
@pytest.mark.parametrize("kind", ["idea", "triage"])
def test_ws_promote_004_promote_duplicate_check_ignores_source(hosted, forge_home, fake_gh, tmp_path, kind):
    src = make_source(hosted[0], forge_home, fake_gh, tmp_path, kind=kind)
    if kind == "idea":  # the idea filed the very issue it promotes
        state = read_state(src["state_path"])
        state["lifecycle"]["report_url"] = STUDY_ISSUE["url"]
        write_state(src["state_path"], state)
    assert promote(src["clone"], src["ws"]).json["status"] == "PROMOTED"


# @spec WS-PROMOTE-005
def test_ws_promote_005_promote_new_failure_returned_source_unchanged(source, fake_gh):
    # another open workstream already tracks the issue
    assert new(source["clone"], "--issue", "42").json["status"] == "CREATED"
    other = hosted_state_dir(source["forge_home"], BUILD_WS) / "state.json"
    before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "DUPLICATE" and res.json["workstream"] == BUILD_WS
    assert read_state(other)["identity"]["promoted_from"] is None
    assert source["state_path"].read_text() == before
    assert (source["state_path"].parent / "lock.json").is_file() and source["worktree"].exists()
    assert not archived(source, source["ws"])


# @spec WS-PROMOTE-005
def test_ws_promote_005_gh_failure_is_returned_source_unchanged(source, fake_gh):
    fake_gh.fail("issue view", "no such issue")
    before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "GH_FAILED" and "no such issue" in res.json["detail"]
    source_untouched(source, before)


# @spec WS-PROMOTE-006
def test_ws_promote_006_promote_closes_source_after_creating_build(source):
    assert promote(source["clone"], source["ws"]).json["status"] == "PROMOTED"
    assert not source["state_path"].parent.exists()
    [arch] = archived(source, source["ws"])
    assert (arch / "state.json").is_file()
    assert source["worktree"].exists()  # no --remove-worktree
    assert (build_dir(source) / "state.json").is_file()


# @spec WS-PROMOTE-006
def test_ws_promote_006_remove_worktree_removes_the_source_worktree(source):
    assert promote(source["clone"], source["ws"], "--remove-worktree").json["status"] == "PROMOTED"
    assert not source["worktree"].exists()
    assert len(archived(source, source["ws"])) == 1
    assert (source["clone"] / ".claude/worktrees" / BUILD_WS).is_dir()


# @spec WS-PROMOTE-007
def test_ws_promote_007_promote_returns_promoted_with_build_identity_and_lock(source):
    res = promote(source["clone"], source["ws"])
    assert res.returncode == 0 and res.json["status"] == "PROMOTED"
    ident = res.json["identity"]
    assert (ident["workstream"], ident["flow"], ident["promoted_from"]) == (BUILD_WS, "build", "idea-study")
    assert Path(res.json["worktree_path"]) == source["clone"] / ".claude/worktrees" / BUILD_WS
    lock = json.loads((build_dir(source) / "lock.json").read_text())
    assert (lock["holder_pid"], lock["holder_session"]) == (os.getpid(), HOLDER_SESSION)


# @spec WS-PROMOTE-008
@pytest.mark.parametrize("content", [None, "  \n\t\n"], ids=["missing", "blank"])
def test_ws_promote_008_promote_new_usage_error_exits_source_unchanged(source, content):
    if content is None:
        source["proposal"].unlink()
    else:
        source["proposal"].write_text(content)
    before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"])
    assert res.returncode == 2 and res.stderr
    source_untouched(source, before)
    assert not (source["clone"] / ".claude/worktrees" / BUILD_WS).exists()
    assert not git(source["clone"], "branch", "--list", "feat/42-*")


def existing_build(src, task="Custom task.\n", mode="collab-code"):
    """An open build workstream for the issue marked as promoted from the source, as a failed close leaves it."""
    res = new(src["clone"], "--issue", "42", "--task-file", task_file(src["tmp"], task, "custom.md"),
              "--set", f"mode={mode}")
    assert res.json["status"] == "CREATED", res.json
    sp = Path(res.json["state_path"])
    state = read_state(sp)
    state["identity"]["promoted_from"] = src["ws"]
    write_state(sp, state)
    return sp.parent


# @spec WS-PROMOTE-009
def test_ws_promote_009_promote_refusals_returned_in_documented_order(source, live_pid, fake_gh):
    clone, ws = source["clone"], source["ws"]
    good = source["state_path"].read_text()

    def set_state(text):
        source["state_path"].write_text(text)

    # NOT_HOLDER before STATE_INVALID
    set_state("{not json")
    assert promote(clone, ws, pid=live_pid, session="intruder").json["status"] == "NOT_HOLDER"
    # STATE_INVALID before PROMOTE_NOT_REQUESTED
    assert promote(clone, ws).json["status"] == "STATE_INVALID"
    # PROMOTE_NOT_REQUESTED before WORKTREE_DIRTY
    state = json.loads(good)
    state["lifecycle"]["promote"] = None
    set_state(json.dumps(state))
    (source["worktree"] / "scratch.txt").write_text("x\n")
    assert promote(clone, ws, "--remove-worktree").json["status"] == "PROMOTE_NOT_REQUESTED"
    # WORKTREE_DIRTY before a refusal of new
    set_state(good)
    fake_gh.fail("issue view", "boom")
    assert promote(clone, ws, "--remove-worktree").json["status"] == "WORKTREE_DIRTY"
    # a refusal of new comes next
    assert promote(clone, ws).json["status"] == "GH_FAILED"


# @spec WS-PROMOTE-009
def test_ws_promote_009_worktree_dirty_before_locked(source, live_pid):
    existing = existing_build(source)
    write_lock({"state_path": existing / "state.json"}, live_pid)
    (source["worktree"] / "scratch.txt").write_text("x\n")
    assert promote(source["clone"], source["ws"], "--remove-worktree").json["status"] == "WORKTREE_DIRTY"
    assert promote(source["clone"], source["ws"]).json["status"] == "LOCKED"


# @spec WS-PROMOTE-010
def test_ws_promote_010_promote_retry_reuses_open_build_with_promoted_from(source):
    existing = existing_build(source)
    config_before = read_state(existing / "state.json")["config"]
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "PROMOTED", res.json
    assert res.json["identity"]["workstream"] == BUILD_WS
    assert (existing / "task.md").read_text() == "Custom task.\n"  # not replaced with the proposal
    assert read_state(existing / "state.json")["config"] == config_before  # mode not copied from the source
    assert config_before["mode"] == "collab-code"
    assert not source["state_path"].parent.exists() and len(archived(source, source["ws"])) == 1
    assert sorted(p.name for p in existing.parent.iterdir()) == [BUILD_WS]


# @spec WS-PROMOTE-010
@pytest.mark.parametrize("marker", ["idea-someone-else", None])
def test_ws_promote_010_build_without_this_promoted_from_is_not_taken(source, marker):
    existing = existing_build(source)
    state = read_state(existing / "state.json")
    state["identity"]["promoted_from"] = marker
    write_state(existing / "state.json", state)
    before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "DUPLICATE" and res.json["workstream"] == BUILD_WS
    assert source["state_path"].read_text() == before


# @spec WS-PROMOTE-011
@pytest.mark.parametrize("held", ["none", "dead", "same-holder"])
def test_ws_promote_011_promote_retry_acquires_existing_build_lock(source, dead_pid, held):
    existing = existing_build(source)
    lock = existing / "lock.json"
    if held == "none":
        lock.unlink()
    elif held == "dead":
        write_lock({"state_path": existing / "state.json"}, dead_pid)
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "PROMOTED"
    held_by = json.loads(lock.read_text())
    assert (held_by["holder_pid"], held_by["holder_session"]) == (os.getpid(), HOLDER_SESSION)


# @spec WS-PROMOTE-012
@pytest.mark.parametrize("where", ["live-local", "other-host"])
def test_ws_promote_012_promote_retry_locked_by_other_holder_returns_locked(source, live_pid, dead_pid, where):
    existing = existing_build(source)
    if where == "live-local":
        write_lock({"state_path": existing / "state.json"}, live_pid)
        pid = live_pid
    else:
        write_lock({"state_path": existing / "state.json"}, dead_pid, host="elsewhere.example")
        pid = dead_pid
    lock_before = (existing / "lock.json").read_text()
    state_before = (existing / "state.json").read_text()
    source_before = source["state_path"].read_text()
    res = promote(source["clone"], source["ws"])
    assert res.json["status"] == "LOCKED"
    assert "other-session" in json.dumps(res.json) and str(pid) in json.dumps(res.json)
    assert (existing / "lock.json").read_text() == lock_before
    assert (existing / "state.json").read_text() == state_before
    assert source["state_path"].read_text() == source_before
    assert (source["state_path"].parent / "lock.json").is_file() and not archived(source, source["ws"])


# @spec WS-PROMOTE-013
def test_ws_promote_013_promote_close_failure_returns_close_status_keeps_build_open(source):
    clone = source["clone"]
    git(clone, "worktree", "lock", str(source["worktree"]))  # makes `git worktree remove` fail on a clean tree
    res = promote(clone, source["ws"], "--remove-worktree")
    assert res.returncode == 0 and res.json["status"] == "WORKTREE_DIRTY"
    assert res.json["detail"]
    assert BUILD_WS in json.dumps(res.json)
    assert (build_dir(source) / "state.json").is_file()
    lock = json.loads((build_dir(source) / "lock.json").read_text())
    assert (lock["holder_pid"], lock["holder_session"]) == (os.getpid(), HOLDER_SESSION)
    assert source["state_path"].is_file() and source["worktree"].exists() and not archived(source, source["ws"])
    # a repeated promote finishes the close against the same build workstream
    git(clone, "worktree", "unlock", str(source["worktree"]))
    res = promote(clone, source["ws"], "--remove-worktree")
    assert res.json["status"] == "PROMOTED", res.json
    assert res.json["identity"]["workstream"] == BUILD_WS
    assert not source["worktree"].exists() and len(archived(source, source["ws"])) == 1
    assert sorted(p.name for p in build_dir(source).parent.iterdir()) == [BUILD_WS]
