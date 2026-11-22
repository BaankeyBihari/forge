"""Tests for the installer's plain-files targets, agent profiles and models,
and settings (docs/intent/installer/installer-specs.md: INST-PLAIN, INST-PLUG-009 (plain side), INST-MODEL,
INST-SET). Black box: each test runs claude/installer.sh and inspects what it wrote.
"""

import json
import os
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from install_fixtures import LID_COMMIT, LID_PLUGIN, config_key, git, init_repo, read_record, write_installed_plugins
from install_fixtures import checkout, home, install, repo  # noqa: F401  (fixtures)

PRESETS = ["balanced", "sonnet", "opus", "fable-opus", "fable"]
TOP = ["opus", "sonnet", "opus", "fable", "fable"]
MID = ["sonnet", "sonnet", "opus", "opus", "fable"]
LOW = ["haiku", "sonnet", "sonnet", "sonnet", "sonnet"]
ROWS = {"forge-planner": TOP, "forge-reviewer": TOP,
        "forge-qa": MID, "forge-dev": MID, "forge-verifier": MID, "forge-council": MID,
        "forge-scout": LOW, "forge-publisher": LOW, "forge-reporter": LOW}


def preset_model(agent, preset):
    row = TOP if agent.startswith("forge-council-") else ROWS[agent]
    return row[PRESETS.index(preset)]


# --- helpers -----------------------------------------------------------------------------

def ok(proc):
    assert proc.returncode == 0, proc.stderr
    return proc


def refused(proc, code):
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert f"forge-install: {code}:" in proc.stderr, proc.stderr
    return proc


def agents(checkout):
    return sorted(p.stem for p in (checkout / "claude/agents").glob("*.md"))


def skills(checkout):
    return sorted(p.name for p in (checkout / "claude/skills").iterdir())


def global_config(home):
    return Path(config_key(home / ".claude"))


def other_config(tmp_path, name="cfg"):
    """A CLAUDE_CONFIG_DIR with linked-intent-dev installed, as the home fixture has."""
    cfg = tmp_path / name
    write_installed_plugins(cfg, {LID_PLUGIN: [{"scope": "user", "gitCommitSha": LID_COMMIT}]})
    return cfg


def state_dir(home):
    return os.path.realpath(home / ".forge")


def project_key(repo):
    return os.path.join(config_key(repo), ".claude")


def entry(home, key):
    return read_record(home)["installs"][str(key)]


def split_profile(text):
    lines = text.split("\n")
    assert lines[0] == "---", text
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1:])


def model_of(text):
    values = [line.split(":", 1)[1].strip().strip("'\"") for line in split_profile(text)[0]
              if line.startswith("model:")]
    assert len(values) == 1, text
    return values[0]


def hooks_span(fm):
    """(start, end) of the top-level `hooks:` key and its indented lines, or None."""
    for i, line in enumerate(fm):
        if line.startswith("hooks:"):
            j = i + 1
            while j < len(fm) and fm[j][:1] in (" ", "\t"):
                j += 1
            return i, j
    return None


def yaml_scalar(value):
    value = value.strip()
    if value[:1] == '"':
        return json.loads(value)
    if value[:1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def hook_command(text):
    """The guard hook's command, shell-split, after checking the block's shape."""
    fm = split_profile(text)[0]
    span = hooks_span(fm)
    assert span, fm
    items = [line.strip().lstrip("- ").strip() for line in fm[span[0]:span[1]]]
    pairs = [(k.strip(), v) for k, _, v in (item.partition(":") for item in items)]
    keys = [k for k, _ in pairs]
    assert "PreToolUse" in keys and "matcher" not in keys, fm
    assert [yaml_scalar(v) for k, v in pairs if k == "type"] == ["command"], fm
    commands = [v for k, v in pairs if k == "command"]
    assert len(commands) == 1, fm
    return shlex.split(yaml_scalar(commands[0]))


def set_template_models(checkout, model):
    for path in (checkout / "claude/agents").glob("*.md"):
        path.write_text(re.sub(r"(?m)^model: .*$", f"model: {model}", path.read_text(), count=1))


def add_persona(checkout, name):
    agents_dir = checkout / "claude/agents"
    text = (agents_dir / "forge-council-architect.md").read_text()
    (agents_dir / f"{name}.md").write_text(text.replace("forge-council-architect", name))


def installed_models(agents_dir, names):
    return {a: model_of((agents_dir / f"{a}.md").read_text()) for a in names}


def models_file(tmp_path, data):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data))
    return path


