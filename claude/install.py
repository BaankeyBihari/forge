#!/usr/bin/env python3
"""Forge installer for Claude Code: copies the claude/ tree into a configuration directory
as plain files, records what it wrote, and removes exactly that on --uninstall.

Design: docs/intent/installer/installer-design.md. Run through claude/installer.sh.
"""

import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]  # @spec INST-SRC-007
RECORD = Path(os.path.expanduser("~/.forge/install.json"))
LOCK = Path(os.path.expanduser("~/.forge/install.lock"))

USAGE = """usage: claude/installer.sh [--global | --project <repo>] [--plugin] [--preset <name>] [--models <file>]
       claude/installer.sh --uninstall [--global | --project <repo>] [--force]
       claude/installer.sh --vendor"""

FLAGS = {"--global", "--plugin", "--uninstall", "--force", "--vendor"}
VALUED = {"--project", "--preset", "--models"}

PRESETS = {
    "balanced": {"top": "opus", "mid": "sonnet", "low": "haiku"},
    "sonnet": {"top": "sonnet", "mid": "sonnet", "low": "sonnet"},
    "opus": {"top": "opus", "mid": "opus", "low": "sonnet"},
    "fable-opus": {"top": "fable", "mid": "opus", "low": "sonnet"},
    "fable": {"top": "fable", "mid": "fable", "low": "sonnet"},
}
ROWS = {"forge-planner": "top", "forge-reviewer": "top",
        "forge-qa": "mid", "forge-dev": "mid", "forge-verifier": "mid", "forge-council": "mid",
        "forge-scout": "low", "forge-publisher": "low", "forge-reporter": "low"}

NOTICE = ("Note: Claude Code skips the frontmatter hooks of project agents (.claude/agents/) in -p sessions "
          "and in folders whose workspace "
          "trust was not accepted; the Forge router stops any dispatch whose guard did not run.")


class Usage(Exception):
    pass


class Refuse(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def say(message):
    print(f"forge-installer: {message}")


def warn(message):
    print(f"forge-installer: warning: {message}", file=sys.stderr)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    return sha(Path(path).read_bytes())


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def real(path):
    return os.path.realpath(os.path.expanduser(str(path)))


def pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- arguments

# @spec INST-CLI-003, INST-CLI-004, INST-CLI-005, INST-CLI-006, INST-CLI-007, INST-CLI-012
def parse(argv):
    opts, i = {}, 0
    while i < len(argv):
        arg = argv[i]
        if arg in opts:
            raise Usage(f"{arg} given more than once")
        if arg in FLAGS:
            opts[arg] = True
        elif arg in VALUED:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise Usage(f"{arg} needs a value")
            opts[arg] = argv[i + 1]
            i += 1
        else:
            raise Usage(f"unknown argument {arg!r}")
        i += 1
    if "--preset" in opts and opts["--preset"] not in PRESETS:
        raise Usage(f"unknown preset {opts['--preset']!r}; choose one of {', '.join(PRESETS)}")
    if "--vendor" in opts and len(opts) > 1:
        raise Usage("--vendor takes no other option")
    if "--global" in opts and "--project" in opts:
        raise Usage("--global and --project exclude each other")
    if "--force" in opts and "--uninstall" not in opts:
        raise Usage("--force works only with --uninstall")
    if "--uninstall" in opts:
        extra = {"--plugin", "--preset", "--models"} & set(opts)
        if extra:
            raise Usage(f"--uninstall does not take {', '.join(sorted(extra))}")
    elif "--plugin" in opts and "--project" in opts:
        raise Usage("--plugin installs only at global scope")
    return opts


# --------------------------------------------------------------------------- lock and record

@contextlib.contextmanager
# @spec INST-SEQ-001, INST-SEQ-002, INST-SEQ-003
def install_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            try:
                holder = json.loads(LOCK.read_text())
                pid = holder.get("pid") if isinstance(holder, dict) else None
            except (OSError, ValueError):
                pid = None
            if attempt or not isinstance(pid, int) or isinstance(pid, bool) or pid_alive(pid):
                raise Refuse("INSTALL_BUSY", f"another installer run holds {LOCK}")
            LOCK.unlink(missing_ok=True)  # its process is gone
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"pid": os.getpid()}))
    try:
        yield
    finally:
        LOCK.unlink(missing_ok=True)


