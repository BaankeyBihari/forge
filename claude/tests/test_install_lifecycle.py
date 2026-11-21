"""Installer lifecycle tests: install record, install sequence, overlap rules, running
workstreams, pins, uninstall, vendor, output (docs/intent/installer/installer-specs.md).

Black box: every test runs claude/installer.sh through the conftest fixtures and inspects
files, exit codes, output, and ~/.forge/install.json under the fake home.
"""

import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

from conftest import LID_COMMIT, LID_PLUGIN, git, init_repo, read_record, record_path, \
    config_key, write_installed_plugins, write_rev

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="permission bits do not bind root")


# ---------------------------------------------------------------- helpers

def ok(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def assert_refused(proc, code):
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert re.search(rf"^forge-install: {code}: \S", proc.stderr, re.M), proc.stderr


def out(proc):
    return proc.stdout + proc.stderr


def gkey(home):
    return config_key(home / ".claude")


def pkey(repo):
    return os.path.join(os.path.realpath(repo), ".claude")


def default_state(home):
    return os.path.realpath(home / ".forge")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seed_record(home, installs):
    target = record_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"installs": installs}, indent=2, ensure_ascii=False) + "\n")


def entry(scope, key_dir, repo=None, **extra):
    base = {"cli": "claude", "scope": scope, "repo": str(repo) if repo else None,
            "state_dir": str(key_dir / "state"), "source": "/nowhere", "source_sha": None,
            "source_dirty": None, "agent_prefix": "forge:" if scope == "plugin" else "",
            "models": {}, "files": {}, "settings_file": str(key_dir / "settings.json"),
            "settings_created": False, "settings_added": {}, "exclude_added": [],
            "backup": None, "complete": True}
    base.update(extra)
    return base


def snapshot(*roots):
    """Every regular file and link under roots: path -> content or link target."""
    snap = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = Path(dirpath, name)
                if p.is_symlink():
                    snap[str(p)] = ("link", os.readlink(p))
                elif p.is_file() and not (p.name == "index" and p.parent.name == ".git"):
                    snap[str(p)] = ("file", p.read_bytes())
    return snap


@contextlib.contextmanager
def read_only(path, mode=0o555):
    os.chmod(path, mode)
    try:
        yield
    finally:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def write_ws_lock(state_dir, ws="ws-alpha", raw=None, **fields):
    lock = Path(state_dir) / "state/acme/app" / ws / "lock.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        data = {"holder_pid": os.getpid(), "holder_session": "sess-1", "host": socket.gethostname(),
                "acquired_at": "2026-11-21T00:00:00Z"}
        data.update(fields)
        raw = json.dumps({k: v for k, v in data.items() if v is not None})
    lock.write_text(raw)
    return lock


def installed_files(config):
    found = set()
    for top in list(config.glob("skills/forge*")) + list(config.glob("agents/forge-*.md")):
        paths = [top] if top.is_file() else [p for p in top.rglob("*") if p.is_file()]
        found.update(str(p) for p in paths)
    return found


def last_line(text):
    return [line for line in text.splitlines() if line.strip()][-1]


# ---------------------------------------------------------------- install record

# @spec INST-REC-001
def test_record_stays_at_home_forge_when_state_dir_moves(install, home, tmp_path):
    ok(install(FORGE_HOME=tmp_path / "state"))
    assert record_path(home).is_file()
    assert not (tmp_path / "state/install.json").exists()


# @spec INST-REC-002
def test_global_install_keyed_by_config_dir(install, home):
    ok(install())
    assert list(read_record(home)["installs"]) == [gkey(home)]


# @spec INST-REC-002
def test_project_install_keyed_by_repo_dot_claude(install, home, repo):
    ok(install("--project", repo))
    assert list(read_record(home)["installs"]) == [pkey(repo)]


REC_FIELDS = {"cli", "scope", "repo", "state_dir", "source", "source_sha", "source_dirty", "agent_prefix",
              "models", "files", "settings_file", "settings_created", "settings_added", "exclude_added",
              "backup", "complete"}


