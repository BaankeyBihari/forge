#!/usr/bin/env python3
"""Forge workstream identity, creation, lock, and drift detection.

Design: docs/intent/workstream-state/workstream-state-design.md.
Specs: docs/intent/workstream-state/workstream-state-specs.md.

    resolve_state.py wake  [--workstream SLUG] --holder-pid PID --holder-session ID [--steal]
    resolve_state.py new   (--issue REF | --name SLUG | --branch NAME | --pr REF) [--type TYPE] [--task-file FILE]
                           [--base REF [--base-confirmed]] --worktree-root DIR
                           --holder-pid PID --holder-session ID [--set KEY=VALUE ...]
    resolve_state.py list
    resolve_state.py release --workstream SLUG --holder-pid PID --holder-session ID
    resolve_state.py close   --workstream SLUG --holder-pid PID --holder-session ID [--remove-worktree]

Every outcome with a status prints one JSON object and exits 0. Usage errors and
running outside a git repository exit non-zero with a message on stderr.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SCHEMA_VERSION = 1
PARTITIONS = ["hub", "shared", "planner", "qa", "dev", "verifier", "reviewer", "scout"]
TYPES = ["feat", "fix", "chore", "docs", "refactor"]
MODES = ["delivery", "collab-plan", "collab-qa", "collab-dev", "collab-code", "collab"]
CONFIG_DEFAULTS = {"mode": "collab", "retention": "archive"}


class UsageError(Exception):
    pass


class GitError(Exception):
    pass


# ------------------------------------------------------------------- basics


# @spec WS-STORE-001
def forge_home():
    return Path(os.environ.get("FORGE_HOME") or Path("~/.forge").expanduser())


# @spec WS-ID-003
def slug(text):
    s = re.sub(r"[^a-z0-9._-]", "-", text.lower())
    s = re.sub(r"-+", "-", s)
    return s.strip("-.")


def valid_slug(text):
    return bool(text) and slug(text) == text and ".." not in text


def git(cwd, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc


def git_out(cwd, *args):
    return git(cwd, *args).stdout.strip()


def git_ok(cwd, *args):
    return git(cwd, *args, check=False).returncode == 0


def now_iso(ts=None):
    dt = datetime.fromtimestamp(ts, timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# @spec WS-STATE-001
def write_json_atomic(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


write_state = write_json_atomic


# @spec WS-STATE-002
def load_state(path):
    """Return (state, None) or (None, reason) when the file does not parse or is too new."""
    try:
        state = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return None, f"cannot read state: {e}"
    if not isinstance(state, dict) or not isinstance(state.get("schema_version"), int) \
            or state["schema_version"] > SCHEMA_VERSION:
        return None, "unsupported schema_version"
    return state, None


# ------------------------------------------------------------------ identity


# @spec WS-ID-001, WS-ID-002
def parse_remote(url):
    """Return (org, repo) for a hosted remote with two or more path segments, else None."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        parsed = urlparse(url)
        if parsed.scheme == "file" or not parsed.hostname:
            return None
        path = parsed.path
    else:
        m = re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", url)
        if not m or url.startswith(("/", ".", "~")):
            return None
        path = m.group(2)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return None
    return "-".join(segments[:-1]), segments[-1]