def exclude_lines(repo):
    path = repo / ".git/info/exclude"
    return path.read_text().splitlines() if path.exists() else []


def wanted_excludes(checkout):
    return sorted([f"/.claude/skills/{s}" for s in skills(checkout)]
                  + [f"/.claude/agents/{a}.md" for a in agents(checkout)]
                  + ["/.claude/settings.local.json"])


def settings(path):
    return json.loads(path.read_text())


def edit_rule(state):
    return f"Edit(//{state[1:]}/**)"


def assert_forge_settings(data, state):
    assert data["env"]["FORGE_HOME"] == state, data
    assert state in data["permissions"]["additionalDirectories"], data
    assert edit_rule(state) in data["permissions"]["allow"], data


def all_added(state):
    return {"env.FORGE_HOME": state, "permissions.additionalDirectories": [state],
            "permissions.allow": [edit_rule(state)]}


def backups(cfg):
    return sorted(p for p in cfg.iterdir() if p.name.startswith("settings.json.forge-backup-"))


def stamp(t):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))


# --- plain-files targets -----------------------------------------------------------------

# @spec INST-PLAIN-001
def test_global_install_writes_skills_and_agents_under_config(install, checkout, home):
    ok(install())
    cfg = global_config(home)
    for s in skills(checkout):
        assert (cfg / "skills" / s).is_dir(), s
    for a in agents(checkout):
        assert (cfg / "agents" / f"{a}.md").is_file(), a


# @spec INST-PLAIN-001
def test_global_install_follows_claude_config_dir(install, checkout, home, tmp_path):
    cfg = other_config(tmp_path)
    ok(install(CLAUDE_CONFIG_DIR=cfg))
    real = Path(config_key(cfg))
    for s in skills(checkout):
        assert (real / "skills" / s).is_dir(), s
    for a in agents(checkout):
        assert (real / "agents" / f"{a}.md").is_file(), a
    assert not (home / ".claude/skills").exists() and not (home / ".claude/agents").exists()


# @spec INST-PLAIN-002
def test_project_install_writes_skills_and_agents_under_repo(install, checkout, home, repo):
    ok(install("--project", repo))
    for s in skills(checkout):
        assert (repo / ".claude/skills" / s).is_dir(), s
    for a in agents(checkout):
        assert (repo / ".claude/agents" / f"{a}.md").is_file(), a
    assert not (home / ".claude/skills").exists() and not (home / ".claude/agents").exists()


# @spec INST-PLAIN-003
def test_every_global_profile_gets_guard_hook(install, checkout, home):
    ok(install())
    cfg = global_config(home)
    guard = str(cfg / "skills/forge/scripts/guard_paths.py")
    for a in agents(checkout):
        command = hook_command((cfg / "agents" / f"{a}.md").read_text())
        assert command == [f"FORGE_HOME={state_dir(home)}", "python3", guard], a
    assert Path(guard).is_file() and Path(state_dir(home)).is_dir()


# @spec INST-PLAIN-003
def test_every_project_profile_gets_guard_hook(install, checkout, home, repo):
    ok(install("--project", repo))
    guard = os.path.join(project_key(repo), "skills/forge/scripts/guard_paths.py")
    for a in agents(checkout):
        command = hook_command((repo / ".claude/agents" / f"{a}.md").read_text())
        assert command == [f"FORGE_HOME={state_dir(home)}", "python3", guard], a
    assert Path(guard).is_file()


# @spec INST-PLAIN-003
def test_guard_hook_quotes_paths_with_spaces(install, checkout, tmp_path):
    cfg = other_config(tmp_path, "claude config")
    state = tmp_path / "forge state"
    state.mkdir()
    ok(install(CLAUDE_CONFIG_DIR=cfg, FORGE_HOME=state))
    real = Path(config_key(cfg))
    guard = str(real / "skills/forge/scripts/guard_paths.py")
    for a in agents(checkout):
        command = hook_command((real / "agents" / f"{a}.md").read_text())
        assert command == [f"FORGE_HOME={os.path.realpath(state)}", "python3", guard], a
    assert Path(guard).is_file()