# @spec INST-REC-003
def test_global_record_entry_fields(install, home):
    ok(install())
    e = read_record(home)["installs"][gkey(home)]
    assert REC_FIELDS <= set(e)
    assert (e["cli"], e["scope"], e["repo"], e["exclude_added"], e["complete"]) == \
        ("claude", "global", None, [], True)
    assert e["state_dir"] == default_state(home)


# @spec INST-REC-003
def test_project_record_entry_fields(install, home, repo):
    ok(install("--project", repo))
    e = read_record(home)["installs"][pkey(repo)]
    assert REC_FIELDS <= set(e)
    assert (e["cli"], e["scope"], e["repo"], e["backup"], e["complete"]) == \
        ("claude", "project", os.path.realpath(repo), None, True)
    assert isinstance(e["exclude_added"], list) and e["exclude_added"]


# @spec INST-REC-004
def test_record_files_hold_every_installed_file_with_its_hash(install, home):
    ok(install())
    files = read_record(home)["installs"][gkey(home)]["files"]
    assert installed_files(home / ".claude") <= set(files)
    for path, digest in files.items():
        assert os.path.isabs(path) and sha(path) == digest, path


# @spec INST-REC-005
def test_other_cli_entry_left_unchanged(install, home, tmp_path):
    other = {"cli": "codex", "scope": "global", "anything": [1, {"x": "y"}]}
    seed_record(home, {str(tmp_path / "codex/.codex"): other})
    ok(install())
    assert read_record(home)["installs"][str(tmp_path / "codex/.codex")] == other


# @spec INST-REC-006
def test_stale_project_entry_ignored_removed_and_printed(install, home, tmp_path):
    gone = tmp_path / "gone"
    stale_key = str(gone / ".claude")
    seed_record(home, {stale_key: entry("project", gone / ".claude", repo=gone)})
    proc = ok(install())  # would be INSTALL_OVERLAP if the stale project entry counted
    assert stale_key not in read_record(home)["installs"]
    assert stale_key in out(proc)


# @spec INST-REC-007
def test_record_format_two_space_trailing_newline_non_ascii(install, home, tmp_path):
    seed_record(home, {str(tmp_path / "codex/.codex"): {"cli": "codex", "note": "café"}})
    ok(install())
    text = record_path(home).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    assert "café" in text


# @spec INST-REC-007
def test_record_replaced_by_rename(install, home, tmp_path):
    seed_record(home, {str(tmp_path / "codex/.codex"): {"cli": "codex"}})
    before = record_path(home).read_text()
    held = tmp_path / "held-record"
    os.link(record_path(home), held)
    ok(install())
    assert held.read_text() == before  # an in-place write would have changed the shared inode
    assert gkey(home) in read_record(home)["installs"]
    assert not (home / ".forge/install.json.forge-tmp").exists()


# ---------------------------------------------------------------- install sequence

# @spec INST-SEQ-001
def test_install_lock_removed_when_run_ends(install, home):
    ok(install())
    assert not (home / ".forge/install.lock").exists()
    ok(install("--uninstall"))
    assert not (home / ".forge/install.lock").exists()


@pytest.mark.parametrize("content", ["live", "", "not a lock"])
# @spec INST-SEQ-002
def test_busy_lock_refuses_and_stays(install, home, content):
    lock = home / ".forge/install.lock"
    lock.parent.mkdir(parents=True)
    text = str(os.getpid()) if content == "live" else content
    lock.write_text(text)
    proc = install()
    assert_refused(proc, "INSTALL_BUSY")
    assert str(lock) in proc.stderr or os.path.realpath(lock) in proc.stderr
    assert lock.read_text() == text
    assert not record_path(home).exists()


# @spec INST-SEQ-004
def test_late_check_refusal_writes_nothing(install, home, repo):
    write_installed_plugins(home / ".claude", {})  # PLUGIN_MISSING: a check late in the list
    before = snapshot(home, repo)
    assert_refused(install("--project", repo), "PLUGIN_MISSING")
    assert snapshot(home, repo) == before


