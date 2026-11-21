"""Installer tests: entry point, command line, state directory, source tree
(docs/intent/installer/installer-specs.md, INST-CLI, INST-STATE, INST-SRC).

Black box: each test runs claude/installer.sh from a throwaway checkout (conftest.py) and
inspects exit codes, output, the fake home, and ~/.forge/install.json.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (LID_COMMIT, LID_PLUGIN, base_env, build_checkout, config_key, git, init_repo,
                      read_record, record_path, run_installer, write_installed_plugins, write_rev)

REAL_PYTHON = os.path.realpath(sys.executable)


def refused(proc, code):
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert any(line.startswith(f"forge-install: {code}: ") for line in proc.stderr.splitlines()), proc.stderr


def usage(proc):
    refused_lines = [line for line in proc.stderr.splitlines() if line.startswith("forge-install: USAGE: ")]
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert refused_lines, proc.stderr


def ok(proc):
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    return proc


def entry(home, key):
    return read_record(home)["installs"][key]


def global_key(home):
    return config_key(home / ".claude")


def project_key(repo):
    return os.path.join(os.path.realpath(repo), ".claude")


def snapshot(root):
    """Every path under root with its type, content or link target, and mtime."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            if os.path.islink(p):
                out[p] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[p] = ("dir", st.st_mtime_ns)
            else:
                out[p] = ("file", st.st_mtime_ns, Path(p).read_bytes())
    return out


def bin_dir(path, **overrides):
    """A PATH directory linking every executable on the current PATH, except that each
    override name links to its given target, or is left out when the target is None."""
    path.mkdir()
    for name, target in overrides.items():
        if target is not None:
            (path / name).symlink_to(target)
    for d in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(d):
            continue
        for e in os.scandir(d):
            link = path / e.name
            try:
                if e.name in overrides or os.path.lexists(link) or e.is_dir() or not os.access(e.path, os.X_OK):
                    continue
            except OSError:
                continue
            link.symlink_to(e.path)
    return path


def stub_install_py(checkout, exit_code):
    """Replace the checkout's install.py by a stub that records its arguments."""
    (checkout / "claude/install.py").write_text(
        "import json, sys, pathlib\n"
        "pathlib.Path(__file__).with_name('stub-ran.json').write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.exit({exit_code})\n")
    return checkout / "claude/stub-ran.json"


def old_python(tmp_path):
    """A python3 that reports version 3.9.18 however it is asked."""
    site = tmp_path / "fakesite"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import sys, collections\n"
        "V = collections.namedtuple('version_info', 'major minor micro releaselevel serial')\n"
        "sys.version_info = V(3, 9, 18, 'final', 0)\n"
        "sys.hexversion = 0x030912f0\n"
        "sys.version = '3.9.18 (fake)'\n")
    fake = tmp_path / "python3-old"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1" in --version|-V) echo "Python 3.9.18"; exit 0;; esac\n'
        f'PYTHONPATH="{site}" exec "{REAL_PYTHON}" "$@"\n')
    fake.chmod(0o755)
    return fake


def run_in(cwd, checkout, home, *args, **env_overrides):
    """run_installer, but with cwd outside the checkout."""
    assert (checkout / "claude/installer.sh").is_file(), "claude/installer.sh is not implemented yet"
    assert (checkout / "claude/install.py").is_file(), "claude/install.py is not implemented yet"
    return subprocess.run(["bash", str(checkout / "claude/installer.sh"), *map(str, args)], cwd=cwd,
                          env=base_env(home, **env_overrides), capture_output=True, text=True)


def installed_skills(home):
    return home / ".claude/skills"


# ---------------------------------------------------------------- Entry point


# @spec INST-CLI-001
@pytest.mark.parametrize("python", ["missing", "old"])
def test_python_missing_or_old_refuses_without_running_install_py(tmp_path, checkout, home, python):
    marker = stub_install_py(checkout, 0)
    target = None if python == "missing" else old_python(tmp_path)
    path = bin_dir(tmp_path / "bin", python3=target)
    proc = run_installer(checkout, home, PATH=path)
    refused(proc, "PYTHON_TOO_OLD")
    assert not marker.exists()