# @spec INST-PLAIN-004
def test_tracked_planned_file_refused(install, tmp_path):
    proj = init_repo(tmp_path / "tracked", files={".claude/agents/forge-dev.md": "mine\n"})
    (proj / ".claude/agents/forge-dev.md").unlink()  # still tracked, no longer on disk
    refused(install("--project", proj), "TRACKED_TARGET")


# @spec INST-PLAIN-004
def test_tracked_settings_local_refused(install, repo):
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.local.json").write_text("{}\n")
    git(repo, "add", "-f", ".claude/settings.local.json")  # -f: a user's global gitignore may list it
    git(repo, "commit", "-q", "-m", "track settings")
    refused(install("--project", repo), "TRACKED_TARGET")


# @spec INST-PLAIN-005, INST-PLAIN-007
def test_project_install_appends_exclude_line_per_top_level_path(install, checkout, repo):
    before = exclude_lines(repo)
    ok(install("--project", repo))
    after = exclude_lines(repo)
    assert after[:len(before)] == before
    assert sorted(after[len(before):]) == wanted_excludes(checkout)


# @spec INST-PLAIN-005
def test_project_install_records_appended_exclude_lines(install, checkout, home, repo):
    ok(install("--project", repo))
    assert sorted(entry(home, project_key(repo))["exclude_added"]) == wanted_excludes(checkout)


# @spec INST-PLAIN-005
def test_project_install_skips_exclude_line_already_present(install, checkout, home, repo):
    path = repo / ".git/info/exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in exclude_lines(repo)) + "/.claude/skills/forge\n")
    ok(install("--project", repo))
    assert exclude_lines(repo).count("/.claude/skills/forge") == 1
    expected = [line for line in wanted_excludes(checkout) if line != "/.claude/skills/forge"]
    assert sorted(entry(home, project_key(repo))["exclude_added"]) == expected


# @spec INST-PLAIN-006
def test_plain_install_prints_frontmatter_hook_caveat(install):
    proc = ok(install())
    out = proc.stdout + proc.stderr
    assert "-p" in out and "trust" in out.lower() and "guard" in out.lower() and "project agents" in out, out


# @spec INST-PLAIN-008
def test_uninstall_keeps_directories_above_top_level_paths(install, repo):
    ok(install("--project", repo))
    ok(install("--uninstall", "--project", repo))
    for d in (".claude", ".claude/skills", ".claude/agents"):
        assert (repo / d).is_dir(), d


# @spec INST-PLAIN-009
def test_project_reinstall_keeps_wanted_exclude_lines(install, checkout, home, repo):
    ok(install("--project", repo))
    ok(install("--project", repo))
    assert sorted(entry(home, project_key(repo))["exclude_added"]) == wanted_excludes(checkout)
    lines = exclude_lines(repo)
    for line in wanted_excludes(checkout):
        assert lines.count(line) == 1, line


# @spec INST-PLAIN-009
def test_project_reinstall_drops_unwanted_exclude_lines(install, checkout, home, repo):
    ok(install("--project", repo))
    (checkout / "claude/agents/forge-scout.md").unlink()
    ok(install("--project", repo))
    dropped = "/.claude/agents/forge-scout.md"
    added = entry(home, project_key(repo))["exclude_added"]
    assert dropped not in exclude_lines(repo) and dropped not in added
    assert sorted(added) == wanted_excludes(checkout)


# @spec INST-PLAIN-010
def test_exclude_file_link_stays_link(install, repo, tmp_path):
    path = repo / ".git/info/exclude"
    target = tmp_path / "elsewhere/exclude"
    target.parent.mkdir()
    target.write_text(path.read_text() if path.exists() else "")
    path.unlink(missing_ok=True)
    path.symlink_to(target)
    ok(install("--project", repo))
    assert path.is_symlink() and os.readlink(path) == str(target)
    assert "/.claude/skills/forge" in target.read_text().splitlines()