class Checkout:
    """Facts about the repository and checkout the script runs in."""

    def __init__(self, cwd):
        proc = git(cwd, "rev-parse", "--show-toplevel", check=False)
        if proc.returncode != 0:
            raise GitError("not inside a git work tree")
        self.root = proc.stdout.strip()
        self.common = git_out(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        origin = git(self.root, "remote", "get-url", "origin", check=False)
        self.origin = origin.stdout.strip() if origin.returncode == 0 else None
        hosted = parse_remote(self.origin) if self.origin else None
        org, repo = hosted if hosted else ("local", os.path.basename(os.path.dirname(self.common)))
        self.org, self.repo = slug(org), slug(repo)
        self.has_commits = git_ok(self.root, "rev-parse", "--verify", "--quiet", "HEAD")

    # @spec WS-ID-006, WS-ID-007
    def branch(self):
        """Current branch name, or None when HEAD is detached."""
        if not self.has_commits:
            return git_out(self.root, "symbolic-ref", "--short", "HEAD")
        name = git_out(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        return None if name == "HEAD" else name

    def head(self):
        return git_out(self.root, "rev-parse", "HEAD") if self.has_commits else None

    def branch_workstream(self):
        branch = self.branch()
        if branch is None:
            return f"detached-{git_out(self.root, 'rev-parse', '--short=7', 'HEAD')}"
        return slug(branch)

    def main_root(self):
        first = git_out(self.root, "worktree", "list", "--porcelain").splitlines()[0]
        return first.split(" ", 1)[1]

    def repo_dir(self):
        return forge_home() / "state" / self.org / self.repo

    def identity(self, workstream):
        return {"org": self.org, "repo": self.repo, "workstream": workstream, "branch": self.branch(),
                "git_common_dir": self.common, "worktree_path": self.root}


# @spec WS-ID-004, WS-ID-005
def resolve_workstream(checkout, explicit):
    if explicit is None and os.environ.get("FORGE_WORKSTREAM"):
        explicit = os.environ["FORGE_WORKSTREAM"]
    if explicit is not None:
        if not valid_slug(explicit):
            raise UsageError(f"invalid workstream name {explicit!r}: use lowercase letters, digits, '.', '_', '-'")
        return explicit, True
    return checkout.branch_workstream(), False


# ---------------------------------------------------------------------- lock


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (ValueError, OverflowError):
        return False
    return True


def read_lock(sd):
    try:
        return json.loads((Path(sd) / "lock.json").read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        return {}  # being written by another process right now


def _lock_record(pid, session):
    return {"holder_pid": int(pid), "holder_session": session, "host": socket.gethostname(),
            "acquired_at": now_iso()}


# @spec WS-LOCK-001
def try_acquire(sd, pid, session):
    try:
        fd = os.open(Path(sd) / "lock.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        json.dump(_lock_record(pid, session), f)
    return True


def is_holder(lock, pid, session):
    return bool(lock) and lock.get("holder_pid") == int(pid) and lock.get("holder_session") == session


# @spec WS-LOCK-002, WS-LOCK-003, WS-LOCK-004, WS-LOCK-005, WS-LOCK-006
def take_lock(sd, pid, session, steal=False):
    """Return (ok, lock, cleared_dead_lock)."""
    if steal:
        write_json_atomic(Path(sd) / "lock.json", _lock_record(pid, session))
        return True, read_lock(sd), False
    cleared = False
    for _ in range(5):
        lock = read_lock(sd)
        if lock is None:
            if try_acquire(sd, pid, session):
                return True, read_lock(sd), cleared
            continue  # another process created it first: evaluate theirs
        if is_holder(lock, pid, session):
            return True, lock, cleared
        if lock and lock.get("host") == socket.gethostname() and not pid_alive(lock.get("holder_pid")):
            (Path(sd) / "lock.json").unlink(missing_ok=True)
            cleared = True
            continue
        return False, lock, cleared
    return False, read_lock(sd), cleared


# --------------------------------------------------------------------- drift


def _commit_kind(root, sha):
    trailer = git_out(root, "show", "-s", "--format=%(trailers:key=Forge-Agent,valueonly)", sha)
    return "forge" if trailer else "human"


# @spec WS-DRIFT-001, WS-DRIFT-002, WS-DRIFT-003, WS-DRIFT-004, WS-DRIFT-006, WS-DRIFT-007, WS-DRIFT-008
def detect_drift(checkout, state):
    """Return (drift, orphaned) or (None, None) when the anchor no longer exists."""
    last = (state.get("anchors") or {}).get("last_known_head_sha")
    head = checkout.head()
    none = {"kind": "none", "new_commits": [], "orphaned_commits": []}
    if not last or not head or last == head:
        return none, []
    root = checkout.root
    if not git_ok(root, "cat-file", "-e", f"{last}^{{commit}}"):
        return None, None
    ahead = git_ok(root, "merge-base", "--is-ancestor", last, head)
    behind = git_ok(root, "merge-base", "--is-ancestor", head, last)
    new = []
    orphaned = []
    if not behind:
        shas = git_out(root, "rev-list", "--first-parent", "--reverse", f"{last}..{head}").split()
        new = [{"sha": s, "kind": _commit_kind(root, s)} for s in shas]
    if not ahead:
        orphaned = git_out(root, "rev-list", "--reverse", f"{head}..{last}").split()
    kind = "ahead" if ahead else "behind" if behind else "diverged"
    return {"kind": kind, "new_commits": new, "orphaned_commits": orphaned}, orphaned


# @spec WS-DRIFT-009
def dirty_paths(checkout):
    out = git(checkout.root, "status", "--porcelain=v1", "-z", "-uall").stdout
    entries = out.split("\0")
    paths = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        if not entry:
            i += 1
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if status[0] in "RC":
            i += 1  # the rename source follows
        i += 1
    return paths


# ---------------------------------------------------------------------- list


# @spec WS-LIST-001, WS-LIST-002, WS-LIST-003
def list_workstreams(checkout):
    entries = []
    base = checkout.repo_dir()
    if not base.is_dir():
        return entries
    for sd in base.iterdir():
        sp = sd / "state.json"
        if not sp.is_file():
            continue
        mtime = sp.stat().st_mtime
        lock = read_lock(sd)
        lock_info = None
        if lock:
            lock_info = {**lock, "alive": lock.get("host") == socket.gethostname()
                         and pid_alive(lock.get("holder_pid"))}
        state, err = load_state(sp)
        if err:
            entry = {"workstream": sd.name, "status": "STATE_INVALID", "lock": lock_info}
        else:
            ident = state.get("identity") or {}
            lc = state.get("lifecycle") or {}
            entry = {"workstream": sd.name, "status": "OK", "branch": ident.get("branch"),
                     "worktree_path": ident.get("worktree_path"), "current_phase": lc.get("current_phase"),
                     "last_status": lc.get("last_status"), "lock": lock_info}
        entry["updated_at"] = now_iso(mtime)
        entries.append((mtime, entry))
    return [e for _, e in sorted(entries, key=lambda x: x[0], reverse=True)]


# ---------------------------------------------------------------------- wake


# @spec WS-ID-008, WS-ID-009, WS-ID-010, WS-ID-011, WS-WAKE-001, WS-WAKE-002, WS-WAKE-003, WS-WAKE-004
# @spec WS-DRIFT-005
def wake(cwd, workstream=None, holder_pid=None, holder_session=None, steal=False):
    """Wake report for the resolved workstream; the router imports this."""
    checkout = Checkout(cwd)
    ws, explicit = resolve_workstream(checkout, workstream)
    sd = checkout.repo_dir() / ws
    sp = sd / "state.json"
    report = {"identity": checkout.identity(ws), "state_path": str(sp), "lock": read_lock(sd) or None}

    if not sp.exists():
        if not explicit and checkout.branch() in ("main", "master"):
            return {"status": "LIST", **report, "workstreams": list_workstreams(checkout)}
        return {"status": "NO_STATE", **report}

    state, err = load_state(sp)
    if err:
        return {"status": "STATE_INVALID", **report, "detail": err}
    recorded = state.get("identity") or {}
    if recorded.get("git_common_dir") != checkout.common:
        return {"status": "IDENTITY_MISMATCH", **report, "detail": "state belongs to another clone"}
    build = recorded.get("flow", "build") == "build"
    if not explicit and build and recorded.get("branch") != checkout.branch():
        return {"status": "IDENTITY_MISMATCH", **report,
                "detail": f"state is for branch {recorded.get('branch')}"}
    if explicit and (recorded.get("worktree_path") != checkout.root
                     or (build and recorded.get("branch") != checkout.branch())):
        return {"status": "ELSEWHERE", **report,
                "elsewhere": {"branch": recorded.get("branch"), "worktree_path": recorded.get("worktree_path")}}

    ok, lock, cleared = take_lock(sd, holder_pid, holder_session, steal)
    report["lock"] = lock or None
    if not ok:
        return {"status": "LOCKED", **report}

    drift, orphaned = detect_drift(checkout, state)
    if drift is None:
        return {"status": "DRIFT_UNKNOWN", **report}
    stale = state["anchors"].setdefault("stale_shas", [])
    added = [s for s in orphaned if s not in stale]
    if added:
        stale.extend(added)
        write_state(sp, state)
    result = {"status": "OK", **report, "drift": drift, "dirty": dirty_paths(checkout)}
    if cleared:
        result["cleared_dead_lock"] = True
    return result


# ----------------------------------------------------------------------- new


def gh_json(cwd, *args):
    proc = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()
    return json.loads(proc.stdout), None


# @spec WS-NEW-001
def issue_slug(number, title):
    full = f"{number}-{slug(title)}".strip("-")
    if not slug(title):
        return str(number)
    if len(full) <= 48:
        return full
    cut = full[:49].rfind("-")
    return full[:cut] if cut > 0 else full[:48]


# @spec WS-NEW-007
def default_branch(checkout):
    root = checkout.root
    if checkout.origin:
        target = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
        if target.returncode == 0 and target.stdout.strip().startswith("origin/"):
            return target.stdout.strip()[len("origin/"):]
        for name in ("main", "master"):
            if git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}"):
                return name
    for name in ("main", "master"):
        if git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    return "main"


def open_states(checkout):
    base = checkout.repo_dir()
    if not base.is_dir():
        return []
    out = []
    for sd in base.iterdir():
        state, err = load_state(sd / "state.json")
        if not err:
            out.append((sd.name, state))
    return out


def checked_out_at(checkout, branch):
    current = None
    for line in git_out(checkout.root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = line.split(" ", 1)[1]
        elif line == f"branch refs/heads/{branch}":
            return current
    return None


# @spec WS-NEW-021
def detect_repo_mode(path):
    path = Path(path)
    if (path / "docs/high-level-design.md").is_file() or (path / "docs/intent").is_dir():
        return "LID"
    for name in ("AGENTS.md", "CLAUDE.md"):
        f = path / name
        if f.is_file() and re.search(r"^## LID\s*$", f.read_text(errors="replace"), re.M):
            return "LID"
    return "NAIVE"


# @spec WS-STORE-002, WS-STORE-003, WS-NEW-020
def build_config(sets):
    config = dict(CONFIG_DEFAULTS)
    try:
        config.update({k: v for k, v in json.loads((forge_home() / "config.json").read_text()).items()
                       if k in CONFIG_DEFAULTS})
    except FileNotFoundError:
        pass
    for item in sets:
        if "=" not in item:
            raise UsageError(f"--set expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        try:
            config[key] = json.loads(value)
        except ValueError:
            config[key] = value
    if config["mode"] not in MODES:
        raise UsageError(f"mode {config['mode']!r} is not one of {', '.join(MODES)}")
    return config


def initial_state(identity, config, base_sha):
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": identity,
        "config": config,
        "anchors": {"workstream_base_sha": base_sha, "plan_base_sha": None, "last_known_head_sha": None,
                    "verifier_pass_sha": None, "reviewer_pass_sha": None, "stale_shas": []},
        "lifecycle": {"current_phase": "INIT", "pass": None, "dispatched": None, "awaiting": None,
                      "resume_phase": None, "repair_queue": [], "attempts": {}, "blocker_counts": {},
                      "last_agent": None, "last_status": None},
        "scope_manifest": {"assigned": [], "completed": [], "remaining": []},
        "blockers": [],
        "council_decisions": [],
        "approvals": [],
    }


# @spec WS-NEW-001, WS-NEW-002, WS-NEW-003, WS-NEW-004, WS-NEW-005, WS-NEW-006, WS-NEW-008, WS-NEW-009
# @spec WS-NEW-010, WS-NEW-011, WS-NEW-012, WS-NEW-013, WS-NEW-014, WS-NEW-015, WS-NEW-016, WS-NEW-017
# @spec WS-NEW-018, WS-NEW-019, WS-NEW-022, WS-NEW-023, WS-NEW-024, WS-NEW-025
def new(cwd, args):
    checkout = Checkout(cwd)
    root = checkout.root
    config = build_config(args.set or [])
    issue_url = pr_url = None
    flow = "build"

    if args.name is not None and not valid_slug(args.name):
        raise UsageError(f"invalid --name {args.name!r}: use lowercase letters, digits, '.', '_', '-'")
    task = None
    if args.task_file is not None:
        try:
            task = Path(args.task_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise UsageError(f"cannot read --task-file {args.task_file}: {e}")
        if not task.strip():
            raise UsageError(f"--task-file {args.task_file} is empty")

    if args.issue:
        issue, err = gh_json(root, "issue", "view", args.issue, "--json", "number,title,labels,url")
        if err:
            return {"status": "GH_FAILED", "detail": err}
        issue_url = issue["url"]
        kind = args.type or ("fix" if any("bug" in lbl["name"].lower() for lbl in issue.get("labels", []))
                             else "feat")
        name = issue_slug(issue["number"], issue["title"])
        branch, ws = f"{kind}/{name}", f"{kind}-{name}"
    elif args.pr:
        pr, err = gh_json(root, "pr", "view", args.pr, "--json", "number,url,headRefName,baseRefName")
        if err:
            return {"status": "GH_FAILED", "detail": err}
        pr_url, flow = pr["url"], "pr_review"
        branch, ws = None, f"pr-{pr['number']}"
    elif args.branch:
        branch, ws = args.branch, slug(args.branch)
        if not git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            raise UsageError(f"no local branch {branch}")
    else:
        kind = args.type or "feat"
        branch, ws = f"{kind}/{args.name}", f"{kind}-{args.name}"

    for other, state in open_states(checkout):
        ident = state.get("identity") or {}
        if (issue_url and ident.get("issue") == issue_url) or (pr_url and ident.get("pr") == pr_url):
            return {"status": "DUPLICATE", "workstream": other}
    sd = checkout.repo_dir() / ws
    if sd.exists():
        return {"status": "STATE_EXISTS", "workstream": ws}
    if branch and not args.branch and git_ok(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        return {"status": "BRANCH_EXISTS", "branch": branch}

    default = default_branch(checkout)
    if args.base:
        base_ref = args.base
        base_branch = base_ref[len("origin/"):] if checkout.origin and base_ref.startswith("origin/") else base_ref
        if base_branch != default and not args.base_confirmed:
            return {"status": "BASE_CONFIRM_REQUIRED", "base": base_ref, "default_branch": default}
    elif pr_url:
        base_branch, base_ref = pr["baseRefName"], None
    else:
        base_branch = default
        if checkout.origin:
            git(root, "fetch", "--quiet", "origin", default)
            base_ref = f"origin/{default}"
        else:
            base_ref = default

    if args.branch:
        elsewhere = checked_out_at(checkout, branch)
        if elsewhere:
            return {"status": "BRANCH_CHECKED_OUT", "branch": branch, "worktree_path": elsewhere}

    wt_root = Path(args.worktree_root)
    if not wt_root.is_absolute():
        wt_root = Path(checkout.main_root()) / wt_root
    worktree = str(wt_root / ws)
    wt_root.mkdir(parents=True, exist_ok=True)
    if pr_url:
        git(root, "fetch", "--quiet", "origin", f"pull/{pr['number']}/head")
        base_sha = git_out(root, "rev-parse", "FETCH_HEAD")
        git(root, "worktree", "add", "--quiet", "--detach", worktree, base_sha)
    elif args.branch:
        base_sha = git_out(root, "rev-parse", f"refs/heads/{branch}")
        git(root, "worktree", "add", "--quiet", worktree, branch)
    else:
        base_sha = git_out(root, "rev-parse", f"{base_ref}^{{commit}}")
        git(root, "worktree", "add", "--quiet", "--no-track", "-b", branch, worktree, base_ref)
    worktree = git_out(worktree, "rev-parse", "--show-toplevel")

    parent = next((other for other, state in open_states(checkout)
                   if (state.get("identity") or {}).get("branch") == base_branch), None)
    identity = {"org": checkout.org, "repo": checkout.repo, "workstream": ws, "flow": flow, "branch": branch,
                "base_branch": base_branch, "issue": issue_url, "pr": pr_url, "parent_workstream": parent,
                "git_common_dir": checkout.common, "worktree_path": worktree}
    config = {"repo_mode": detect_repo_mode(worktree), **config}

    sd.mkdir(parents=True)
    for part in PARTITIONS:
        (sd / "memory" / part).mkdir(parents=True)
    (sd / "scout").mkdir()
    (sd / "council").mkdir()
    (sd / "envelopes").mkdir()
    write_state(sd / "state.json", initial_state(identity, config, base_sha))
    if task is not None:
        (sd / "task.md").write_text(task, encoding="utf-8")
    try_acquire(sd, args.holder_pid, args.holder_session)
    return {"status": "CREATED", "identity": identity, "worktree_path": worktree,
            "state_path": str(sd / "state.json")}


# ------------------------------------------------------------ release / close


# @spec WS-LOCK-007, WS-LOCK-008
def release(cwd, args):
    sd = Checkout(cwd).repo_dir() / args.workstream
    if not is_holder(read_lock(sd), args.holder_pid, args.holder_session):
        return {"status": "NOT_HOLDER", "lock": read_lock(sd)}
    (sd / "lock.json").unlink()
    return {"status": "RELEASED"}


# @spec WS-CLOSE-001, WS-CLOSE-002, WS-CLOSE-003, WS-CLOSE-004, WS-CLOSE-005, WS-CLOSE-006, WS-CLOSE-007
# @spec WS-CLOSE-008
def close(cwd, args):
    reason = None
    if args.reason_file is not None:
        try:
            reason = Path(args.reason_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise UsageError(f"cannot read --reason-file {args.reason_file}: {e}")
        if not reason:
            raise UsageError(f"--reason-file {args.reason_file} is empty")
    checkout = Checkout(cwd)
    sd = checkout.repo_dir() / args.workstream
    if not is_holder(read_lock(sd), args.holder_pid, args.holder_session):
        return {"status": "NOT_HOLDER", "lock": read_lock(sd)}
    state, err = load_state(sd / "state.json")
    if err:
        return {"status": "STATE_INVALID", "detail": err}
    if args.remove_worktree:
        worktree = state["identity"].get("worktree_path")
        proc = git(checkout.main_root(), "worktree", "remove", worktree, check=False)
        if proc.returncode != 0:
            return {"status": "WORKTREE_DIRTY", "detail": proc.stderr.strip()}
    if reason is not None:  # a drop always archives, whatever the retention
        state["dropped"] = {"reason": reason, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        write_json_atomic(sd / "state.json", state)
    elif (state.get("config") or {}).get("retention", "archive") == "delete":
        shutil.rmtree(sd)
        return {"status": "CLOSED", "retention": "delete"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = forge_home() / "archive" / checkout.org / checkout.repo / f"{args.workstream}-{stamp}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(sd), str(dest))
    return {"status": "CLOSED", "retention": "archive", "archive_path": str(dest)}


# ------------------------------------------------------------------------ CLI


def _holder(p):
    p.add_argument("--holder-pid", required=True, type=int)
    p.add_argument("--holder-session", required=True)


# @spec WS-CLI-001, WS-CLI-002, WS-CLI-003
def main(argv=None):
    parser = argparse.ArgumentParser(prog="resolve_state.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("wake")
    p.add_argument("--workstream")
    _holder(p)
    p.add_argument("--steal", action="store_true")
    p = sub.add_parser("new")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--issue")
    src.add_argument("--name")
    src.add_argument("--branch")
    src.add_argument("--pr")
    p.add_argument("--type", choices=TYPES)
    p.add_argument("--base")
    p.add_argument("--base-confirmed", action="store_true")
    p.add_argument("--worktree-root", required=True)
    _holder(p)
    p.add_argument("--set", action="append")
    p.add_argument("--task-file")
    sub.add_parser("list")
    for name in ("release", "close"):
        p = sub.add_parser(name)
        p.add_argument("--workstream", required=True)
        _holder(p)
        if name == "close":
            p.add_argument("--remove-worktree", action="store_true")
            p.add_argument("--reason-file")
    args = parser.parse_args(argv)
    cwd = os.getcwd()
    try:
        if args.command == "wake":
            result = wake(cwd, args.workstream, args.holder_pid, args.holder_session, args.steal)
        elif args.command == "new":
            result = new(cwd, args)
        elif args.command == "list":
            result = {"status": "OK", "workstreams": list_workstreams(Checkout(cwd))}
        elif args.command == "release":
            result = release(cwd, args)
        else:
            result = close(cwd, args)
    except UsageError as e:
        print(f"resolve_state.py: {e}", file=sys.stderr)
        return 2
    except GitError as e:
        print(f"resolve_state.py: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