@needs_non_root
# @spec INST-SEQ-005
def test_record_entry_written_before_files(install, home, checkout):
    skills = home / ".claude/skills"
    skills.mkdir()
    with read_only(skills):
        proc = install()
    assert proc.returncode == 1
    e = read_record(home)["installs"][gkey(home)]
    assert e["complete"] is False
    planned = str(skills / "forge/SKILL.md")
    assert e["files"][planned] == sha(checkout / "claude/skills/forge/SKILL.md")
    assert default_state(home) in json.dumps(e["settings_added"])
    assert e["exclude_added"] == []


# @spec INST-SEQ-006
def test_changed_install_file_replaced_by_rename(install, home, tmp_path):
    ok(install())
    agent = home / ".claude/agents/forge-dev.md"
    before = agent.read_text()
    held = tmp_path / "held-agent"
    os.link(agent, held)
    ok(install("--preset", "opus"))
    assert agent.read_text() != before
    assert held.read_text() == before


# @spec INST-SEQ-007
def test_reinstall_removes_dropped_skill_and_its_directories(install, home, checkout):
    ok(install())
    dropped = home / ".claude/skills/forge-memory"
    assert dropped.is_dir()
    (checkout / "claude/skills/forge-memory").unlink()
    ok(install())
    assert not dropped.exists()
    assert not [p for p in read_record(home)["installs"][gkey(home)]["files"] if p.startswith(str(dropped) + "/")]


# @spec INST-SEQ-007
def test_reinstall_removes_dropped_agent(install, home, checkout):
    ok(install())
    (checkout / "claude/agents/forge-reporter.md").unlink()
    ok(install())
    dropped = home / ".claude/agents/forge-reporter.md"
    assert not dropped.exists()
    assert str(dropped) not in read_record(home)["installs"][gkey(home)]["files"]


@needs_non_root
# @spec INST-SEQ-008
def test_settings_failure_after_files_before_exclude(install, home, repo):
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{}\n")
    exclude = repo / ".git/info/exclude"
    exclude_before = exclude.read_text()
    with read_only(settings, 0o444):
        proc = install("--project", repo)
    assert proc.returncode == 1
    assert (repo / ".claude/agents/forge-dev.md").is_file()  # files step ran before settings
    assert exclude.read_text() == exclude_before            # exclude step not reached
    assert read_record(home)["installs"][pkey(repo)]["complete"] is False


@needs_non_root
# @spec INST-SEQ-009
def test_step_failure_stops_with_step_failed_and_incomplete_entry(install, home):
    agents = home / ".claude/agents"
    agents.mkdir()
    with read_only(agents):
        proc = install()
    assert_refused(proc, "STEP_FAILED")
    assert read_record(home)["installs"][gkey(home)]["complete"] is False


# @spec INST-SEQ-010
def test_reinstall_removes_leftover_tmp_files(install, home):
    ok(install())
    leftover = home / ".claude/agents/.forge-dev.md.forge-tmp"
    leftover.write_text("partial")
    ok(install("--preset", "opus"))
    assert not leftover.exists()


# @spec INST-SEQ-010
def test_uninstall_removes_leftover_tmp_files(install, home):
    ok(install())
    leftover = home / ".claude/agents/.forge-dev.md.forge-tmp"
    leftover.write_text("partial")
    ok(install("--uninstall"))
    assert not leftover.exists()


@needs_non_root
# @spec INST-SEQ-011
def test_reinstall_first_entry_keeps_earlier_files(install, home, checkout):
    ok(install())
    earlier = set(read_record(home)["installs"][gkey(home)]["files"])
    (checkout / "claude/skills/forge-memory").unlink()
    (checkout / "claude/skills/forge/NOTES.md").write_text("notes\n")
    with read_only(home / ".claude/agents"):
        proc = install("--preset", "opus")  # agent files change, so the files step must write there
    assert proc.returncode == 1
    e = read_record(home)["installs"][gkey(home)]
    assert e["complete"] is False
    assert earlier <= set(e["files"])
    assert str(home / ".claude/skills/forge/NOTES.md") in e["files"]


# ---------------------------------------------------------------- overlap rules

# @spec INST-OVL-001
def test_other_cli_global_entry_does_not_block_project_install(install, home, repo, tmp_path):
    seed_record(home, {str(tmp_path / "codex/.codex"): {"cli": "codex", "scope": "global"}})
    ok(install("--project", repo))


