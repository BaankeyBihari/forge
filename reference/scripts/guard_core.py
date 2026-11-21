#!/usr/bin/env python3
"""CLI-neutral guard decisions for Forge agents' tool calls.

Design: docs/intent/guard/guard-design.md. Specs: docs/intent/guard/guard-specs.md.

decide(call) takes the acting agent and the call (agent_type, agent_id, session_id, cwd,
scratchpad_dir, tool_name, tool_input, plus `target` for write tools or `command` for
shell calls) and returns {"allow": bool, "code": str | None, "reason": str}.
Each CLI adapter maps its own hook input onto this shape.
"""

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolve_state  # noqa: E402
import verify_gate  # noqa: E402

WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
BUILDERS = {"forge-planner", "forge-qa", "forge-dev"}
CLASS_OWNER = {"test": "forge-qa", "plan": "forge-planner", "production": "forge-dev"}
PARTITION_OWNER = {"planner": "forge-planner", "qa": "forge-qa", "dev": "forge-dev",
                   "verifier": "forge-verifier", "reviewer": "forge-reviewer", "scout": "forge-scout"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
GIT_FORBIDDEN = {"merge", "checkout", "switch", "worktree", "rebase", "reset", "clean", "stash", "restore",
                 "update-ref", "filter-branch"}
PUSH_FORBIDDEN = {"--force", "-f", "--force-with-lease", "--mirror", "--delete"}
GH_REPORTER = {("pr", "create"), ("pr", "edit"), ("pr", "review"), ("pr", "comment"), ("issue", "comment")}
GH_FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}
FINGERPRINT_TTL = 24 * 3600


def allow():
    return {"allow": True, "code": None, "reason": ""}


def block(code, reason):
    return {"allow": False, "code": code, "reason": reason}


def _inside(path, root):
    return root is not None and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def _real(path):
    return os.path.realpath(path) if path else None


# --------------------------------------------------------------- workstream


# @spec GUARD-WS-001
def find_workstream(checkout, session_id):
    """(state_dir, state) of the workstream whose lock holder is this session, or None."""
    base = checkout.repo_dir()
    if not base.is_dir():
        return None
    matches = []
    for lock_path in sorted(base.glob("*/lock.json")):
        lock = resolve_state.read_lock(lock_path.parent)
        if lock and lock.get("holder_session") == session_id:
            state, err = resolve_state.load_state(lock_path.parent / "state.json")
            if not err:
                matches.append((lock_path.parent, state))
    for sd, state in matches:  # prefer the workstream whose worktree contains cwd
        if _inside(checkout.root, _real((state.get("identity") or {}).get("worktree_path"))):
            return sd, state
    return matches[0] if matches else None


# ------------------------------------------------------------- guard record


# @spec GUARD-REC-001, GUARD-REC-003, GUARD-REC-004, GUARD-REC-005, GUARD-REC-006
def record_guard(agent, sd, state):
    """Adds agent to envelopes/<dispatch_id>.guard.json while a dispatch is out: evidence the guard ran."""
    d = (state.get("lifecycle") or {}).get("dispatched")
    if not d:
        return
    env = Path(sd) / "envelopes"
    env.mkdir(parents=True, exist_ok=True)
    path = env / f"{d.get('dispatch_id')}.guard.json"
    with open(env / f"{d.get('dispatch_id')}.guard.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file closes
        try:
            agents = json.loads(path.read_text()).get("agents")
        except (FileNotFoundError, ValueError, AttributeError):
            agents = None
        if not (isinstance(agents, list) and all(isinstance(a, str) for a in agents)):
            agents = []
        if agent not in agents:
            resolve_state.write_json_atomic(path, {"agents": agents + [agent]})


# ---------------------------------------------------------- circuit breaker


# @spec GUARD-DUP-001, GUARD-DUP-002, GUARD-DUP-003
def duplicate_check(call):
    guard_dir = resolve_state.forge_home() / "guard"
    now = time.time()
    if guard_dir.is_dir():
        for d in guard_dir.iterdir():
            if d.is_dir() and now - d.stat().st_mtime > FINGERPRINT_TTL:
                shutil.rmtree(d, ignore_errors=True)
    canonical = json.dumps(call.get("tool_input"), sort_keys=True, separators=(",", ":"))
    fingerprint = f"{call.get('tool_name')}:{hashlib.sha256(canonical.encode()).hexdigest()}"
    safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "_", str(s or "none"))  # noqa: E731
    path = guard_dir / safe(call.get("session_id")) / f"{safe(call.get('agent_id'))}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        history = []
    repeated = len(history) >= 3 and all(f == fingerprint for f in history[-3:])
    resolve_state.write_json_atomic(path, (history + [fingerprint])[-3:])
    if repeated:
        return block("DUPLICATE_CALL", "this is the fourth identical call in a row; change approach or stop")
    return None