# @spec INST-PLUG-009
def test_plain_install_writes_no_forge_install_json(install, home):
    ok(install())
    assert not (global_config(home) / "skills/forge/scripts/forge_install.json").exists()


# --- agent profiles and models -----------------------------------------------------------

# @spec INST-MODEL-001
def test_agent_set_is_the_agents_md_files(install, checkout, home):
    add_persona(checkout, "forge-council-historian")
    (checkout / "claude/agents/README.txt").write_text("not an agent\n")
    ok(install())
    cfg = global_config(home)
    names = agents(checkout)
    assert "forge-council-historian" in names
    assert sorted(os.listdir(cfg / "agents")) == sorted(f"{a}.md" for a in names)
    assert sorted(entry(home, cfg)["models"]) == names


# @spec INST-MODEL-002
def test_profile_is_template_with_model_and_hook_only(install, checkout, home):
    ok(install("--preset", "sonnet"))
    cfg = global_config(home)
    for a in agents(checkout):
        template = (checkout / "claude/agents" / f"{a}.md").read_text()
        profile = (cfg / "agents" / f"{a}.md").read_text()
        t_fm, t_body = split_profile(template)
        p_fm, p_body = split_profile(profile)
        start, end = hooks_span(p_fm)
        p_fm = p_fm[:start] + p_fm[end:]
        assert p_body == t_body, a
        assert [x for x in p_fm if not x.startswith("model:")] == [x for x in t_fm if not x.startswith("model:")], a
        assert model_of(profile) == preset_model(a, "sonnet"), a


@pytest.mark.parametrize("where", ["removed", "in_body"])
# @spec INST-MODEL-003
def test_template_without_frontmatter_model_refused(install, checkout, where):
    path = checkout / "claude/agents/forge-dev.md"
    text = re.sub(r"(?m)^model: .*\n", "", path.read_text(), count=1)
    path.write_text(text + ("model: sonnet\n" if where == "in_body" else ""))
    refused(install(), "TEMPLATE_INVALID")


# @spec INST-MODEL-003
def test_template_matching_no_preset_row_refused(install, checkout):
    agents_dir = checkout / "claude/agents"
    text = (agents_dir / "forge-dev.md").read_text().replace("name: forge-dev", "name: forge-extra")
    (agents_dir / "forge-extra.md").write_text(text)
    refused(install(), "TEMPLATE_INVALID")


# @spec INST-MODEL-013
def test_template_with_hooks_key_refused(install, checkout):
    path = checkout / "claude/agents/forge-dev.md"
    path.write_text(path.read_text().replace("\nmodel:", "\nhooks: {}\nmodel:", 1))
    refused(install(), "TEMPLATE_INVALID")


def check_preset(install, checkout, home, preset, names):
    assert names
    set_template_models(checkout, "inherit")
    ok(install("--preset", preset))
    got = installed_models(global_config(home) / "agents", names)
    assert got == {a: preset_model(a, preset) for a in names}


@pytest.mark.parametrize("preset", PRESETS)
# @spec INST-MODEL-004
def test_preset_models_planner_reviewer_personas(install, checkout, home, preset):
    personas = [a for a in agents(checkout) if a.startswith("forge-council-")]
    assert personas
    check_preset(install, checkout, home, preset, ["forge-planner", "forge-reviewer", *personas])


@pytest.mark.parametrize("preset", PRESETS)
# @spec INST-MODEL-005
def test_preset_models_qa_dev_verifier_council(install, checkout, home, preset):
    check_preset(install, checkout, home, preset, ["forge-qa", "forge-dev", "forge-verifier", "forge-council"])


@pytest.mark.parametrize("preset", PRESETS)
# @spec INST-MODEL-006
def test_preset_models_scout_publisher_reporter(install, checkout, home, preset):
    check_preset(install, checkout, home, preset, ["forge-scout", "forge-publisher", "forge-reporter"])