# @spec INST-CLI-002
def test_entry_point_passes_arguments_and_exit_code_through(checkout, home):
    marker = stub_install_py(checkout, 7)
    args = ["--project", "a dir with spaces", "--preset", "", "--x=1"]
    proc = run_installer(checkout, home, *args)
    assert proc.returncode == 7, proc.stderr
    assert json.loads(marker.read_text()) == args


# ---------------------------------------------------------------- Command line


# @spec INST-CLI-003
@pytest.mark.parametrize("args", [(), ("--global",)], ids=["default", "explicit"])
def test_install_defaults_to_global_scope(install, home, args):
    ok(install(*args))
    assert list(read_record(home)["installs"]) == [global_key(home)]
    assert entry(home, global_key(home))["scope"] == "global"


# @spec INST-CLI-003
def test_install_with_project_is_project_scope(install, home, repo):
    ok(install("--project", repo))
    assert list(read_record(home)["installs"]) == [project_key(repo)]
    assert entry(home, project_key(repo))["scope"] == "project"


@pytest.fixture
def models_file(tmp_path):
    f = tmp_path / "models.json"
    f.write_text("{}")
    return f


# @spec INST-CLI-004
@pytest.mark.parametrize("form", ["global+project", "plugin+project"])
def test_install_conflicting_scope_options_are_usage_errors(install, repo, form):
    args = {"global+project": ["--global", "--project", repo],
            "plugin+project": ["--plugin", "--project", repo]}[form]
    usage(install(*args))


# @spec INST-CLI-005
@pytest.mark.parametrize("form", ["plugin", "preset", "models", "vendor", "global+project"])
def test_uninstall_with_install_only_options_is_usage_error(install, repo, models_file, form):
    extra = {"plugin": ["--plugin"], "preset": ["--preset", "balanced"], "models": ["--models", models_file],
             "vendor": ["--vendor"], "global+project": ["--global", "--project", repo]}[form]
    usage(install("--uninstall", *extra))


# @spec INST-CLI-006
@pytest.mark.parametrize("form", ["global", "project", "plugin", "preset", "models", "uninstall", "force"])
def test_vendor_with_any_other_option_is_usage_error(install, repo, models_file, form):
    extra = {"global": ["--global"], "project": ["--project", repo], "plugin": ["--plugin"],
             "preset": ["--preset", "balanced"], "models": ["--models", models_file],
             "uninstall": ["--uninstall"], "force": ["--force"]}[form]
    usage(install("--vendor", *extra))


BAD_ARGS = {
    "unknown": ["--bogus"],
    "unknown-short": ["-g"],
    "unknown-with-uninstall": ["--uninstall", "--bogus"],
    "repeated-global": ["--global", "--global"],
    "repeated-plugin": ["--plugin", "--plugin"],
    "repeated-preset": ["--preset", "opus", "--preset", "opus"],
    "repeated-project": ["--project", "{repo}", "--project", "{repo}"],
    "repeated-models": ["--models", "{models}", "--models", "{models}"],
    "repeated-uninstall": ["--uninstall", "--uninstall"],
    "repeated-force": ["--uninstall", "--force", "--force"],
    "repeated-vendor": ["--vendor", "--vendor"],
    "missing-project": ["--project"],
    "missing-preset": ["--preset"],
    "missing-models": ["--models"],
    "missing-uninstall-project": ["--uninstall", "--project"],
    "bad-preset": ["--preset", "turbo"],
    "bad-preset-case": ["--preset", "Balanced"],
    "empty-preset": ["--preset", ""],
}


def bad_args(name, repo, models):
    return [a.format(repo=repo, models=models) for a in BAD_ARGS[name]]


# @spec INST-CLI-007
@pytest.mark.parametrize("name", list(BAD_ARGS))
def test_malformed_arguments_are_usage_errors(install, repo, models_file, name):
    usage(install(*bad_args(name, repo, models_file)))


# @spec INST-CLI-008
@pytest.mark.parametrize("name", ["unknown", "repeated-preset", "missing-project", "bad-preset"])
def test_usage_error_prints_code_and_usage_text(install, repo, models_file, name):
    proc = install(*bad_args(name, repo, models_file))
    usage(proc)
    rest = [line for line in proc.stderr.splitlines() if not line.startswith("forge-install: USAGE: ")]
    assert any(line.strip() for line in rest), proc.stderr