# ------------------------------------------------------------- write targets


# @spec GUARD-WRITE-001, GUARD-WRITE-002, GUARD-WRITE-003, GUARD-WRITE-004, GUARD-WRITE-005
# @spec GUARD-WRITE-006, GUARD-WRITE-007, GUARD-WRITE-008, GUARD-WRITE-009
def check_write(agent, call, sd, state):
    target = _real(os.path.join(call.get("cwd") or ".", call["target"]))
    if agent.startswith("forge-council-"):
        return block("BOUNDARY_VIOLATION", f"council personas may not write files ({target})")
    worktree = _real((state.get("identity") or {}).get("worktree_path"))
    sd = _real(str(sd))
    if _inside(target, worktree):
        rel = os.path.relpath(target, worktree).replace(os.sep, "/")
        cls = verify_gate.classify_path(rel, (state.get("config") or {}).get("test_patterns"))
        if CLASS_OWNER[cls] == agent:
            return None
        reason = f"{target} is a {cls} path; only {CLASS_OWNER[cls]} may write it"
        if agent == "forge-dev" and cls == "test":
            reason += ". Stop and return TEST_CHANGE_REQUESTED with the change you need."
        return block("BOUNDARY_VIOLATION", reason)
    for part, owner in PARTITION_OWNER.items():
        if _inside(target, os.path.join(sd, "memory", part)):
            return None if agent == owner else block(
                "BOUNDARY_VIOLATION", f"memory/{part} belongs to {owner} ({target})")
    for sub, owner in (("scout", "forge-scout"), ("council", "forge-council")):
        if _inside(target, os.path.join(sd, sub)):
            return None if agent == owner else block("BOUNDARY_VIOLATION", f"{sub}/ belongs to {owner} ({target})")
    d = (state.get("lifecycle") or {}).get("dispatched")
    if d and agent == d.get("agent") and target == os.path.join(sd, "envelopes", f"{d.get('dispatch_id')}.json"):
        return None
    if _inside(target, _real(call.get("scratchpad_dir"))):
        return None
    return block("BOUNDARY_VIOLATION", f"{target} is outside every place {agent} may write")


# ------------------------------------------------------------------ commands


def split_parts(command):
    lexer = shlex.shlex(command.replace("\n", ";"), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    parts, current = [], []
    for token in lexer:
        if token in SEPARATORS:
            if current:
                parts.append(current)
            current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    return parts


def _strip_prefixes(words):
    i = 0
    while True:
        while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
            i += 1
        if i < len(words) and os.path.basename(words[i]) == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or "=" in words[i]):
                i += 2 if words[i] in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string") else 1
            continue
        return words[i:]


