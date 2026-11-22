"""Tests for the Claude Code PreToolUse adapter guard_paths.py (docs/intent/guard/guard-specs.md).

Reuses the reference suite's fixtures (temporary repo, FORGE_HOME, a created workstream).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "reference/scripts/tests"))

from conftest import HOLDER_SESSION, forge_home, repo, workstream  # noqa: E402,F401  (fixtures)

ADAPTER = ROOT / "claude/hooks/guard_paths.py"


SCRIPTS = ROOT / "reference/scripts"


def hook(payload, raw=None, adapter=ADAPTER, argv=None, pythonpath=str(SCRIPTS)):
    """Runs the adapter; from the checkout, guard_core is found through PYTHONPATH as if installed beside it."""
    assert adapter.is_file(), "guard_paths.py is not implemented yet"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    proc = subprocess.run(argv or [sys.executable, str(adapter)],
                          input=raw if raw is not None else json.dumps(payload),
                          capture_output=True, text=True, env=env)
    return proc


def event(ws, agent, tool_name, tool_input, **extra):
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": HOLDER_SESSION,
        "cwd": str(ws["worktree"]),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "scratchpad_dir": str(ws["worktree"].parent / "scratch"),
        **extra,
    }
    if agent is not None:
        payload["agent_type"] = agent
        payload["agent_id"] = f"id-{agent}"
    return payload


# @spec GUARD-SCOPE-001
def test_non_forge_calls_untouched(workstream):
    (workstream["worktree"] / ".STOP_FORGE").write_text("")
    for agent in [None, "general-purpose", "Explore"]:
        proc = hook(event(workstream, agent, "Bash", {"command": "git push --force"}))
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), agent


# @spec GUARD-SCOPE-001
def test_unparseable_input_passes(workstream):
    for raw in ["not json", "[1, 2]", ""]:
        proc = hook(None, raw=raw)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), raw


# @spec GUARD-SCOPE-002
def test_internal_error_blocks_forge_call(workstream):
    proc = hook(event(workstream, "forge-dev", "Write", "not-an-object"))
    assert proc.returncode == 2 and proc.stderr


# @spec GUARD-ADAPT-001, GUARD-ADAPT-002, GUARD-ADAPT-004
def test_write_target_from_file_path(workstream):
    target = workstream["worktree"] / "tests/test_a.py"
    proc = hook(event(workstream, "forge-dev", "Write", {"file_path": str(target), "content": "x"}))
    assert proc.returncode == 2
    assert "BOUNDARY_VIOLATION" in proc.stderr and "TEST_CHANGE_REQUESTED" in proc.stderr


# @spec GUARD-ADAPT-002
def test_edit_target_from_file_path(workstream):
    target = workstream["worktree"] / "tests/test_a.py"
    proc = hook(event(workstream, "forge-dev", "Edit",
                      {"file_path": str(target), "old_string": "a", "new_string": "b"}))
    assert proc.returncode == 2


# @spec GUARD-ADAPT-002
def test_notebook_target_from_notebook_path(workstream):
    target = workstream["worktree"] / "tests/nb.ipynb"
    proc = hook(event(workstream, "forge-dev", "NotebookEdit", {"notebook_path": str(target), "new_source": "x"}))
    assert proc.returncode == 2 and "BOUNDARY_VIOLATION" in proc.stderr


# @spec GUARD-ADAPT-001, GUARD-ADAPT-004
def test_bash_command_mapped(workstream):
    proc = hook(event(workstream, "forge-dev", "Bash", {"command": "git push"}))
    assert proc.returncode == 2 and "COMMAND_NOT_ALLOWED" in proc.stderr


# @spec GUARD-ADAPT-003
def test_allowed_call_is_silent(workstream):
    target = workstream["worktree"] / "src/a.py"
    proc = hook(event(workstream, "forge-dev", "Write", {"file_path": str(target), "content": "x"}))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


# @spec GUARD-ADAPT-001
def test_session_and_scratchpad_mapped(workstream):
    scratch = workstream["worktree"].parent / "scratch"
    proc = hook(event(workstream, "forge-verifier", "Write", {"file_path": str(scratch / "n.txt"), "content": "x"}))
    assert proc.returncode == 0
    proc = hook(event(workstream, "forge-verifier", "Read", {"file_path": "x"}, session_id="stranger"))
    assert proc.returncode == 2 and "NO_ACTIVE_WORKSTREAM" in proc.stderr


OLD_PYTHON = """
import runpy, sys
sys.version_info = (3, 10, 9, "final", 0)
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit as e:
    print("imported" if "guard_core" in sys.modules else "not-imported")
    raise
