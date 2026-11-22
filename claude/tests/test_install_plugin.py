"""Tests for the installer's plugin mode and Forge's version (docs/intent/installer/installer-specs.md:
INST-PLUG, INST-UNI-004, INST-SRC-008 to INST-SRC-010). Black box: each test runs claude/installer.sh
with a PATH holding a fake `claude` (install_fixtures.FAKE_CLAUDE) that logs every call and edits
the plugin records the way Claude Code does.
"""

import json
import os
import re
import shlex

import pytest

from install_fixtures import VERSION, claude_calls, config_key, read_record, record_path, tool_path
from install_fixtures import checkout, home, install, repo  # noqa: F401  (fixtures)

AGENTS_DIR = "agents"


@pytest.fixture
def plugin(install, tmp_path):
    """Callable: plugin(*args, with_claude=True, **env) runs the installer with the fake claude on PATH."""
    log = tmp_path / "claude-calls.jsonl"

    def _run(*args, with_claude=True, **env):
        path = tool_path(tmp_path / ("bin" if with_claude else "bin-noclaude"), with_claude)
        return install(*args, PATH=path, FAKE_CLAUDE_LOG=log, **env)
    _run.log = log
    return _run


def ok(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def refused(proc, code):
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert re.search(rf"^forge-install: {code}: \S", proc.stderr, re.M), proc.stderr
    return proc


def cfg(home):
    return config_key(home / ".claude")


def build(home):
    return os.path.realpath(home / ".forge") + "/plugin"


def known(home, entries):
    path = home / ".claude/plugins/known_marketplaces.json"
    path.write_text(json.dumps(entries))


def frontmatter(text):
    lines = text.split("\n")
    return lines[1:lines.index("---", 1)]


# --- build ---------------------------------------------------------------------------------

# @spec INST-PLUG-001
def test_plugin_build_is_a_local_marketplace(plugin, checkout, home):
    ok(plugin("--plugin"))
    root = build(home)
    market = json.loads(open(f"{root}/.claude-plugin/marketplace.json").read())
    assert market["name"] == "forge-local"
    assert market["plugins"] == [{"name": "forge", "source": "./forge"}]
    for skill in (p.name for p in (checkout / "claude/skills").iterdir()):
        assert os.path.isfile(f"{root}/forge/skills/{skill}/SKILL.md"), skill
    for agent in (p.name for p in (checkout / "claude/agents").glob("*.md")):
        assert os.path.isfile(f"{root}/forge/{AGENTS_DIR}/{agent}"), agent
    assert not os.path.exists(home / ".claude/agents/forge-dev.md")
    assert not os.path.exists(home / ".claude/skills/forge")


# @spec INST-PLUG-002
def test_plugin_manifest_names_forge_and_version(plugin, home):
    ok(plugin("--plugin"))
    assert json.loads(open(f"{build(home)}/forge/.claude-plugin/plugin.json").read()) == \
        {"name": "forge", "version": VERSION}


def hook_command(home):
    hooks = json.loads(open(f"{build(home)}/forge/hooks/hooks.json").read())["hooks"]
    assert list(hooks) == ["PreToolUse"], hooks
    (group,) = hooks["PreToolUse"]
    assert "matcher" not in group, group
    (hook,) = group["hooks"]
    assert hook["type"] == "command", hook
    return hook["command"]


# @spec INST-PLUG-003
def test_plugin_hook_runs_installed_guard(plugin, home):
    ok(plugin("--plugin"))
    command = hook_command(home)
    assert command.endswith(' python3 "${CLAUDE_PLUGIN_ROOT}"/skills/forge/scripts/guard_paths.py'), command
    assert shlex.split(command)[0] == f"FORGE_HOME={os.path.realpath(home / '.forge')}"
    assert os.path.isfile(f"{build(home)}/forge/skills/forge/scripts/guard_paths.py")


# @spec INST-PLUG-003
def test_plugin_hook_quotes_state_dir(plugin, home, tmp_path):
    state = tmp_path / "state with 'quote"
    ok(plugin("--plugin", FORGE_HOME=state))
    command = json.loads(open(f"{os.path.realpath(state)}/plugin/forge/hooks/hooks.json").read()) \
        ["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert shlex.split(command)[0] == f"FORGE_HOME={os.path.realpath(state)}"


# @spec INST-PLUG-004
def test_plugin_profiles_carry_model_but_no_hooks(plugin, home):
    ok(plugin("--plugin"))
    for profile in (home / ".forge/plugin/forge" / AGENTS_DIR).glob("*.md"):
        fm = frontmatter(profile.read_text())
        assert not any(line.startswith("hooks:") for line in fm), profile
        assert sum(line.startswith("model:") for line in fm) == 1, profile


# @spec INST-PLUG-005
def test_plugin_without_claude_refused(plugin, home):
    refused(plugin("--plugin", with_claude=False), "CLAUDE_MISSING")
    assert not record_path(home).exists()
    assert not os.path.exists(build(home))


# @spec INST-PLUG-006
def test_forge_local_elsewhere_refused(plugin, home, tmp_path):
    known(home, {"forge-local": {"source": {"source": "directory", "path": str(tmp_path)},
                                 "installLocation": str(tmp_path)}})
    refused(plugin("--plugin"), "MARKETPLACE_CONFLICT")
    assert claude_calls(plugin.log) == []
    assert not os.path.exists(build(home))


# @spec INST-PLUG-007
def test_plugin_registers_marketplace_then_installs(plugin, home):
    ok(plugin("--plugin"))
    assert claude_calls(plugin.log) == [["plugin", "marketplace", "add", build(home)],
                                        ["plugin", "install", "forge@forge-local"]]


# @spec INST-PLUG-007
def test_plugin_reinstall_skips_marketplace_add(plugin, home):
    ok(plugin("--plugin"))
    plugin.log.unlink()
    ok(plugin("--plugin"))
    assert claude_calls(plugin.log) == [["plugin", "install", "forge@forge-local"]]


# @spec INST-PLUG-007
def test_failed_claude_command_stops_with_step_failed(plugin, home):
    proc = plugin("--plugin", FAKE_CLAUDE_FAIL="install")
    refused(proc, "STEP_FAILED")
    assert read_record(home)["installs"][cfg(home)]["complete"] is False


# @spec INST-PLUG-007
def test_settings_keep_claude_entries_and_forge_grants(plugin, home):
    ok(plugin("--plugin"))
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"] == {"forge@forge-local": True}
    assert "forge-local" in settings["extraKnownMarketplaces"]
    assert settings["env"]["FORGE_HOME"] == os.path.realpath(home / ".forge")


# @spec INST-PLUG-008
def test_plugin_record_entry(plugin, home):
    ok(plugin("--plugin"))
    e = read_record(home)["installs"][cfg(home)]
    assert (e["scope"], e["agent_prefix"], e["repo"], e["complete"]) == ("plugin", "forge:", None, True)
    assert all(path.startswith(build(home) + "/") for path in e["files"]), e["files"]


# @spec INST-PLUG-008
def test_plain_record_has_empty_prefix(plugin, home):
    ok(plugin())
    assert read_record(home)["installs"][cfg(home)]["agent_prefix"] == ""


# @spec INST-PLUG-009
def test_plugin_writes_forge_install_json(plugin, home):
    ok(plugin("--plugin"))
    path = f"{build(home)}/forge/skills/forge/scripts/forge_install.json"
    assert json.loads(open(path).read()) == {"agent_prefix": "forge:"}
    assert path in read_record(home)["installs"][cfg(home)]["files"]


# @spec INST-OUT-002
def test_plugin_summary_line(plugin, home):
    line = ok(plugin("--plugin")).stdout.strip().splitlines()[-1]
    assert line.startswith(f"forge-install: installed plugin plugin {cfg(home)} models=balanced files="), line


# --- uninstall -----------------------------------------------------------------------------

# @spec INST-UNI-004
def test_plugin_uninstall_runs_claude_and_removes_build(plugin, home):
    ok(plugin("--plugin"))
    plugin.log.unlink()
    ok(plugin("--uninstall"))
    assert claude_calls(plugin.log) == [["plugin", "uninstall", "forge@forge-local"],
                                        ["plugin", "marketplace", "remove", "forge-local"]]
    assert not os.path.exists(build(home))
    assert not record_path(home).exists()


# @spec INST-UNI-004
def test_plugin_uninstall_skips_entries_already_gone(plugin, home):
    ok(plugin("--plugin"))
    (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    known(home, {})
    plugin.log.unlink()
    ok(plugin("--uninstall"))
    assert claude_calls(plugin.log) == []
    assert not os.path.exists(build(home))


# --- source: VERSION and forge_install.json ------------------------------------------------

# @spec INST-SRC-009
def test_version_recorded(plugin, checkout, home):
    (checkout / "VERSION").write_text("2027.3.12\n")
    ok(plugin())
    assert read_record(home)["installs"][cfg(home)]["version"] == "2027.3.12"


# @spec INST-SRC-010
@pytest.mark.parametrize("content", [None, "", "2026.11", "2026.13.1", "2026.011.1", "2026.11.0",
                                     "v2026.11.1", "2026.11.1\n\n", " 2026.11.1", "26.11.1"])
def test_bad_version_refused(plugin, checkout, home, content):
    if content is None:
        (checkout / "VERSION").unlink()
    else:
        (checkout / "VERSION").write_text(content)
    refused(plugin(), "SOURCE_INVALID")
    assert not record_path(home).exists()


# @spec INST-SRC-010
@pytest.mark.parametrize("content", ["2026.1.1", "2026.12.9", "2026.11.1"])
def test_good_version_without_newline_accepted(plugin, checkout, content):
    (checkout / "VERSION").write_text(content)
    ok(plugin())


# @spec INST-SRC-008
def test_forge_install_json_in_source_refused(plugin, checkout):
    (checkout / "reference/scripts/forge_install.json").write_text('{"agent_prefix": "forge:"}\n')
    refused(plugin(), "SOURCE_INVALID")


# --- overlap with plain files (unchanged rule, now reachable) -------------------------------

# @spec INST-OVL-005
def test_plain_install_refused_over_real_plugin_install(plugin, home):
    ok(plugin("--plugin"))
    refused(plugin(), "INSTALL_OVERLAP")