# @spec INST-CLI-008
@pytest.mark.parametrize("args", [["--bogus"], ["--global", "--project", "{repo}"], ["--force"],
                                  ["--vendor", "--global"], ["--uninstall", "--plugin"]])
def test_usage_error_writes_no_file(tmp_path, install, repo, models_file, args):
    before = snapshot(tmp_path)
    usage(install(*[a.format(repo=repo) for a in args]))
    assert snapshot(tmp_path) == before


# @spec INST-CLI-009
def test_project_key_is_real_path_of_repo(tmp_path, install, home, repo):
    link = tmp_path / "proj-link"
    link.symlink_to(repo)
    ok(install("--project", link))
    assert list(read_record(home)["installs"]) == [project_key(repo)]


# @spec INST-CLI-010
@pytest.mark.parametrize("where", ["subdirectory", "not-git", "missing", "linked-worktree"])
def test_project_not_a_repo_root_refuses(tmp_path, install, repo, where):
    if where == "subdirectory":
        target = repo / "sub"
        target.mkdir()
    elif where == "not-git":
        target = tmp_path / "plain"
        target.mkdir()
    elif where == "missing":
        target = tmp_path / "nowhere"
    else:
        target = tmp_path / "wt"
        git(repo, "worktree", "add", "-q", str(target))
    refused(install("--project", target), "NOT_A_REPO_ROOT")


# @spec INST-CLI-011
def test_uninstall_without_project_removes_config_install(install, home):
    ok(install())
    ok(install("--uninstall"))
    assert not record_path(home).exists() or global_key(home) not in read_record(home)["installs"]


# @spec INST-CLI-011
def test_uninstall_without_project_leaves_project_installs(install, home, repo):
    ok(install("--project", repo))
    refused(install("--uninstall"), "NOT_INSTALLED")
    assert list(read_record(home)["installs"]) == [project_key(repo)]


# @spec INST-CLI-011, INST-CLI-013
def test_uninstall_without_project_uses_claude_config_dir(tmp_path, install, home):
    cfg = tmp_path / "cfg"
    write_installed_plugins(cfg, {LID_PLUGIN: [{"scope": "user", "gitCommitSha": LID_COMMIT}]})
    ok(install(CLAUDE_CONFIG_DIR=cfg))
    ok(install("--uninstall", CLAUDE_CONFIG_DIR=cfg))
    assert not record_path(home).exists() or config_key(cfg) not in read_record(home)["installs"]


# @spec INST-CLI-012
@pytest.mark.parametrize("args", [["--force"], ["--global", "--force"], ["--project", "{repo}", "--force"],
                                  ["--preset", "opus", "--force"]])
def test_force_without_uninstall_is_usage_error(install, repo, args):
    usage(install(*[a.format(repo=repo) for a in args]))


# @spec INST-CLI-013
def test_config_dir_is_real_path_of_claude_config_dir(tmp_path, install, home):
    real = tmp_path / "cfg-real"
    write_installed_plugins(real, {LID_PLUGIN: [{"scope": "user", "gitCommitSha": LID_COMMIT}]})
    link = tmp_path / "cfg-link"
    link.symlink_to(real)
    ok(install(CLAUDE_CONFIG_DIR=link))
    assert list(read_record(home)["installs"]) == [config_key(real)]


# @spec INST-CLI-013
def test_empty_claude_config_dir_falls_back_to_home_claude(install, home):
    ok(install(CLAUDE_CONFIG_DIR=""))
    assert list(read_record(home)["installs"]) == [global_key(home)]


# @spec INST-CLI-013
def test_config_dir_is_real_path_of_home_claude(tmp_path, install, home):
    real = tmp_path / "claude-real"
    (home / ".claude").rename(real)
    (home / ".claude").symlink_to(real)
    ok(install())
    assert list(read_record(home)["installs"]) == [config_key(real)]


# @spec INST-CLI-013
def test_plugin_check_reads_claude_config_dir(tmp_path, install):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    refused(install(CLAUDE_CONFIG_DIR=cfg), "PLUGIN_MISSING")