"""


# @spec GUARD-ADAPT-005
def test_old_python_blocks_forge_calls_before_import(workstream):
    payload = event(workstream, "forge-verifier", "Read", {"file_path": "n.txt"})
    proc = hook(payload, argv=[sys.executable, "-c", OLD_PYTHON, str(ADAPTER)])
    assert proc.returncode == 2
    assert "PYTHON_TOO_OLD" in proc.stderr
    assert proc.stdout.strip() == "not-imported"


# @spec GUARD-ADAPT-005
def test_old_python_leaves_non_forge_calls_alone(workstream):
    payload = event(workstream, None, "Bash", {"command": "git push --force"})
    proc = hook(payload, argv=[sys.executable, "-c", OLD_PYTHON, str(ADAPTER)])
    assert (proc.returncode, proc.stderr) == (0, "")


# @spec GUARD-ADAPT-006
def test_imports_guard_core_from_its_own_directory(workstream, tmp_path):
    scripts = tmp_path / "skills" / "forge" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ADAPTER, scripts)
    for f in SCRIPTS.glob("*.py"):
        shutil.copy(f, scripts)
    decoy = tmp_path / "skills" / "reference" / "scripts"
    decoy.mkdir(parents=True)
    (decoy / "guard_core.py").write_text('raise RuntimeError("decoy guard_core imported")\n')
    payload = event(workstream, "forge-dev", "Write", {"file_path": str(workstream["worktree"] / "src/a.py"),
                                                       "content": "x"})
    proc = hook(payload, adapter=scripts / "guard_paths.py", pythonpath=None)
    assert (proc.returncode, proc.stderr) == (0, "")


# @spec GUARD-SCOPE-001
def test_plugin_agent_names_are_guarded(workstream):
    proc = hook(event(workstream, "forge:forge-dev", "Bash", {"command": "git push"}))
    assert proc.returncode == 2 and "COMMAND_NOT_ALLOWED" in proc.stderr


# @spec GUARD-SCOPE-001
@pytest.mark.parametrize("agent", ["forge:other", "forge:", "forgex-dev", "other:forge-dev"])
def test_other_prefixed_names_untouched(workstream, agent):
    (workstream["worktree"] / ".STOP_FORGE").write_text("")
    proc = hook(event(workstream, agent, "Bash", {"command": "git push --force"}))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", ""), agent


# @spec GUARD-SCOPE-003
def test_plugin_prefix_stripped_before_rules_and_record(workstream):
    state = json.loads(workstream["state_path"].read_text())
    state["lifecycle"]["dispatched"] = {"dispatch_id": "d-1", "agent": "forge-dev", "mode": "full",
                                        "base_sha": "0" * 40}
    workstream["state_path"].write_text(json.dumps(state))
    target = workstream["worktree"] / "src/a.py"
    proc = hook(event(workstream, "forge:forge-dev", "Write", {"file_path": str(target), "content": "x"}))
    assert (proc.returncode, proc.stderr) == (0, "")
    record = workstream["state_path"].parent / "envelopes" / "d-1.guard.json"
    assert json.loads(record.read_text()) == {"agents": ["forge-dev"]}