# @spec INST-OVL-002
def test_same_key_and_scope_reinstalls_in_place(install, home):
    ok(install())
    files = read_record(home)["installs"][gkey(home)]["files"]
    ok(install())
    rec = read_record(home)["installs"]
    assert list(rec) == [gkey(home)] and rec[gkey(home)]["complete"] is True
    assert set(rec[gkey(home)]["files"]) == set(files)


# @spec INST-OVL-003
def test_project_install_refused_over_global(install, repo):
    ok(install())
    assert_refused(install("--project", repo), "INSTALL_OVERLAP")


# @spec INST-OVL-004
def test_global_install_refused_over_projects_naming_each(install, tmp_path):
    a, b = init_repo(tmp_path / "repo-a"), init_repo(tmp_path / "repo-b")
    ok(install("--project", a))
    ok(install("--project", b))
    proc = install()
    assert_refused(proc, "INSTALL_OVERLAP")
    assert os.path.realpath(a) in proc.stderr and os.path.realpath(b) in proc.stderr


@pytest.mark.parametrize("scope_args", [(), ("--global",), ("--project", "REPO")])
# @spec INST-OVL-005
def test_plain_install_refused_over_plugin(install, home, repo, scope_args):
    seed_record(home, {gkey(home): entry("plugin", home / ".claude")})
    args = [str(repo) if a == "REPO" else a for a in scope_args]
    assert_refused(install(*args), "INSTALL_OVERLAP")


# @spec INST-OVL-006
def test_overlap_names_existing_install_and_uninstall_command(install, home, repo):
    ok(install("--project", repo))
    proc = install()
    assert_refused(proc, "INSTALL_OVERLAP")
    assert "--uninstall" in proc.stderr and os.path.realpath(repo) in proc.stderr


@pytest.mark.parametrize("kind", ["file", "directory", "link"])
# @spec INST-OVL-007
def test_unrecorded_existing_target_refused(install, home, tmp_path, kind):
    config = home / ".claude"
    if kind == "file":
        (config / "agents").mkdir()
        (config / "agents/forge-dev.md").write_text("mine\n")
    elif kind == "directory":
        (config / "skills/forge").mkdir(parents=True)
        (config / "skills/forge/SKILL.md").write_text("mine\n")
    else:
        (tmp_path / "elsewhere").mkdir()
        (config / "skills").mkdir()
        (config / "skills/forge-dev").symlink_to(tmp_path / "elsewhere")
    assert_refused(install(), "FILE_EXISTS")


# @spec INST-OVL-008
def test_reinstall_refused_naming_every_edited_file(install, home):
    ok(install())
    edited = [home / ".claude/agents/forge-dev.md", home / ".claude/skills/forge/SKILL.md"]
    for p in edited:
        p.write_text("edited\n")
    proc = install()
    assert_refused(proc, "FILES_EDITED")
    for p in edited:
        assert str(p) in proc.stderr


# @spec INST-OVL-009
def test_reinstall_rewrites_absent_recorded_file(install, home):
    ok(install())
    agent = home / ".claude/agents/forge-dev.md"
    agent.unlink()
    ok(install())
    assert sha(agent) == read_record(home)["installs"][gkey(home)]["files"][str(agent)]


# @spec INST-OVL-010
def test_project_install_beside_other_repo_project_install(install, home, tmp_path):
    a, b = init_repo(tmp_path / "repo-a"), init_repo(tmp_path / "repo-b")
    ok(install("--project", a))
    ok(install("--project", b))
    assert {pkey(a), pkey(b)} <= set(read_record(home)["installs"])


# @spec INST-OVL-011
def test_project_key_equal_to_config_dir_refused(install, repo):
    write_installed_plugins(repo / ".claude", {LID_PLUGIN: [{"scope": "user", "gitCommitSha": LID_COMMIT}]})
    assert_refused(install("--project", repo, CLAUDE_CONFIG_DIR=repo / ".claude"), "INSTALL_OVERLAP")


# ---------------------------------------------------------------- running workstreams