# @spec INST-CLI-014
@pytest.mark.parametrize("form", ["install", "vendor"])
def test_git_missing_refuses(tmp_path, checkout, home, form):
    pin = init_repo(tmp_path / "pin")
    write_rev(checkout, {"pin": {"repo": str(pin), "commit": git(pin, "rev-parse", "HEAD")}})
    path = bin_dir(tmp_path / "bin", git=None, python3=REAL_PYTHON)
    args = ["--vendor"] if form == "vendor" else []
    refused(run_installer(checkout, home, *args, PATH=path), "GIT_MISSING")


# ---------------------------------------------------------------- State directory


# @spec INST-STATE-001
@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_state_dir_defaults_to_home_forge(install, home, value):
    ok(install(FORGE_HOME=value))
    assert entry(home, global_key(home))["state_dir"] == os.path.realpath(home / ".forge")


# @spec INST-STATE-001
def test_state_dir_is_real_path_of_forge_home(tmp_path, install, home):
    real = tmp_path / "state-real"
    real.mkdir()
    link = tmp_path / "state-link"
    link.symlink_to(real)
    ok(install(FORGE_HOME=link))
    assert entry(home, global_key(home))["state_dir"] == os.path.realpath(real)


# @spec INST-STATE-002
@pytest.mark.parametrize("rel", [".", "state", "a/b/state"], ids=["repo-root", "absent-child", "absent-deep"])
def test_state_dir_inside_git_work_tree_refuses(install, repo, rel):
    refused(install(FORGE_HOME=repo / rel), "STATE_DIR_IN_REPO")


# @spec INST-STATE-002
def test_default_state_dir_in_home_work_tree_refuses(install, home):
    git(home, "init", "-q")
    refused(install(), "STATE_DIR_IN_REPO")


# ---------------------------------------------------------------- Source tree


def source_files(skill_dir):
    """{relative path: bytes} of every file reachable under skill_dir, links followed,
    skipping tests/, __pycache__/ and dot names."""
    out = {}
    for root, dirs, files in os.walk(skill_dir, followlinks=True):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__") and not d.startswith(".")]
        for f in files:
            if not f.startswith("."):
                p = Path(root) / f
                out[p.relative_to(skill_dir)] = p.read_bytes()
    return out


# @spec INST-SRC-001
def test_skills_are_copied_as_regular_files_following_links(checkout, install, home):
    (checkout / "claude/skills/forge/LINKED.md").symlink_to("SKILL.md")
    ok(install())
    for skill in (checkout / "claude/skills").iterdir():
        for rel, data in source_files(skill).items():
            target = installed_skills(home) / skill.name / rel
            assert not target.is_symlink() and target.is_file(), target
            assert target.read_bytes() == data, target
    for root, dirs, files in os.walk(installed_skills(home)):
        for name in dirs + files:
            assert not os.path.islink(os.path.join(root, name)), os.path.join(root, name)


# @spec INST-SRC-002
def test_skill_copy_skips_tests_pycache_and_dot_names(checkout, install, home):
    forge = checkout / "claude/skills/forge"
    first_linked = sorted((checkout / "reference/skills").iterdir())[0].name
    junk = {
        forge / "tests/t.py": "forge/tests",
        forge / "deep/__pycache__/m.pyc": "forge/deep/__pycache__",
        forge / "deep/.hidden.md": "forge/deep/.hidden.md",
        forge / "deep/.dotdir/x.md": "forge/deep/.dotdir",
        checkout / "reference/scripts/__pycache__/c.pyc": "forge/scripts/__pycache__",
        checkout / "reference/skills" / first_linked / "tests/x.py": f"{first_linked}/tests",
    }
    for path in junk:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("junk\n")
    (forge / "deep/keep.md").write_text("keep\n")
    ok(install())
    for rel in junk.values():
        assert not os.path.lexists(installed_skills(home) / rel), rel
    assert (installed_skills(home) / "forge/deep/keep.md").read_text() == "keep\n"
    for root, dirs, files in os.walk(installed_skills(home)):
        for name in dirs + files:
            assert name not in ("tests", "__pycache__") and not name.startswith("."), os.path.join(root, name)


