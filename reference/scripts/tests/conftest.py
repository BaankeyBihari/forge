"""Shared fixtures for the Forge script tests.

Every test runs against throwaway git repositories under pytest's tmp_path,
with FORGE_HOME pointed at a temporary directory and a fake `gh` first on PATH,
so nothing touches the user's real repositories, ~/.forge, or GitHub.

Run with: uvx pytest reference/scripts/tests
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
HOLDER_SESSION = "test-session"

# Ignore the user's global and system git config, so their hooks (core.hooksPath) and
# settings neither slow nor change the tests; each test repository sets its own identity.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


# --------------------------------------------------------------------------- git


def git(cwd, *args, env=None, check=True):
    """Run git in cwd and return stripped stdout."""
    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=full_env, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def head(cwd):
    return git(cwd, "rev-parse", "HEAD")


def commit(cwd, files, message="change", trailers=None, delete=()):
    """Write files ({path: content}), delete paths, and commit.

    trailers: dict of Forge trailer name -> value (or list of values for repeats).
    Returns the new commit SHA.
    """
    for rel, content in files.items():
        path = Path(cwd, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    for rel in delete:
        git(cwd, "rm", "-q", rel)
    git(cwd, "add", "-A")
    body = message
    if trailers:
        lines = []
        for key, value in trailers.items():
            for v in value if isinstance(value, list) else [value]:
                lines.append(f"{key}: {v}")
        body += "\n\n" + "\n".join(lines)
    git(cwd, "commit", "-q", "--allow-empty", "-m", body)
    return head(cwd)


def forge_trailers(agent, stage, mode, input_sha):
    return {
        "Forge-Agent": agent,
        "Forge-Stage": stage,
        "Forge-Mode": mode,
        "Forge-Input-SHA": input_sha,
    }


def init_repo(path, initial=True):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    if initial:
        commit(path, {"README.md": "hello\n"}, "initial")
    return path


@pytest.fixture
def repo(tmp_path):
    """A local repository (no origin) at tmp_path/project with one commit on main."""
    return init_repo(tmp_path / "project")


@pytest.fixture
def empty_repo(tmp_path):
    """A repository with no commits, on unborn branch main."""
    return init_repo(tmp_path / "empty", initial=False)


def set_origin(path, url):
    """Point origin at url (identity tests only: nothing is fetched from it)."""
    if git(path, "remote", check=False):
        git(path, "remote", "set-url", "origin", url)
    else:
        git(path, "remote", "add", "origin", url)


@pytest.fixture
def cloned_repo(tmp_path):
    """A clone of a local bare repository. Returns (clone_path, bare_path).

    origin is a local path, so the identity is local/<clone dir name>, but
    fetches and refs/remotes/origin/HEAD behave as for a real remote.
    """
    seed = init_repo(tmp_path / "seed")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    clone = tmp_path / "widgets"
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    git(clone, "config", "user.email", "test@example.invalid")
    git(clone, "config", "user.name", "Test")
    git(clone, "config", "commit.gpgsign", "false")
    return clone, bare


# ------------------------------------------------------------------ environment


@pytest.fixture
def forge_home(tmp_path, monkeypatch):
    home = tmp_path / "forge-home"
    home.mkdir()
    monkeypatch.setenv("FORGE_HOME", str(home))
    monkeypatch.delenv("FORGE_WORKSTREAM", raising=False)
    return home


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A fake `gh` on PATH driven by a JSON file.

    Write responses with fake_gh.set(key, payload) where key is "issue view" or
    "pr view"; payload is the JSON object printed. fake_gh.fail(key, message)
    makes that subcommand exit 1 with message on stderr. Every invocation's argv
    is appended to fake_gh.calls().
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    responses = tmp_path / "gh-responses.json"
    calls = tmp_path / "gh-calls.jsonl"
    responses.write_text("{}")
    script = bindir / "gh"
    script.write_text(
        f"""#!{sys.executable}
import json, sys
argv = sys.argv[1:]
with open({str(calls)!r}, "a") as f:
    f.write(json.dumps(argv) + "\\n")
key = " ".join(argv[:2])
data = json.load(open({str(responses)!r}))
entry = data.get(key)
if entry is None:
    sys.stderr.write("fake gh: no response for " + key + "\\n")
    sys.exit(1)
if entry.get("fail"):
    sys.stderr.write(entry["fail"])
    sys.exit(1)
print(json.dumps(entry["out"]))
"""
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    class FakeGh:
        def set(self, key, payload):
            data = json.loads(responses.read_text())
            data[key] = {"out": payload}
            responses.write_text(json.dumps(data))

        def fail(self, key, message):
            data = json.loads(responses.read_text())
            data[key] = {"fail": message}
            responses.write_text(json.dumps(data))

        def calls(self):
            if not calls.exists():
                return []
            return [json.loads(line) for line in calls.read_text().splitlines()]

    return FakeGh()


# ----------------------------------------------------------------- script runs


class Result:
    def __init__(self, proc):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr

    @property
    def json(self):
        return json.loads(self.stdout)


def run_script(name, *args, cwd, stdin=None, env=None):
    """Run reference/scripts/<name> with the current Python and return a Result."""
    # A missing script would satisfy "exits non-zero" and "changes nothing" assertions.
    assert (SCRIPTS / name).is_file(), f"{name} is not implemented yet"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=cwd,
        input=stdin,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )
    return Result(proc)


def holder_args(pid=None, session=HOLDER_SESSION):
    return ["--holder-pid", str(pid or os.getpid()), "--holder-session", session]


def load_module(name):
    """Import reference/scripts/<name>.py by path (the scripts are not a package)."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dead_pid():
    """A PID that is certainly not running: a child that has exited and been reaped."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ----------------------------------------------------------------- workstreams


def state_dir(forge_home, org, repo_name, workstream):
    return Path(forge_home, "state", org, repo_name, workstream)


def read_state(path):
    return json.loads(Path(path).read_text())


def write_state(path, state):
    Path(path).write_text(json.dumps(state, indent=2))


def write_guard_record(state_path, dispatched, agents=None):
    """Writes envelopes/<dispatch_id>.guard.json as the guard would, listing the dispatched agent by default."""
    env = Path(state_path).parent / "envelopes"
    env.mkdir(exist_ok=True)
    path = env / f"{dispatched['dispatch_id']}.guard.json"
    path.write_text(json.dumps({"agents": [dispatched["agent"]] if agents is None else agents}))
    return path


@pytest.fixture
def workstream(repo, forge_home):
    """A build workstream created with `resolve_state.py new --name demo`.

    Returns a dict with worktree, state_path, slug, and the created report.
    Depends on resolve_state.py `new` working; router tests build on it.
    """
    res = run_script(
        "resolve_state.py",
        "new",
        "--name",
        "demo",
        "--worktree-root",
        ".claude/worktrees",
        *holder_args(),
        cwd=repo,
    )
    assert res.returncode == 0, res.stderr
    report = res.json
    assert report["status"] == "CREATED", report
    worktree = Path(report["worktree_path"])
    sp = state_dir(forge_home, "local", "project", "feat-demo") / "state.json"
    return {
        "repo": repo,
        "worktree": worktree,
        "state_path": sp,
        "slug": "feat-demo",
        "report": report,
    }