# @spec INST-REC-001, INST-REC-008
def load_record():
    if not RECORD.exists():
        return {"installs": {}}
    try:
        data = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refuse("RECORD_INVALID", f"{RECORD} cannot be read: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("installs"), dict):
        raise Refuse("RECORD_INVALID", f"{RECORD} has no installs object")
    return data


# @spec INST-REC-007
def save_record(data):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_name("install.json.forge-tmp")
    tmp.write_text(dump(data), encoding="utf-8")
    os.replace(tmp, RECORD)


# @spec INST-REC-005, INST-REC-006
def claude_entries(record):
    """The record's live claude entries; stale project entries are removed from record and printed."""
    live = {}
    for key, e in list(record["installs"].items()):
        if not isinstance(e, dict) or e.get("cli") != "claude":
            continue
        if e.get("scope") == "project" and not os.path.isdir(str(e.get("repo"))):
            say(f"dropping record entry {key}: its repository no longer exists")
            del record["installs"][key]
            continue
        live[key] = e
    return live


# --------------------------------------------------------------------------- environment

# @spec INST-CLI-013
def config_dir():
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    return real(value) if value else real("~/.claude")


# @spec INST-STATE-001
def state_dir():
    value = os.environ.get("FORGE_HOME")
    return real(value) if value else real("~/.forge")


# @spec INST-STATE-002
def check_state_dir(state):
    probe = Path(state)
    while not probe.exists():
        probe = probe.parent
    result = git("rev-parse", "--is-inside-work-tree", cwd=probe)
    if result.returncode == 0 and result.stdout.strip() == "true":
        raise Refuse("STATE_DIR_IN_REPO", f"state directory {state} is inside a git working tree; "
                                          "set FORGE_HOME to a directory outside every repository")


# @spec INST-SRC-005, INST-SRC-006
def source_state():
    top = git("rev-parse", "--show-toplevel", cwd=CHECKOUT)
    if top.returncode != 0 or real(top.stdout.strip()) != str(CHECKOUT):
        return None, None
    head = git("rev-parse", "HEAD", cwd=CHECKOUT)
    status = git("status", "--porcelain", "--", "claude", "reference", cwd=CHECKOUT)
    dirty = bool(status.stdout.strip())
    if dirty:
        warn(f"{CHECKOUT} has uncommitted changes under claude/ or reference/; installing them as they are")
    return (head.stdout.strip() or None) if head.returncode == 0 else None, dirty


# --------------------------------------------------------------------------- pins

# @spec INST-PIN-001
def load_rev():
    path = CHECKOUT / "rev"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Refuse("REV_INVALID", f"{path} cannot be read: {e}")
    if not isinstance(data, dict):
        raise Refuse("REV_INVALID", f"{path} is not a JSON object")
    for name, pin in data.items():
        ok = (isinstance(pin, dict) and isinstance(pin.get("repo"), str)
              and isinstance(pin.get("commit"), str) and re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
              and ("plugin" not in pin or isinstance(pin["plugin"], str))
              and ("required" not in pin or isinstance(pin["required"], bool))
              and not (pin.get("required") and "plugin" not in pin))
        if not ok:
            raise Refuse("REV_INVALID", f"pin {name!r} in {path} needs string repo, 40-hex commit, "
                                        "string plugin, boolean required, and a plugin when required")
    return data


# @spec INST-PIN-002, INST-PIN-003, INST-PIN-004
def check_pins(pins, config):
    path = Path(config) / "plugins/installed_plugins.json"
    note = ""
    try:
        plugins = json.loads(path.read_text()).get("plugins", {})
        if not isinstance(plugins, dict):
            raise ValueError("no plugins object")
    except (OSError, ValueError, AttributeError) as e:
        plugins, note = {}, f" ({path} cannot be read: {e})"
    for name, pin in pins.items():
        if "plugin" not in pin:
            continue
        entries = plugins.get(pin["plugin"])
        counted = [e for e in entries if isinstance(e, dict) and e.get("scope") == "user"] \
            if isinstance(entries, list) else []
        if not counted:
            if pin.get("required"):
                raise Refuse("PLUGIN_MISSING", f"Claude Code plugin {pin['plugin']} is not installed "
                                               f"for the user{note}; install it first")
            continue
        for e in counted:
            if e.get("gitCommitSha") != pin["commit"]:
                warn(f"{pin['plugin']} is installed at {e.get('gitCommitSha')}, rev pins {pin['commit']}")


# --------------------------------------------------------------------------- source tree

def skipped(name):
    return name.startswith(".")


# @spec INST-SRC-001, INST-SRC-002, INST-SRC-003, INST-SRC-004, INST-SRC-008
def skill_sources():
    """{skill name: {relative path: (bytes, mode)}} for every skill under claude/skills."""
    root = CHECKOUT / "claude/skills"
    skills = {}
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() and not entry.exists():
            raise Refuse("SOURCE_INVALID", f"broken link {entry}")
        if not entry.is_dir():
            continue
        if entry.name == "tests" or entry.name.startswith("."):
            raise Refuse("SOURCE_INVALID", f"{entry} cannot be a skill")
        files = {}
        walk_skill(entry, Path(), {real(entry)}, files)
        skills[entry.name] = files
    if "forge" not in skills:
        raise Refuse("SOURCE_INVALID", f"{root}/forge is missing")
    for name in ("guard_paths.py", "forge_install.json"):
        if Path("scripts", name) in skills["forge"]:
            raise Refuse("SOURCE_INVALID", f"{root}/forge/scripts already holds a {name}")
    guard = CHECKOUT / "claude/hooks/guard_paths.py"
    skills["forge"][Path("scripts/guard_paths.py")] = (guard.read_bytes(), os.stat(guard).st_mode & 0o777)
    return skills


# @spec INST-SRC-009, INST-SRC-010
def read_version():
    path = CHECKOUT / "VERSION"
    try:
        text = path.read_text()
    except OSError as e:
        raise Refuse("SOURCE_INVALID", f"{path} cannot be read: {e}")
    version = text[:-1] if text.endswith("\n") else text
    if not re.fullmatch(r"[0-9]{4}\.([1-9]|1[0-2])\.[1-9][0-9]*", version):
        raise Refuse("SOURCE_INVALID", f"{path} must hold one line yyyy.m.count, such as 2026.11.1")
    return version


def walk_skill(directory, rel, stack, files):
    for entry in sorted(os.scandir(directory), key=lambda e: e.name):
        if skipped(entry.name):
            continue
        path = Path(entry.path)
        if path.is_symlink() and not path.exists():
            raise Refuse("SOURCE_INVALID", f"broken link {path}")
        if path.is_dir():
            if entry.name in ("tests", "__pycache__"):
                continue
            target = real(path)
            if target in stack:
                raise Refuse("SOURCE_INVALID", f"cyclic link {path}")
            walk_skill(path, rel / entry.name, stack | {target}, files)
        elif path.is_file():
            files[rel / entry.name] = (path.read_bytes(), os.stat(path).st_mode & 0o777)


# --------------------------------------------------------------------------- agents and models

def agent_row(name):
    return "top" if name.startswith("forge-council-") else ROWS.get(name)


# @spec INST-MODEL-001, INST-MODEL-003, INST-MODEL-013
def agent_templates():
    templates = {}
    for path in sorted((CHECKOUT / "claude/agents").glob("*.md")):
        text = path.read_text()
        lines = text.split("\n")
        try:
            end = lines.index("---", 1) if lines[0] == "---" else None
        except ValueError:
            end = None
        fm = lines[1:end] if end else []
        if not end or sum(line.startswith("model:") for line in fm) != 1:
            raise Refuse("TEMPLATE_INVALID", f"{path} needs exactly one model: line in its frontmatter")
        if any(line.startswith("hooks:") for line in fm):
            raise Refuse("TEMPLATE_INVALID", f"{path} already has a hooks: key")
        if agent_row(path.stem) is None:
            raise Refuse("TEMPLATE_INVALID", f"{path.stem} matches no row of the preset table")
        templates[path.stem] = lines
    return templates


# @spec INST-MODEL-007
def load_models_file(value, agents):
    try:
        data = json.loads(Path(value).read_text())
    except (OSError, ValueError) as e:
        raise Refuse("MODELS_INVALID", f"{value} cannot be read: {e}")
    if not isinstance(data, dict):
        raise Refuse("MODELS_INVALID", f"{value} is not a JSON object")
    for key, model in data.items():
        if key != "default" and key not in agents:
            raise Refuse("MODELS_INVALID", f"{key} is not a Forge agent")
        if not isinstance(model, str) or not re.fullmatch(r"\S+", model):
            raise Refuse("MODELS_INVALID", f"{key} needs a model name without whitespace")
    return data


# @spec INST-MODEL-004, INST-MODEL-005, INST-MODEL-006, INST-MODEL-008, INST-MODEL-009, INST-MODEL-010, INST-MODEL-011
def resolve_models(opts, agents, previous):
    preset = opts.get("--preset")
    if preset or "--models" in opts or previous is None:
        table = PRESETS[preset or "balanced"]
        base = {a: table[agent_row(a)] for a in agents}
        choice = preset or "balanced"
    else:
        recorded = previous.get("models") or {}
        base = {a: recorded.get(a) or PRESETS["balanced"][agent_row(a)] for a in agents}
        choice = "recorded"
    if "--models" in opts:
        overrides = load_models_file(opts["--models"], agents)
        base = {a: overrides.get(a, overrides.get("default", base[a])) for a in agents}
        choice += f"+{opts['--models']}"
    return base, choice


# @spec INST-MODEL-002, INST-PLAIN-003, INST-PLUG-004
def render_profile(lines, model, guard_command):
    end = lines.index("---", 1)
    fm = [f"model: {model}" if line.startswith("model:") else line for line in lines[1:end]]
    if guard_command is None:  # plugin: Claude Code ignores hooks in plugin agents
        return "\n".join(["---", *fm, *lines[end:]])
    fm += ["hooks:", "  PreToolUse:", "    - hooks:", "        - type: command",
           f"          command: {json.dumps(guard_command)}"]
    return "\n".join(["---", *fm, *lines[end:]])


# --------------------------------------------------------------------------- settings

# @spec INST-SET-006
def read_settings(path):
    if not os.path.lexists(path):
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refuse("SETTINGS_INVALID", f"{path} cannot be read: {e}")
    bad = not isinstance(data, dict)
    if not bad:
        env, perms = data.get("env", {}), data.get("permissions", {})
        bad = not isinstance(env, dict) or not isinstance(perms, dict) or any(
            not isinstance(perms.get(k, []), list) for k in ("allow", "additionalDirectories"))
    if bad:
        raise Refuse("SETTINGS_INVALID", f"{path} is not a valid settings object")
    return data


def empty_added():
    return {"env.FORGE_HOME": None, "permissions.additionalDirectories": [], "permissions.allow": []}


# @spec INST-SET-002, INST-SET-003, INST-SET-004, INST-SET-005
def apply_settings(data, state, previous_added):
    """Add Forge's values to data in place; returns what was added (recorded per INST-SET-003)."""
    prev = {**empty_added(), **(previous_added or {})}
    added = empty_added()
    env = data.setdefault("env", {})
    current = env.get("FORGE_HOME")
    if current == state:
        added["env.FORGE_HOME"] = state if prev["env.FORGE_HOME"] == state else None
    elif current is None or current == prev["env.FORGE_HOME"]:
        env["FORGE_HOME"] = state
        added["env.FORGE_HOME"] = state
    else:
        raise Refuse("FORGE_HOME_CONFLICT", f"settings set FORGE_HOME to {current}, this install uses {state}")
    perms = data.setdefault("permissions", {})
    for key, want in (("additionalDirectories", state), ("allow", f"Edit(//{state.lstrip('/')}/**)")):
        values = perms.setdefault(key, [])
        for old in prev[f"permissions.{key}"] or []:
            if old != want and old in values:
                values.remove(old)
        if want in values:
            added[f"permissions.{key}"] = [want] if want in (prev[f"permissions.{key}"] or []) else []
        else:
            values.append(want)
            added[f"permissions.{key}"] = [want]
    return added


# @spec INST-UNI-005, INST-UNI-006
def remove_settings(data, added):
    added = {**empty_added(), **(added or {})}
    env = data.get("env")
    if isinstance(env, dict) and added["env.FORGE_HOME"] and env.get("FORGE_HOME") == added["env.FORGE_HOME"]:
        del env["FORGE_HOME"]
        if not env:
            del data["env"]
    perms = data.get("permissions")
    if isinstance(perms, dict):
        touched = False
        for key in ("additionalDirectories", "allow"):
            values = perms.get(key)
            for value in added[f"permissions.{key}"] or []:
                if isinstance(values, list) and value in values:
                    values.remove(value)
                    touched = True
                    if not values:
                        del perms[key]
        if touched and not perms:
            del data["permissions"]


# @spec INST-SET-007, INST-SET-008
def write_settings(path, data, backup_global):
    """Write settings in place; back up an existing global settings file first. Returns the backup path."""
    path = Path(path)
    text = dump(data)
    backup = None
    if os.path.lexists(path):
        if path.read_text(encoding="utf-8") == text:
            return None
        if backup_global:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup, n = path.with_name(f"{path.name}.forge-backup-{stamp}"), 2
            while os.path.lexists(backup):
                backup, n = path.with_name(f"{path.name}.forge-backup-{stamp}-{n}"), n + 1
            shutil.copyfile(path, backup)
            say(f"backed up {path} to {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return str(backup) if backup else None


# --------------------------------------------------------------------------- files

def top_level(target, path, plugin_root=None):
    if plugin_root and Path(plugin_root) in Path(path).parents:
        return str(plugin_root)
    parts = Path(os.path.relpath(path, target)).parts
    return str(Path(target, *parts[:2])) if parts[0] in ("skills", "agents") else str(path)


# @spec INST-SEQ-006
def write_file(path, data, mode):
    path = Path(path)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.forge-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# @spec INST-PLAIN-008, INST-PLAIN-011
def prune(path, top):
    """Remove directories left empty from path's parent up to top, never above it.
    A directory holding only a __pycache__ directory (Python's bytecode cache) counts as empty."""
    d, top = Path(path).parent, Path(top)
    while d == top or top in d.parents:
        cache = d / "__pycache__"
        if cache.is_dir() and not cache.is_symlink() and [e.name for e in d.iterdir()] == ["__pycache__"]:
            shutil.rmtree(cache)
        try:
            d.rmdir()
        except OSError:
            return
        if d == top:
            return
        d = d.parent


# @spec INST-SEQ-010
def clean_tmp(target, tops):
    for top in tops:
        p = Path(top)
        candidates = p.rglob(".*.forge-tmp") if p.is_dir() else p.parent.glob(f".{p.name}.forge-tmp")
        for leftover in list(candidates):
            leftover.unlink(missing_ok=True)


# @spec INST-PLAIN-005, INST-PLAIN-009, INST-PLAIN-010
def update_exclude(repo, add, remove):
    path = Path(repo) / ".git/info/exclude"
    text = path.read_text() if os.path.lexists(path) else ""
    lines = [line for line in text.splitlines() if line not in set(remove)]
    present = set(lines)
    lines += [line for line in add if line not in present]
    if os.path.lexists(path) or lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n" if lines else "")


# --------------------------------------------------------------------------- workstreams

# @spec INST-RUN-001
def check_workstreams(state_dirs):
    active = []
    for state in sorted({s for s in state_dirs if s}):
        for lock in sorted(Path(state).glob("state/*/*/*/lock.json")):
            try:
                holder = json.loads(lock.read_text())
                held = (not isinstance(holder, dict) or "host" not in holder
                        or holder["host"] != socket.gethostname() or pid_alive(holder.get("holder_pid")))
            except (OSError, ValueError):
                held = True
            if held:
                active.append("/".join(lock.parent.parts[-3:]))
    if active:
        raise Refuse("WORKSTREAM_ACTIVE", f"workstreams are running: {', '.join(active)}; finish them, "
                                          "or stop them with /forge stop and close the session")


# --------------------------------------------------------------------------- plugin

MARKETPLACE, PLUGIN_ID = "forge-local", "forge@forge-local"


def plugin_records(config):
    """(installed plugin ids, {marketplace name: installLocation}) from Claude Code's plugin records."""
    out = []
    for name, pick in (("installed_plugins.json", lambda d: d.get("plugins")),
                       ("known_marketplaces.json", lambda d: {k: (v or {}).get("installLocation")
                                                              for k, v in d.items() if isinstance(v, dict)})):
        try:
            data = pick(json.loads((Path(config) / "plugins" / name).read_text()))
        except (OSError, ValueError, AttributeError):
            data = None
        out.append(data if isinstance(data, dict) else {})
    return out


def run_claude(*args):
    result = subprocess.run(["claude", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise Refuse("STEP_FAILED", f"claude {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    say(f"ran claude {' '.join(args)}")


# @spec INST-PLUG-001, INST-PLUG-002, INST-PLUG-003, INST-PLUG-009
def plugin_build(root, state, version):
    """The plugin's own files under root (<state dir>/plugin): path -> (bytes, mode)."""
    command = f'FORGE_HOME={shlex.quote(state)} python3 "${{CLAUDE_PLUGIN_ROOT}}"/skills/forge/scripts/guard_paths.py'
    files = {
        ".claude-plugin/marketplace.json": {"name": MARKETPLACE, "owner": {"name": "Forge"},
                                            "plugins": [{"name": "forge", "source": "./forge"}]},
        "forge/.claude-plugin/plugin.json": {"name": "forge", "version": version},
        "forge/hooks/hooks.json": {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}},
        "forge/skills/forge/scripts/forge_install.json": {"agent_prefix": "forge:"},
    }
    return {os.path.join(root, rel): (dump(data).encode(), 0o644) for rel, data in files.items()}


# --------------------------------------------------------------------------- install

def uninstall_command(key, entry):
    if entry.get("scope") == "project":
        return f"claude/installer.sh --uninstall --project {shlex.quote(str(entry.get('repo')))}"
    return "claude/installer.sh --uninstall"


# @spec INST-OVL-001, INST-OVL-002, INST-OVL-003, INST-OVL-004, INST-OVL-005, INST-OVL-006, INST-OVL-010, INST-OVL-011
def check_overlap(entries, key, scope, config):
    if scope == "project" and key == config:
        raise Refuse("INSTALL_OVERLAP", f"{key} is the Claude Code configuration directory itself")
    clashes = []
    for k, e in entries.items():
        if k == key and e.get("scope") == scope:
            continue
        if scope == "global" and (e.get("scope") == "project" or k == key):
            clashes.append((k, e))
        elif scope == "project" and e.get("scope") in ("global", "plugin"):
            clashes.append((k, e))
        elif scope == "plugin" and (e.get("scope") == "project" or k == key):
            clashes.append((k, e))
    if clashes:
        names = "; ".join(f"{e.get('scope')} install {e.get('repo') or k} "
                          f"(remove it with {uninstall_command(k, e)})" for k, e in clashes)
        raise Refuse("INSTALL_OVERLAP", f"overlaps the existing {names}")


# @spec INST-CLI-009, INST-CLI-010, INST-CLI-014, INST-PLAIN-001, INST-PLAIN-002, INST-PLAIN-004, INST-PLAIN-006, INST-PLAIN-007, INST-OVL-007, INST-OVL-008, INST-OVL-009, INST-REC-002, INST-REC-003, INST-REC-004, INST-SEQ-004, INST-SEQ-005, INST-SEQ-007, INST-SEQ-008, INST-SEQ-009, INST-SEQ-011, INST-SET-001, INST-SET-009, INST-MODEL-012, INST-OUT-002
# @spec INST-PLUG-005, INST-PLUG-006, INST-PLUG-007, INST-PLUG-008
def install(opts):
    if not shutil.which("git"):
        raise Refuse("GIT_MISSING", "the installer needs git on PATH")
    config, state = config_dir(), state_dir()
    check_state_dir(state)
    if "--project" in opts:
        repo = real(opts["--project"])
        if not os.path.isdir(os.path.join(repo, ".git")) or os.path.islink(os.path.join(repo, ".git")):
            raise Refuse("NOT_A_REPO_ROOT", f"{repo} is not the root of a git repository's main worktree")
        scope, target = "project", os.path.join(repo, ".claude")
        settings_file = os.path.join(target, "settings.local.json")
        key = target
    else:
        plugin = bool(opts.get("--plugin"))
        repo, scope = None, "plugin" if plugin else "global"
        target = os.path.join(state, "plugin", "forge") if plugin else config
        settings_file, key = os.path.join(config, "settings.json"), config
    plugin_root = os.path.join(state, "plugin") if scope == "plugin" else None
    if plugin_root:
        if not shutil.which("claude"):
            raise Refuse("CLAUDE_MISSING", "a plugin install needs the claude command on PATH")
        where = plugin_records(config)[1].get(MARKETPLACE)
        if where is not None and real(where) != plugin_root:
            raise Refuse("MARKETPLACE_CONFLICT", f"a marketplace named {MARKETPLACE} is registered at {where}; "
                                                 f"remove it with: claude plugin marketplace remove {MARKETPLACE}")
    record = load_record()
    entries = claude_entries(record)
    check_overlap(entries, key, scope, config)
    previous = entries.get(key)
    if previous is not None:
        check_workstreams([previous.get("state_dir"), state])
    check_pins(load_rev(), config)

    skills, templates, version = skill_sources(), agent_templates(), read_version()
    models, choice = resolve_models(opts, list(templates), previous)
    guard = os.path.join(target, "skills/forge/scripts/guard_paths.py")
    guard_command = None if plugin_root else f"FORGE_HOME={shlex.quote(state)} python3 {shlex.quote(guard)}"
    planned = plugin_build(plugin_root, state, version) if plugin_root else {}  # path -> (bytes, mode)
    tops = [plugin_root] if plugin_root else []
    for name, files in skills.items():
        if not plugin_root:
            tops.append(os.path.join(target, "skills", name))
        for rel, content in files.items():
            planned[os.path.join(target, "skills", name, str(rel))] = content
    for name, lines in templates.items():
        path = os.path.join(target, "agents", f"{name}.md")
        if not plugin_root:
            tops.append(path)
        planned[path] = (render_profile(lines, models[name], guard_command).encode(), 0o644)
    hashes = {p: sha(data) for p, (data, _) in planned.items()}

    prev_files = (previous or {}).get("files") or {}
    for path in tops + list(planned):
        if os.path.lexists(path) and path not in prev_files and \
                not any(p.startswith(path + os.sep) for p in prev_files):
            raise Refuse("FILE_EXISTS", f"{path} exists and was not installed by Forge; move it away first")
    edited = [p for p, h in prev_files.items() if os.path.lexists(p) and
              (not os.path.isfile(p) or os.path.islink(p) or file_sha(p) not in (h, hashes.get(p)))]
    if edited:
        raise Refuse("FILES_EDITED", "installed files were edited since install: " + ", ".join(sorted(edited))
                     + "; move or delete them, or run --uninstall --force")
    exclude_wanted = []
    if scope == "project":
        rels = [os.path.relpath(p, repo) for p in list(planned) + [settings_file]]
        tracked = git("ls-files", "-z", "--", *rels, cwd=repo).stdout.split("\0")
        tracked = [t for t in tracked if t]
        if tracked:
            raise Refuse("TRACKED_TARGET", f"git tracks {', '.join(tracked)}; a project install writes only untracked files")
        exclude_wanted = ["/" + os.path.relpath(p, repo) for p in tops + [settings_file]]
    settings_existed = os.path.lexists(settings_file)
    settings_data = read_settings(settings_file)
    prev_added = (previous or {}).get("settings_added")
    planned_added = apply_settings(json.loads(json.dumps(settings_data)), state, prev_added)
    source_sha, source_dirty = source_state()

    exclude_text = Path(repo, ".git/info/exclude").read_text().splitlines() \
        if scope == "project" and os.path.lexists(os.path.join(repo, ".git/info/exclude")) else []
    prev_exclude = (previous or {}).get("exclude_added") or []
    exclude_new = [line for line in exclude_wanted if line not in exclude_text]
    entry = {
        "cli": "claude", "scope": scope, "repo": repo, "state_dir": state,
        "source": str(CHECKOUT), "source_sha": source_sha, "source_dirty": source_dirty,
        "version": version, "agent_prefix": "forge:" if plugin_root else "", "models": models,
        "files": {**hashes, **prev_files},
        "settings_file": settings_file,
        "settings_created": previous.get("settings_created", False) if previous else not settings_existed,
        "settings_added": merge_added(prev_added, planned_added),
        "exclude_added": list(dict.fromkeys(prev_exclude + exclude_new)),
        "backup": (previous or {}).get("backup"),
        "complete": False,
    }
    record["installs"][key] = entry
    save_record(record)

    step = "files"
    try:
        os.makedirs(state, exist_ok=True)
        clean_tmp(target, tops)
        for path, (data, mode) in planned.items():
            write_file(path, data, mode)
        for path in prev_files:
            if path not in planned and os.path.lexists(path):
                os.unlink(path)
                prune(path, top_level(target, path, plugin_root))
        say(f"wrote {len(planned)} files under {plugin_root or target}")
        if plugin_root:
            step = "claude plugin"
            if real(plugin_records(config)[1].get(MARKETPLACE) or "/nonexistent") != plugin_root:
                run_claude("plugin", "marketplace", "add", plugin_root)
            run_claude("plugin", "install", PLUGIN_ID)
        step = "settings"
        data = read_settings(settings_file)
        added = apply_settings(data, state, prev_added)
        backup = write_settings(settings_file, data, backup_global=scope != "project")
        if backup:
            entry["backup"] = backup
        step = "exclude"
        if scope == "project":
            update_exclude(repo, exclude_wanted, [line for line in prev_exclude if line not in exclude_wanted])
            present_before = set(exclude_text)
            entry["exclude_added"] = [line for line in exclude_wanted
                                      if line in prev_exclude or line not in present_before]
    except (OSError, Refuse) as e:
        raise Refuse("STEP_FAILED", f"the {step} step failed: {e}; rerun the installer to finish")
    entry.update(files=hashes, settings_added=added, complete=True)
    save_record(record)
    if not plugin_root:
        say(NOTICE)
    say(f"installed {'plugin' if plugin_root else 'plain'} {scope} {key} models={choice} files={len(planned)}")
    return 0


def merge_added(earlier, planned):
    merged = {**empty_added(), **(earlier or {})}
    if planned["env.FORGE_HOME"]:
        merged["env.FORGE_HOME"] = planned["env.FORGE_HOME"]
    for key in ("permissions.additionalDirectories", "permissions.allow"):
        merged[key] = list(dict.fromkeys((merged[key] or []) + planned[key]))
    return merged


# --------------------------------------------------------------------------- uninstall

# @spec INST-CLI-011, INST-UNI-001, INST-UNI-002, INST-UNI-003, INST-UNI-007, INST-UNI-008, INST-UNI-009, INST-UNI-010, INST-UNI-011, INST-UNI-012
def uninstall(opts):
    config = config_dir()
    key = os.path.join(real(opts["--project"]), ".claude") if "--project" in opts else config
    record = load_record()
    entries = claude_entries(record)
    entry = entries.get(key)
    if entry is None:
        raise Refuse("NOT_INSTALLED", f"no Forge install is recorded for {key}")
    check_workstreams([entry.get("state_dir")])
    files = entry.get("files") or {}
    plugin_root = os.path.join(entry.get("state_dir") or "", "plugin") if entry.get("scope") == "plugin" else None
    tops = sorted({top_level(key, p, plugin_root) for p in files})
    kept, failed, errors = {}, {}, []

    clean_tmp(key, tops)
    for path, digest in files.items():
        if not os.path.lexists(path):
            continue
        if not opts.get("--force") and (os.path.islink(path) or not os.path.isfile(path) or file_sha(path) != digest):
            kept[path] = digest
            say(f"kept edited file {path}")
            continue
        try:
            os.unlink(path)
            prune(path, top_level(key, path, plugin_root))
        except OSError as e:
            failed[path] = digest
            errors.append(f"removing {path}: {e}")

    if plugin_root:  # @spec INST-UNI-004
        installed, known = plugin_records(config)
        for wanted, args in ((PLUGIN_ID in installed, ("plugin", "uninstall", PLUGIN_ID)),
                             (MARKETPLACE in known, ("plugin", "marketplace", "remove", MARKETPLACE))):
            if wanted:
                try:
                    run_claude(*args)
                except (OSError, Refuse) as e:
                    errors.append(str(e))

    settings_failed = False
    settings_file = entry.get("settings_file")
    if settings_file and os.path.lexists(settings_file):
        try:
            data = read_settings(settings_file)
            remove_settings(data, entry.get("settings_added"))
            if data == {} and entry.get("settings_created"):
                os.unlink(settings_file)
            else:
                backup = write_settings(settings_file, data, backup_global=entry.get("scope") != "project")
                if backup:
                    entry["backup"] = backup
        except (OSError, Refuse) as e:
            settings_failed = True
            errors.append(f"settings {settings_file}: {e}")

    left = {**kept, **failed}
    keep_lines = []
    repo = entry.get("repo")
    if entry.get("scope") == "project" and repo:
        keep_lines = sorted({"/" + os.path.relpath(top_level(key, p), repo) for p in left})
        if settings_failed:
            keep_lines.append("/" + os.path.relpath(settings_file, repo))
        recorded = entry.get("exclude_added") or []
        try:
            update_exclude(repo, [], [line for line in recorded if line not in keep_lines])
        except OSError as e:
            errors.append(f"exclude: {e}")
            keep_lines = recorded
        for line in keep_lines:
            say(f"kept exclude line {line}; remove it once you have dealt with the file")

    if left or errors:
        entry.update(files=left, exclude_added=[line for line in keep_lines],
                     settings_added=entry.get("settings_added") if settings_failed else empty_added())
        record["installs"][key] = entry
    else:
        del record["installs"][key]
    if record["installs"]:
        save_record(record)
    else:
        RECORD.unlink(missing_ok=True)
    if errors:
        raise Refuse("STEP_FAILED", "; ".join(errors))
    if kept:
        raise Refuse("FILES_KEPT", "kept edited files: " + ", ".join(sorted(kept))
                     + "; move or delete them, or rerun with --force")
    say(f"uninstalled {key}")
    return 0


# --------------------------------------------------------------------------- vendor

# @spec INST-VEN-001, INST-VEN-002, INST-VEN-003
def vendor(opts):
    if not shutil.which("git"):
        raise Refuse("GIT_MISSING", "--vendor needs git on PATH")
    failures = []
    for name, pin in load_rev().items():
        dest = CHECKOUT / ".vendor" / name
        steps = [["fetch", "-q", "origin"]] if dest.exists() else []
        if not dest.exists():
            clone = git("clone", "-q", pin["repo"], str(dest))
            if clone.returncode != 0:
                failures.append(f"{name}: clone failed: {clone.stderr.strip()}")
                continue
        for args in steps + [["checkout", "-q", "--detach", pin["commit"]]]:
            result = git(*args, cwd=dest)
            if result.returncode != 0:
                failures.append(f"{name}: git {args[0]} failed: {result.stderr.strip()}")
                break
        else:
            say(f"vendored {name} at {pin['commit']}")
    if failures:
        raise Refuse("VENDOR_FAILED", "; ".join(failures))
    return 0


# --------------------------------------------------------------------------- main

# @spec INST-CLI-008, INST-OUT-001, INST-OUT-003, INST-OUT-004
def main(argv):
    try:
        opts = parse(argv)
    except Usage as e:
        print(f"forge-installer: USAGE: {e}\n{USAGE}", file=sys.stderr)
        return 2
    try:
        with install_lock():
            if opts.get("--vendor"):
                return vendor(opts)
            if opts.get("--uninstall"):
                return uninstall(opts)
            return install(opts)
    except Refuse as e:
        print(f"forge-installer: {e.code}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