@pytest.mark.parametrize("content, key", [
    (None, None),
    ("not json", None),
    ("[]", None),
    ('"opus"', None),
    ('{"forge-bogus": "opus"}', "forge-bogus"),
    ('{"forge-dev": ""}', "forge-dev"),
    ('{"forge-dev": "claude opus"}', "forge-dev"),
    ('{"forge-dev": 5}', "forge-dev"),
    ('{"default": null}', "default"),
], ids=["missing", "not-json", "list", "string", "unknown-agent", "empty-value", "whitespace-value",
        "number-value", "null-default"])
# @spec INST-MODEL-007
def test_invalid_models_file_refused(install, tmp_path, content, key):
    path = tmp_path / "models.json"
    if content is not None:
        path.write_text(content)
    proc = refused(install("--models", path), "MODELS_INVALID")
    if key:
        assert key in proc.stderr, proc.stderr


# @spec INST-MODEL-008
def test_models_alone_applies_over_balanced(install, checkout, home, tmp_path):
    set_template_models(checkout, "inherit")
    ok(install("--models", models_file(tmp_path, {"forge-dev": "haiku"})))
    names = agents(checkout)
    expected = {a: "haiku" if a == "forge-dev" else preset_model(a, "balanced") for a in names}
    assert installed_models(global_config(home) / "agents", names) == expected


# @spec INST-MODEL-008
def test_models_applies_over_given_preset(install, checkout, home, tmp_path):
    ok(install("--preset", "fable", "--models", models_file(tmp_path, {"forge-dev": "haiku"})))
    names = agents(checkout)
    expected = {a: "haiku" if a == "forge-dev" else preset_model(a, "fable") for a in names}
    assert installed_models(global_config(home) / "agents", names) == expected


# @spec INST-MODEL-008
def test_reinstall_with_models_alone_ignores_recorded_preset(install, checkout, home, tmp_path):
    ok(install("--preset", "fable"))
    ok(install("--models", models_file(tmp_path, {"forge-dev": "haiku"})))
    names = agents(checkout)
    expected = {a: "haiku" if a == "forge-dev" else preset_model(a, "balanced") for a in names}
    assert installed_models(global_config(home) / "agents", names) == expected


# @spec INST-MODEL-009
def test_reinstall_without_flags_keeps_recorded_models(install, checkout, home):
    ok(install("--preset", "fable"))
    ok(install())
    names = agents(checkout)
    assert installed_models(global_config(home) / "agents", names) == {a: preset_model(a, "fable") for a in names}


# @spec INST-MODEL-009
def test_reinstall_without_flags_gives_unrecorded_agent_balanced(install, checkout, home):
    ok(install("--preset", "fable"))
    add_persona(checkout, "forge-council-historian")
    ok(install())
    got = installed_models(global_config(home) / "agents", ["forge-council-historian", "forge-council-architect"])
    assert got == {"forge-council-historian": "opus", "forge-council-architect": "fable"}


# @spec INST-MODEL-010
def test_first_install_without_flags_uses_balanced(install, checkout, home):
    set_template_models(checkout, "inherit")
    ok(install())
    names = agents(checkout)
    assert installed_models(global_config(home) / "agents", names) == {a: preset_model(a, "balanced") for a in names}


# @spec INST-MODEL-011
def test_models_own_key_then_default(install, checkout, home, tmp_path):
    ok(install("--models", models_file(tmp_path, {"forge-dev": "haiku", "default": "claude-opus-4-1"})))
    names = agents(checkout)
    expected = {a: "haiku" if a == "forge-dev" else "claude-opus-4-1" for a in names}
    assert installed_models(global_config(home) / "agents", names) == expected


# @spec INST-MODEL-011
def test_models_without_default_falls_back_to_base(install, checkout, home, tmp_path):
    ok(install("--preset", "sonnet", "--models", models_file(tmp_path, {"forge-scout": "claude-opus-4-1"})))
    names = agents(checkout)
    expected = {a: "claude-opus-4-1" if a == "forge-scout" else preset_model(a, "sonnet") for a in names}
    assert installed_models(global_config(home) / "agents", names) == expected


