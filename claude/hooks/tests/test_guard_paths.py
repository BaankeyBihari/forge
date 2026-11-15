"""Tests for the Claude Code PreToolUse adapter guard_paths.py (docs/intent/guard/guard-specs.md).

Reuses the reference suite's fixtures (temporary repo, FORGE_HOME, a created workstream).
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "reference/scripts/tests"))

from conftest import HOLDER_SESSION, forge_home, repo, workstream  # noqa: E402,F401  (fixtures)

ADAPTER = ROOT / "claude/hooks/guard_paths.py"


def hook(payload, raw=None):
    assert ADAPTER.is_file(), "guard_paths.py is not implemented yet"
    proc = subprocess.run([sys.executable, str(ADAPTER)], input=raw if raw is not None else json.dumps(payload),
                          capture_output=True, text=True)
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