HELD_LOCKS = {
    "unparseable": {"raw": "{not json"},
    "no host": {"host": None, "holder_pid": "DEAD"},
    "other host": {"host": "not-" + socket.gethostname(), "holder_pid": "DEAD"},
    "live pid": {},
}


@pytest.mark.parametrize("variant", list(HELD_LOCKS))
# @spec INST-RUN-001
def test_reinstall_refused_while_workstream_lock_held(install, home, variant):
    ok(install())
    fields = {k: (dead_pid() if v == "DEAD" else v) for k, v in HELD_LOCKS[variant].items()}
    write_ws_lock(default_state(home), **fields)
    proc = install()
    assert_refused(proc, "WORKSTREAM_ACTIVE")
    assert "ws-alpha" in proc.stderr


# @spec INST-RUN-001
def test_refusal_names_each_active_workstream(install, home):
    ok(install())
    write_ws_lock(default_state(home), "ws-alpha")
    write_ws_lock(default_state(home), "ws-beta")
    proc = install()
    assert_refused(proc, "WORKSTREAM_ACTIVE")
    assert "ws-alpha" in proc.stderr and "ws-beta" in proc.stderr


# @spec INST-RUN-001
def test_dead_local_workstream_lock_does_not_block_reinstall(install, home):
    ok(install())
    write_ws_lock(default_state(home), holder_pid=dead_pid())
    ok(install())


# @spec INST-RUN-001
def test_first_install_not_blocked_by_workstream_lock(install, home):
    write_ws_lock(default_state(home))
    ok(install())


@pytest.mark.parametrize("where", ["affected", "requested"])
# @spec INST-RUN-001
def test_lock_under_affected_or_requested_state_dir_blocks(install, tmp_path, where):
    a, b = tmp_path / "state-a", tmp_path / "state-b"
    ok(install(FORGE_HOME=a))
    write_ws_lock(a if where == "affected" else b)
    assert_refused(install(FORGE_HOME=b), "WORKSTREAM_ACTIVE")


# ---------------------------------------------------------------- pins and plugin check

GOOD_PIN = {"repo": "https://github.com/jszmajda/lid", "commit": LID_COMMIT, "plugin": LID_PLUGIN, "required": True}
BAD_REVS = {
    "not json": "{nope",
    "not an object": "[]",
    "pin not an object": {"lid": "x"},
    "repo not a string": {"lid": {**GOOD_PIN, "repo": 5}},
    "no repo": {"lid": {k: v for k, v in GOOD_PIN.items() if k != "repo"}},
    "short commit": {"lid": {**GOOD_PIN, "commit": "abc123"}},
    "no commit": {"lid": {k: v for k, v in GOOD_PIN.items() if k != "commit"}},
    "plugin not a string": {"lid": {**GOOD_PIN, "plugin": 7}},
    "required not a boolean": {"lid": {**GOOD_PIN, "required": "yes"}},
    "required without plugin": {"lid": {k: v for k, v in GOOD_PIN.items() if k != "plugin"}},
}


@pytest.mark.parametrize("variant", list(BAD_REVS))
# @spec INST-PIN-001
def test_invalid_rev_refuses_install(install, checkout, variant):
    rev = BAD_REVS[variant]
    if isinstance(rev, str):
        (checkout / "rev").write_text(rev)
    else:
        write_rev(checkout, rev)
    assert_refused(install(), "REV_INVALID")


@pytest.mark.parametrize("args", [(), ("--vendor",)])
# @spec INST-PIN-001
def test_missing_rev_refuses_install_and_vendor(install, checkout, args):
    (checkout / "rev").unlink()
    assert_refused(install(*args), "REV_INVALID")


# @spec INST-PIN-002, INST-PIN-003
def test_non_user_scope_entry_not_counted(install, home):
    write_installed_plugins(home / ".claude", {LID_PLUGIN: [{"scope": "project", "gitCommitSha": LID_COMMIT}]})
    assert_refused(install(), "PLUGIN_MISSING")


# @spec INST-PIN-002, INST-PIN-003
def test_missing_installed_plugins_counts_none(install, home):
    (home / ".claude/plugins/installed_plugins.json").unlink()
    assert_refused(install(), "PLUGIN_MISSING")