# @spec INST-SRC-003
def test_each_skill_directory_installs_as_skill_of_same_name(checkout, install, home):
    (checkout / "claude/skills/forge-extra").mkdir()
    (checkout / "claude/skills/forge-extra/SKILL.md").write_text("---\nname: forge-extra\n---\n")
    ok(install())
    source = {p.name for p in (checkout / "claude/skills").iterdir() if p.is_dir()}
    assert {p.name for p in installed_skills(home).iterdir()} == source


# @spec INST-SRC-004
def test_guard_paths_is_copied_into_forge_scripts(checkout, install, home):
    ok(install())
    copied = installed_skills(home) / "forge/scripts/guard_paths.py"
    assert copied.read_bytes() == (checkout / "claude/hooks/guard_paths.py").read_bytes()


# @spec INST-SRC-005
def test_source_and_sha_recorded(tmp_path, checkout, home):
    link = tmp_path / "forge-link"
    link.symlink_to(checkout)
    ok(run_installer(link, home))
    e = entry(home, global_key(home))
    assert e["source"] == os.path.realpath(checkout)
    assert e["source_sha"] == git(checkout, "rev-parse", "HEAD")


# @spec INST-SRC-005
def test_source_sha_null_outside_git(checkout, install, home):
    shutil.rmtree(checkout / ".git")
    ok(install())
    assert entry(home, global_key(home))["source_sha"] is None


# @spec INST-SRC-006
def test_clean_checkout_records_not_dirty(install, home):
    ok(install())
    assert entry(home, global_key(home))["source_dirty"] is False


# @spec INST-SRC-006
@pytest.mark.parametrize("change", ["modified-claude", "untracked-claude", "untracked-reference"])
def test_dirty_checkout_records_dirty_and_installs(checkout, install, home, change):
    if change == "modified-claude":
        with open(checkout / "claude/agents/forge-dev.md", "a") as f:
            f.write("\nlocal edit\n")
    elif change == "untracked-claude":
        (checkout / "claude/skills/forge/new.md").write_text("new\n")
    else:
        (checkout / "reference/scripts/new.txt").write_text("new\n")
    ok(install())
    assert entry(home, global_key(home))["source_dirty"] is True


# @spec INST-SRC-006
def test_source_dirty_null_outside_git(checkout, install, home):
    shutil.rmtree(checkout / ".git")
    ok(install())
    assert entry(home, global_key(home))["source_dirty"] is None


# @spec INST-SRC-007
def test_checkout_is_directory_holding_install_py(tmp_path, checkout, home):
    other = build_checkout(tmp_path / "other")
    (other / "claude/skills/forge-only-other").mkdir()
    (other / "claude/skills/forge-only-other/SKILL.md").write_text("x\n")
    ok(run_in(other, checkout, home))
    assert entry(home, global_key(home))["source"] == os.path.realpath(checkout)
    assert not os.path.lexists(installed_skills(home) / "forge-only-other")


def _broken_link(c):
    (c / "claude/skills/forge/broken.md").symlink_to("missing.md")


def _broken_skill_link(c):
    (c / "claude/skills/forge-ghost").symlink_to("../../reference/skills/forge-ghost")


def _cyclic_link(c):
    (c / "claude/skills/forge/loop").symlink_to(".")


def _skill_dir(name):
    def make(c):
        (c / "claude/skills" / name).mkdir()
        (c / "claude/skills" / name / "SKILL.md").write_text("x\n")
    return make


def _guard_in_scripts(c):
    (c / "reference/scripts/guard_paths.py").write_text("# stray\n")


# @spec INST-SRC-008
@pytest.mark.parametrize("breakage", [_broken_link, _broken_skill_link, _cyclic_link, _skill_dir("tests"),
                                      _skill_dir(".hidden"), _guard_in_scripts],
                         ids=["broken-link", "broken-skill-link", "cyclic-link", "tests-skill", "dot-skill",
                              "guard-already-in-scripts"])
def test_invalid_source_refuses(checkout, install, breakage):
    breakage(checkout)
    refused(install(), "SOURCE_INVALID")
