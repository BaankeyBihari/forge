"""Shared fixtures for the installer tests (docs/intent/installer/installer-specs.md).

Each test builds a throwaway Forge checkout (a git repository holding a copy of the real
claude/ and reference/ trees, with the repository's links recreated and a `rev`), and runs
its claude/installer.sh with HOME, CLAUDE_CONFIG_DIR and FORGE_HOME pointed into tmp_path,
so nothing touches the user's real ~/.claude, ~/.forge or repositories.

Test modules import the fixtures they use by name (a second conftest.py would collide with
reference/scripts/tests/conftest.py when both suites run together).

Run with: uvx pytest claude/tests
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LID_COMMIT = "9679360f382b5c265da073c4e06badd3afcca7d0"
LID_PLUGIN = "linked-intent-dev@jszmajda-lid"
VERSION = "2026.11.1"

os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# The default core.excludesFile ($XDG_CONFIG_HOME/git/ignore) is read even with GIT_CONFIG_GLOBAL unset.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="forge-test-xdg-")


def git(cwd, *args, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(path, files=None):
    """Create a git repository at path with one commit holding files ({relpath: text})."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "Test")
    for rel, text in (files or {"README.md": "x\n"}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def _copy(src, dst):
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("tests", "__pycache__", ".*"))


def build_checkout(path, rev=None):
    """A Forge checkout at path: claude/ and reference/ copied from the real repository,
    claude/skills/forge-* and the scripts links recreated as relative symlinks, `rev`
    written (default: linked-intent-dev pinned and required), committed to git."""
    (path / "claude").mkdir(parents=True)
    for name in ("installer.sh", "install.py"):
        if (ROOT / "claude" / name).exists():
            shutil.copy2(ROOT / "claude" / name, path / "claude" / name)
    _copy(ROOT / "claude/agents", path / "claude/agents")
    _copy(ROOT / "claude/hooks", path / "claude/hooks")
    _copy(ROOT / "reference/scripts", path / "reference/scripts")
    _copy(ROOT / "reference/skills", path / "reference/skills")
    for skill_scripts in (path / "reference/skills").glob("*/scripts"):
        if skill_scripts.is_symlink():
            skill_scripts.unlink()
            skill_scripts.symlink_to("../../scripts")
    forge = path / "claude/skills/forge"
    forge.mkdir(parents=True)
    shutil.copy2(ROOT / "claude/skills/forge/SKILL.md", forge / "SKILL.md")
    (forge / "scripts").symlink_to("../../../reference/scripts")
    for skill in sorted((path / "reference/skills").iterdir()):
        (path / "claude/skills" / skill.name).symlink_to(f"../../reference/skills/{skill.name}")
    write_rev(path, rev)
    (path / "VERSION").write_text(VERSION + "\n")
    init_repo(path, files={})
    return path


def write_rev(checkout, rev=None):
    if rev is None:
        rev = {"linked-intent-dev": {"repo": "https://github.com/jszmajda/lid", "commit": LID_COMMIT,
                                     "plugin": LID_PLUGIN, "required": True}}
    (checkout / "rev").write_text(json.dumps(rev, indent=2) + "\n")


def write_installed_plugins(config, plugins):
    """plugins: {plugin_id: [{"scope": ..., "gitCommitSha": ...}, ...]}"""
    target = config / "plugins/installed_plugins.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": 2, "plugins": plugins}))


@pytest.fixture
def home(tmp_path):
    """A fake home directory with linked-intent-dev installed at the pinned commit (user scope)."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    write_installed_plugins(h / ".claude", {LID_PLUGIN: [{"scope": "user", "gitCommitSha": LID_COMMIT}]})
    return h


@pytest.fixture
def checkout(tmp_path):
    return build_checkout(tmp_path / "forge")


@pytest.fixture
def repo(tmp_path):
    """A project repository to install into."""
    return init_repo(tmp_path / "proj")


def base_env(home, **overrides):
    """Environment for an installer run: HOME set, CLAUDE_CONFIG_DIR and FORGE_HOME unset
    unless given in overrides (a None value removes the variable)."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "FORGE_HOME")}
    env["HOME"] = str(home)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def run_installer(checkout, home, *args, env=None, **env_overrides):
    """Run <checkout>/claude/installer.sh with args; returns the CompletedProcess."""
    script = checkout / "claude/installer.sh"
    assert script.is_file(), "claude/installer.sh is not implemented yet"
    assert (checkout / "claude/install.py").is_file(), "claude/install.py is not implemented yet"
    return subprocess.run(["bash", str(script), *map(str, args)], cwd=checkout,
                          env=env if env is not None else base_env(home, **env_overrides),
                          capture_output=True, text=True)


def record_path(home):
    return home / ".forge/install.json"


def read_record(home):
    return json.loads(record_path(home).read_text())


def config_key(path):
    return os.path.realpath(path)


FAKE_CLAUDE = r"""#!/usr/bin/env python3
# Fake `claude` for the installer tests: logs argv to $FAKE_CLAUDE_LOG and imitates the
# `claude plugin` subcommands the installer runs, editing the files Claude Code 2.1.330 edits.
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
fail = os.environ.get("FAKE_CLAUDE_FAIL")
if fail and fail in " ".join(args):
    print("fake failure", file=sys.stderr)
    sys.exit(1)
cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
plugins = cfg / "plugins"
plugins.mkdir(parents=True, exist_ok=True)


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    path.write_text(json.dumps(data, indent=2))


known_path, installed_path, settings_path = (plugins / "known_marketplaces.json",
                                             plugins / "installed_plugins.json", cfg / "settings.json")
known, installed = load(known_path, {}), load(installed_path, {"version": 2, "plugins": {}})
settings = load(settings_path, {})
if args[:3] == ["plugin", "marketplace", "add"]:
    where = os.path.realpath(args[3])
    name = json.loads(Path(where, ".claude-plugin/marketplace.json").read_text())["name"]
    known[name] = {"source": {"source": "directory", "path": where}, "installLocation": where}
    settings.setdefault("extraKnownMarketplaces", {})[name] = {"source": {"source": "directory", "path": where}}
elif args[:2] == ["plugin", "install"]:
    installed["plugins"][args[2]] = [{"scope": "user", "installPath": "/cache", "version": "x"}]
    settings.setdefault("enabledPlugins", {})[args[2]] = True
elif args[:2] == ["plugin", "uninstall"]:
    if args[2] not in installed["plugins"]:
        print("not found in installed plugins", file=sys.stderr)
        sys.exit(1)
    del installed["plugins"][args[2]]
    settings.get("enabledPlugins", {}).pop(args[2], None)
elif args[:3] == ["plugin", "marketplace", "remove"]:
    if args[3] not in known:
        print("Marketplace not found", file=sys.stderr)
        sys.exit(1)
    del known[args[3]]
    settings.get("extraKnownMarketplaces", {}).pop(args[3], None)
else:
    sys.exit(3)
save(known_path, known)
save(installed_path, installed)
save(settings_path, settings)
"""


def tool_path(bin_dir, with_claude=True):
    """A PATH holding only bash, python3, git, and (unless with_claude is false) the fake claude."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("bash", "python3", "git"):
        link = bin_dir / tool
        if not link.exists():
            link.symlink_to(shutil.which(tool))
    fake = bin_dir / "claude"
    if with_claude:
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
    elif fake.exists():
        fake.unlink()
    return str(bin_dir)


def claude_calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def install(checkout, home):
    """Callable: install(*args, **env_overrides) runs the installer from the default checkout."""
    def _run(*args, **env_overrides):
        return run_installer(checkout, home, *args, **env_overrides)
    return _run