@needs_non_root
# @spec INST-PIN-002, INST-PIN-003
def test_unreadable_installed_plugins_counts_none(install, home):
    with read_only(home / ".claude/plugins/installed_plugins.json", 0o000):
        proc = install()
    assert_refused(proc, "PLUGIN_MISSING")


# @spec INST-PIN-003
def test_missing_optional_plugin_does_not_refuse(install, checkout):
    write_rev(checkout, {"lid": GOOD_PIN, "extra": {"repo": "https://example.invalid/x", "commit": "a" * 40,
                                                    "plugin": "extra@nowhere", "required": False}})
    ok(install())


# @spec INST-PIN-004
def test_commit_mismatch_warns_and_installs(install, home):
    other = "b" * 40
    write_installed_plugins(home / ".claude", {LID_PLUGIN: [{"scope": "user", "gitCommitSha": other}]})
    proc = ok(install())
    assert LID_COMMIT in out(proc) and other in out(proc)


# ---------------------------------------------------------------- uninstall

@pytest.mark.parametrize("has_global", [False, True])
# @spec INST-UNI-001
def test_uninstall_without_entry_refused(install, repo, has_global):
    if has_global:
        ok(install())
    assert_refused(install("--uninstall", "--project", repo), "NOT_INSTALLED")


# @spec INST-UNI-002
def test_uninstall_refused_while_workstream_runs_removes_nothing(install, home):
    ok(install())
    files = read_record(home)["installs"][gkey(home)]["files"]
    write_ws_lock(default_state(home))
    assert_refused(install("--uninstall"), "WORKSTREAM_ACTIVE")
    assert all(Path(p).is_file() for p in files)


# @spec INST-UNI-002
def test_uninstall_refused_while_install_lock_held(install, home):
    ok(install())
    (home / ".forge/install.lock").write_text(str(os.getpid()))
    assert_refused(install("--uninstall"), "INSTALL_BUSY")
    assert all(Path(p).is_file() for p in read_record(home)["installs"][gkey(home)]["files"])


# @spec INST-UNI-003
def test_uninstall_removes_files_and_emptied_skill_dirs(install, home):
    ok(install())
    files = read_record(home)["installs"][gkey(home)]["files"]
    ok(install("--uninstall"))
    assert not [p for p in files if os.path.lexists(p)]
    assert not list((home / ".claude/skills").glob("forge*"))


# @spec INST-UNI-003
def test_uninstall_treats_absent_file_as_removed(install, home):
    ok(install())
    (home / ".claude/agents/forge-dev.md").unlink()
    ok(install("--uninstall"))


# @spec INST-UNI-003
def test_uninstall_keeps_and_names_edited_file(install, home):
    ok(install())
    edited = home / ".claude/agents/forge-dev.md"
    edited.write_text("mine\n")
    proc = install("--uninstall")
    assert edited.read_text() == "mine\n"
    assert str(edited) in out(proc)
    assert not (home / ".claude/agents/forge-qa.md").exists()


# @spec INST-UNI-003
def test_uninstall_force_removes_edited_file(install, home):
    ok(install())
    edited = home / ".claude/agents/forge-dev.md"
    edited.write_text("mine\n")
    ok(install("--uninstall", "--force"))
    assert not edited.exists()


# @spec INST-UNI-005, INST-UNI-006
def test_uninstall_restores_user_settings_values(install, repo):
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    original = {"env": {"OTHER": "1"}, "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(x)"]}, "theme": "dark"}
    settings.write_text(json.dumps(original, indent=2) + "\n")
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    assert json.loads(settings.read_text()) == original


# @spec INST-UNI-005
def test_uninstall_leaves_value_user_changed(install, home, repo):
    ok(install("--project", repo))
    settings = repo / ".claude/settings.local.json"
    data = json.loads(settings.read_text())
    data["env"]["FORGE_HOME"] = "/elsewhere"
    settings.write_text(json.dumps(data, indent=2) + "\n")
    ok(install("--uninstall", "--project", repo))
    assert json.loads(settings.read_text())["env"]["FORGE_HOME"] == "/elsewhere"