# @spec INST-MODEL-012
def test_record_holds_every_resolved_model(install, checkout, home, tmp_path):
    ok(install("--preset", "opus", "--models", models_file(tmp_path, {"forge-dev": "haiku"})))
    expected = {a: "haiku" if a == "forge-dev" else preset_model(a, "opus") for a in agents(checkout)}
    assert entry(home, global_config(home))["models"] == expected


# --- settings ----------------------------------------------------------------------------

# @spec INST-SET-001
def test_global_install_creates_config_settings_json(install, home):
    ok(install())
    assert_forge_settings(settings(global_config(home) / "settings.json"), state_dir(home))


# @spec INST-SET-001
def test_global_settings_file_follows_claude_config_dir(install, home, tmp_path):
    cfg = other_config(tmp_path)
    ok(install(CLAUDE_CONFIG_DIR=cfg))
    assert_forge_settings(settings(cfg / "settings.json"), state_dir(home))
    assert not (home / ".claude/settings.json").exists()


# @spec INST-SET-001
def test_project_install_creates_settings_local_json(install, home, repo):
    ok(install("--project", repo))
    assert_forge_settings(settings(repo / ".claude/settings.local.json"), state_dir(home))
    assert not (home / ".claude/settings.json").exists()
    assert not (repo / ".claude/settings.json").exists()