def _check_git(agent, args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    if sub == "push":
        for a in rest:
            short_force = a.startswith("-") and not a.startswith("--") and "f" in a[1:]
            if a in PUSH_FORBIDDEN or a.startswith("--force") or short_force or a.startswith("+") \
                    or re.match(r"^[^-].*:\+", a):
                return "force, mirror, delete, and + refspec pushes are never allowed"
        if agent != "forge-publisher":
            return "only forge-publisher may push"
    elif sub == "commit":
        if agent not in BUILDERS:
            return "only forge-planner, forge-qa, and forge-dev may commit"
        if "--amend" in rest:
            return "amending rewrites history and breaks drift tracking"
    elif sub in GIT_FORBIDDEN:
        return f"git {sub} is not allowed for Forge agents"
    elif sub == "branch" and any(a in ("-d", "-D", "--delete") or re.match(r"^-[a-zA-Z]*[dD]", a) for a in rest):
        return "deleting branches is not allowed"
    elif sub == "tag" and any(a in ("-d", "--delete") for a in rest):
        return "deleting tags is not allowed"
    return None


def _api_method(rest):
    for i, a in enumerate(rest):
        if a in ("-X", "--method") and i + 1 < len(rest):
            return rest[i + 1].upper()
        if a.startswith("--method="):
            return a.split("=", 1)[1].upper()
        if a.startswith("-X") and len(a) > 2:
            return a[2:].upper()
    return "POST" if any(a in GH_FIELD_FLAGS or a.split("=", 1)[0] in GH_FIELD_FLAGS for a in rest) else "GET"


def _check_gh(agent, args):
    if not args:
        return None
    sub = args[0]
    action = args[1] if len(args) > 1 else None
    if (sub, action) in GH_REPORTER:
        return None if agent == "forge-reporter" else f"only forge-reporter may run gh {sub} {action}"
    if sub == "api":
        method = _api_method(args[1:])
        if method != "GET" and agent != "forge-reporter":
            return f"only forge-reporter may call gh api with {method}"
        return None
    if (sub, action) == ("run", "rerun"):
        return None if agent == "forge-scout" else "only forge-scout may re-run CI"
    if (sub, action) == ("pr", "merge") or sub == "release" or (sub == "repo" and action not in ("view", "clone")):
        return f"gh {sub} {action or ''} is not allowed for Forge agents".replace("  ", " ")
    return None


def _check_words(agent, words, depth=0):
    words = _strip_prefixes(words)
    if not words:
        return None
    name = os.path.basename(words[0])
    if name in ("sh", "bash", "zsh") and depth < 5:
        i = 1
        has_c = False
        while i < len(words) and words[i].startswith("-") and words[i] != "--":
            if not words[i].startswith("--") and "c" in words[i][1:]:
                has_c = True
            i += 1
        if has_c and i < len(words):
            return check_command(agent, words[i], depth + 1)
        return None
    if name == "git":
        problem = _check_git(agent, words[1:])
    elif name == "gh":
        problem = _check_gh(agent, words[1:])
    else:
        return None
    if problem:
        return block("COMMAND_NOT_ALLOWED", f"'{shlex.join(words)}': {problem}")
    return None


# @spec GUARD-CMD-001, GUARD-CMD-002, GUARD-CMD-003, GUARD-CMD-004, GUARD-CMD-005, GUARD-CMD-006
# @spec GUARD-CMD-007, GUARD-CMD-008, GUARD-CMD-009, GUARD-CMD-010, GUARD-CMD-011
def check_command(agent, command, depth=0):
    for words in split_parts(command):
        found = _check_words(agent, words, depth)
        if found:
            return found
    return None


# --------------------------------------------------------------------- entry


# @spec GUARD-ORDER-001, GUARD-KILL-001, GUARD-WS-002, GUARD-REC-002
def decide(call):
    agent = call.get("agent_type") or ""
    try:
        checkout = resolve_state.Checkout(call.get("cwd") or ".")
    except resolve_state.GitError:
        checkout = None
    found = find_workstream(checkout, call.get("session_id")) if checkout else None
    if found:
        record_guard(agent, *found)  # before any block, so a blocked call still shows the guard ran
    if checkout:
        for root in (checkout.main_root(), checkout.root):
            if Path(root, ".STOP_FORGE").exists():
                return block("KILL_SWITCH", f"{Path(root, '.STOP_FORGE')} exists; Forge agents are stopped")
    if not found:
        return block("NO_ACTIVE_WORKSTREAM", "no Forge workstream in this repository is held by this session")
    sd, state = found
    dup = duplicate_check(call)
    if dup:
        return dup
    tool = call.get("tool_name")
    if tool in WRITE_TOOLS:
        return check_write(agent, call, sd, state) or allow()
    if tool == "Bash" and call.get("command") is not None:
        return check_command(agent, call["command"]) or allow()
    return allow()