# @spec INST-UNI-006
def test_uninstall_drops_emptied_keys(install, repo):
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text('{"theme": "dark"}\n')
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    assert json.loads(settings.read_text()) == {"theme": "dark"}


# @spec INST-UNI-007
def test_uninstall_deletes_settings_file_it_created(install, repo):
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    assert not (repo / ".claude/settings.local.json").exists()


# @spec INST-UNI-007
def test_uninstall_keeps_empty_settings_file_it_did_not_create(install, repo):
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{}\n")
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    assert json.loads(settings.read_text()) == {}


# @spec INST-UNI-008
def test_uninstall_removes_exclude_lines(install, repo):
    exclude = repo / ".git/info/exclude"
    before = exclude.read_text()
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    assert exclude.read_text() == before


# @spec INST-UNI-008
def test_uninstall_keeps_and_names_exclude_line_of_kept_file(install, repo):
    ok(install("--project", repo))
    (repo / ".claude/agents/forge-dev.md").write_text("mine\n")
    proc = install("--uninstall", "--project", repo)
    lines = (repo / ".git/info/exclude").read_text().splitlines()
    assert "/.claude/agents/forge-dev.md" in lines
    assert "/.claude/agents/forge-qa.md" not in lines
    assert "/.claude/agents/forge-dev.md" in out(proc)


@needs_non_root
# @spec INST-UNI-009
def test_uninstall_step_failure_runs_remaining_steps_and_keeps_entries(install, home, repo):
    ok(install("--project", repo))
    agents = repo / ".claude/agents"
    agent_files = {str(p) for p in agents.glob("forge-*.md")}
    with read_only(agents):
        proc = install("--uninstall", "--project", repo)
    assert_refused(proc, "STEP_FAILED")
    settings = repo / ".claude/settings.local.json"
    assert not settings.exists() or default_state(home) not in settings.read_text()  # settings step still ran
    assert agent_files <= set(read_record(home)["installs"][pkey(repo)]["files"])


@needs_non_root
# @spec INST-UNI-009
def test_uninstall_step_failed_outranks_files_kept(install, repo):
    ok(install("--project", repo))
    (repo / ".claude/skills/forge/SKILL.md").write_text("mine\n")
    with read_only(repo / ".claude/agents"):
        proc = install("--uninstall", "--project", repo)
    assert_refused(proc, "STEP_FAILED")


# @spec INST-UNI-010
def test_uninstall_of_last_install_deletes_record(install, home):
    ok(install())
    ok(install("--uninstall"))
    assert not record_path(home).exists()


# @spec INST-UNI-010
def test_uninstall_keeps_other_installs_in_record(install, home, tmp_path):
    a, b = init_repo(tmp_path / "repo-a"), init_repo(tmp_path / "repo-b")
    ok(install("--project", a))
    ok(install("--project", b))
    ok(install("--uninstall", "--project", a))
    installs = read_record(home)["installs"]
    assert pkey(a) not in installs and pkey(b) in installs


# @spec INST-UNI-011
def test_uninstall_never_restores_backup(install, home):
    settings = home / ".claude/settings.json"
    settings.write_text('{"theme": "dark"}\n')
    ok(install())
    data = json.loads(settings.read_text())
    data["later"] = 1
    settings.write_text(json.dumps(data, indent=2) + "\n")
    ok(install("--uninstall"))
    assert json.loads(settings.read_text()) == {"theme": "dark", "later": 1}


# @spec INST-UNI-011
def test_uninstall_never_touches_workstream_state(install, home):
    ok(install())
    notes = Path(default_state(home)) / "state/acme/app/ws-alpha/notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("keep\n")
    ok(install("--uninstall"))
    assert notes.read_text() == "keep\n"


# @spec INST-UNI-012
def test_uninstall_with_kept_file_leaves_only_it_in_record(install, home, repo):
    ok(install("--project", repo))
    kept = repo / ".claude/agents/forge-dev.md"
    kept.write_text("mine\n")
    proc = install("--uninstall", "--project", repo)
    assert_refused(proc, "FILES_KEPT")
    e = read_record(home)["installs"][pkey(repo)]
    assert set(e["files"]) == {str(kept)}
    assert e["exclude_added"] == ["/.claude/agents/forge-dev.md"]