# @spec INST-SET-002
def test_settings_name_forge_home_state_dir(install, home, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    ok(install(FORGE_HOME=state))
    real = os.path.realpath(state)
    data = settings(global_config(home) / "settings.json")
    assert_forge_settings(data, real)
    assert f"Edit(//{real.lstrip('/')}/**)" in data["permissions"]["allow"]


# @spec INST-SET-002
def test_settings_values_added_beside_existing_ones(install, home):
    path = global_config(home) / "settings.json"
    path.write_text(json.dumps({"model": "opus", "env": {"FOO": "1"}, "permissions": {"allow": ["Bash(ls:*)"]}}))
    ok(install())
    data = settings(path)
    assert data["model"] == "opus" and data["env"]["FOO"] == "1" and "Bash(ls:*)" in data["permissions"]["allow"]
    assert_forge_settings(data, state_dir(home))


# @spec INST-SET-003
def test_settings_added_records_every_value_added(install, home):
    ok(install())
    assert entry(home, global_config(home))["settings_added"] == all_added(state_dir(home))


# @spec INST-SET-003
def test_settings_added_omits_values_already_present(install, home):
    state = state_dir(home)
    path = global_config(home) / "settings.json"
    path.write_text(json.dumps({"env": {"FORGE_HOME": state}, "permissions": {"additionalDirectories": [state]}}))
    ok(install())
    added = entry(home, global_config(home))["settings_added"]
    assert added.get("permissions.allow") == [edit_rule(state)]
    assert not added.get("env.FORGE_HOME") and not added.get("permissions.additionalDirectories"), added
    assert settings(path)["permissions"]["additionalDirectories"].count(state) == 1


# @spec INST-SET-004
def test_foreign_forge_home_refused(install, home):
    (global_config(home) / "settings.json").write_text(json.dumps({"env": {"FORGE_HOME": "/elsewhere/forge"}}))
    proc = refused(install(), "FORGE_HOME_CONFLICT")
    assert "/elsewhere/forge" in proc.stderr and state_dir(home) in proc.stderr, proc.stderr


# @spec INST-SET-004
def test_recorded_forge_home_replaced(install, home, tmp_path):
    first, second = tmp_path / "state-a", tmp_path / "state-b"
    first.mkdir()
    second.mkdir()
    ok(install(FORGE_HOME=first))
    ok(install(FORGE_HOME=second))
    assert settings(global_config(home) / "settings.json")["env"]["FORGE_HOME"] == os.path.realpath(second)


# @spec INST-SET-005
def test_reinstall_keeps_wanted_settings_added(install, home):
    ok(install())
    ok(install())
    assert entry(home, global_config(home))["settings_added"] == all_added(state_dir(home))


# @spec INST-SET-005
def test_reinstall_removes_unwanted_settings_added(install, home, tmp_path):
    first, second = tmp_path / "state-a", tmp_path / "state-b"
    first.mkdir()
    second.mkdir()
    ok(install(FORGE_HOME=first))
    ok(install(FORGE_HOME=second))
    old, new = os.path.realpath(first), os.path.realpath(second)
    data = settings(global_config(home) / "settings.json")
    assert old not in data["permissions"]["additionalDirectories"]
    assert edit_rule(old) not in data["permissions"]["allow"]
    assert_forge_settings(data, new)
    assert entry(home, global_config(home))["settings_added"] == all_added(new)


@pytest.mark.parametrize("content", [
    "{", "[]", '"x"', '{"env": []}', '{"permissions": "x"}',
    '{"permissions": {"allow": "x"}}', '{"permissions": {"additionalDirectories": {}}}',
], ids=["not-json", "list", "string", "env-list", "permissions-string", "allow-string", "dirs-object"])
# @spec INST-SET-006
def test_invalid_global_settings_refused(install, home, content):
    (global_config(home) / "settings.json").write_text(content)
    refused(install(), "SETTINGS_INVALID")


# @spec INST-SET-006
def test_invalid_project_settings_refused(install, repo):
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.local.json").write_text('{"permissions": {"allow": "x"}}')
    refused(install("--project", repo), "SETTINGS_INVALID")


# @spec INST-SET-007
def test_settings_link_stays_link(install, home, tmp_path):
    target = tmp_path / "dotfiles/settings.json"
    target.parent.mkdir()
    target.write_text('{"model": "opus"}\n')
    link = global_config(home) / "settings.json"
    link.symlink_to(target)
    ok(install())
    assert link.is_symlink() and os.readlink(link) == str(target)
    data = settings(target)
    assert data["model"] == "opus"
    assert_forge_settings(data, state_dir(home))


# @spec INST-SET-007
def test_settings_written_two_space_indented_in_key_order(install, home):
    path = global_config(home) / "settings.json"
    path.write_text('{"zeta": 1, "alpha": {"y": 2, "b": 3}}')
    ok(install())
    text = path.read_text()
    data = json.loads(text)
    assert text.rstrip("\n") == json.dumps(data, indent=2)
    assert [k for k in data if k in ("zeta", "alpha")] == ["zeta", "alpha"]
    assert list(data["alpha"]) == ["y", "b"]


# @spec INST-SET-008
def test_settings_backed_up_once_before_change(install, home):
    cfg = global_config(home)
    original = '{"model": "opus"}\n'
    (cfg / "settings.json").write_text(original)
    proc = ok(install())
    found = backups(cfg)
    assert len(found) == 1, found
    m = re.fullmatch(r"settings\.json\.forge-backup-(\d{8}T\d{6}Z)", found[0].name)
    assert m, found[0].name
    taken = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    assert abs(time.time() - taken) < 600
    assert found[0].read_text() == original
    assert found[0].name in proc.stdout + proc.stderr
    assert os.path.realpath(entry(home, cfg)["backup"]) == str(found[0])


# @spec INST-SET-008
def test_settings_backup_name_gets_suffix_when_taken(install, home):
    cfg = global_config(home)
    original = '{"model": "opus"}\n'
    (cfg / "settings.json").write_text(original)
    now = int(time.time())
    taken = {stamp(t) for t in range(now - 5, now + 300)}
    for s in taken:
        (cfg / f"settings.json.forge-backup-{s}").write_text("old\n")
    ok(install())
    new = [p for p in backups(cfg) if p.read_text() == original]
    assert len(new) == 1, new
    m = re.fullmatch(r"settings\.json\.forge-backup-(\d{8}T\d{6}Z)-2", new[0].name)
    assert m and m.group(1) in taken, new[0].name


# @spec INST-SET-009
def test_settings_created_recorded_true_when_created(install, home):
    ok(install())
    assert entry(home, global_config(home))["settings_created"] is True


# @spec INST-SET-009
def test_settings_created_recorded_false_when_existing(install, home):
    (global_config(home) / "settings.json").write_text("{}\n")
    ok(install())
    assert entry(home, global_config(home))["settings_created"] is False


# @spec INST-SET-009
def test_reinstall_keeps_recorded_settings_created(install, home):
    ok(install())
    ok(install())
    assert entry(home, global_config(home))["settings_created"] is True