# ---------------------------------------------------------------- vendor

def upstream(tmp_path, name):
    path = init_repo(tmp_path / f"up-{name}", {"README.md": f"{name} 1\n"})
    first = git(path, "rev-parse", "HEAD")
    (path / "README.md").write_text(f"{name} 2\n")
    git(path, "commit", "-qam", "second")
    return path, first


def vendor_pin(repo, commit):
    return {"repo": str(repo), "commit": commit, "plugin": "x@local", "required": False}


def head_of(path):
    return git(path, "rev-parse", "HEAD")


def is_detached(path):
    return subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=path).returncode != 0


# @spec INST-VEN-001, INST-VEN-002
def test_vendor_clones_each_pin_by_name_detached_at_commit(install, checkout, tmp_path):
    up, first = upstream(tmp_path, "alpha")
    write_rev(checkout, {"alpha": vendor_pin(up, first)})
    ok(install("--vendor"))
    clone = checkout / ".vendor/alpha"
    assert head_of(clone) == first and is_detached(clone)


# @spec INST-VEN-002
def test_vendor_fetches_existing_clone_to_new_pin(install, checkout, tmp_path):
    up, first = upstream(tmp_path, "alpha")
    write_rev(checkout, {"alpha": vendor_pin(up, first)})
    ok(install("--vendor"))
    (up / "README.md").write_text("alpha 3\n")
    git(up, "commit", "-qam", "third")
    third = head_of(up)
    write_rev(checkout, {"alpha": vendor_pin(up, third)})
    ok(install("--vendor"))
    clone = checkout / ".vendor/alpha"
    assert head_of(clone) == third and is_detached(clone)


@pytest.mark.parametrize("failure", ["missing repo", "unknown commit"])
# @spec INST-VEN-003
def test_vendor_failure_reported_others_continue(install, checkout, tmp_path, failure):
    good, first = upstream(tmp_path, "good")
    bad = vendor_pin(tmp_path / "missing", "c" * 40) if failure == "missing repo" else vendor_pin(good, "c" * 40)
    write_rev(checkout, {"bad-pin": bad, "good": vendor_pin(good, first)})
    proc = install("--vendor")
    assert_refused(proc, "VENDOR_FAILED")
    assert "bad-pin" in out(proc)
    assert head_of(checkout / ".vendor/good") == first


# ---------------------------------------------------------------- output

# @spec INST-OUT-001
def test_refusal_prints_code_line_and_exits_1(install):
    proc = install("--uninstall")
    assert proc.returncode == 1
    assert re.search(r"^forge-install: NOT_INSTALLED: \S", proc.stderr, re.M), proc.stderr


def summary_count_ok(line, key, count):
    return re.search(rf"(?<!\d){count}(?!\d)", line.replace(key, "")) is not None


# @spec INST-OUT-002
def test_first_install_summary(install, home):
    proc = ok(install())
    line = last_line(proc.stdout)
    count = len(read_record(home)["installs"][gkey(home)]["files"])
    assert gkey(home) in line and "global" in line and "balanced" in line
    assert summary_count_ok(line, gkey(home), count)


# @spec INST-OUT-002
def test_reinstall_without_model_flags_summary_says_recorded(install, home):
    ok(install())
    assert "recorded" in last_line(ok(install()).stdout)


# @spec INST-OUT-002
def test_summary_names_preset_and_models_file(install, tmp_path):
    models = tmp_path / "models.json"
    models.write_text('{"default": "sonnet"}\n')
    line = last_line(ok(install("--preset", "opus", "--models", models)).stdout)
    assert f"opus+{models}" in line


# @spec INST-OUT-003
def test_refused_reinstall_changes_no_file_and_drops_its_lock(install, home):
    ok(install())
    (home / ".claude/agents/forge-dev.md").write_text("edited\n")
    before = snapshot(home)
    assert_refused(install(), "FILES_EDITED")
    assert snapshot(home) == before
    assert not (home / ".forge/install.lock").exists()
